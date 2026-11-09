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
  if (m.mode === "idle") {
    out.push(`<div class="warn"><b>켜 둔 모델이 없습니다</b> — 공통 모델을 모두 꺼 두었고
      카메라별로 고른 모델도 없어 아무것도 추론하지 않습니다. 오른쪽 '배치된 모델' 에서
      <b>사용</b> 을 누르면 시작합니다.</div>`);
  } else if (m.mode === "dry-run") {
    out.push(`<div class="warn"><b>dry-run</b> — 모델이 없어 합성 박스를 발행하고 있습니다.
      위에서 ONNX 모델을 올리면 실제 추론으로 바뀝니다.</div>`);
  }
  // 켜 둔 공통 모델 가운데 이벤트로 이어지는 클래스가 하나도 없는 것을 짚는다.
  // 사람 모델처럼 박스만 보려고 켠 것도 있으니, 이름을 들어 알려 줄 뿐 막지는 않는다.
  const noItem = (state.models || []).filter((x) => x.active && x.classes.length
    && !x.classes.some((c) => c.item && c.enabled !== false));
  if (noItem.length) {
    out.push(`<div class="warn">${noItem.map((x) => `<b>${esc(x.name)}</b>`).join(", ")} —
      탐지 항목에 연결된 클래스가 없어 박스는 그리지만 <b>이벤트는 쌓이지 않습니다</b>.
      필요하면 '클래스 관리' 에서 연결하세요.</div>`);
  }
  $("notice").innerHTML = out.join("");
}

function renderInference() {
  const inf = state.inference || {};
  const m = state.module;
  $("infer-kv").innerHTML = [
    ["모드", { idle: "켜 둔 모델 없음", "dry-run": "dry-run" }[m.mode] || "추론"],
    ["공통 모델", (m.models || []).join(" + ") || "-"],
    ["장치", inf.device || "-"],
    // 모델마다 자기 입력 크기로 판독한다(AMR 640, 사람 1280 처럼). 설정값 하나로 보이면 틀린다.
    ["입력 크기", (state.models || []).filter((x) => x.active)
      .map((x) => `${x.name.replace(/\.onnx$/, "")} ${x.imgsz}`).join(" · ") || m.imgsz],
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
  const shown = currentModel();

  $("model-rows").innerHTML = models.length
    ? models.map((m) => {
        // 모델마다 따로 켜고 끈다 — 여럿이 동시에 '사용 중' 일 수 있다. 예전에는 공통
        // 모델이 하나라 하나를 켜면 다른 하나가 꺼졌다.
        const badge = m.active ? ' <span class="pill on">사용 중</span>' : "";
        // 표가 지금 어느 모델을 보여 주는지 행에서도 알 수 있어야 한다. 모델이 하나뿐일
        // 때 '클래스' 를 눌러도 화면이 안 바뀌어 '반응이 없다' 로 보였다.
        const here = shown && shown.name === m.name;
        return `<tr${here ? ' class="picked"' : ""}>
        <td>
          <b>${esc(m.name)}</b>${badge}
          ${m.note ? `<div class="muted">${esc(m.note)}</div>` : ""}
        </td>
        <td class="num">${m.size_mb}MB<div class="muted">입력 ${m.imgsz}</div></td>
        <td class="num">${m.classes.length || "-"}</td>
        <td style="white-space:nowrap">
          ${m.active
            ? `<button data-unuse="${esc(m.name)}">사용 중단</button>`
            : `<button class="primary" data-use="${esc(m.name)}">사용</button>`}
          <button data-show="${esc(m.name)}">클래스 관리</button>
          ${m.active
            ? "" : `<button class="danger" data-del="${esc(m.name)}">삭제</button>`}
        </td>
      </tr>`;
      }).join("")
    : `<tr><td colspan="4" class="muted">올린 모델이 없습니다.</td></tr>`;

  $("model-rows").querySelectorAll("[data-use]").forEach((b) => {
    b.onclick = () => act(`/api/models/${b.dataset.use}/use`, "POST",
                          `${b.dataset.use} 을(를) 공통 모델에 더했습니다. 켜 둔 다른 모델도 그대로 돕니다.`);
  });
  $("model-rows").querySelectorAll("[data-unuse]").forEach((b) => {
    b.onclick = () => act(`/api/models/${b.dataset.unuse}/unuse`, "POST",
                          `${b.dataset.unuse} 만 공통 모델에서 뺐습니다. 모델 파일은 그대로 있습니다.`);
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
  const common = state?.module?.models || [];

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
    const hint = !on || picked.length ? ""
      : common.length
        ? `<div class="why">안 고르면 공통 모델 <b>${esc(common.join(" + "))}</b></div>`
        : `<div class="why">켜 둔 공통 모델이 없어, 여기서 고르지 않으면 이 카메라는 쉽니다</div>`;
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
  if (c.off) {
    return `<span class="pill stop">사용 안 함</span>
      <div class="why">영상도 열지 않습니다</div>`;
  }
  const running = c.effective || [];
  if (!running.length) {
    return `<span class="pill unknown">쉬는 중</span>
      <div class="why">돌릴 모델이 없습니다 — 왼쪽에서 고르거나 '배치된 모델' 에서 공통 모델을 켜세요</div>`;
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
  if (state?.module?.mode === "idle") {
    el.className = "state stop";
    el.innerHTML = `<b>켜 둔 모델이 없습니다</b> — 박스도 이벤트도 나가지 않습니다.
      오른쪽 '배치된 모델'에서 <b>사용</b>을 누르면 시작합니다.`;
    return;
  }
  const inf = state?.inference || {};
  const on = cameras.filter((c) => !c.off);
  const live = on.filter((c) => (inf.cameras || []).includes(c.camera_id)).length;
  const offCount = cameras.length - on.length;
  // 장치는 실제로 도는 카메라들의 것만 모은다. 꺼 둔 카메라의 '사용 안 함' 이 장치
  // 이름에 섞여 들어가면 무엇으로 추론하는지가 안 읽힌다.
  const devs = [...new Set(on.map((c) => (inf.devices || {})[String(c.camera_id)])
                             .filter((d) => d && d !== "사용 안 함" && d !== "모델 없음"))];
  el.className = "state" + (live ? " on" : " stop");
  el.innerHTML = `<b>${live} / ${on.length}대 동작 중</b>`
    + ` · 공통 모델 ${esc((state?.module?.models || []).join(" + ") || "없음")}`
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
  // 켠 클래스만 늘어놓는다. COCO 처럼 80종 중 하나만 쓰는 모델이면 꺼 둔 79줄이 표를
  // 덮어 정작 쓰는 것이 안 보인다. 몇 개를 꺼 뒀는지는 마지막 줄에 적는다.
  const used = model.classes.filter((c) => c.enabled !== false);
  const off = model.classes.length - used.length;
  $("class-rows").innerHTML = (used.length ? used.map((c) => `<tr>
      <td><code>${esc(c.key)}</code></td>
      <td>${c.alias ? esc(c.alias) : `<span class="muted">${esc(c.key)}</span>`}</td>
      <td class="muted">${c.memo ? esc(c.memo) : "-"}</td>
      <td>${c.item
        ? esc(solutionName(c.item))
        : '<span class="muted">연결 안 함 — 박스만</span>'}</td>
    </tr>`).join("")
    : `<tr><td colspan="4" class="muted">켠 클래스가 없습니다 — 이 모델은 아무것도 그리지 않습니다.</td></tr>`)
    + (off ? `<tr><td colspan="4" class="muted">끈 클래스 ${off}개 — 박스도 이벤트도 없습니다.
               '클래스 관리'에서 켤 수 있습니다.</td></tr>` : "");
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

  // 예전에 저장된 표에는 enabled 가 없다 — 없으면 켠 것으로 본다(서버도 같다).
  const on = (c) => c.enabled !== false;
  $("modal-rows").innerHTML = model.classes.length
    ? model.classes.map((c, i) => `<tr${on(c) ? "" : ' class="off"'}>
        <td><input type="checkbox" data-enabled="${i}" ${on(c) ? "checked" : ""} /></td>
        <td><code>${esc(c.key)}</code></td>
        <td><input data-alias="${i}" value="${esc(c.alias)}"
                   placeholder="${esc(c.key)}" /></td>
        <td><input data-memo="${i}" value="${esc(c.memo || "")}"
                   placeholder="무엇을 잡는 클래스인지, 주의할 점은" /></td>
        <td><select data-item="${i}">${options(c.item)}</select></td>
      </tr>`).join("")
    : `<tr><td colspan="5" class="muted">이 모델에서 클래스를 읽지 못했습니다.</td></tr>`;

  $("modal-rows").querySelectorAll("input,select").forEach((el) => {
    el.oninput = () => { dirty = true; };
  });
  $("modal-rows").querySelectorAll("[data-enabled]").forEach((box) => {
    box.onchange = () => {
      dirty = true;
      box.closest("tr").classList.toggle("off", !box.checked);
      countEnabled();
    };
  });
  countEnabled();
  msg("class-msg", "");
  $("class-modal").hidden = false;
  const first = $("modal-rows").querySelector("input[data-alias]");
  if (first) first.focus();
}

function countEnabled() {
  const boxes = [...$("modal-rows").querySelectorAll("[data-enabled]")];
  $("class-count").textContent = boxes.length
    ? `${boxes.filter((b) => b.checked).length} / ${boxes.length}개 사용` : "";
}

function setAllEnabled(value) {
  $("modal-rows").querySelectorAll("[data-enabled]").forEach((box) => {
    box.checked = value;
    box.closest("tr").classList.toggle("off", !value);
  });
  dirty = true;
  countEnabled();
}
$("class-all-on").onclick = () => setAllEnabled(true);
$("class-all-off").onclick = () => setAllEnabled(false);

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
    enabled: !!(document.querySelector(`[data-enabled="${i}"]`) || { checked: true }).checked,
  }));
  if (rows.length && !rows.some((r) => r.enabled)
      && !confirm("모든 클래스를 껐습니다. 이 모델은 아무것도 그리지 않게 됩니다. 저장할까요?")) {
    return;
  }
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

/* ── 이미지 판별률 평가 ────────────────────────────────────────────── */

let evalSets = [];
let evalView = null;       // 지금 보여 주는 결과 { set, run, summary, rows }
let evalFilter = "all";    // all | ng | ok
let evalTimer = null;      // 도는 평가를 지켜보는 주기
let shotList = [];         // 크게 보기에서 앞뒤로 넘길 목록(지금 거른 것)
let shotIndex = -1;

const runUrl = (set, run) => `/api/eval/runs/${encodeURIComponent(set)}/${encodeURIComponent(run)}`;
// 결과 이미지는 원본 이름에서 확장자만 .jpg 로 바뀐다(evaluate.py 가 그렇게 쓴다).
const shotName = (image) => image.replace(/\.[^.]+$/, "") + ".jpg";

async function loadEvalSets() {
  let out;
  try {
    out = await api("/api/eval/sets");
  } catch (e) {
    msg("eval-msg", e.message, "err");
    return;
  }
  evalSets = out.items || [];
  if (out.error) msg("eval-msg", out.error, "err");

  const sel = $("eval-set");
  const keep = sel.value || (out.job && out.job.set) || "";
  sel.innerHTML = evalSets.length
    ? evalSets.map((s) => `<option value="${esc(s.name)}"${s.name === keep ? " selected" : ""}>
        ${esc(s.name)} — 이미지 ${s.images} · 정답 ${s.labels}</option>`).join("")
    : `<option value="">세트가 없습니다 — '세트 올리기' 로 올리세요</option>`;
  renderEvalRuns();

  // 화면을 연 사이 이미 돌고 있던 평가(다른 창, 스크립트 아닌 화면 실행)를 이어서 보여 준다.
  if (out.job && out.job.status === "running" && !evalTimer) watchRun(out.job.set, out.job.run);
}

function renderEvalRuns() {
  const set = evalSets.find((s) => s.name === $("eval-set").value);
  const runs = set ? set.runs : [];
  const sel = $("eval-runs");
  const keep = evalView && set && evalView.set === set.name ? evalView.run : "";
  sel.innerHTML = runs.length
    ? runs.map((r) => `<option value="${esc(r.run)}"${r.run === keep ? " selected" : ""}>
        ${esc(r.run)} · ${r.rate == null ? `판독만 ${r.data}장` : `${r.rate.toFixed(1)} %`}</option>`).join("")
    : `<option value="">아직 없습니다</option>`;
  $("eval-run").disabled = !set || !!evalTimer;
}

$("eval-set").onchange = () => {
  renderEvalRuns();
  // 세트를 바꾸면 그 세트의 가장 최근 결과를 곧바로 보여 준다. 빈 화면을 두면 한 번 더 골라야 한다.
  const set = evalSets.find((s) => s.name === $("eval-set").value);
  if (set && set.runs.length) showRun(set.name, set.runs[0].run);
  else { evalView = null; $("eval-result").innerHTML = ""; }
};
$("eval-runs").onchange = () => {
  if ($("eval-runs").value) showRun($("eval-set").value, $("eval-runs").value);
};

$("eval-run").onclick = async () => {
  const set = $("eval-set").value;
  if (!set) return;
  msg("eval-msg", "시작하는 중…");
  try {
    const out = await api(`/api/eval/sets/${encodeURIComponent(set)}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ predict_only: $("eval-predict-only").checked }),
    });
    watchRun(out.set, out.run);
  } catch (e) {
    msg("eval-msg", e.message, "err");
  }
};

/** 도는 평가를 1초마다 본다. 끝나면 그 결과를 그린다. */
function watchRun(set, run) {
  clearInterval(evalTimer);
  $("eval-run").disabled = true;
  $("eval-progress").hidden = false;
  const tick = async () => {
    let out;
    try {
      out = await api(runUrl(set, run));
    } catch (e) {
      return;                  // 잠깐 못 받은 것 — 다음 주기에 다시 본다
    }
    if (out.status === "running") {
      const j = out.job;
      const pct = j.total ? (j.done / j.total) * 100 : 0;
      $("eval-progress").firstElementChild.style.width = `${pct}%`;
      msg("eval-msg", `${set} 판독 중 — ${j.done} / ${j.total || "?"}장`
        + (j.current ? ` · ${j.current}` : "")
        + (j.ok + j.ng ? ` · 일치 ${j.ok} · 불일치 ${j.ng}` : ""));
      return;
    }
    clearInterval(evalTimer);
    evalTimer = null;
    $("eval-progress").hidden = true;
    if (out.status === "error") {
      msg("eval-msg", `평가를 멈췄습니다: ${out.job.error}`, "err");
    } else {
      msg("eval-msg", `${set} 평가를 마쳤습니다 (${out.summary.seconds}초).`, "ok");
      $("eval-set").value = set;
      evalView = { set, run, ...out };
      evalFilter = "all";
      renderEvalResult();
    }
    await loadEvalSets();
  };
  tick();
  evalTimer = setInterval(tick, 1000);
}

async function showRun(set, run) {
  try {
    const out = await api(runUrl(set, run));
    if (out.status !== "done") { watchRun(set, run); return; }
    evalView = { set, run, ...out };
    evalFilter = "all";
    renderEvalResult();
  } catch (e) {
    msg("eval-msg", e.message, "err");
  }
}

function renderEvalResult() {
  const v = evalView;
  if (!v) { $("eval-result").innerHTML = ""; return; }
  const s = v.summary;
  const graded = s.rate != null;
  const ng = v.rows.filter((r) => r.verdict === "불일치").length;
  const ok = v.rows.filter((r) => r.verdict === "일치").length;

  const rate = graded
    ? `<div class="rate">
        <span class="big">${s.rate.toFixed(1)} %</span>
        <span class="pill ${s.pass ? "on" : "stop"}">${s.pass ? "적합" : "부적합"}</span>
        <span class="formula">이미지 판별률 = 일치 <b>${s.matched}</b> / 데이터 <b>${s.data}</b> × 100
          &nbsp;·&nbsp; 기준 ${s.criterion} % 이상</span>
      </div>`
    : `<div class="rate"><span class="formula">판독만 했습니다 — 데이터 <b>${s.data}</b>장.
        정답이 없어 판별률은 없습니다.</span></div>`;

  const meta = [
    ["세트", `${s.set} / ${s.run}`],
    ["모델", `${s.models.map((n) => (s.imgsz && s.imgsz[n] ? `${n} 입력 ${s.imgsz[n]}` : n))
      .join(" + ")} (${s.device})`],
    ["최소 점수", s.thresholds.conf],
    ["일치 IoU", s.thresholds.match_iou],
    ["소요", `${s.seconds}초`],
  ].map(([k, val]) => `<span>${k} <b>${esc(val)}</b></span>`).join("")
    + (s.ignored_detections ? `<span>평가 대상 밖 탐지 <b>${s.ignored_detections}</b>개(버림)</span>` : "");

  const warn = s.class_names_assumed
    ? `<div class="warn">classes.txt 가 없어 정답 클래스를 <b>0=AMR, 1=Human</b> 으로 가정했습니다.
        라벨링한 순서가 다르면 숫자가 틀립니다 — 세트 폴더에 classes.txt 를 넣으세요.</div>` : "";

  const perClass = s.per_class
    ? `<table class="per-class"><thead><tr><th>클래스</th><th>맞춤</th><th>놓침</th><th>헛탐지</th></tr></thead>
        <tbody>${Object.entries(s.per_class).map(([c, x]) => `<tr><td><b>${esc(c)}</b></td>
          <td class="num">${x.tp}</td><td class="num">${x.fn}</td><td class="num">${x.fp}</td></tr>`).join("")}
        </tbody></table>` : "";

  const base = runUrl(v.set, v.run);
  const filters = `<div class="filters">
      ${graded ? [["all", `전체 ${v.rows.length}`], ["ng", `불일치 ${ng}`], ["ok", `일치 ${ok}`]]
        .map(([k, t]) => `<button data-filter="${k}"${evalFilter === k ? ' class="on"' : ""}>${t}</button>`).join("")
        : `<span class="muted">${v.rows.length}장</span>`}
      <div class="grow" style="flex:1"></div>
      <a href="${base}/results.csv" download><button>결과표 CSV</button></a>
      <a href="${base}/annotated.zip" download><button>결과 묶음 ZIP</button></a>
    </div>`;

  shotList = v.rows.filter((r) => evalFilter === "all"
    || (evalFilter === "ng" ? r.verdict === "불일치" : r.verdict === "일치"));
  const shots = shotList.length
    ? `<div class="shots">${shotList.map((r, i) => `<div class="shot" data-shot="${i}">
        <img loading="lazy" src="${base}/image/${encodeURIComponent(shotName(r.image))}?w=440" alt="" />
        <div class="cap">
          ${r.verdict === "-" ? "" : `<span class="pill ${r.verdict === "일치" ? "on" : "stop"}">${esc(r.verdict)}</span>`}
          <span class="name" title="${esc(r.image)}">${esc(r.image)}</span>
          <span class="n">${shotCounts(r)}</span>
        </div></div>`).join("")}</div>`
    : `<p class="muted">해당하는 이미지가 없습니다.</p>`;

  $("eval-result").innerHTML = rate + `<div class="eval-meta">${meta}</div>` + warn + perClass + filters + shots;
  $("eval-result").querySelectorAll("[data-filter]").forEach((b) => {
    b.onclick = () => { evalFilter = b.dataset.filter; renderEvalResult(); };
  });
  $("eval-result").querySelectorAll("[data-shot]").forEach((el) => {
    el.onclick = () => openShot(Number(el.dataset.shot));
  });
  renderEvalRuns();
}

/** 격자 칸 아래의 짧은 숫자. 불일치면 무엇이 틀렸는지, 판독만이면 무엇을 잡았는지. */
function shotCounts(r) {
  if (r.verdict === "-") return `AMR ${r.det_AMR} · Human ${r.det_Human}`;
  if (r.verdict === "일치") return `정답 ${Number(r.gt_AMR) + Number(r.gt_Human)}`;
  return [Number(r.missed) ? `놓침 ${r.missed}` : "", Number(r.false_pos) ? `헛탐지 ${r.false_pos}` : ""]
    .filter(Boolean).join(" · ");
}

function openShot(i) {
  if (!shotList.length) return;
  shotIndex = (i + shotList.length) % shotList.length;
  const r = shotList[shotIndex];
  $("shot-title").textContent = `${r.image}  (${shotIndex + 1} / ${shotList.length})`;
  $("shot-img").src = `${runUrl(evalView.set, evalView.run)}/image/${encodeURIComponent(shotName(r.image))}`;
  $("shot-detail").textContent = r.verdict === "-"
    ? `탐지: ${r.detections || "없음"}`
    : `${r.verdict} · 정답 AMR ${r.gt_AMR} · Human ${r.gt_Human} · 탐지 AMR ${r.det_AMR} · Human ${r.det_Human}`
      + ` · 맞춤 ${r.matched} · 놓침 ${r.missed} · 헛탐지 ${r.false_pos}`
      + (r.detections ? `  —  ${r.detections}` : "");
  $("shot-modal").hidden = false;
}
const closeShot = () => { $("shot-modal").hidden = true; $("shot-img").src = ""; };
$("shot-close").onclick = closeShot;
$("shot-prev").onclick = () => openShot(shotIndex - 1);
$("shot-next").onclick = () => openShot(shotIndex + 1);
$("shot-modal").onclick = (e) => { if (e.target === $("shot-modal")) closeShot(); };
document.addEventListener("keydown", (e) => {
  if ($("shot-modal").hidden) return;
  if (e.key === "Escape") closeShot();
  else if (e.key === "ArrowLeft") openShot(shotIndex - 1);
  else if (e.key === "ArrowRight") openShot(shotIndex + 1);
});

/* 세트 올리기 — 폴더째 고른다. 파일마다 폴더 안의 상대 경로를 같이 보낸다
   (images/a.jpg, labels/a.txt). 서버가 그 모양 그대로 세트 폴더에 둔다. */
const EVAL_FILE = /\.(jpe?g|png|bmp|txt|names)$/i;

$("eval-upload-toggle").onclick = () => { $("eval-upload").hidden = !$("eval-upload").hidden; };
$("eval-folder").onchange = () => {
  const first = $("eval-folder").files[0];
  const top = first && (first.webkitRelativePath || "").split("/")[0];
  // 폴더 이름이 쓸 만하면 세트 이름으로 미리 채운다. 한글 폴더면 비워 두고 사람이 짓게 한다.
  if (top && !$("eval-name").value && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(top)) {
    $("eval-name").value = top;
  }
};
$("eval-upload-btn").onclick = async () => {
  // 이미지·정답·클래스 이름만 보낸다. 폴더에 영상이나 지난 결과가 섞여 있어도 그것까지 올리지 않는다.
  const files = [...$("eval-folder").files].filter((f) =>
    EVAL_FILE.test(f.name) && !/(^|\/)results\//.test(f.webkitRelativePath || ""));
  const name = $("eval-name").value.trim();
  if (!files.length) { msg("eval-msg", "이미지가 든 폴더를 고르세요.", "err"); return; }
  if (!name) { msg("eval-msg", "세트 이름을 적으세요.", "err"); return; }
  const form = new FormData();
  form.append("name", name);
  files.forEach((f) => {
    form.append("files", f, f.name);
    form.append("paths", f.webkitRelativePath || f.name);
  });
  const mb = files.reduce((n, f) => n + f.size, 0) / 1024 / 1024;
  $("eval-upload-btn").disabled = true;
  msg("eval-msg", `올리는 중… (파일 ${files.length}개 · ${mb.toFixed(1)}MB)`);
  try {
    const out = await api("/api/eval/sets", { method: "POST", body: form });
    msg("eval-msg", `세트 ${out.name} 를 올렸습니다 — 이미지 ${out.images}장 · 정답 ${out.labels}개`
      + (out.labels && out.labels !== out.images ? " (정답이 빠진 이미지가 있어 이대로는 평가가 멈춥니다)" : "")
      + (out.labels ? "" : " (정답이 없어 판독만 합니다)"), out.labels && out.labels !== out.images ? "err" : "ok");
    $("eval-folder").value = "";
    $("eval-name").value = "";
    $("eval-upload").hidden = true;
    $("eval-set").value = out.name;
    await loadEvalSets();
    $("eval-set").value = out.name;
    $("eval-set").onchange();
  } catch (e) {
    msg("eval-msg", e.message, "err");
  } finally {
    $("eval-upload-btn").disabled = false;
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
  // 평가 카드는 나머지와 무관하다. 고른 세트의 가장 최근 결과를 곧바로 띄운다.
  loadEvalSets().then(() => { if (!evalTimer) $("eval-set").onchange(); });
  // 탐지 항목이 먼저 있어야 클래스 표의 드롭다운이 첫 그림에서 채워진다.
  await loadSolutions();
  await Promise.all([refresh(), camerasReady]);

  setInterval(refresh, 3000);
  // 탐지 항목은 플랫폼에서 가끔 늘어난다. 자주 볼 필요는 없다.
  setInterval(loadSolutions, 30000);
  // 담당 카메라는 플랫폼에서 바뀐다(20초 주기로 반영된다). 그보다 자주 볼 필요는 없다.
  setInterval(loadCameras, 15000);
  // 세트·결과 목록. 스크립트로 돌린 결과도 여기 나타나게 가끔 다시 읽는다.
  setInterval(() => { if (!evalTimer) loadEvalSets(); }, 30000);
})();
