/* 이미지 판별률 평가 화면. 서버는 eval_api.py, 판독 코어는 evaluate.py.
 *
 * 객체감지 플러그인 화면에 있던 것을 옮겼다. label.js 와 한 페이지에 실리므로 이름이
 * 부딪히지 않게 통째로 함수 안에 넣는다.
 */
(() => {
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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

// 평가 탭을 처음 열 때 읽는다. 라벨링만 하는 동안 세트 폴더를 뒤질 이유가 없다.
let started = false;
window.evalTabShown = () => {
  if (!started) {
    started = true;
    loadEvalSets().then(() => { if (!evalTimer) $("eval-set").onchange(); });
    // 세트·결과 목록. 스크립트로 돌린 결과나 라벨링에서 만든 세트도 나타나게 가끔 다시 읽는다.
    setInterval(() => { if (!evalTimer && !$("tab-eval").hidden) loadEvalSets(); }, 30000);
  } else if (!evalTimer) {
    loadEvalSets();
  }
};
})();
