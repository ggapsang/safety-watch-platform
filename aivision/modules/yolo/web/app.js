/* 모듈 화면 로직. 의존성 없음.
 *
 * 상태는 /api/state 한 곳에서만 받는다. 조각조각 부르면 화면 일부는 새 값, 일부는 헌 값이
 * 되어 "학습이 끝났는데 목록에는 아직 돌고 있음" 같은 어긋남이 생긴다.
 *
 * 폴링 주기를 학습 중일 때만 빠르게 한다. 학습은 몇 시간을 돌지만 그 사이 화면을 계속
 * 켜 두는 사람이 있고, 반대로 아무 일도 없을 때 2초마다 두드릴 이유는 없다.
 */
const IDLE_MS = 5000;
const BUSY_MS = 2000;

let timer = null;
let lastLogLen = 0;

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
  if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
  return body;
}

function say(el, text, ok) {
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
  if (ok) window.setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, 6000);
}

/* ── 그리기 ─────────────────────────────────────────────────────── */

function renderNotice(state) {
  const bits = [];
  if (state.module.mode === "dry-run") {
    bits.push("지금은 <b>dry-run</b> 입니다 — 모델 파일이 없어 합성 박스를 발행합니다. " +
              "아래에서 학습을 돌리거나, 이미 있는 <code>.onnx</code> 를 모델 폴더에 두고 모듈을 다시 시작하세요.");
  }
  if (!state.pretrained.length) {
    bits.push("사전학습 가중치(<code>yolov7_training.pt</code>)가 모델 폴더에 없습니다. " +
              "공식 YOLOv7 릴리스에서 받아 두어야 전이학습을 시작할 수 있습니다.");
  }
  if (!state.datasets.length) {
    bits.push("데이터셋 yaml 을 찾지 못했습니다. 학습 데이터를 볼륨에 넣고 " +
              "<code>train:</code> · <code>nc:</code> 가 있는 yaml 을 함께 두세요.");
  }
  $("notice").innerHTML = bits.length
    ? `<div class="warn">${bits.join("<br>")}</div>` : "";
}

function renderForm(state) {
  const ds = $("data-select");
  if (ds.dataset.count !== String(state.datasets.length)) {
    ds.dataset.count = String(state.datasets.length);
    ds.innerHTML = state.datasets.length
      ? state.datasets.map((d) =>
          `<option value="${esc(d.path)}">${esc(d.name)}${d.nc != null ? ` — ${d.nc}클래스` : ""}</option>`).join("")
      : `<option value="">(없음)</option>`;
  }
  const chosen = state.datasets.find((d) => d.path === ds.value);
  $("data-hint").textContent = chosen
    ? `${chosen.path}${chosen.names ? "  ·  " + chosen.names : ""}`
    : "볼륨에서 찾은 데이터셋 yaml 입니다.";

  const ws = $("weights-select");
  if (ws.dataset.count !== String(state.pretrained.length)) {
    ws.dataset.count = String(state.pretrained.length);
    ws.innerHTML = state.pretrained.length
      ? state.pretrained.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")
      : `<option value="">(없음 — 처음부터 학습)</option>`;
  }
  if (!$("device-input").value) $("device-input").placeholder = state.training.device || "0";

  const busy = state.training.busy;
  $("start-btn").disabled = busy || !state.datasets.length;
  $("cancel-btn").disabled = !busy;
}

function renderProgress(state) {
  const run = state.training.run;
  if (!run) { $("progress").innerHTML = `<p class="desc muted">돌고 있는 학습이 없습니다.</p>`; return; }
  const pct = run.epochs ? Math.min(100, Math.round((run.epoch / run.epochs) * 100)) : 0;
  $("progress").innerHTML = `
    <div class="kv">
      <span><b>${esc(run.name)}</b></span>
      <span class="pill ${esc(run.status)}">${esc(run.status)}</span>
      <span>epoch <b>${run.epoch}</b> / ${run.epochs}</span>
      <span class="muted">${esc(run.started_at)}</span>
      ${run.error ? `<span style="color:var(--error)">${esc(run.error)}</span>` : ""}
    </div>
    <div class="bar"><i style="width:${pct}%"></i></div>`;
}

function renderRuns(state) {
  const rows = state.runs;
  $("run-rows").innerHTML = rows.length ? rows.map((r) => {
    const pts = (r.weights || []).join(", ") || "-";
    const canPublish = (r.weights || []).includes("best.pt");
    return `<tr>
      <td><b>${esc(r.name)}</b>${(r.onnx || []).length ? ` <span class="muted">(onnx 있음)</span>` : ""}</td>
      <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
      <td class="num">${r.epoch ?? "-"}${r.epochs ? ` / ${r.epochs}` : ""}</td>
      <td class="muted">${esc(pts)}</td>
      <td style="text-align:right; white-space:nowrap">
        <button data-publish="${esc(r.name)}" ${canPublish ? "" : "disabled"}>모델로 쓰기</button>
        <button class="danger" data-delete="${esc(r.name)}">삭제</button>
      </td>
    </tr>`;
  }).join("") : `<tr><td colspan="5" class="muted">학습 기록이 없습니다.</td></tr>`;
}

function renderModels(state) {
  $("model-rows").innerHTML = state.models.length ? state.models.map((m) => `
    <tr><td>${esc(m.name)}${state.module.model.endsWith("/" + m.name) ? ` <span class="pill running">사용 중</span>` : ""}</td>
        <td class="num">${m.size_mb}MB</td><td class="muted">${esc(m.mtime)}</td></tr>`).join("")
    : `<tr><td colspan="3" class="muted">배치된 모델이 없습니다.</td></tr>`;
}

function renderInference(state) {
  const inf = state.inference || {};
  const cm = state.module.class_map || {};
  const codes = Object.entries(cm).map(([k, v]) => `${k} -> ${v}`).join(", ");
  $("infer-kv").innerHTML = `
    <span>모드 <b>${esc(state.module.mode)}</b></span>
    <span>장치 <b>${esc(inf.device || "-")}</b></span>
    <span>수신 <b class="num">${inf.frames ?? 0}</b></span>
    <span>발행 <b class="num">${inf.published ?? 0}</b></span>
    <span>임계값 <b class="num">${state.module.conf_thres}</b></span>`;
  const cams = inf.cameras || [];
  $("infer-rows").innerHTML = `
    <tr><td class="muted">할당 카메라</td><td>${cams.length ? cams.join(", ") : "없음 — 플랫폼에서 할당하세요"}</td></tr>
    <tr><td class="muted">클래스 매핑</td><td>${codes ? esc(codes) : "비어 있음 — CLASS_MAP 을 채우세요"}</td></tr>
    ${Object.entries(inf.errors || {}).map(([c, e]) =>
      `<tr><td class="muted">카메라 ${esc(c)}</td><td style="color:var(--error)">${esc(e)}</td></tr>`).join("")}`;
}

async function renderLog() {
  const { lines } = await api("/api/logs?limit=400");
  const el = $("log");
  if (!lines.length) { el.textContent = "로그가 없습니다."; return; }
  // 사용자가 위로 스크롤해 읽고 있으면 끌어내리지 않는다.
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (lines.length !== lastLogLen) {
    el.textContent = lines.join("\n");
    lastLogLen = lines.length;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }
}

/* ── 폴링 ───────────────────────────────────────────────────────── */

async function tick() {
  let busy = false;
  try {
    const state = await api("/api/state");
    busy = state.training.busy;
    renderNotice(state);
    renderForm(state);
    renderProgress(state);
    renderRuns(state);
    renderModels(state);
    renderInference(state);
    await renderLog();
  } catch (err) {
    $("notice").innerHTML = `<div class="warn">모듈에 연결할 수 없습니다: ${esc(err.message)}</div>`;
  }
  window.clearTimeout(timer);
  timer = window.setTimeout(tick, busy ? BUSY_MS : IDLE_MS);
}

/* ── 조작 ───────────────────────────────────────────────────────── */

$("train-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    name: f.get("name").trim(),
    data: f.get("data"),
    weights: f.get("weights") || "",
    epochs: Number(f.get("epochs")),
    batch: Number(f.get("batch")),
    imgsz: Number(f.get("imgsz")),
    device: f.get("device").trim(),
  };
  $("start-btn").disabled = true;
  try {
    await api("/api/train", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    say($("train-msg"), `학습을 시작했습니다: ${body.name}`, true);
  } catch (err) {
    say($("train-msg"), err.message, false);
  }
  tick();
});

$("cancel-btn").addEventListener("click", async () => {
  if (!window.confirm("돌고 있는 학습을 취소합니다. 지금까지의 가중치는 남습니다.")) return;
  try {
    await api("/api/train/cancel", { method: "POST" });
    say($("train-msg"), "취소했습니다.", true);
  } catch (err) {
    say($("train-msg"), err.message, false);
  }
  tick();
});

$("run-rows").addEventListener("click", async (e) => {
  const publish = e.target.dataset.publish;
  const del = e.target.dataset.delete;
  const msg = $("run-msg");
  try {
    if (publish) {
      e.target.disabled = true;
      say(msg, "ONNX 로 내보내는 중입니다. 1~2분 걸립니다.", true);
      const r = await api("/api/publish", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run: publish, weights: "best.pt" }),
      });
      say(msg, `${r.model} (${r.size_mb}MB) 를 배치했습니다. 모듈을 다시 시작하면 이 모델로 돕니다.`, true);
    } else if (del) {
      if (!window.confirm(`${del} 산출물을 지웁니다. 가중치도 함께 사라집니다.`)) return;
      await api(`/api/runs/${encodeURIComponent(del)}`, { method: "DELETE" });
      say(msg, `${del} 을 지웠습니다.`, true);
    } else {
      return;
    }
  } catch (err) {
    say(msg, err.message, false);
  }
  tick();
});

tick();
