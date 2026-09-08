// shim.js 자체 검증 — 실제 mqtt.js 클라이언트를 데모 브로커에 붙여보고,
// fetch 라우터가 서버 DTO 형태를 돌려주는지 확인한다. (canvas 경로는 stub)
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire("C:/Users/PC3/Projects/ISECO/web/package.json");
const mqtt = require("mqtt");

// ── 최소 브라우저 stub ────────────────────────────────────────────────────
const listeners = {};
const stubCanvas = () => ({
  width: 0, height: 0,
  getContext: () => new Proxy({}, {
    get: (_, k) => (k === "measureText" ? () => ({ width: 10 })
      : k === "createRadialGradient" || k === "createLinearGradient"
        ? () => ({ addColorStop() { } })
        : () => { }),
    set: () => true,
  }),
  toDataURL: () => "data:image/png;base64,AA==",
  captureStream: () => ({ getTracks: () => [] }),
});
globalThis.window = globalThis;
globalThis.document = {
  createElement: (t) => (t === "canvas" ? stubCanvas() : { style: {}, addEventListener() { } }),
  addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
  getElementById: () => null,
  querySelector: () => null,
};
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => { };

const shim = readFileSync("C:/Users/PC3/AppData/Local/Temp/claude/C--Users-PC3-Projects-ISECO/081c0475-491c-4fcf-af14-5da0be0c58a3/scratchpad/demo/shim.js", "utf8");
new Function(shim)();

// ── 1. fetch 라우터 ───────────────────────────────────────────────────────
const cams = await (await fetch("/api/cameras")).json();
assert.equal(cams.length, 13, "카메라 13대");
assert.deepEqual(cams.map((c) => c.id).slice(0, 3), ["cam1", "cam2", "cam3"], "자연수 정렬");
assert.equal(cams.filter((c) => c.status === "offline").length, 2, "오프라인 2대");
for (const c of cams) {
  assert.ok(/^SOL-00[45]$/.test(c.sols[0]), "솔루션 코드");
  assert.equal(c.detectionSource, "HANHWA");
  assert.ok(c.mqttTopic.startsWith(c.mac), "토픽 = MAC 접두");
  assert.equal(typeof c.today, "number");
}

const evs = await (await fetch("/api/events")).json();
assert.ok(evs.length > 150, `이벤트 충분: ${evs.length}`);
assert.ok(evs[0].ts > evs[evs.length - 1].ts, "최신순 정렬");
assert.ok(/^EVT-\d{5}$/.test(evs[0].id), "이벤트 ID 형식");
const camIds = new Set(cams.map((c) => c.id));
for (const e of evs) {
  assert.ok(camIds.has(e.cam), `카메라 조인: ${e.cam}`);
  assert.ok(["active", "acknowledged", "resolved"].includes(e.status));
  assert.ok(!Number.isNaN(Date.parse(e.ts)), "ts ISO");
  if (e.status === "resolved") assert.ok(e.resolvedBy && e.actionNote, "조치 정보");
  if (e.sol === "SOL-004") assert.equal(e.sev, "critical");
}
const todayIso = new Date().toISOString().slice(0, 10);
assert.ok(evs.some((e) => e.ts.slice(0, 10) === todayIso), "금일 이벤트 존재");
assert.ok(evs.some((e) => e.status === "active"), "활성 이벤트 존재");

// 조치 전이
const act = evs.find((e) => e.status === "active");
const bad = await fetch(`/api/events/${act.id}`, { method: "PATCH", body: JSON.stringify({ action: "resolve", by: "김민수" }) });
assert.equal(bad.status, 409, "active → resolve 는 409");
const ack = await (await fetch(`/api/events/${act.id}`, { method: "PATCH", body: JSON.stringify({ action: "acknowledge", by: "김민수" }) })).json();
assert.equal(ack.status, "acknowledged");
assert.equal(ack.acknowledgedBy, "김민수");
const res = await (await fetch(`/api/events/${act.id}`, { method: "PATCH", body: JSON.stringify({ action: "resolve", by: "이준호", note: "조치 완료" }) })).json();
assert.equal(res.status, "resolved");
assert.equal(res.actionNote, "조치 완료");
assert.equal((await fetch(`/api/events/${act.id}`, { method: "PATCH", body: JSON.stringify({ action: "acknowledge", by: "x" }) })).status, 409);

// 카메라 위치 편집
const moved = await (await fetch("/api/cameras/cam1", { method: "PATCH", body: JSON.stringify({ location: "새 위치" }) })).json();
assert.equal(moved.location, "새 위치");
assert.equal((await fetch("/api/cameras/cam1", { method: "PATCH", body: JSON.stringify({ location: "  " }) })).status, 400);
assert.equal((await fetch("/api/cameras/nope", { method: "PATCH", body: JSON.stringify({ location: "x" }) })).status, 404);

// 설정
const s0 = await (await fetch("/api/settings")).json();
assert.equal(s0.solutionRisk["SOL-004"], "critical");
const s1 = await (await fetch("/api/settings", { method: "PUT", body: JSON.stringify({ solutionRisk: { "SOL-005": "critical" } }) })).json();
assert.equal(s1.solutionRisk["SOL-005"], "critical");

// WHEP
const whep = await fetch("whep://demo/cam1/whep", { method: "POST", body: "v=0" });
assert.ok(whep.ok && (await whep.text()).startsWith("v=0"), "WHEP SDP 응답");

// ── 2. 실제 mqtt.js 클라이언트 ↔ 데모 브로커 ───────────────────────────────
globalThis.WebSocket = window.WebSocket;
const client = mqtt.connect("ws://demo.iseco.local:9001", { forceNativeWebSocket: true, reconnectPeriod: 0 });
const got = [];
client.on("message", (t, p) => got.push([t, p.toString()]));

await new Promise((ok, no) => {
  client.on("connect", ok);
  client.on("error", no);
  setTimeout(() => no(new Error("CONNACK 타임아웃")), 3000);
});
await client.subscribeAsync("iseco/ui/refresh");
window.__DEMO_BROKER__.publish("iseco/ui/refresh", "");
window.__DEMO_BROKER__.publish("E4:30:22:F3:30:A1/heartbeat", "");   // 필터 밖 → 무시돼야 함
await new Promise((r) => setTimeout(r, 120));
assert.deepEqual(got, [["iseco/ui/refresh", ""]], "구독 필터 일치분만 전달: " + JSON.stringify(got));

const wild = mqtt.connect("ws://demo.iseco.local:9001", { forceNativeWebSocket: true, reconnectPeriod: 0 });
const all = [];
wild.on("message", (t, p) => all.push([t, p.toString()]));
await new Promise((ok, no) => { wild.on("connect", ok); wild.on("error", no); setTimeout(() => no(new Error("2nd CONNACK 타임아웃")), 3000); });
await wild.subscribeAsync("#");
const relay = JSON.stringify({ UtcTime: new Date().toISOString(), Source: { RelayToken: "Relay-1" }, Data: { LogicalState: "active" } });
window.__DEMO_BROKER__.publish("E4:30:22:F3:30:A1/heartbeat", "");
window.__DEMO_BROKER__.publish("E4:30:22:F3:31:AA/onvif-ej/Device/tns1:Trigger/tns1:Relay/&Relay-1", relay);
await new Promise((r) => setTimeout(r, 120));
assert.equal(all.length, 2, "# 구독은 전부 수신: " + JSON.stringify(all));
assert.equal(all[1][1], relay, "JSON 페이로드 무손상");
assert.ok(all[1][0].endsWith("&Relay-1"), "특수문자 토픽 무손상");

client.end(true); wild.end(true);
console.log("shim 검증 통과 —", evs.length, "이벤트 /", cams.length, "카메라");
process.exit(0);
