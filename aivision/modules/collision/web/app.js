/* 모듈 화면의 동작.
 *
 * 상태는 /api/state 하나로만 받는다. 조각조각 부르면 화면의 일부만 새것이 되어
 * '보정은 저장했는데 상태는 옛것' 같은 어긋남이 생긴다.
 *
 * 폴링은 3초다. 편집 중인 칸은 덮어쓰지 않는다 — 3초마다 입력칸이 초기화되면
 * 좌표를 적을 수가 없다. 보정 점은 사람이 찍는 동안 완전히 로컬 상태이고,
 * '보정 저장' 을 누를 때만 서버로 간다.
 */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined ? "-" : Number(v).toFixed(d));

let state = null;
let solutions = [];
let pickedCamera = null;      // 보정 화면이 보고 있는 카메라
let points = [];              // 찍는 중인 4점 (로컬)
let pointsDirty = false;      // 손대는 중이면 폴링이 덮어쓰지 않는다
let labelsDirty = false;
let noteDirty = false;
let shotCamera = null;        // 지금 <img> 가 물고 있는 카메라 (깜빡임 방지)

function msg(el, text, kind) {
  const node = $(el);
  node.textContent = text || "";
  node.className = "msg" + (kind ? " " + kind : "");
}

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없는 응답 */ }
  if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
  return body;
}

const json = (method, body) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

/* ── 상태 ──────────────────────────────────────────────────────────── */

async function refresh() {
  try {
    state = await api("/api/state");
  } catch (e) {
    $("notice").innerHTML =
      `<div class="warn">모듈에 연결하지 못했습니다: ${esc(e.message)}</div>`;
    return;
  }
  renderNotice();
  renderStatus();
  renderLabels();
  renderItems();
  renderTuning();
  renderCameras();
  renderFeed();
}

function cameraById(id) {
  return (state.cameras || []).find((c) => c.camera_id === id) || null;
}

function renderNotice() {
  const out = [];
  const m = state.module;
  const svc = state.service || {};

  if (!state.platform_ok) {
    out.push(`<div class="warn"><b>플랫폼이 보이지 않습니다</b> — 담당 카메라 목록과
      탐지 항목을 가져오지 못합니다. 판정과 발행은 브로커만 살아 있으면 계속 돕니다.</div>`);
  }
  if (!m.labels.amr.length || !m.labels.person.length) {
    out.push(`<div class="warn"><b>라벨이 연결되지 않았습니다</b> — 받은 박스를 AMR 과
      사람으로 가르지 못해 판정이 돌지 않습니다. 왼쪽 '라벨 연결' 에서 지정하세요.</div>`);
  }
  if (!m.items.risk && !m.items.collision) {
    out.push(`<div class="warn"><b>탐지 항목이 연결되지 않았습니다</b> — 판정은 하지만
      이벤트를 쌓지 않습니다. 왼쪽 '탐지 항목 연결' 에서 고르세요.</div>`);
  }
  const uncalibrated = (state.cameras || []).filter((c) => c.assigned && !c.calibrated);
  if (uncalibrated.length) {
    out.push(`<div class="warn"><b>보정되지 않은 카메라가 있습니다</b>
      (${uncalibrated.map((c) => esc(c.camera_id)).join(", ")}) — 거리를 미터로 잴 수 없어
      그 카메라는 판정하지 않습니다. 오른쪽에서 바닥 4점을 찍으세요.</div>`);
  }
  if (!(svc.cameras || []).length) {
    out.push(`<div class="warn">할당된 카메라가 없습니다. 플랫폼 관리자 화면에서
      이 모듈에 카메라를 할당하세요.</div>`);
  }
  $("notice").innerHTML = out.join("");
}

function renderStatus() {
  const svc = state.service || {};
  $("status-kv").innerHTML = [
    ["담당", (svc.cameras || []).length + "대"],
    ["보정됨", (svc.calibrated || []).length + "대"],
    ["구독 발행자", (svc.sources || []).join(", ") || "-"],
    ["받은 프레임", (svc.received ?? 0).toLocaleString()],
    ["버린 프레임", (svc.dropped ?? 0).toLocaleString()],
    ["발행", (svc.published ?? 0).toLocaleString()],
  ].map(([k, v]) => `<span>${k} <b>${esc(v)}</b></span>`).join("");

  const rows = (state.cameras || []).filter((c) => c.assigned);
  $("status-rows").innerHTML = rows.length ? rows.map((c) => {
    const rt = c.runtime || {};
    const level = { warn: `<span class="pill warn">주의</span>`,
                    imminent: `<span class="pill err">임박</span>` }[rt.level] || "";
    const collision = rt.collisions
      ? `<span class="pill err">충돌 ${rt.collisions}</span>` : "";
    const idle = rt.idle_sec === null || rt.idle_sec === undefined
      ? `<span class="pill off">입력 없음</span>`
      : (rt.idle_sec > 10 ? `<span class="pill off">${fmt(rt.idle_sec, 0)}초째 조용</span>`
                          : `<span class="pill on">수신 중</span>`);
    return `<tr>
      <td class="num">카메라 ${c.camera_id}${c.camera_name ? " · " + esc(c.camera_name) : ""}</td>
      <td>${c.calibrated ? `<span class="pill on">±${fmt(c.error_m)}m</span>`
                         : `<span class="pill off">미보정</span>`}</td>
      <td class="num">AMR ${rt.tracks ? rt.tracks.amr : 0} / 사람 ${rt.tracks ? rt.tracks.person : 0}</td>
      <td>${c.enabled ? idle : `<span class="pill off">꺼 둠</span>`} ${level} ${collision}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="4" class="muted">할당된 카메라가 없습니다.</td></tr>`;
}

/* ── 라벨 ──────────────────────────────────────────────────────────── */

function renderLabels() {
  if (!labelsDirty) {
    $("amr-labels").value = state.module.labels.amr.join(", ");
    $("person-labels").value = state.module.labels.person.join(", ");
  }
  // 실제로 들어오고 있는 라벨을 보여 준다. 오타로 연결이 안 되는 것이 가장 흔한 사고다.
  const pubs = new Set();
  (state.service.per_camera || []).forEach((c) => (c.publishers || []).forEach((p) => pubs.add(p)));
  $("seen-labels").innerHTML = pubs.size
    ? `지금 받고 있는 발행자: <b>${[...pubs].map(esc).join(", ")}</b>`
    : `아직 받은 박스가 없습니다. 다른 모듈이 이 카메라로 발행하고 있는지 확인하세요.`;
}

["amr-labels", "person-labels"].forEach((id) =>
  $(id).addEventListener("input", () => { labelsDirty = true; }));

$("save-labels").onclick = async () => {
  const split = (id) => $(id).value.split(",").map((s) => s.trim()).filter(Boolean);
  try {
    await api("/api/labels", json("PUT", { amr: split("amr-labels"), person: split("person-labels") }));
    labelsDirty = false;
    msg("labels-msg", "저장했습니다.", "ok");
    refresh();
  } catch (e) { msg("labels-msg", e.message, "err"); }
};

/* ── 탐지 항목 ─────────────────────────────────────────────────────── */

async function loadSolutions() {
  try {
    const body = await api("/api/solutions");
    solutions = body.items || [];
  } catch { solutions = []; }
}

function renderItems() {
  const fill = (id, picked) => {
    const el = $(id);
    if (document.activeElement === el) return;
    const options = [`<option value="">(이벤트 만들지 않음)</option>`].concat(
      solutions.map((s) => `<option value="${esc(s.code)}">${esc(s.code)} — ${esc(s.name)}</option>`));
    // 플랫폼에서 목록을 못 받았어도 지금 설정값은 남아 있어야 한다
    if (picked && !solutions.some((s) => s.code === picked)) {
      options.push(`<option value="${esc(picked)}">${esc(picked)} (목록에 없음)</option>`);
    }
    el.innerHTML = options.join("");
    el.value = picked || "";
  };
  fill("risk-item", state.module.items.risk);
  fill("collision-item", state.module.items.collision);
}

$("save-items").onclick = async () => {
  try {
    await api("/api/items", json("PUT", {
      risk: $("risk-item").value, collision: $("collision-item").value }));
    msg("items-msg", "저장했습니다. 플랫폼에 다시 등록했습니다.", "ok");
    refresh();
  } catch (e) { msg("items-msg", e.message, "err"); }
};

/* ── 임계값 ────────────────────────────────────────────────────────── */

// 이름과 설명을 코드에 둔다. 값의 뜻을 모르면 슬라이더는 위험하기만 하다.
const TUNING_META = {
  pair_window: ["짝짓기 창 (초)", "이 시간 안에 도착한 판독끼리만 같은 순간으로 봅니다. 발행자가 여럿일 때 씁니다.", 0.1],
  track_max_age: ["트랙 유지 (초)", "이만큼 안 보여도 같은 물체로 붙들고 있습니다. 가림을 견디는 값입니다.", 0.1],
  hold_sec: ["해제 대기 (초)", "위험 조건이 사라진 뒤 이만큼 지나야 '해제' 를 냅니다. 깜빡임을 막습니다.", 0.5],
  min_conf: ["이벤트 최소 점수", "받은 박스의 점수가 이보다 낮으면 이벤트로 올리지 않습니다.", 0.01],
  r_amr: ["AMR 반경 (m)", "AMR 을 원으로 볼 때의 반지름입니다.", 0.05],
  r_h: ["사람 반경 (m)", "사람을 원으로 볼 때의 반지름입니다.", 0.05],
  sigma: ["계측 여유 σ (m)", "보정·검출 오차를 감안해 더하는 여유입니다. 보정 오차가 크면 키우세요.", 0.05],
  v_h_max: ["사람 최대 속도 (m/s)", "사람이 어느 방향으로든 이만큼 빨리 움직일 수 있다고 봅니다. 임박 판정이 이 값에 가장 민감합니다.", 0.1],
  t_warn: ["주의 전망 시간 (초)", "AMR 이 이 시간 동안 갈 거리까지를 통로로 봅니다. 임박보다 넉넉해야 주의가 먼저 뜹니다.", 0.5],
  t_imminent: ["임박 전망 시간 (초)", "이 시간 안에 닿을 것 같으면 임박입니다. 오알람이 잦으면 이것부터 줄이세요.", 0.1],
  corridor_half_w: ["통로 반폭 (m)", "AMR 진행 통로의 좌우 폭(절반)입니다.", 0.1],
  corridor_min_len: ["통로 최소 길이 (m)", "서 있는 AMR 앞에도 이만큼은 통로로 봅니다.", 0.1],
  eps_contact: ["접촉 거리 ε (m)", "바닥 투영 거리가 이보다 가까우면 접촉으로 봅니다.", 0.05],
  dt_window: ["동시성 창 Δt (초)", "접촉과 충격이 이 시간 안에 같이 보여야 충돌로 확정합니다.", 0.1],
  amr_stop_dv: ["AMR 급정지 (m/s)", "최근 최고 속력에서 이만큼 떨어지면 충격으로 봅니다.", 0.05],
  person_dv: ["사람 속도 급변 (m/s)", "사람 속력이 한 칸에 이만큼 바뀌면 충격으로 봅니다.", 0.05],
  aspect_flip: ["전도 배수 (w/h)", "사람 박스의 가로세로비가 이 배수만큼 커지면 넘어진 것으로 봅니다.", 0.1],
  graze_v: ["스침 기준 (m/s)", "이보다 느린 접촉은 '접촉·스침' 으로 기록합니다.", 0.05],
  severe_v: ["심각 기준 (m/s)", "이보다 빠른 충돌은 '심각' 으로 기록합니다.", 0.05],
};

const TUNING_GROUPS = [
  ["판정 공통", ["pair_window", "track_max_age", "hold_sec", "min_conf"]],
  ["위험 알람", ["r_amr", "r_h", "sigma", "v_h_max", "t_warn", "t_imminent",
                 "corridor_half_w", "corridor_min_len"]],
  ["충돌 판독", ["eps_contact", "dt_window", "amr_stop_dv", "person_dv", "aspect_flip",
                 "graze_v", "severe_v"]],
];

function renderTuning() {
  const focused = document.activeElement ? document.activeElement.id : "";
  const parts = [];
  for (const [title, names] of TUNING_GROUPS) {
    parts.push(`<h3>${esc(title)}</h3>`);
    for (const name of names) {
      const meta = TUNING_META[name];
      const lim = state.limits[name];
      if (!meta || !lim) continue;
      const value = state.tuning[name];
      // 손대고 있는 칸은 덮어쓰지 않는다. 3초마다 값이 되돌아가면 숫자를 칠 수 없다.
      if (focused === `t-${name}` || focused === `n-${name}`) continue;
      parts.push(`<label>
        <span>${esc(meta[0])}</span>
        <div class="tune">
          <input type="range" id="t-${name}" min="${lim.min}" max="${lim.max}"
                 step="${meta[2]}" value="${value}" />
          <input type="number" id="n-${name}" min="${lim.min}" max="${lim.max}"
                 step="${meta[2]}" value="${value}" />
        </div>
        <em>${esc(meta[1])} (기본 ${lim.default})</em>
      </label>`);
    }
  }
  if (focused.startsWith("t-") || focused.startsWith("n-")) return;  // 조작 중이면 통째로 둔다
  $("tuning").innerHTML = parts.join("");
  for (const name of Object.keys(TUNING_META)) {
    const range = $(`t-${name}`);
    const num = $(`n-${name}`);
    if (!range || !num) continue;
    range.addEventListener("input", () => { num.value = range.value; });
    num.addEventListener("input", () => { range.value = num.value; });
    range.addEventListener("change", () => saveTuning(name, range.value));
    num.addEventListener("change", () => saveTuning(name, num.value));
  }
}

async function saveTuning(name, value) {
  try {
    await api("/api/tuning", json("PUT", { [name]: Number(value) }));
    msg("tuning-msg", `${TUNING_META[name][0]} 을(를) ${value} 로 바꿨습니다.`, "ok");
    refresh();
  } catch (e) { msg("tuning-msg", e.message, "err"); }
}

/* ── 보정 ──────────────────────────────────────────────────────────── */

function renderCameras() {
  const cams = state.cameras || [];
  const pick = $("camera-pick");
  if (document.activeElement !== pick) {
    const ids = cams.map((c) => c.camera_id);
    if (pickedCamera === null && ids.length) pickedCamera = ids[0];
    pick.innerHTML = cams.map((c) => {
      const mark = c.assigned ? "" : " (담당 아님)";
      const name = c.camera_name ? ` · ${c.camera_name}` : "";
      return `<option value="${c.camera_id}">카메라 ${c.camera_id}${esc(name)}${mark}</option>`;
    }).join("") || `<option value="">(카메라 없음)</option>`;
    if (pickedCamera !== null) pick.value = String(pickedCamera);
  }

  const cam = pickedCamera === null ? null : cameraById(pickedCamera);
  if (cam && !pointsDirty) points = (cam.points || []).map((p) => ({ ...p }));
  if (cam && !noteDirty) $("camera-note").value = cam.note || "";
  $("toggle-enabled").textContent = cam && cam.enabled === false ? "판정 켜기" : "판정 끄기";
  $("calib-of").textContent = cam
    ? (cam.calibrated ? `— 보정됨 (평균 오차 ${fmt(cam.error_m)}m)` : "— 미보정")
    : "";
  if (cam && cam.calibrated && cam.error_m > state.warn_error_m) {
    $("calib-of").innerHTML =
      `— <span class="pill warn">오차 ${fmt(cam.error_m)}m — 점을 다시 찍어 보세요</span>`;
  }
  renderShot();
  renderPoints();
  renderPreview();

  // 스위치는 손대는 중이 아닐 때만 서버 값으로 맞춘다 — 누르는 순간 폴링이 되돌리면
  // 체크박스가 혼자 튕겨 나간 것처럼 보인다.
  if (document.activeElement !== $("pub-live")) {
    $("pub-live").checked = state.module.publish_live;
  }
  if (document.activeElement !== $("pub-zones")) {
    $("pub-zones").checked = state.module.overlay_zones;
  }
}

function renderShot() {
  const host = $("shot");
  if (pickedCamera === null) {
    host.innerHTML = `<div class="missing">카메라가 없습니다.</div>`;
    shotCamera = null;
    return;
  }
  if (shotCamera !== pickedCamera) {
    shotCamera = pickedCamera;
    host.innerHTML =
      `<img id="shot-img" alt="카메라 ${pickedCamera} 스냅샷"
            src="/api/cameras/${pickedCamera}/snapshot.jpg?t=${Date.now()}" />`;
    const img = $("shot-img");
    img.onerror = () => {
      host.innerHTML = `<div class="missing">스냅샷을 가져오지 못했습니다.<br />
        카메라가 꺼져 있거나 플랫폼이 보이지 않습니다. 스트림이 돌아오면 '스냅샷 새로' 를 누르세요.</div>`;
    };
    img.onclick = (ev) => addPoint(ev, img);
  }
  // 점은 매번 다시 그린다 (이미지는 그대로 둔다 — 3초마다 깜빡이면 점을 찍을 수 없다)
  [...host.querySelectorAll(".dot")].forEach((d) => d.remove());
  const img = $("shot-img");
  if (!img) return;
  points.forEach((p, i) => {
    const dot = document.createElement("div");
    dot.className = "dot";
    dot.textContent = i + 1;
    dot.style.left = `${p.u * img.clientWidth}px`;
    dot.style.top = `${p.v * img.clientHeight}px`;
    host.appendChild(dot);
  });
}

function addPoint(ev, img) {
  if (points.length >= 4) {
    msg("calib-msg", "네 점을 다 찍었습니다. 다시 찍으려면 '점 다시 찍기' 를 누르세요.", "err");
    return;
  }
  const rect = img.getBoundingClientRect();
  // 정규화 좌표로 저장한다. 픽셀로 두면 스트림 해상도가 바뀔 때 보정이 통째로 틀어진다.
  const u = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
  const v = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height));
  points.push({ u: Number(u.toFixed(5)), v: Number(v.toFixed(5)), x: 0, y: 0 });
  pointsDirty = true;
  msg("calib-msg", `${points.length}번째 점을 찍었습니다. 오른쪽에 실제 좌표(m)를 적으세요.`);
  renderShot();
  renderPoints();
}

function renderPoints() {
  const rows = points.map((p, i) => `<tr>
    <td class="num">${i + 1}</td>
    <td class="num muted">${fmt(p.u, 3)}, ${fmt(p.v, 3)}</td>
    <td><input type="number" step="0.01" id="px-${i}" value="${p.x}" /></td>
    <td><input type="number" step="0.01" id="py-${i}" value="${p.y}" /></td>
  </tr>`);
  for (let i = points.length; i < 4; i++) {
    rows.push(`<tr><td class="num">${i + 1}</td>
      <td colspan="3" class="muted">스냅샷을 눌러 점을 찍으세요</td></tr>`);
  }
  $("point-rows").innerHTML = rows.join("");
  points.forEach((p, i) => {
    ["x", "y"].forEach((axis) => {
      const el = $(`p${axis}-${i}`);
      el.addEventListener("input", () => {
        points[i][axis] = Number(el.value || 0);
        pointsDirty = true;
      });
    });
  });
}

$("camera-pick").addEventListener("change", () => {
  pickedCamera = Number($("camera-pick").value);
  pointsDirty = false;
  noteDirty = false;
  points = [];
  msg("calib-msg", "");
  renderCameras();
  pollOverlay();
});

$("reset-points").onclick = () => {
  points = [];
  pointsDirty = true;
  msg("calib-msg", "점을 지웠습니다. 스냅샷을 눌러 다시 찍으세요.");
  renderShot();
  renderPoints();
};

$("reload-shot").onclick = () => { shotCamera = null; renderShot(); };

$("save-calib").onclick = async () => {
  if (points.length !== 4) { msg("calib-msg", "네 점이 필요합니다.", "err"); return; }
  try {
    const body = await api(`/api/cameras/${pickedCamera}/calibration`,
                           json("PUT", { points }));
    pointsDirty = false;
    msg("calib-msg", body.warn
      ? `저장했습니다. 다만 평균 오차가 ${fmt(body.error_m)}m 로 큽니다 — 점이나 실측값을 다시 보세요.`
      : `저장했습니다. 평균 오차 ${fmt(body.error_m)}m.`, body.warn ? "err" : "ok");
    refresh();
  } catch (e) { msg("calib-msg", e.message, "err"); }
};

$("clear-calib").onclick = async () => {
  if (!confirm("이 카메라의 보정을 지웁니다. 지우면 그 카메라는 판정하지 않습니다.")) return;
  try {
    await api(`/api/cameras/${pickedCamera}/calibration`, { method: "DELETE" });
    points = [];
    pointsDirty = false;
    msg("calib-msg", "보정을 지웠습니다.", "ok");
    refresh();
  } catch (e) { msg("calib-msg", e.message, "err"); }
};

$("camera-note").addEventListener("input", () => { noteDirty = true; });

$("save-note").onclick = async () => {
  try {
    await api(`/api/cameras/${pickedCamera}`, json("PUT", { note: $("camera-note").value }));
    noteDirty = false;
    msg("calib-msg", "메모를 저장했습니다.", "ok");
    refresh();
  } catch (e) { msg("calib-msg", e.message, "err"); }
};

$("toggle-enabled").onclick = async () => {
  const cam = cameraById(pickedCamera);
  try {
    await api(`/api/cameras/${pickedCamera}`, json("PUT", { enabled: !(cam && cam.enabled) }));
    msg("calib-msg", "바꿨습니다.", "ok");
    refresh();
  } catch (e) { msg("calib-msg", e.message, "err"); }
};

/* ── 미리보기 (구역 오버레이) ──────────────────────────────────────── */

/* 영상은 MJPEG 중계(<img>), 구역은 그 위의 SVG 다. 둘을 겹치는 방식이 코어
 * 대시보드(LiveVideo.tsx)와 같아서, 여기서 보이는 것과 종합 현황에서 보일 것이
 * 어긋나지 않는다.
 *
 * 폴링은 4Hz 다. 판정 루프가 10Hz 이므로 그보다 자주 물어도 새 그림이 없고,
 * 더 느리면 임박 구역이 늘었다 줄었다 하는 것이 뚝뚝 끊겨 보인다.
 */
const ZONE_STYLE = {
  corridor: { "": ["rgba(204,120,92,.28)", "#cc785c"],
              warn: ["rgba(212,160,23,.32)", "#d4a017"],
              imminent: ["rgba(198,69,69,.38)", "#c64545"] },
  reach: { "": ["rgba(79,124,172,.26)", "#4f7cac"],
           warn: ["rgba(79,124,172,.3)", "#4f7cac"],
           imminent: ["rgba(198,69,69,.3)", "#c64545"] },
};

let previewOn = true;
let previewCamera = null;
let overlayTimer = null;

function renderPreview() {
  const host = $("preview");
  $("preview-of").textContent = pickedCamera === null ? "" : `— 카메라 ${pickedCamera}`;

  if (!previewOn || pickedCamera === null) {
    host.innerHTML = `<div class="off">미리보기를 정지했습니다. 영상 중계와 폴링을
      멈춘 상태입니다.</div>`;
    previewCamera = null;
    return;
  }
  if (previewCamera !== pickedCamera) {
    previewCamera = pickedCamera;
    // 스트림은 카메라가 바뀔 때만 새로 연다. 매번 다시 열면 화면이 계속 깜빡인다.
    host.innerHTML =
      `<img id="preview-img" alt="카메라 ${pickedCamera} 라이브"
            src="/api/cameras/${pickedCamera}/stream?t=${Date.now()}" />
       <svg id="preview-svg" viewBox="0 0 1 1" preserveAspectRatio="none"></svg>
       <div class="tags" id="preview-tags"></div>`;
    $("preview-img").onerror = () => {
      host.innerHTML = `<div class="off">영상을 가져오지 못했습니다.<br />
        카메라가 꺼져 있거나 플랫폼이 보이지 않습니다.</div>`;
      previewCamera = null;
    };
  }
}

async function pollOverlay() {
  if (!previewOn || pickedCamera === null || document.hidden) return;
  let data;
  try {
    data = await api(`/api/overlay/${pickedCamera}`);
  } catch { return; }
  drawOverlay(data);
}

function drawOverlay(data) {
  const svg = $("preview-svg");
  const tags = $("preview-tags");
  if (!svg || !tags) return;

  const parts = [];
  const labels = [];
  const showBox = $("show-bbox").checked;

  for (const z of data.zones || []) {
    const [fill, stroke] = (ZONE_STYLE[z.kind] || ZONE_STYLE.corridor)[z.level || ""]
      || ZONE_STYLE.corridor[""];
    const points = (z.points || []).map((p) => `${p[0]},${p[1]}`).join(" ");
    if (!points) continue;
    parts.push(`<polygon points="${points}" fill="${fill}" stroke="${stroke}"
      stroke-width="2" stroke-dasharray="${z.kind === "reach" ? "4 3" : "0"}"
      vector-effect="non-scaling-stroke" />`);
    // 이름표는 다각형의 가장 위쪽 꼭짓점에 붙인다
    const top = (z.points || []).reduce((a, b) => (b[1] < a[1] ? b : a));
    labels.push([top[0], top[1], z.label, stroke]);
  }

  // 코어(종합 현황)가 그리게 될 사각형. 다각형과 얼마나 다른지 눈으로 보라고 둔다.
  if (showBox) {
    for (const b of data.zone_boxes || []) {
      parts.push(`<rect x="${b.x1}" y="${b.y1}" width="${Math.max(0, b.x2 - b.x1)}"
        height="${Math.max(0, b.y2 - b.y1)}" fill="none" stroke="#8e8b82"
        stroke-width="1.5" stroke-dasharray="6 4" vector-effect="non-scaling-stroke" />`);
    }
  }

  for (const t of data.tracks || []) {
    const b = t.box;
    const stroke = t.level === "imminent" ? "#c64545"
      : t.level === "warn" ? "#d4a017" : "#efe9de";
    parts.push(`<rect x="${b.x1}" y="${b.y1}" width="${Math.max(0, b.x2 - b.x1)}"
      height="${Math.max(0, b.y2 - b.y1)}" fill="none" stroke="${stroke}"
      stroke-width="2" vector-effect="non-scaling-stroke" />`);
    labels.push([b.x1, b.y1, `${b.label} · ${fmt(t.speed_ms)}m/s`, stroke]);
  }

  svg.innerHTML = parts.join("");
  tags.innerHTML = labels.map(([x, y, text, color]) =>
    `<span style="left:${(x * 100).toFixed(2)}%; top:${(y * 100).toFixed(2)}%;
       color:${color}">${esc(text)}</span>`).join("");

  if (!data.assigned) {
    msg("overlay-msg", "이 카메라는 이 모듈에 할당되지 않았습니다 — 판정도 그림도 없습니다.");
  } else if (!data.calibrated) {
    msg("overlay-msg", "보정 전이라 구역을 그릴 수 없습니다. 위에서 바닥 4점을 찍으세요.", "err");
  } else if (!(data.zones || []).length) {
    msg("overlay-msg", "AMR·사람이 보이지 않습니다. 발행자가 박스를 내고 있는지 확인하세요.");
  } else {
    msg("overlay-msg", "");
  }
}

$("preview-toggle").onclick = () => {
  previewOn = !previewOn;
  $("preview-toggle").textContent = previewOn ? "미리보기 정지" : "미리보기 시작";
  renderPreview();
};

$("show-bbox").addEventListener("change", () => pollOverlay());

async function saveOverlay() {
  try {
    await api("/api/overlay", json("PUT", {
      publish_live: $("pub-live").checked, zones: $("pub-zones").checked }));
    msg("overlay-msg", $("pub-live").checked
      ? "종합 현황으로 내보냅니다. 우리가 만든 구역만 나가므로 다른 모듈의 박스와 겹치지 않습니다."
      : "내보내기를 껐습니다. 이 화면에서는 계속 보입니다.", "ok");
    refresh();
  } catch (e) { msg("overlay-msg", e.message, "err"); }
}

["pub-live", "pub-zones"].forEach((id) => $(id).addEventListener("change", saveOverlay));

/* ── 최근 판정 ─────────────────────────────────────────────────────── */

function renderFeed() {
  const rows = (state.judgements || []).filter((j) => j.state !== "evidence");
  $("feed-rows").innerHTML = rows.length ? rows.map((j) => {
    const kind = j.kind === "collision"
      ? `<span class="pill err">충돌</span>`
      : `<span class="pill ${j.level === "imminent" ? "err" : "warn"}">위험</span>`;
    const stateTag = j.state === "active" ? "발생" : "해제";
    return `<tr>
      <td class="when">${esc((j.ts || "").replace("T", " ").replace("Z", ""))}</td>
      <td class="num">${esc(j.camera_id)}</td>
      <td>${kind} ${esc(stateTag)}</td>
      <td>${esc(j.detail)}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="4" class="muted">아직 판정이 없습니다.</td></tr>`;

  const shots = (state.judgements || []).filter((j) => j.evidence).slice(0, 8);
  $("evidence").innerHTML = shots.map((j) =>
    `<a href="/api/evidence/${encodeURIComponent(j.evidence)}" target="_blank"
        title="카메라 ${esc(j.camera_id)} · ${esc(j.ts)}">
       <img src="/api/evidence/${encodeURIComponent(j.evidence)}" alt="증거 스냅샷" />
     </a>`).join("");
}

/* ── 기동 ──────────────────────────────────────────────────────────── */

(async function start() {
  // 플랫폼 탭 안이면(iframe) 제목을 감춘다 — 프레임 위에 플랫폼이 이미 이름과 설명을
  // 그려 준다. 코어가 무엇을 보내 주지 않아도 알 수 있는 사실이라 이렇게 본다.
  try {
    if (window.self !== window.top) document.body.classList.add("embedded");
  } catch { document.body.classList.add("embedded"); }  // 크로스 오리진이면 곧 임베드다

  await loadSolutions();
  await refresh();
  setInterval(refresh, 3000);
  // 구역은 훨씬 자주 바뀐다(AMR 이 움직이는 동안 늘었다 줄었다 한다). 상태 폴링과
  // 주기를 나눠 둔다 — 3초마다 그리면 그림이 뚝뚝 끊기고, 0.25초마다 상태를 통째로
  // 받으면 그것대로 낭비다.
  overlayTimer = setInterval(pollOverlay, 250);
  // 탭이 가려지면 영상 중계를 붙들고 있을 이유가 없다. 카메라 쪽 부담이다.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollOverlay();
  });
  // 탐지 항목은 자주 바뀌지 않는다. 플랫폼이 늦게 떴을 때를 위해 가끔만 다시 받는다.
  setInterval(async () => { await loadSolutions(); if (state) renderItems(); }, 30000);
  window.addEventListener("resize", renderShot);
})();
