/* 모듈 화면의 동작.
 *
 * 상태는 /api/state 하나로만 받는다. 조각조각 부르면 화면의 일부만 새것이 되어
 * '모델은 바뀌었는데 클래스 표는 옛것' 같은 어긋남이 생긴다.
 *
 * 폴링은 3초다. 이 화면에서 빨리 바뀌는 것은 추론 상태(프레임 수)뿐이라 더 자주 볼
 * 이유가 없다. 대신 사람이 무언가를 누른 직후에는 곧바로 한 번 더 받는다.
 *
 * 편집 중인 표는 덮어쓰지 않는다. 3초마다 입력칸이 초기화되면 이름을 적을 수 없다.
 */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let state = null;
let solutions = [];
let editingModel = "";     // 클래스 표를 보여 주고 있는 모델
let dirty = false;         // 표를 손대는 중이면 폴링이 덮어쓰지 않는다

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

/* ── 상태 ──────────────────────────────────────────────────────────── */

async function refresh() {
  try {
    state = await api("/api/state");
  } catch (e) {
    $("notice").innerHTML = `<div class="warn">모듈에 연결하지 못했습니다: ${esc(e.message)}</div>`;
    return;
  }
  renderNotice();
  renderInference();
  renderTuning();
  renderModels();
  if (!dirty) renderClasses();
}

function renderNotice() {
  const m = state.module;
  const out = [];
  if (m.mode === "dry-run") {
    out.push(`<div class="warn"><b>dry-run</b> — 모델이 없어 합성 박스를 발행하고 있습니다.
      위에서 ONNX 모델을 올리면 실제 추론으로 바뀝니다.</div>`);
  }
  const active = (state.models || []).find((x) => x.active);
  if (active && active.classes.length && !active.classes.some((c) => c.item)) {
    out.push(`<div class="warn">탐지 항목에 연결된 클래스가 없습니다 —
      박스는 그리지만 <b>이벤트는 쌓이지 않습니다</b>. 오른쪽 '탐지 대상' 에서 연결하세요.</div>`);
  }
  $("notice").innerHTML = out.join("");
}

function renderInference() {
  const inf = state.inference || {};
  const m = state.module;
  $("infer-kv").innerHTML = [
    ["모드", m.mode === "dry-run" ? "dry-run" : "추론"],
    ["모델", m.model || "-"],
    ["장치", inf.device || "-"],
    ["입력 크기", m.imgsz],
    ["누적 프레임", (inf.frames ?? 0).toLocaleString()],
    ["발행", (inf.published ?? 0).toLocaleString()],
  ].map(([k, v]) => `<span>${k} <b>${esc(v)}</b></span>`).join("");

  const cams = inf.cameras || [];
  const errors = inf.errors || {};
  $("infer-rows").innerHTML = cams.length
    ? cams.map((c) => {
        const err = errors[String(c)];
        return `<tr><td class="num">카메라 ${c}</td><td>${
          err ? `<span class="pill unknown">${esc(err)}</span>` : "정상"
        }</td></tr>`;
      }).join("")
    : `<tr><td colspan="2" class="muted">할당된 카메라가 없습니다.
         플랫폼 관리자 화면에서 이 모듈에 카메라를 할당하세요.</td></tr>`;
}

/* ── 민감도 ────────────────────────────────────────────────────────── */

// 이름과 설명을 코드에 둔다. 값의 뜻을 모르면 슬라이더는 위험하기만 하다.
const TUNING_META = {
  conf_thres: ["최소 점수 (conf)", "이보다 낮은 후보는 추론 단계에서 버립니다.", 0.01],
  iou_thres: ["겹침 기준 (IoU)", "같은 물체로 볼 겹침 정도입니다. 낮출수록 박스가 줄어듭니다.", 0.01],
  min_conf: ["이벤트 최소 점수", "이 점수 이상일 때만 이벤트로 올립니다. 박스는 그대로 그립니다.", 0.01],
  min_box_px: ["최소 박스 크기 (px)",
    "sqrt(가로×세로)가 이보다 작으면 버립니다. 0 이면 안 버립니다. " +
    "작은 오탐은 라벨에서 지우는 것보다 여기서 거르는 편이 낫습니다.", 1],
  sample_fps: ["초당 처리 장수", "초당 몇 장을 볼지. 높이면 반응이 빨라지고 CPU 를 더 씁니다.", 0.1],
};

function renderTuning() {
  if (document.activeElement && document.activeElement.dataset.tuning) return;
  const limits = state.limits || {};
  $("tuning").innerHTML = Object.entries(TUNING_META).map(([key, [label, desc, step]]) => {
    const lim = limits[key] || { min: 0, max: 1 };
    const value = state.tuning[key];
    return `<label>
      <span>${esc(label)} — <b data-out="${key}">${value}</b></span>
      <input type="range" data-tuning="${key}" min="${lim.min}" max="${lim.max}"
             step="${step}" value="${value}" />
      <em>${esc(desc)}</em>
    </label>`;
  }).join("");

  $("tuning").querySelectorAll("[data-tuning]").forEach((input) => {
    input.oninput = () => {
      document.querySelector(`[data-out="${input.dataset.tuning}"]`).textContent = input.value;
    };
    // 저장은 손을 뗄 때 한 번만 한다. 드래그하는 내내 저장하면 워커가 계속 다시 뜬다.
    input.onchange = () => saveTuning(input.dataset.tuning, input.value);
  });
}

async function saveTuning(key, value) {
  msg("tuning-msg", "저장 중…");
  try {
    await api("/api/tuning", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: Number(value) }),
    });
    msg("tuning-msg", "저장했습니다. 추론 워커가 곧 새 값으로 다시 뜹니다.", "ok");
    refresh();
  } catch (e) {
    msg("tuning-msg", e.message, "err");
  }
}

/* ── 모델 ──────────────────────────────────────────────────────────── */

function renderModels() {
  const models = state.models || [];
  $("model-rows").innerHTML = models.length
    ? models.map((m) => `<tr>
        <td>
          <b>${esc(m.name)}</b>${m.active ? ' <span class="pill unknown">사용 중</span>' : ""}
          ${m.note ? `<div class="muted">${esc(m.note)}</div>` : ""}
        </td>
        <td class="num">${m.size_mb}MB</td>
        <td class="num">${m.classes.length || "-"}</td>
        <td style="white-space:nowrap">
          ${m.active ? "" : `<button data-use="${esc(m.name)}">사용</button>`}
          <button data-show="${esc(m.name)}">클래스</button>
          ${m.active ? "" : `<button class="danger" data-del="${esc(m.name)}">삭제</button>`}
        </td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="muted">올린 모델이 없습니다.</td></tr>`;

  $("model-rows").querySelectorAll("[data-use]").forEach((b) => {
    b.onclick = () => act(`/api/models/${b.dataset.use}/use`, "POST",
                          `${b.dataset.use} 을(를) 적용했습니다.`);
  });
  $("model-rows").querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = () => {
      if (!confirm(`${b.dataset.del} 을(를) 지웁니다. 되돌릴 수 없습니다.`)) return;
      act(`/api/models/${b.dataset.del}`, "DELETE", "지웠습니다.");
    };
  });
  $("model-rows").querySelectorAll("[data-show]").forEach((b) => {
    b.onclick = () => { editingModel = b.dataset.show; dirty = false; renderClasses(); };
  });
}

async function act(path, method, okText) {
  msg("model-msg", "처리 중…");
  try {
    await api(path, { method });
    msg("model-msg", okText, "ok");
    await refresh();
  } catch (e) {
    msg("model-msg", e.message, "err");
  }
}

$("upload-btn").onclick = async () => {
  const file = $("file-input").files[0];
  if (!file) { msg("upload-msg", "파일을 고르세요.", "err"); return; }
  const form = new FormData();
  form.append("file", file);
  $("upload-btn").disabled = true;
  msg("upload-msg", `올리는 중… (${(file.size / 1024 / 1024).toFixed(1)}MB)`);
  try {
    const out = await api("/api/models", { method: "POST", body: form });
    msg("upload-msg", out.named
      ? `올렸습니다. 클래스 ${out.classes.length}개를 모델에서 읽었습니다.`
      : `올렸습니다. 모델에 클래스 이름이 없어 인덱스 ${out.classes.length}개로 만들었습니다 — 이름을 붙여 주세요.`,
      "ok");
    $("file-input").value = "";
    editingModel = out.name;
    dirty = false;
    await refresh();
  } catch (e) {
    msg("upload-msg", e.message, "err");
  } finally {
    $("upload-btn").disabled = false;
  }
};

/* ── 탐지 대상 ─────────────────────────────────────────────────────── */

function currentModel() {
  const models = state.models || [];
  return models.find((m) => m.name === editingModel)
      || models.find((m) => m.active) || models[0] || null;
}

function renderClasses() {
  const model = currentModel();
  if (!model) {
    $("class-rows").innerHTML =
      `<tr><td colspan="3" class="muted">모델을 먼저 올리세요.</td></tr>`;
    return;
  }
  editingModel = model.name;
  if (!model.classes.length) {
    $("class-rows").innerHTML =
      `<tr><td colspan="3" class="muted">이 모델에서 클래스를 읽지 못했습니다.</td></tr>`;
    return;
  }

  const options = (selected) => [`<option value="">(연결 안 함 — 박스만)</option>`]
    .concat(solutions.map((s) =>
      `<option value="${esc(s.code)}"${s.code === selected ? " selected" : ""}>
         ${esc(s.name)} (${esc(s.code)})</option>`))
    // 플랫폼에서 목록을 못 받았는데 이미 저장된 코드가 있으면 그것도 남겨 둔다.
    .concat(selected && !solutions.some((s) => s.code === selected)
      ? [`<option value="${esc(selected)}" selected>${esc(selected)}</option>`] : [])
    .join("");

  $("class-rows").innerHTML = model.classes.map((c, i) => `<tr>
      <td><code>${esc(c.key)}</code></td>
      <td><input data-alias="${i}" value="${esc(c.alias)}"
                 placeholder="${esc(c.key)}" /></td>
      <td><select data-item="${i}">${options(c.item)}</select></td>
    </tr>`).join("");

  $("class-rows").querySelectorAll("input,select").forEach((el) => {
    el.oninput = () => { dirty = true; };
  });
}

$("save-classes").onclick = async () => {
  const model = currentModel();
  if (!model) return;
  const rows = model.classes.map((c, i) => ({
    key: c.key,
    alias: document.querySelector(`[data-alias="${i}"]`).value,
    item: document.querySelector(`[data-item="${i}"]`).value,
  }));
  msg("class-msg", "저장 중…");
  try {
    await api(`/api/models/${model.name}/classes`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    dirty = false;
    msg("class-msg", "저장했습니다. 추론 워커가 곧 새 설정으로 다시 뜹니다.", "ok");
    await refresh();
  } catch (e) {
    msg("class-msg", e.message, "err");
  }
};

/* ── 기동 ──────────────────────────────────────────────────────────── */

async function loadSolutions() {
  try {
    const out = await api("/api/solutions");
    solutions = out.items || [];
    if (out.error) {
      $("class-msg").textContent = out.error + " — 탐지 항목을 고를 수 없습니다.";
      $("class-msg").className = "msg err";
    }
  } catch { solutions = []; }
}

(async () => {
  await loadSolutions();
  await refresh();
  setInterval(refresh, 3000);
  // 탐지 항목은 플랫폼에서 가끔 늘어난다. 자주 볼 필요는 없다.
  setInterval(loadSolutions, 30000);
})();
