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
  // 카메라 표는 /api/state(모델 목록)와 /api/cameras(담당 카메라) 두 곳에서 온다.
  // 어느 쪽이 먼저 오든 맞게 보이도록, 상태를 새로 받을 때마다 이미 들고 있는
  // 카메라로 다시 그린다 — 안 그러면 카메라가 먼저 도착했을 때 체크박스 자리가
  // '올려 둔 모델이 없습니다' 로 남고 15초 뒤에야 채워진다.
  if (cameras.length) renderCameras("");
  // 본문 표는 읽기 전용이라 언제 다시 그려도 된다. 편집 중인 모달은 건드리지 않는다 —
  // 3초마다 입력칸이 초기화되면 이름을 적을 수가 없다.
  renderClasses();
}

function renderNotice() {
  const m = state.module;
  const out = [];
  if (m.mode === "stopped") {
    out.push(`<div class="warn"><b>사용 중단</b> — 추론을 멈춰 두었습니다. 박스도 이벤트도
      나가지 않습니다. 오른쪽 '배치된 모델' 에서 <b>사용</b> 을 누르면 다시 시작합니다.</div>`);
  } else if (m.mode === "dry-run") {
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
    ["모드", { stopped: "사용 중단", "dry-run": "dry-run" }[m.mode] || "추론"],
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
  // 손대고 있는 칸은 덮어쓰지 않는다. 3초마다 값이 되돌아가면 숫자를 칠 수가 없다.
  const busy = document.activeElement && document.activeElement.dataset.tuning;
  if (busy) return;

  const limits = state.limits || {};
  $("tuning").innerHTML = Object.entries(TUNING_META).map(([key, [label, desc, step]]) => {
    const lim = limits[key] || { min: 0, max: 1 };
    const value = state.tuning[key];
    // 바와 숫자를 나란히 둔다. 바로는 0.55 와 0.56 을 가려 짚을 수 없고,
    // 숫자만 두면 어느 쯤인지 감이 안 온다. 둘은 같은 값을 가리킨다.
    return `<label>
      <span>${esc(label)}</span>
      <div class="tune">
        <input type="range" data-tuning="${key}" data-kind="range"
               min="${lim.min}" max="${lim.max}" step="${step}" value="${value}" />
        <input type="number" data-tuning="${key}" data-kind="number"
               min="${lim.min}" max="${lim.max}" step="${step}" value="${value}" />
      </div>
      <em>${esc(desc)} (${lim.min} ~ ${lim.max})</em>
    </label>`;
  }).join("");

  $("tuning").querySelectorAll("[data-tuning]").forEach((input) => {
    const key = input.dataset.tuning;
    const pair = (kind) => $("tuning").querySelector(`[data-tuning="${key}"][data-kind="${kind}"]`);

    // 한쪽을 움직이면 다른 쪽이 따라온다. 저장은 아직 하지 않는다.
    input.oninput = () => {
      const other = pair(input.dataset.kind === "range" ? "number" : "range");
      if (other) other.value = input.value;
    };

    if (input.dataset.kind === "range") {
      // 드래그하는 내내 저장하면 워커가 계속 다시 뜬다. 손을 뗄 때 한 번만 보낸다.
      input.onchange = () => saveTuning(key, input.value);
    } else {
      // 숫자 칸은 다 치고 나서(포커스가 떠날 때나 Enter) 보낸다. 한 글자마다 보내면
      // '0.5' 를 치는 도중의 '0' 이 저장된다.
      input.onblur = () => saveTuning(key, input.value);
      input.onkeydown = (e) => { if (e.key === "Enter") input.blur(); };
      input.onchange = null;
    }
  });
}

async function saveTuning(key, value) {
  // 빈 칸이나 글자는 보내지 않는다. 서버가 400 을 돌려주는 것보다, 원래 값으로
  // 되돌려 보여 주는 편이 '무엇이 적용돼 있는지' 를 헷갈리지 않게 한다.
  if (value === "" || Number.isNaN(Number(value))) {
    msg("tuning-msg", "숫자를 입력하세요.", "err");
    renderTuning();
    return;
  }
  if (Number(value) === state.tuning[key]) return;   // 안 바뀌었으면 워커를 흔들지 않는다
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
  const stopped = state.module.mode === "stopped";
  const shown = currentModel();

  $("model-rows").innerHTML = models.length
    ? models.map((m) => {
        // 세 상태를 구분해 보여 준다 — 돌고 있음 / 골라 뒀지만 멈춤 / 안 씀.
        const badge = !m.active ? ""
          : stopped ? ' <span class="pill unknown">사용 중단</span>'
                    : ' <span class="pill on">사용 중</span>';
        // 표가 지금 어느 모델을 보여 주는지 행에서도 알 수 있어야 한다. 모델이 하나뿐일
        // 때 '클래스' 를 눌러도 화면이 안 바뀌어 '반응이 없다' 로 보였다.
        const here = shown && shown.name === m.name;
        return `<tr${here ? ' class="picked"' : ""}>
        <td>
          <b>${esc(m.name)}</b>${badge}
          ${m.note ? `<div class="muted">${esc(m.note)}</div>` : ""}
        </td>
        <td class="num">${m.size_mb}MB</td>
        <td class="num">${m.classes.length || "-"}</td>
        <td style="white-space:nowrap">
          ${m.active && !stopped
            ? `<button data-unuse="${esc(m.name)}">사용 중단</button>`
            : `<button class="primary" data-use="${esc(m.name)}">사용</button>`}
          <button data-show="${esc(m.name)}">클래스 관리</button>
          ${m.active && !stopped
            ? "" : `<button class="danger" data-del="${esc(m.name)}">삭제</button>`}
        </td>
      </tr>`;
      }).join("")
    : `<tr><td colspan="4" class="muted">올린 모델이 없습니다.</td></tr>`;

  $("model-rows").querySelectorAll("[data-use]").forEach((b) => {
    b.onclick = () => act(`/api/models/${b.dataset.use}/use`, "POST",
                          `${b.dataset.use} 으로 추론을 시작합니다.`);
  });
  $("model-rows").querySelectorAll("[data-unuse]").forEach((b) => {
    b.onclick = () => act(`/api/models/${b.dataset.unuse}/unuse`, "POST",
                          "추론을 멈췄습니다. 모델은 그대로 있습니다.");
  });
  $("model-rows").querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = () => {
      if (!confirm(`${b.dataset.del} 을(를) 지웁니다. 되돌릴 수 없습니다.`)) return;
      act(`/api/models/${b.dataset.del}`, "DELETE", "지웠습니다.");
    };
  });
  $("model-rows").querySelectorAll("[data-show]").forEach((b) => {
    b.onclick = () => openClassEditor(b.dataset.show);
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

/* ── 카메라별 모델 ─────────────────────────────────────────────────── */

let cameras = [];
let cameraBusy = false;    // 저장이 오가는 중이면 3초 폴링이 체크를 되돌리지 않게 한다

async function loadCameras() {
  try {
    const out = await api("/api/cameras");
    cameras = out.items || [];
    renderCameras(out.error || "");
  } catch (e) {
    renderCameras(e.message);
  }
}

function renderCameras(error) {
  // 저장이 오가는 동안에는 덮어쓰지 않는다. 체크를 누른 직후 폴링이 끼어들면 방금
  // 누른 칸이 잠깐 원래대로 돌아갔다가 다시 바뀐다 — 눌러도 안 먹는 것처럼 보인다.
  if (cameraBusy) return;
  const models = (state?.models || []).map((m) => m.name);
  const active = state?.module?.model || "";

  if (error) {
    $("camera-rows").innerHTML =
      `<tr><td colspan="5" class="muted">${esc(error)}</td></tr>`;
    return;
  }
  if (!cameras.length) {
    $("camera-rows").innerHTML =
      `<tr><td colspan="5" class="muted">담당 카메라가 없습니다.
         플랫폼(플러그인 탭 위쪽)에서 이 모듈에 카메라를 할당하세요.</td></tr>`;
    return;
  }

  $("camera-rows").innerHTML = cameras.map((c) => {
    const picked = c.models || [];
    const on = !c.off;
    // 켜진 쪽을 체크된 상태로 보여 준다. 예전에는 '사용 안 함' 에 체크하게 해서,
    // 체크가 곧 끔이라는 뒤집힌 뜻이었다 — 한 줄만 봐서는 어느 쪽이 켠 건지 몰랐다.
    const use = `<label class="use ${on ? "on" : "off"}">
      <input type="checkbox" data-cam="${c.camera_id}" data-use ${on ? "checked" : ""} />
      <span>${on ? "사용" : "사용 안 함"}</span></label>`;
    const boxes = models.length
      ? models.map((m) => `<label>
          <input type="checkbox" data-cam="${c.camera_id}" value="${esc(m)}"
                 ${picked.includes(m) ? "checked" : ""} ${on ? "" : "disabled"} />
          ${esc(m)}</label>`).join("")
      : `<span class="muted">올려 둔 모델이 없습니다</span>`;
    // 아무것도 안 골랐을 때 무엇으로 도는지 그 자리에 적는다. 안 적으면 빈 체크박스
    // 줄만 보고 '아무 모델도 안 돈다' 로 읽는다.
    const hint = on && !picked.length && active
      ? `<div class="why">안 고르면 공통 모델 <b>${esc(active)}</b></div>` : "";
    return `<tr>
      <td><b>${esc(c.name || c.camera_id)}</b></td>
      <td class="muted">${esc(c.location)}</td>
      <td>${use}</td>
      <td><div class="picks${on ? "" : " disabled"}">${boxes}</div>${hint}</td>
      <td>${effectiveLabel(c)}</td>
    </tr>`;
  }).join("");

  renderCameraSummary();

  $("camera-rows").querySelectorAll("input[data-cam]").forEach((box) => {
    box.onchange = () => saveCamera(box.dataset.cam);
  });
}

/** 지금 이 카메라에서 **실제로** 도는 것.
 *
 * 설정이 아니라 현재 상태를 쓴다. 예전에는 카메라별 '사용 안 함' 만 보고 나머지를 전부
 * 초록색으로 칠했다 — 모듈이 통째로 중단돼 아무것도 안 나가는 동안에도 표는 여섯 줄
 * 모두 '돌고 있다' 고 말했고, 박스가 왜 안 그려지는지 화면 어디에도 없었다.
 *
 * 멈추는 이유가 네 가지라 네 가지를 다 구분해 보여 준다. 무엇을 눌러 되살릴지는
 * 이유마다 다르다.
 */
function effectiveLabel(c) {
  if (state?.module?.mode === "stopped") {
    return `<span class="pill stop">중단됨</span>
      <div class="why">모듈 전체가 멈춰 있습니다 — '배치된 모델'에서 <b>사용</b></div>`;
  }
  if (c.off) {
    return `<span class="pill stop">사용 안 함</span>
      <div class="why">영상도 열지 않습니다</div>`;
  }
  const running = c.effective || [];
  if (!running.length) {
    return `<span class="pill stop">모델 없음</span>
      <div class="why">모델을 고르거나 공통 모델을 지정하세요</div>`;
  }

  // 설정상 돌아야 하는 것과 정말 도는 것은 다르다. 스트림이 끊기면 설정은 그대로인 채
  // 워커만 죽는다 — 그 차이가 안 보이면 카메라 문제를 설정 문제로 착각한다.
  const inf = state?.inference || {};
  const cam = String(c.camera_id);
  const err = (inf.errors || {})[cam];
  const live = (inf.cameras || []).includes(c.camera_id);

  if (err) {
    return `<span class="pill stop">오류</span><div class="why">${esc(err)}</div>`;
  }
  if (!live) {
    return `<span class="pill unknown">대기 중</span>
      <div class="why">워커가 뜨는 중입니다</div>`;
  }
  // 장치는 부가 정보다. 이 카메라의 것을 쓴다 — 전체 집계(inf.device)를 쓰면 한 대만
  // 꺼 두어도 모든 줄에 'cuda, 사용 안 함' 이 붙는다.
  const dev = (inf.devices || {})[cam] || inf.device || "-";
  return `<span class="pill on">동작 중</span>
    <div class="why">${esc(running.join(" + "))} · ${esc(dev)}</div>`;
}

/** 표 위의 한 줄 요약. 여섯 줄을 다 읽지 않아도 지금 몇 대가 도는지 보이게 한다. */
function renderCameraSummary() {
  const el = $("camera-summary");
  if (!el) return;
  if (state?.module?.mode === "stopped") {
    el.className = "state stop";
    el.innerHTML = `<b>중단됨</b> — 박스도 이벤트도 나가지 않습니다.
      오른쪽 '배치된 모델'에서 <b>사용</b>을 누르면 다시 시작합니다.`;
    return;
  }
  const inf = state?.inference || {};
  const on = cameras.filter((c) => !c.off);
  const live = on.filter((c) => (inf.cameras || []).includes(c.camera_id)).length;
  const offCount = cameras.length - on.length;
  // 장치는 실제로 도는 카메라들의 것만 모은다. 꺼 둔 카메라의 '사용 안 함' 이 장치
  // 이름에 섞여 들어가면 무엇으로 추론하는지가 안 읽힌다.
  const devs = [...new Set(on.map((c) => (inf.devices || {})[String(c.camera_id)])
                             .filter((d) => d && d !== "사용 안 함"))];
  el.className = "state" + (live ? " on" : " stop");
  el.innerHTML = `<b>${live} / ${on.length}대 동작 중</b>`
    + ` · 공통 모델 ${esc(state?.module?.model || "없음")}`
    + ` · ${esc(devs.join(", ") || "-")}`
    + ` · 초당 ${state?.tuning?.sample_fps ?? "-"}장`
    + (offCount ? ` · <span class="muted">사용 안 함 ${offCount}대</span>` : "");
}

/** 그 카메라 행의 체크박스를 모아 한 번에 저장한다.
 *
 * 체크 하나마다 보내지 않는다 — 서버가 '이 카메라의 모델은 이것들' 을 통째로 받게 해야
 * 더하기/빼기 순서에 따라 결과가 달라지는 일이 없다. */
async function saveCamera(cameraId) {
  const row = $("camera-rows").querySelectorAll(`input[data-cam="${cameraId}"]`);
  let off = false;
  const picked = [];
  row.forEach((box) => {
    // '사용' 은 켜진 쪽이 체크다. 서버가 들고 있는 것은 그 반대(off)라 여기서 뒤집는다 —
    // 화면은 사람이 읽기 좋은 쪽, 저장은 '예외를 기록' 하는 쪽이 각각 자연스럽다.
    if (box.dataset.use !== undefined) off = !box.checked;
    else if (box.checked) picked.push(box.value);
  });

  msg("camera-msg", "저장 중…");
  cameraBusy = true;
  try {
    await api(`/api/cameras/${cameraId}/models`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ models: picked, off }),
    });
    msg("camera-msg",
      off ? `카메라 ${cameraId} 는 추론하지 않습니다. 영상도 열지 않습니다.`
      : picked.length
        ? `카메라 ${cameraId} 는 ${picked.join(", ")} 로 봅니다. 워커가 곧 다시 뜹니다.`
        : `카메라 ${cameraId} 를 공통 모델로 되돌렸습니다.`, "ok");
  } catch (e) {
    msg("camera-msg", e.message, "err");
  } finally {
    // 성공이든 실패든 서버 값으로 맞춰 다시 그린다. 실패했는데 누른 대로 남아 있으면
    // 저장된 줄 안다.
    cameraBusy = false;
    await Promise.all([refresh(), loadCameras()]);
  }
}

/* ── 탐지 대상 ─────────────────────────────────────────────────────── */

function currentModel() {
  const models = state.models || [];
  return models.find((m) => m.name === editingModel)
      || models.find((m) => m.active) || models[0] || null;
}

const solutionName = (code) => {
  const hit = solutions.find((s) => s.code === code);
  return hit ? `${hit.name} (${hit.code})` : code;
};

/** 본문의 읽기 전용 요약. 편집은 모달에서 한다 — 편집기를 두 벌 두면 언젠가 어긋난다. */
function renderClasses() {
  const model = currentModel();
  $("class-of").textContent = model ? model.name : "";
  if (!model) {
    $("class-rows").innerHTML =
      `<tr><td colspan="4" class="muted">모델을 먼저 올리세요.</td></tr>`;
    return;
  }
  editingModel = model.name;
  if (!model.classes.length) {
    $("class-rows").innerHTML =
      `<tr><td colspan="4" class="muted">이 모델에서 클래스를 읽지 못했습니다.</td></tr>`;
    return;
  }
  $("class-rows").innerHTML = model.classes.map((c) => `<tr>
      <td><code>${esc(c.key)}</code></td>
      <td>${c.alias ? esc(c.alias) : `<span class="muted">${esc(c.key)}</span>`}</td>
      <td class="muted">${c.memo ? esc(c.memo) : "-"}</td>
      <td>${c.item
        ? esc(solutionName(c.item))
        : '<span class="muted">연결 안 함 — 박스만</span>'}</td>
    </tr>`).join("");
}

/* ── 클래스 편집기(모달) ───────────────────────────────────────────── */

function openClassEditor(name) {
  editingModel = name;
  dirty = false;
  const model = currentModel();
  if (!model) return;

  $("modal-of").textContent = model.name;
  const options = (selected) => [`<option value="">(연결 안 함 — 박스만)</option>`]
    .concat(solutions.map((s) =>
      `<option value="${esc(s.code)}"${s.code === selected ? " selected" : ""}>
         ${esc(s.name)} (${esc(s.code)})</option>`))
    // 플랫폼에서 목록을 못 받았는데 이미 저장된 코드가 있으면 그것도 남겨 둔다.
    .concat(selected && !solutions.some((s) => s.code === selected)
      ? [`<option value="${esc(selected)}" selected>${esc(selected)}</option>`] : [])
    .join("");

  $("modal-rows").innerHTML = model.classes.length
    ? model.classes.map((c, i) => `<tr>
        <td><code>${esc(c.key)}</code></td>
        <td><input data-alias="${i}" value="${esc(c.alias)}"
                   placeholder="${esc(c.key)}" /></td>
        <td><input data-memo="${i}" value="${esc(c.memo || "")}"
                   placeholder="무엇을 잡는 클래스인지, 주의할 점은" /></td>
        <td><select data-item="${i}">${options(c.item)}</select></td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="muted">이 모델에서 클래스를 읽지 못했습니다.</td></tr>`;

  $("modal-rows").querySelectorAll("input,select").forEach((el) => {
    el.oninput = () => { dirty = true; };
  });
  msg("class-msg", "");
  $("class-modal").hidden = false;
  const first = $("modal-rows").querySelector("input");
  if (first) first.focus();
}

function closeClassEditor() {
  if (dirty && !confirm("저장하지 않은 내용이 있습니다. 닫을까요?")) return;
  dirty = false;
  $("class-modal").hidden = true;
}

$("modal-close").onclick = closeClassEditor;
$("modal-cancel").onclick = closeClassEditor;
// 바깥을 눌러도 닫힌다. 안쪽 클릭까지 닫히면 편집 중에 사라져 버린다.
$("class-modal").onclick = (e) => { if (e.target === $("class-modal")) closeClassEditor(); };
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("class-modal").hidden) closeClassEditor();
});

$("save-classes").onclick = async () => {
  const model = currentModel();
  if (!model) return;
  const val = (sel) => (document.querySelector(sel) || { value: "" }).value;
  const rows = model.classes.map((c, i) => ({
    key: c.key,
    alias: val(`[data-alias="${i}"]`),
    memo: val(`[data-memo="${i}"]`),
    item: val(`[data-item="${i}"]`),
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
    $("class-modal").hidden = true;
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
  // 카메라 표는 상태·탐지항목과 아무 상관이 없으므로 나란히 부른다. 예전에는 주기만
  // 걸어 두고 첫 호출을 안 해서, 화면을 연 뒤 **첫 15초 동안 표가 비어 있었다** —
  // setInterval 은 곧바로 한 번 돌지 않는다.
  const camerasReady = loadCameras();
  // 탐지 항목이 먼저 있어야 클래스 표의 드롭다운이 첫 그림에서 채워진다.
  await loadSolutions();
  await Promise.all([refresh(), camerasReady]);

  setInterval(refresh, 3000);
  // 탐지 항목은 플랫폼에서 가끔 늘어난다. 자주 볼 필요는 없다.
  setInterval(loadSolutions, 30000);
  // 담당 카메라는 플랫폼에서 바뀐다(20초 주기로 반영된다). 그보다 자주 볼 필요는 없다.
  setInterval(loadCameras, 15000);
})();
