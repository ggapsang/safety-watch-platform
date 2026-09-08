/* ISECO 데모 런타임 — 실 서버(Nest API + MQTT 브로커 + MediaMTX) 대체 stub.
   단일 HTML 아티팩트에서 대시보드를 그대로 구동하기 위한 목업 계층이다.
   · /api/*        → 메모리 목업 저장소 (events · cameras · settings)
   · ws://…:9001   → 미니 MQTT 브로커 (CONNACK/SUBACK/PINGRESP + 합성 퍼블리시)
   · WHEP          → canvas.captureStream 합성 영상 (실제 카메라 없음)
   실제 배포 코드에는 포함되지 않는다. */
(function () {
  "use strict";

  var MQTT_URL = "ws://demo.iseco.local:9001";
  window.__ISECO_CONFIG__ = { mediamtxUrl: "whep://demo", mqttWsUrl: MQTT_URL };

  // ── 결정적 PRNG (같은 날 = 같은 데이터) ────────────────────────────────────
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  var today0 = new Date(); today0.setHours(0, 0, 0, 0);
  var rnd = mulberry32(Math.floor(today0.getTime() / 86400000));
  function pick(arr) { return arr[Math.floor(rnd() * arr.length)]; }
  function ri(lo, hi) { return lo + Math.floor(rnd() * (hi - lo + 1)); }

  // ── 카메라 (cameras.yml SoT 모사 · 안전 13대) ──────────────────────────────
  var SITES = [
    ["1소성로 전실", "SOL-004"],
    ["2소성로 전실", "SOL-004"],
    ["원료 야드 A", "SOL-004"],
    ["폐열 보일러실", "SOL-004"],
    ["전기실 배전반", "SOL-004"],
    ["유압 펌프실", "SOL-004"],
    ["벨트컨베이어 3호", "SOL-004"],
    ["시멘트 사일로 2호", "SOL-004"],
    ["출하 상차장", "SOL-004"],
    ["크러셔 정비장", "SOL-005"],
    ["포장 라인 B", "SOL-005"],
    ["중앙 제어실 통로", "SOL-005"],
    ["폐기물 투입구", "SOL-005"]
  ];
  var PPE_TOPIC = "/onvif-ej/Device/tns1:Trigger/tns1:Relay/&Relay-1";
  var OFFLINE = { cam6: 1, cam13: 1 };

  var cameras = SITES.map(function (s, i) {
    var n = i + 1;
    var mac = "E4:30:22:F3:" + String(30 + Math.floor(i / 6)).padStart(2, "0") + ":" +
      (0xa0 + n).toString(16).toUpperCase();
    var id = "cam" + n;
    return {
      id: id,
      name: "현장 카메라 #" + n,
      location: s[0],
      sols: [s[1]],
      status: OFFLINE[id] ? "offline" : "normal",
      ip: "192.168.10." + (10 + n),
      mac: mac,
      mqttTopic: mac + (s[1] === "SOL-004" ? "/fireAlarm" : PPE_TOPIC),
      detectionSource: "HANHWA",
      model: "XNV-C9083R"
    };
  });
  var camById = {}; cameras.forEach(function (c) { camById[c.id] = c; });

  // ── 이벤트 (최근 180일, 서버 DTO 형태) ────────────────────────────────────
  var TYPE = { "SOL-004": "화재 감지", "SOL-005": "보호구 미착용" };
  var SEV = { "SOL-004": "critical", "SOL-005": "warning" };
  var STAFF = ["김민수", "이준호", "박서영", "최동현", "정하늘"];
  var NOTES = [
    "현장 확인 후 오탐으로 판정. 열원=이동식 히터.",
    "작업자 안전모 착용 재지시. 반장 교육 완료.",
    "설비 과열 확인, 냉각팬 교체 조치.",
    "출입 통제 후 정상 확인. 추가 조치 없음.",
    "보호구 미착용 3회 누적 — 부서장 통보.",
    "감지 구역 청소 및 렌즈 오염 제거."
  ];
  // 카메라별 발생 가중치 — 특정 구역이 상습 지점
  var WEIGHT = cameras.map(function (c, i) { return c.status === "offline" ? 0.2 : [3, 2, 1, 1.5, 1, 1, 2.5, 1, 1.5, 4, 3.5, 1, 2][i]; });
  var wSum = WEIGHT.reduce(function (a, b) { return a + b; }, 0);
  function pickCam() {
    var r = rnd() * wSum;
    for (var i = 0; i < WEIGHT.length; i++) { r -= WEIGHT[i]; if (r <= 0) return cameras[i]; }
    return cameras[0];
  }

  function dayCount(d) {
    if (d > 90) return ri(0, 1);
    if (d > 30) return ri(0, 2);
    if (d > 7) return ri(1, 3);
    if (d > 0) return ri(2, 5);
    return ri(3, 6);
  }

  var events = [];
  for (var d = 179; d >= 0; d--) {
    var n = dayCount(d);
    for (var k = 0; k < n; k++) {
      var cam = pickCam();
      var sol = cam.sols[0];
      var ts = new Date(today0.getTime() - d * 86400000);
      // 주간 조업시간(06–20시) 집중
      ts.setHours(ri(6, 20), ri(0, 59), ri(0, 59), 0);
      if (d === 0 && ts.getTime() > Date.now()) ts.setTime(Date.now() - ri(60, 3600) * 1000);
      var status = d === 0
        ? (rnd() < 0.3 ? "active" : rnd() < 0.45 ? "acknowledged" : "resolved")
        : d <= 3 ? (rnd() < 0.15 ? "acknowledged" : "resolved")
          : "resolved";
      var ackAt = null, ackBy = null, resAt = null, resBy = null, note = null;
      if (status !== "active") {
        ackAt = new Date(ts.getTime() + ri(60, 1200) * 1000);
        ackBy = pick(STAFF);
      }
      if (status === "resolved") {
        resAt = new Date(ackAt.getTime() + ri(300, 3600) * 1000);
        resBy = rnd() < 0.7 ? ackBy : pick(STAFF);
        note = pick(NOTES);
      }
      events.push({
        _t: ts.getTime(),
        cam: cam.id,
        camLocation: cam.location,
        sol: sol,
        type: TYPE[sol],
        sev: SEV[sol],
        status: status,
        hasSnapshot: sol === "SOL-005" ? true : rnd() < 0.55,
        acknowledgedAt: ackAt, acknowledgedBy: ackBy,
        resolvedAt: resAt, resolvedBy: resBy, actionNote: note
      });
    }
  }
  events.sort(function (a, b) { return a._t - b._t; });
  // 대응 대기(활성) 이벤트는 대시보드 KPI의 핵심 지표 — 난수로 0건이 되지 않게 최근 2건 고정.
  events.slice(-2).forEach(function (e) {
    e.status = "active";
    e.acknowledgedAt = e.acknowledgedBy = e.resolvedAt = e.resolvedBy = e.actionNote = null;
  });
  var seq = 0;
  events.forEach(function (e) { e.id = "EVT-" + String(++seq).padStart(5, "0"); });

  function evDto(e) {
    return {
      id: e.id, ts: new Date(e._t).toISOString(),
      cam: e.cam, camLocation: (camById[e.cam] || {}).location || e.camLocation,
      sol: e.sol, type: e.type, sev: e.sev, status: e.status, hasSnapshot: e.hasSnapshot,
      acknowledgedAt: e.acknowledgedAt ? e.acknowledgedAt.toISOString() : null,
      acknowledgedBy: e.acknowledgedBy,
      resolvedAt: e.resolvedAt ? e.resolvedAt.toISOString() : null,
      resolvedBy: e.resolvedBy, actionNote: e.actionNote
    };
  }

  function camDto(c) {
    var t0 = today0.getTime(), today = 0, total = 0;
    events.forEach(function (e) {
      if (e.cam !== c.id) return;
      total++; if (e._t >= t0) today++;
    });
    return {
      id: c.id, name: c.name, location: c.location, sols: c.sols, status: c.status,
      ip: c.ip, mac: c.mac, mqttTopic: c.mqttTopic, detectionSource: c.detectionSource,
      today: today, total: total
    };
  }

  var settings = { solutionRisk: { "SOL-004": "critical", "SOL-005": "warning" } };

  // ── 합성 캡쳐 이미지 (이벤트 상세 모달) ────────────────────────────────────
  var snapCache = {};
  function scene(ctx, w, h, warm) {
    var g = ctx.createRadialGradient(w * 0.65, h * 0.8, 0, w * 0.5, h * 0.5, w * 0.85);
    g.addColorStop(0, warm ? "#4a2a17" : "#25302f");
    g.addColorStop(1, "#0e0d0b");
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(160,157,150,.13)"; ctx.lineWidth = 1;
    for (var i = 1; i < 9; i++) {           // 원경 소실점 격자 (바닥)
      ctx.beginPath();
      ctx.moveTo((w / 8) * i, h * 0.55);
      ctx.lineTo(w * 0.5 + ((w / 8) * i - w * 0.5) * 2.6, h);
      ctx.stroke();
    }
    for (var j = 1; j < 5; j++) {
      var y = h * 0.55 + (h * 0.45 * j * j) / 20;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    ctx.fillStyle = "rgba(20,19,15,.55)";   // 설비 실루엣
    ctx.fillRect(w * 0.06, h * 0.3, w * 0.16, h * 0.42);
    ctx.fillRect(w * 0.78, h * 0.24, w * 0.14, h * 0.5);
  }
  function box(ctx, x, y, bw, bh, color, label) {
    ctx.strokeStyle = color; ctx.lineWidth = 2.5;
    ctx.strokeRect(x, y, bw, bh);
    ctx.font = "600 15px system-ui, sans-serif";
    var tw = ctx.measureText(label).width + 14;
    ctx.fillStyle = color; ctx.fillRect(x, y - 24, tw, 24);
    ctx.fillStyle = "#14130f"; ctx.fillText(label, x + 7, y - 7);
  }
  window.__DEMO_SNAP__ = function (id) {
    var e = events.filter(function (x) { return x.id === id; })[0];
    var kind = e && e.sol === "SOL-004" ? "fire" : "ppe";
    if (snapCache[kind]) return snapCache[kind];
    var c = document.createElement("canvas"); c.width = 960; c.height = 540;
    var ctx = c.getContext("2d");
    scene(ctx, 960, 540, kind === "fire");
    if (kind === "fire") {
      var gl = ctx.createRadialGradient(430, 400, 0, 430, 400, 210);
      gl.addColorStop(0, "rgba(232,120,48,.75)");
      gl.addColorStop(1, "rgba(232,120,48,0)");
      ctx.fillStyle = gl; ctx.fillRect(200, 200, 470, 340);
      box(ctx, 336, 300, 190, 180, "#e8703a", "화재 감지  0.93");
    } else {
      ctx.fillStyle = "rgba(28,32,34,.9)";
      ctx.fillRect(390, 250, 62, 190); ctx.beginPath(); ctx.arc(421, 236, 24, 0, 7); ctx.fill();
      ctx.fillStyle = "rgba(120,140,150,.85)";
      ctx.fillRect(600, 268, 58, 172); ctx.beginPath(); ctx.arc(629, 252, 22, 0, 7); ctx.fill();
      ctx.fillStyle = "#5db8a6"; ctx.beginPath(); ctx.arc(629, 246, 24, Math.PI, 2 * Math.PI); ctx.fill();
      box(ctx, 372, 206, 100, 244, "#d4a017", "보호구 미착용  0.88");
    }
    ctx.font = "500 13px ui-monospace, monospace"; ctx.fillStyle = "rgba(250,249,245,.5)";
    ctx.fillText("DEMO CAPTURE · 합성 이미지 (실제 촬영본 아님)", 18, 522);
    return (snapCache[kind] = c.toDataURL("image/png"));
  };

  // ── WHEP 대체: canvas 합성 스트림 ─────────────────────────────────────────
  function demoStream() {
    var c = document.createElement("canvas"); c.width = 640; c.height = 360;
    var ctx = c.getContext("2d");
    var raf = 0, t = 0;
    function frame() {
      t += 1;
      scene(ctx, 640, 360, (t / 600) % 2 < 1);
      ctx.fillStyle = "rgba(93,184,166,.10)";                 // 스캔 라인
      ctx.fillRect(0, (t * 2) % 400 - 40, 640, 40);
      var bx = 250 + Math.sin(t / 55) * 60;
      ctx.strokeStyle = "rgba(93,184,166,.85)"; ctx.lineWidth = 2;
      ctx.strokeRect(bx, 150, 90, 130);
      ctx.font = "600 11px ui-monospace, monospace";
      ctx.fillStyle = "rgba(93,184,166,.9)";
      ctx.fillText("TRACK 01", bx, 145);
      // 하단 좌·우는 앱의 vbottom 바(카메라명·시각)가 덮으므로 가운데에만 워터마크를 둔다.
      ctx.fillStyle = "rgba(250,249,245,.38)";
      ctx.textAlign = "center";
      ctx.fillText("DEMO STREAM · 합성 영상", 320, 346);
      ctx.textAlign = "left";
      raf = requestAnimationFrame(frame);
    }
    frame();
    var stream = c.captureStream(12);
    stream.__stop = function () { cancelAnimationFrame(raf); };
    return stream;
  }

  var NativeRTC = window.RTCPeerConnection;
  function DemoPC() { this.ontrack = null; this._s = null; }
  DemoPC.prototype.addTransceiver = function () { };
  DemoPC.prototype.createOffer = function () {
    return Promise.resolve({ type: "offer", sdp: "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=demo\r\nt=0 0\r\n" });
  };
  DemoPC.prototype.setLocalDescription = function () { return Promise.resolve(); };
  DemoPC.prototype.setRemoteDescription = function () {
    var self = this;
    return Promise.resolve().then(function () {
      self._s = demoStream();
      if (self.ontrack) self.ontrack({ streams: [self._s] });
    });
  };
  DemoPC.prototype.close = function () { if (this._s && this._s.__stop) this._s.__stop(); };
  DemoPC.prototype.addEventListener = function () { };
  window.RTCPeerConnection = DemoPC;
  window.__NATIVE_RTC__ = NativeRTC;

  // ── fetch 라우터 ─────────────────────────────────────────────────────────
  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  function json(body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200, headers: { "Content-Type": "application/json" }
    });
  }
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || String(input);
    var method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
    // WHEP은 body가 SDP 평문이라 JSON 파싱이 실패한다 — 실패 시 빈 객체로.
    var body = {};
    if (init && typeof init.body === "string") {
      try { body = JSON.parse(init.body) || {}; } catch (_) { body = {}; }
    }
    var res = route(url, method, body);
    if (res) return Promise.resolve(res);
    return nativeFetch ? nativeFetch(input, init) : Promise.reject(new Error("no fetch"));
  };

  function route(url, method, body) {
    if (url.indexOf("/whep") !== -1) {
      return new Response("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=demo\r\nt=0 0\r\n", { status: 201 });
    }
    if (url.indexOf("/api/") === -1) return null;
    var path = url.slice(url.indexOf("/api/") + 4);

    if (path === "/events" && method === "GET") {
      return json(events.slice().sort(function (a, b) { return b._t - a._t; }).map(evDto));
    }
    var mEv = path.match(/^\/events\/([^/]+)$/);
    if (mEv && method === "PATCH") {
      var e = events.filter(function (x) { return x.id === decodeURIComponent(mEv[1]); })[0];
      if (!e) return json({ message: "이벤트를 찾을 수 없습니다" }, 404);
      var by = (body.by || "").trim();
      if (!by) return json({ message: "담당자를 입력하세요" }, 400);
      if (body.action === "acknowledge") {
        if (e.status !== "active") return json({ message: "이미 확인된 이벤트입니다" }, 409);
        e.status = "acknowledged"; e.acknowledgedAt = new Date(); e.acknowledgedBy = by;
      } else if (body.action === "resolve") {
        if (e.status !== "acknowledged") return json({ message: "확인 후 조치할 수 있습니다" }, 409);
        e.status = "resolved"; e.resolvedAt = new Date(); e.resolvedBy = by;
        e.actionNote = (body.note || "").trim() || null;
      } else return json({ message: "알 수 없는 action" }, 400);
      broker.publish("iseco/ui/refresh", "");
      return json(evDto(e));
    }

    if (path === "/cameras" && method === "GET") {
      return json(cameras.slice().sort(function (a, b) {
        return a.id.localeCompare(b.id, undefined, { numeric: true });
      }).map(camDto));
    }
    var mCam = path.match(/^\/cameras\/([^/]+)$/);
    if (mCam && method === "PATCH") {
      var c = camById[decodeURIComponent(mCam[1])];
      if (!c) return json({ message: "카메라를 찾을 수 없습니다" }, 404);
      var loc = (body.location || "").trim();
      if (!loc) return json({ message: "설치 위치를 입력하세요" }, 400);
      c.location = loc;
      broker.publish("iseco/ui/refresh", "");
      return json(camDto(c));
    }

    if (path === "/settings" && method === "GET") return json(settings);
    if (path === "/settings" && method === "PUT") {
      settings = { solutionRisk: Object.assign({}, settings.solutionRisk, body.solutionRisk) };
      return json(settings);
    }
    if (path === "/health") return json({ status: "ok", demo: true });
    return json({ message: "데모 stub에 없는 경로: " + path }, 404);
  }

  // ── 미니 MQTT 브로커 (ws 전송 계층 대체) ──────────────────────────────────
  var enc = new TextEncoder();
  function varint(n) {
    var out = [];
    do { var b = n % 128; n = Math.floor(n / 128); if (n > 0) b |= 128; out.push(b); } while (n > 0);
    return out;
  }
  function pubPacket(topic, payload) {
    var tb = enc.encode(topic), pb = enc.encode(payload);
    var rem = 2 + tb.length + pb.length;
    var head = [0x30].concat(varint(rem));
    var buf = new Uint8Array(head.length + rem);
    buf.set(head, 0);
    var o = head.length;
    buf[o++] = tb.length >> 8; buf[o++] = tb.length & 255;
    buf.set(tb, o); o += tb.length; buf.set(pb, o);
    return buf;
  }
  function topicMatch(filter, topic) {
    if (filter === "#") return true;
    var f = filter.split("/"), t = topic.split("/");
    for (var i = 0; i < f.length; i++) {
      if (f[i] === "#") return true;
      if (i >= t.length) return false;
      if (f[i] !== "+" && f[i] !== t[i]) return false;
    }
    return f.length === t.length;
  }

  var broker = {
    clients: [],
    publish: function (topic, payload) {
      var pkt = pubPacket(topic, payload);
      this.clients.forEach(function (cl) {
        if (cl.subs.some(function (f) { return topicMatch(f, topic); })) cl.deliver(pkt);
      });
    }
  };
  window.__DEMO_BROKER__ = broker;

  function DemoSocket(url) {
    var self = this;
    this.url = url; this.binaryType = "arraybuffer"; this.bufferedAmount = 0;
    this.readyState = 0; this.protocol = "mqtt";
    this._listeners = {}; this.subs = []; this._rx = new Uint8Array(0);
    broker.clients.push(this);
    setTimeout(function () {
      if (self.readyState !== 0) return;
      self.readyState = 1;
      self._emit("open", {});
    }, 120);
  }
  DemoSocket.CONNECTING = 0; DemoSocket.OPEN = 1; DemoSocket.CLOSING = 2; DemoSocket.CLOSED = 3;
  DemoSocket.prototype.CONNECTING = 0; DemoSocket.prototype.OPEN = 1;
  DemoSocket.prototype.CLOSING = 2; DemoSocket.prototype.CLOSED = 3;

  DemoSocket.prototype.addEventListener = function (type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  };
  DemoSocket.prototype.removeEventListener = function (type, fn) {
    var l = this._listeners[type] || [];
    var i = l.indexOf(fn); if (i !== -1) l.splice(i, 1);
  };
  DemoSocket.prototype._emit = function (type, ev) {
    ev.type = type; ev.target = this;
    (this._listeners[type] || []).forEach(function (fn) { fn(ev); });
    var h = this["on" + type];
    if (typeof h === "function") h.call(this, ev);
  };
  DemoSocket.prototype.deliver = function (bytes) {
    if (this.readyState !== 1) return;
    this._emit("message", { data: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) });
  };
  DemoSocket.prototype._reply = function (arr) {
    this.deliver(new Uint8Array(arr));
  };
  DemoSocket.prototype.send = function (chunk) {
    var u8 = chunk instanceof ArrayBuffer ? new Uint8Array(chunk)
      : chunk && chunk.buffer ? new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength)
        : enc.encode(String(chunk));
    var merged = new Uint8Array(this._rx.length + u8.length);
    merged.set(this._rx, 0); merged.set(u8, this._rx.length);
    this._rx = merged;
    this._drain();
  };
  DemoSocket.prototype._drain = function () {
    while (this._rx.length >= 2) {
      var rem = 0, mult = 1, i = 1, b;
      do {
        if (i >= this._rx.length) return;          // 길이 varint 미완 — 다음 chunk 대기
        b = this._rx[i++]; rem += (b & 127) * mult; mult *= 128;
      } while (b & 128);
      if (this._rx.length < i + rem) return;       // 본문 미완
      var type = this._rx[0] >> 4;
      var payload = this._rx.subarray(i, i + rem);
      this._rx = this._rx.slice(i + rem);
      this._handle(type, payload);
    }
  };
  DemoSocket.prototype._handle = function (type, p) {
    if (type === 1) { this._reply([0x20, 0x02, 0x00, 0x00]); return; }        // CONNECT → CONNACK
    if (type === 12) { this._reply([0xd0, 0x00]); return; }                   // PINGREQ → PINGRESP
    if (type === 14) { this.close(); return; }                                // DISCONNECT
    if (type === 8) {                                                         // SUBSCRIBE → SUBACK
      var id = (p[0] << 8) | p[1], o = 2, codes = [], dec = new TextDecoder();
      while (o + 2 <= p.length) {
        var len = (p[o] << 8) | p[o + 1]; o += 2;
        this.subs.push(dec.decode(p.subarray(o, o + len))); o += len;
        o += 1;                                                              // requested qos
        codes.push(0x00);
      }
      this._reply([0x90].concat(varint(2 + codes.length), [id >> 8, id & 255], codes));
      return;
    }
    if (type === 10) {                                                        // UNSUBSCRIBE → UNSUBACK
      this._reply([0xb0, 0x02, p[0], p[1]]);
    }
  };
  DemoSocket.prototype.close = function () {
    if (this.readyState === 3) return;
    this.readyState = 3;
    var i = broker.clients.indexOf(this);
    if (i !== -1) broker.clients.splice(i, 1);
    this._emit("close", { code: 1000, wasClean: true });
  };
  window.WebSocket = DemoSocket;

  // ── 합성 트래픽: heartbeat · 신규 이벤트 ──────────────────────────────────
  var live = cameras.filter(function (c) { return c.status === "normal"; });
  var hbIdx = 0;
  setInterval(function () {
    var c = live[hbIdx++ % live.length];
    broker.publish(c.mac + "/heartbeat", "");
  }, 4000);

  setInterval(function () {
    var cam = pickCam();
    if (cam.status === "offline") return;
    var sol = cam.sols[0];
    var e = {
      _t: Date.now(), id: "EVT-" + String(++seq).padStart(5, "0"),
      cam: cam.id, camLocation: cam.location, sol: sol, type: TYPE[sol], sev: SEV[sol],
      status: "active", hasSnapshot: true,
      acknowledgedAt: null, acknowledgedBy: null, resolvedAt: null, resolvedBy: null, actionNote: null
    };
    events.push(e);
    if (sol === "SOL-004") {
      broker.publish(cam.mac + "/fireAlarm", "");
    } else {
      broker.publish(cam.mac + PPE_TOPIC, relayPayload("active"));
      // 실장비는 탐지 active 1회 → 약 15초 후 inactive 1회를 발행한다(protocol/ 관측).
      setTimeout(function () {
        broker.publish(cam.mac + PPE_TOPIC, relayPayload("inactive"));
      }, 15000);
    }
    broker.publish("iseco/ui/refresh", "");
  }, 25000);

  function relayPayload(state) {
    return JSON.stringify({
      UtcTime: new Date().toISOString(),
      Source: { RelayToken: "Relay-1" },
      Data: { LogicalState: state }
    });
  }

  // ── 사용자 매뉴얼: 정적 /manual.html 대신 오버레이 iframe ─────────────────
  function manualBox() { return document.getElementById("demo-manual"); }
  document.addEventListener("click", function (ev) {
    var a = ev.target && ev.target.closest && ev.target.closest('a[href="/manual.html"]');
    if (!a) return;
    ev.preventDefault();
    var box = manualBox();
    if (!box) return;
    box.querySelector("iframe").srcdoc = window.__DEMO_MANUAL_HTML__ || "";
    box.style.display = "block";
  });
  function closeManual() {
    var box = manualBox();
    if (box) { box.style.display = "none"; box.querySelector("iframe").srcdoc = ""; }
  }
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.querySelector("#demo-manual .close");
    if (btn) btn.addEventListener("click", closeManual);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeManual(); });
})();
