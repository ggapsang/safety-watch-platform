/* 데이터셋·라벨링 탭의 동작. 서버는 dataset_api.py. (학습·평가 플러그인 화면 — index.html)
 *
 * 박스는 늘 0~1 정규화 좌표로 들고 있다(YOLO 라벨과 같은 단위). 화면 좌표는 그릴 때와
 * 마우스를 읽을 때만 계산한다 — 확대·이동을 몇 번 해도 박스가 밀리지 않는다.
 *
 * 저장은 다른 이미지로 넘어갈 때 저절로 된다. '저장' 을 따로 누르게 하면 백 장 중
 * 몇 장은 반드시 빠뜨린다.
 */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없는 응답 */ }
  if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
  return body;
}
const putJSON = (path, data) => api(path, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const postJSON = (path, data) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

let msgTimer = 0;
function msg(text, kind, sticky) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
  clearTimeout(msgTimer);
  if (text && !sticky && kind !== "err") msgTimer = setTimeout(() => msg(""), 5000);
}

/* 대시보드 박스 색과 같은 규칙(web/src/lib/boxcolor.ts, evaluate.box_color). */
const PALETTE = ["#e8703a", "#4a9eda", "#5db872", "#d4a017", "#a978d8", "#3fb8ae", "#e0679b", "#8a9a5b"];
function colorOf(name) {
  let h = 5381;
  for (let i = 0; i < name.length; i++) h = ((h << 5) + h + name.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/* ── 상태 ─────────────────────────────────────────────────────────── */

const S = {
  ds: "",              // 데이터셋 이름
  classes: [],
  items: [],           // [{name, boxes, status}]
  filter: "all",
  idx: -1,             // items 안의 지금 이미지
  img: null,           // HTMLImageElement
  boxes: [],           // [{cls, x1, y1, x2, y2, score?}]
  status: "",
  sel: -1,
  cur: 0,              // 새 박스의 클래스
  dirty: false,
  undo: [], redo: [],
  hide: false,
  view: { s: 1, ox: 0, oy: 0 },
  loadSeq: 0,
};

const url = {
  img: (n, w) => `/api/datasets/${enc(S.ds)}/image/${enc(n)}` + (w ? `?w=${w}` : ""),
  label: (n) => `/api/datasets/${enc(S.ds)}/label/${enc(n)}`,
};

function matches(it) {
  switch (S.filter) {
    case "todo": return !it.status;
    case "ok": return it.status === "ok";
    case "skip": return it.status === "skip";
    case "nolabel": return it.boxes == null;
    default: return true;
  }
}

/* ── 데이터셋 ─────────────────────────────────────────────────────── */

async function loadDatasets(pick) {
  const r = await api("/api/datasets");
  const sel = $("ds-select");
  if (r.error) msg(r.error, "err");
  sel.innerHTML = r.items.length
    ? r.items.map((d) => `<option value="${esc(d.name)}">${esc(d.name)} (${d.images}장)</option>`).join("")
    : `<option value="">데이터셋 없음 — '새 데이터셋' 으로 만드세요</option>`;
  const saved = pick || localGet("label.ds");
  if (saved && r.items.some((d) => d.name === saved)) sel.value = saved;
  if (sel.value) await openDataset(sel.value);
  else renderAll();
}

async function openDataset(name, keepImage) {
  await saveIfDirty();
  const prevName = keepImage ? S.items[S.idx]?.name : localGet(`label.img.${name}`);
  const r = await api(`/api/datasets/${enc(name)}`);
  S.ds = name; S.classes = r.classes; S.items = r.items;
  $("ds-select").value = name;
  localSet("label.ds", name);
  if (S.cur >= S.classes.length) S.cur = 0;
  renderClasses(); renderList(); renderCounts(); updateLinks();
  let i = S.items.findIndex((it) => it.name === prevName);
  if (i < 0) i = S.items.findIndex(matches);
  if (i >= 0) await openImage(i, true);
  else { S.idx = -1; S.img = null; S.boxes = []; renderAll(); }
}

function renderCounts() {
  const n = S.items.length;
  const ok = S.items.filter((i) => i.status === "ok").length;
  const skip = S.items.filter((i) => i.status === "skip").length;
  const labeled = S.items.filter((i) => i.boxes != null).length;
  $("counts").innerHTML = `전체 <b>${n}</b> · 사용 <b style="color:#2f7a44">${ok}</b> · 제외 <b>${skip}</b>`
    + ` · 미검토 <b>${n - ok - skip}</b><br>라벨 있음 <b>${labeled}</b> · 없음 <b>${n - labeled}</b>`;
  $("to-eval").textContent = `'사용' ${ok}장으로 평가 세트 만들기`;
}

function rowHTML(it, i) {
  const st = it.status === "ok" ? "✓" : it.status === "skip" ? "✕" : "";
  const n = it.boxes == null ? "라벨 없음" : `박스 ${it.boxes}`;
  return `<div class="item${i === S.idx ? " cur" : ""}" data-i="${i}">`
    + `<img loading="lazy" src="${url.img(it.name, 160)}" alt="" />`
    + `<div class="t"><span class="n">#${i + 1}</span> <span class="muted">${n}</span>`
    + `<span class="f" title="${esc(it.name)}">${esc(it.name)}</span></div>`
    + `<span class="st ${it.status}">${st}</span></div>`;
}

function renderList() {
  const rows = [];
  S.items.forEach((it, i) => { if (matches(it) || i === S.idx) rows.push(rowHTML(it, i)); });
  $("list").innerHTML = rows.join("") || `<div class="empty">${S.ds ? "해당하는 이미지가 없습니다." : ""}</div>`;
  scrollToCurrent();
}

function renderRow(i) {
  const el = $("list").querySelector(`.item[data-i="${i}"]`);
  if (el) el.outerHTML = rowHTML(S.items[i], i);
}

function scrollToCurrent() {
  const el = $("list").querySelector(".item.cur");
  if (el) el.scrollIntoView({ block: "nearest" });
}

/* ── 이미지 열기·저장 ─────────────────────────────────────────────── */

async function openImage(i, force) {
  if (i < 0 || i >= S.items.length || (i === S.idx && !force)) return;
  await saveIfDirty();
  const seq = ++S.loadSeq;
  const it = S.items[i];
  const prev = S.idx;
  S.idx = i;
  if (prev >= 0 && prev < S.items.length) renderRow(prev);
  renderRow(i);
  if (!$("list").querySelector(`.item[data-i="${i}"]`)) renderList();
  scrollToCurrent();
  localSet(`label.img.${S.ds}`, it.name);
  $("sb-name").textContent = it.name;
  $("sb-pos").textContent = `${i + 1} / ${S.items.length}`;
  try {
    const [lab, img] = await Promise.all([api(url.label(it.name)), loadImg(url.img(it.name))]);
    if (seq !== S.loadSeq) return;                 // 그사이 다른 이미지로 넘어감
    S.img = img; S.boxes = lab.boxes; S.status = lab.status;
    S.sel = -1; S.dirty = false; S.undo = []; S.redo = [];
    fit();
    renderAll();
    preload(i);
  } catch (e) {
    if (seq === S.loadSeq) msg(`이미지를 열지 못했습니다: ${e.message}`, "err");
  }
}

function loadImg(src) {
  return new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => fail(new Error("이미지 읽기 실패"));
    im.src = src;
  });
}

function preload(i) {
  for (const j of [i + 1, i - 1]) {
    if (j >= 0 && j < S.items.length) new Image().src = url.img(S.items[j].name);
  }
}

async function save(status) {
  if (S.idx < 0) return;
  const it = S.items[S.idx];
  const body = { boxes: S.boxes.map(({ cls, x1, y1, x2, y2 }) => ({ cls, x1, y1, x2, y2 })) };
  if (status !== undefined) body.status = status;
  const r = await putJSON(url.label(it.name), body);
  it.boxes = S.boxes.length; it.status = r.status;
  if (S.items[S.idx] === it) { S.status = r.status; S.dirty = false; }
  renderRow(S.items.indexOf(it)); renderCounts(); renderSaveState(); renderReview();
}

async function saveIfDirty() {
  if (!S.dirty || S.idx < 0) return;
  try { await save(); }
  catch (e) { msg(`저장하지 못했습니다: ${e.message}`, "err"); throw e; }
}

function step(dir) {
  // 거르기와 맞는 다음 이미지. 방금 '사용' 으로 바꿔 거르기에서 빠진 이미지에서도 앞뒤를 안다.
  for (let j = S.idx + dir; j >= 0 && j < S.items.length; j += dir) {
    if (matches(S.items[j])) return openImage(j);
  }
  msg(dir > 0 ? "마지막 이미지입니다" : "첫 이미지입니다");
}

let marking = false;     // Enter 를 누르고 있으면 키 반복으로 여러 장이 한꺼번에 넘어간다
async function mark(status, advance) {
  if (S.idx < 0 || marking) return;
  marking = true;
  try {
    await save(status);
    if (advance) await step(1);
    if (S.filter !== "all") renderList();
  } catch (e) { msg(`저장하지 못했습니다: ${e.message}`, "err"); }
  finally { marking = false; }
}

/* ── 편집 ─────────────────────────────────────────────────────────── */

const snap = () => JSON.stringify(S.boxes);
function pushUndo(before) {
  S.undo.push(before ?? snap());
  if (S.undo.length > 200) S.undo.shift();
  S.redo = [];
  S.dirty = true;
}
function undo() {
  if (!S.undo.length) return;
  S.redo.push(snap()); S.boxes = JSON.parse(S.undo.pop()); S.sel = -1; S.dirty = true; renderAll();
}
function redoStep() {
  if (!S.redo.length) return;
  S.undo.push(snap()); S.boxes = JSON.parse(S.redo.pop()); S.sel = -1; S.dirty = true; renderAll();
}

function setClass(c) {
  if (c < 0 || c >= S.classes.length) return;
  S.cur = c;
  if (S.sel >= 0 && S.boxes[S.sel].cls !== c) {
    pushUndo(); S.boxes[S.sel].cls = c; delete S.boxes[S.sel].score;
  }
  renderAll();
}

function deleteSel() {
  if (S.sel < 0) return;
  pushUndo(); S.boxes.splice(S.sel, 1); S.sel = -1; renderAll();
}

function iou(a, b) {
  const w = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1));
  const h = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1));
  const i = w * h;
  const u = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - i;
  return u > 0 ? i / u : 0;
}

async function draft() {
  if (S.idx < 0) return;
  const name = S.items[S.idx].name;
  msg("모델로 판독하는 중… (처음엔 모델을 여느라 몇 초 걸립니다)", "busy", true);
  try {
    const r = await postJSON(`/api/datasets/${enc(S.ds)}/draft/${enc(name)}`,
      { min_score: Number($("draft-score").value) });
    if (S.items[S.idx]?.name !== name) return;
    const add = r.boxes.filter((p) => !S.boxes.some((b) => b.cls === p.cls && iou(b, p) > 0.5));
    if (add.length) { pushUndo(); S.boxes.push(...add); }
    renderAll();
    msg(`초안 ${r.boxes.length}개 중 ${add.length}개 더함`
      + (r.boxes.length - add.length ? ` (이미 있는 박스와 겹친 ${r.boxes.length - add.length}개 뺌)` : "")
      + ` — ${r.models.join(", ")}`, "ok");
  } catch (e) { msg(`판독하지 못했습니다: ${e.message}`, "err"); }
}

async function copyPrev() {
  if (S.idx <= 0) return;
  try {
    const lab = await api(url.label(S.items[S.idx - 1].name));
    if (!lab.boxes.length) { msg("앞 이미지에 박스가 없습니다"); return; }
    pushUndo(); S.boxes = lab.boxes; S.sel = -1; renderAll();
    msg(`앞 이미지의 박스 ${lab.boxes.length}개를 가져왔습니다`, "ok");
  } catch (e) { msg(e.message, "err"); }
}

/* ── 캔버스 ───────────────────────────────────────────────────────── */

const cv = $("canvas");
const ctx = cv.getContext("2d");
let cw = 0, ch = 0, raf = 0;

function resize() {
  const r = $("canvas-wrap").getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const keep = S.img && cw ? { cx: (cw / 2 - S.view.ox) / S.view.s, cy: (ch / 2 - S.view.oy) / S.view.s } : null;
  cw = r.width; ch = r.height;
  cv.width = Math.round(cw * dpr); cv.height = Math.round(ch * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (keep) { S.view.ox = cw / 2 - keep.cx * S.view.s; S.view.oy = ch / 2 - keep.cy * S.view.s; }
  else if (S.img && cw) fit();       // 숨긴 탭에서 열린 이미지 — 보이는 순간 화면에 맞춘다
  draw();
}
new ResizeObserver(resize).observe($("canvas-wrap"));

function fit() {
  if (!S.img) return;
  const iw = S.img.naturalWidth, ih = S.img.naturalHeight;
  const s = Math.min(cw / iw, ch / ih) * 0.98;
  S.view = { s, ox: (cw - iw * s) / 2, oy: (ch - ih * s) / 2 };
  draw();
}

// 이미지 픽셀 ↔ 화면(CSS px)
const W = () => S.img.naturalWidth, H = () => S.img.naturalHeight;
const toScreen = (nx, ny) => [S.view.ox + nx * W() * S.view.s, S.view.oy + ny * H() * S.view.s];
const toNorm = (sx, sy) => [(sx - S.view.ox) / (W() * S.view.s), (sy - S.view.oy) / (H() * S.view.s)];
const clip = (v) => Math.min(1, Math.max(0, v));

function draw() {
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(paint);
}

function paint() {
  ctx.clearRect(0, 0, cw, ch);
  $("stage-empty").hidden = !!S.img;
  if (!S.img) return;
  const { s, ox, oy } = S.view;
  ctx.imageSmoothingEnabled = s < 3;
  ctx.drawImage(S.img, ox, oy, W() * s, H() * s);
  if (!S.hide) {
    S.boxes.forEach((b, i) => { if (i !== S.sel) paintBox(b, false); });
    if (S.sel >= 0 && S.boxes[S.sel]) paintBox(S.boxes[S.sel], true);
  }
  if (drag && drag.kind === "new" && drag.box) paintBox(drag.box, true, true);
  // 그리기 안내선 — 빈 곳 위에서만. 작은 AMR 의 모서리를 맞추기 쉽다.
  if (hover && !drag && !hoverHit && !panKey) {
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,.45)"; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hover.x + .5, 0); ctx.lineTo(hover.x + .5, ch);
    ctx.moveTo(0, hover.y + .5); ctx.lineTo(cw, hover.y + .5);
    ctx.stroke(); ctx.restore();
  }
  $("sb-zoom").textContent = `${Math.round(s * 100)}%`;
  $("sb-size").textContent = `${W()}×${H()}`;
}

function paintBox(b, selected, drawing) {
  const name = S.classes[b.cls] ?? `#${b.cls}`;
  const color = colorOf(name);
  const [x1, y1] = toScreen(Math.min(b.x1, b.x2), Math.min(b.y1, b.y2));
  const [x2, y2] = toScreen(Math.max(b.x1, b.x2), Math.max(b.y1, b.y2));
  ctx.save();
  ctx.lineWidth = selected ? 2.5 : 2;
  ctx.strokeStyle = color;
  if (b.score != null) ctx.setLineDash([7, 4]);       // 모델 초안 — 사람이 아직 손대지 않은 것
  if (selected) { ctx.fillStyle = color + "26"; ctx.fillRect(x1, y1, x2 - x1, y2 - y1); }
  ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  ctx.setLineDash([]);
  if (!drawing) {
    const text = b.score != null ? `${name} ${b.score.toFixed(2)}` : name;
    ctx.font = "600 12px Pretendard, 'Segoe UI', 'Malgun Gothic', sans-serif";
    const tw = ctx.measureText(text).width + 8;
    const ty = y1 >= 17 ? y1 - 17 : y1;
    ctx.fillStyle = color; ctx.fillRect(x1 - 1, ty, tw, 17);
    ctx.fillStyle = "#fff"; ctx.fillText(text, x1 + 3, ty + 12.5);
  }
  if (selected && !drawing) {
    ctx.fillStyle = "#fff"; ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    for (const [hx, hy] of handlePoints(x1, y1, x2, y2)) {
      ctx.fillRect(hx - 4, hy - 4, 8, 8); ctx.strokeRect(hx - 4, hy - 4, 8, 8);
    }
  }
  ctx.restore();
}

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
function handlePoints(x1, y1, x2, y2) {
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  return [[x1, y1], [mx, y1], [x2, y1], [x2, my], [x2, y2], [mx, y2], [x1, y2], [x1, my]];
}

/* 마우스 아래 무엇이 있나: 고른 박스의 손잡이 → 박스 안(가장 작은 것) → 없음 */
function hitTest(sx, sy) {
  if (S.hide) return null;
  if (S.sel >= 0) {
    const b = S.boxes[S.sel];
    const [x1, y1] = toScreen(b.x1, b.y1), [x2, y2] = toScreen(b.x2, b.y2);
    const pts = handlePoints(x1, y1, x2, y2);
    for (let k = 0; k < pts.length; k++) {
      if (Math.abs(sx - pts[k][0]) <= 7 && Math.abs(sy - pts[k][1]) <= 7) return { i: S.sel, h: HANDLES[k] };
    }
  }
  let best = null, area = Infinity;
  S.boxes.forEach((b, i) => {
    const [x1, y1] = toScreen(b.x1, b.y1), [x2, y2] = toScreen(b.x2, b.y2);
    if (sx >= x1 - 3 && sx <= x2 + 3 && sy >= y1 - 3 && sy <= y2 + 3) {
      const a = (x2 - x1) * (y2 - y1);
      if (a < area) { area = a; best = { i, h: null }; }
    }
  });
  return best;
}

const CURSORS = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
                  n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };
let drag = null, hover = null, hoverHit = null, panKey = false;

function pos(e) {
  const r = cv.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

cv.addEventListener("contextmenu", (e) => e.preventDefault());

cv.addEventListener("mousedown", (e) => {
  if (!S.img) return;
  const p = pos(e);
  if (e.button === 1 || e.button === 2 || (e.button === 0 && panKey)) {
    drag = { kind: "pan", sx: p.x, sy: p.y, ox: S.view.ox, oy: S.view.oy };
    cv.style.cursor = "grabbing";
    e.preventDefault();
    return;
  }
  if (e.button !== 0) return;
  const hit = hitTest(p.x, p.y);
  const [nx, ny] = toNorm(p.x, p.y);
  if (hit) {
    S.sel = hit.i;
    const b = S.boxes[hit.i];
    S.cur = b.cls;
    drag = { kind: hit.h ? "resize" : "move", h: hit.h, before: snap(), nx, ny, b0: { ...b } };
  } else {
    S.sel = -1;
    drag = { kind: "new", nx: clip(nx), ny: clip(ny), box: null };
  }
  renderAll();
});

window.addEventListener("mousemove", (e) => {
  if (!S.img) return;
  const p = pos(e);
  const inside = p.x >= 0 && p.y >= 0 && p.x <= cw && p.y <= ch;
  hover = inside ? p : null;
  const [nx, ny] = toNorm(p.x, p.y);
  if (inside) $("sb-cursor").textContent = `x ${Math.round(clip(nx) * W())}, y ${Math.round(clip(ny) * H())}`;
  if (!drag) {
    hoverHit = inside ? hitTest(p.x, p.y) : null;
    cv.style.cursor = panKey ? "grab" : hoverHit ? (hoverHit.h ? CURSORS[hoverHit.h] : "move") : "crosshair";
    if (inside) draw();
    return;
  }
  if (drag.kind === "pan") {
    S.view.ox = drag.ox + (p.x - drag.sx); S.view.oy = drag.oy + (p.y - drag.sy);
  } else if (drag.kind === "new") {
    drag.box = { cls: S.cur, x1: drag.nx, y1: drag.ny, x2: clip(nx), y2: clip(ny) };
  } else if (drag.kind === "move") {
    const b0 = drag.b0, w = b0.x2 - b0.x1, h = b0.y2 - b0.y1;
    const x1 = Math.min(1 - w, Math.max(0, b0.x1 + nx - drag.nx));
    const y1 = Math.min(1 - h, Math.max(0, b0.y1 + ny - drag.ny));
    Object.assign(S.boxes[S.sel], { x1, y1, x2: x1 + w, y2: y1 + h });
  } else if (drag.kind === "resize") {
    const b = S.boxes[S.sel], h = drag.h;
    if (h.includes("w")) b.x1 = clip(nx);
    if (h.includes("e")) b.x2 = clip(nx);
    if (h.includes("n")) b.y1 = clip(ny);
    if (h.includes("s")) b.y2 = clip(ny);
  }
  draw();
});

window.addEventListener("mouseup", () => {
  if (!drag) return;
  const d = drag;
  drag = null;
  cv.style.cursor = panKey ? "grab" : "crosshair";
  if (d.kind === "new") {
    const b = d.box;
    if (b) {
      const [ax, ay] = toScreen(b.x1, b.y1), [bx, by] = toScreen(b.x2, b.y2);
      if (Math.abs(bx - ax) >= 4 && Math.abs(by - ay) >= 4) {
        pushUndo();
        S.boxes.push(normBox(b));
        S.sel = S.boxes.length - 1;
      }
    }
  } else if (d.kind === "move" || d.kind === "resize") {
    const b = S.boxes[S.sel];
    Object.assign(b, normBox(b));
    if (snap() !== d.before) { delete b.score; pushUndo(d.before); }
  }
  renderAll();
});

function normBox(b) {
  return { cls: b.cls, x1: Math.min(b.x1, b.x2), y1: Math.min(b.y1, b.y2),
           x2: Math.max(b.x1, b.x2), y2: Math.max(b.y1, b.y2) };
}

cv.addEventListener("wheel", (e) => {
  if (!S.img) return;
  e.preventDefault();
  const p = pos(e);
  const fitS = Math.min(cw / W(), ch / H());
  const s = Math.min(fitS * 40, Math.max(fitS * 0.5, S.view.s * Math.pow(1.0015, -e.deltaY)));
  S.view.ox = p.x - (p.x - S.view.ox) * (s / S.view.s);
  S.view.oy = p.y - (p.y - S.view.oy) * (s / S.view.s);
  S.view.s = s;
  draw();
}, { passive: false });

cv.addEventListener("mouseleave", () => { hover = null; draw(); });

/* ── 키보드 ───────────────────────────────────────────────────────── */

window.addEventListener("keydown", (e) => {
  // 평가 탭을 보는 동안에는 라벨링 단축키(Enter·X·A·D …)가 숨은 편집기를 건드리지 않게.
  if (document.getElementById("tab-label")?.hidden) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  const k = e.key.toLowerCase();
  if (e.ctrlKey || e.metaKey) {
    if (k === "z" && !e.shiftKey) { undo(); e.preventDefault(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { redoStep(); e.preventDefault(); }
    else if (k === "s") { e.preventDefault(); save().then(() => msg("저장했습니다", "ok"), (er) => msg(er.message, "err")); }
    return;
  }
  if (e.key === " ") { if (!panKey) { panKey = true; cv.style.cursor = "grab"; draw(); } e.preventDefault(); return; }
  if (/^[1-9]$/.test(e.key)) { setClass(Number(e.key) - 1); return; }
  if (e.repeat && (k === "enter" || k === "x" || k === "r")) { e.preventDefault(); return; }
  switch (k) {
    case "delete": case "backspace": deleteSel(); break;
    case "enter": mark("ok", true); break;
    case "x": mark("skip", true); break;
    case "a": case "arrowleft": step(-1); break;
    case "d": case "arrowright": step(1); break;
    case "r": draft(); break;
    case "v": copyPrev(); break;
    case "h": S.hide = !S.hide; draw(); break;
    case "f": fit(); break;
    case "escape": S.sel = -1; drag = null; renderAll(); break;
    case "tab":
      if (S.boxes.length) { S.sel = (S.sel + (e.shiftKey ? -1 + S.boxes.length : 1)) % S.boxes.length; S.cur = S.boxes[S.sel].cls; renderAll(); }
      break;
    default: return;
  }
  e.preventDefault();
});
window.addEventListener("keyup", (e) => {
  if (e.key === " ") { panKey = false; cv.style.cursor = "crosshair"; draw(); }
});

window.addEventListener("beforeunload", () => {
  // 탭을 닫거나 설정 화면으로 돌아갈 때 손댄 것을 잃지 않게.
  if (!S.dirty || S.idx < 0) return;
  fetch(url.label(S.items[S.idx].name), {
    method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ boxes: S.boxes.map(({ cls, x1, y1, x2, y2 }) => ({ cls, x1, y1, x2, y2 })) }),
  });
});

/* ── 오른쪽 패널 ──────────────────────────────────────────────────── */

function renderAll() {
  renderClasses(); renderBoxes(); renderReview(); renderSaveState(); draw();
}

function renderClasses() {
  const counts = S.classes.map((_, c) => S.boxes.filter((b) => b.cls === c).length);
  $("classes").innerHTML = S.classes.map((name, c) =>
    `<div class="cls${c === S.cur ? " on" : ""}" data-c="${c}">`
    + `<kbd>${c < 9 ? c + 1 : ""}</kbd><span class="sw" style="background:${colorOf(name)}"></span>`
    + `<span class="nm" title="두 번 눌러 이름 고치기">${esc(name)}</span><span class="cnt">${counts[c] || ""}</span></div>`,
  ).join("") || `<div class="empty" style="padding:4px 0">클래스가 없습니다</div>`;
}

function renderBoxes() {
  $("box-count").textContent = S.boxes.length ? `${S.boxes.length}개` : "";
  $("boxes").innerHTML = S.boxes.map((b, i) => {
    const name = S.classes[b.cls] ?? `#${b.cls}`;
    const w = S.img ? Math.round((b.x2 - b.x1) * W()) : 0, h = S.img ? Math.round((b.y2 - b.y1) * H()) : 0;
    return `<div class="bx${i === S.sel ? " on" : ""}" data-i="${i}">`
      + `<span class="sw" style="background:${colorOf(name)}"></span>`
      + `<span>${esc(name)}${b.score != null ? ` <span class="sz">초안 ${b.score.toFixed(2)}</span>` : ""}</span>`
      + `<span class="sz">${w}×${h}</span><button class="x" data-del="${i}" title="지우기">×</button></div>`;
  }).join("") || `<div class="hint" style="margin:0">${S.img ? "박스가 없습니다 — 빈 곳을 끌어 그리세요" : ""}</div>`;
}

function renderReview() {
  const p = $("review-pill");
  p.className = "pill " + (S.status || "none");
  p.textContent = S.status === "ok" ? "사용" : S.status === "skip" ? "제외" : "미검토";
  for (const id of ["mark-ok", "mark-skip", "mark-clear", "prev", "next", "draft", "copy-prev", "trash", "box-clear"]) {
    $(id).disabled = S.idx < 0;
  }
}

function renderSaveState() {
  const el = $("save-state");
  if (S.idx < 0) { el.textContent = ""; return; }
  el.className = S.dirty ? "dirty" : "saved";
  el.textContent = S.dirty ? "● 저장 안 됨 (넘어가면 저장)" : "저장됨";
}

$("classes").addEventListener("click", (e) => {
  const row = e.target.closest(".cls");
  if (row && !row.querySelector("input")) setClass(Number(row.dataset.c));
});
$("classes").addEventListener("dblclick", (e) => {
  const row = e.target.closest(".cls");
  if (!row) return;
  const c = Number(row.dataset.c);
  const nm = row.querySelector(".nm");
  nm.innerHTML = `<input value="${esc(S.classes[c])}" />`;
  const input = nm.querySelector("input");
  input.focus(); input.select();
  const done = async (commit) => {
    const v = input.value.trim();
    if (commit && v && v !== S.classes[c]) {
      const next = [...S.classes]; next[c] = v;
      await saveClasses(next);
    } else renderClasses();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") done(true);
    else if (ev.key === "Escape") done(false);
    ev.stopPropagation();
  });
  input.addEventListener("blur", () => done(true), { once: true });
});

async function saveClasses(next) {
  try {
    const r = await putJSON(`/api/datasets/${enc(S.ds)}/classes`, { classes: next });
    S.classes = r.classes; renderAll();
    msg("클래스를 저장했습니다", "ok");
  } catch (e) { msg(e.message, "err"); renderClasses(); }
}

$("cls-add").addEventListener("click", () => {
  const v = $("cls-new").value.trim();
  if (!v || !S.ds) return;
  $("cls-new").value = "";
  saveClasses([...S.classes, v]);
});
$("cls-new").addEventListener("keydown", (e) => { if (e.key === "Enter") $("cls-add").click(); });

$("boxes").addEventListener("click", (e) => {
  const del = e.target.closest("[data-del]");
  if (del) { S.sel = Number(del.dataset.del); deleteSel(); return; }
  const row = e.target.closest(".bx");
  if (row) { S.sel = Number(row.dataset.i); S.cur = S.boxes[S.sel].cls; renderAll(); }
});
$("box-clear").addEventListener("click", () => {
  if (!S.boxes.length) return;
  pushUndo(); S.boxes = []; S.sel = -1; renderAll();
  msg("박스를 모두 지웠습니다 — Ctrl+Z 로 되돌릴 수 있습니다");
});

$("mark-ok").addEventListener("click", () => mark("ok", true));
$("mark-skip").addEventListener("click", () => mark("skip", true));
$("mark-clear").addEventListener("click", () => mark("", false));
$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("draft").addEventListener("click", draft);
$("copy-prev").addEventListener("click", copyPrev);
$("draft-score").addEventListener("input", (e) => { $("draft-score-v").textContent = Number(e.target.value).toFixed(2); });

$("trash").addEventListener("click", async () => {
  if (S.idx < 0) return;
  const name = S.items[S.idx].name;
  const btn = $("trash");
  if (btn.dataset.armed !== name) {
    // 확인 창(alert) 대신 한 번 더 누르게 한다.
    btn.dataset.armed = name; btn.textContent = "한 번 더 누르면 뺍니다";
    setTimeout(() => { if (btn.dataset.armed === name) { delete btn.dataset.armed; btn.textContent = "이 이미지 빼기 (휴지통)"; } }, 3000);
    return;
  }
  delete btn.dataset.armed; btn.textContent = "이 이미지 빼기 (휴지통)";
  try {
    S.dirty = false;
    const r = await api(`/api/datasets/${enc(S.ds)}/image/${enc(name)}`, { method: "DELETE" });
    const i = S.idx;
    S.items.splice(i, 1); S.idx = -1;
    renderList(); renderCounts();
    msg(`${name} 을 ${r.to}/ 로 옮겼습니다`, "ok");
    const next = S.items.findIndex((it, j) => j >= i && matches(it));
    if (next >= 0) await openImage(next, true); else { S.img = null; S.boxes = []; renderAll(); }
  } catch (e) { msg(e.message, "err"); }
});

/* ── 목록·거르기 ──────────────────────────────────────────────────── */

$("list").addEventListener("click", (e) => {
  const row = e.target.closest(".item");
  if (row) openImage(Number(row.dataset.i)).catch(() => {});
});
$("filters").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  S.filter = b.dataset.f;
  for (const x of $("filters").children) x.classList.toggle("on", x === b);
  renderList();
  if (S.idx < 0 || !matches(S.items[S.idx])) {
    const i = S.items.findIndex(matches);
    if (i >= 0) openImage(i).catch(() => {});
  }
});

/* ── 위 막대 ──────────────────────────────────────────────────────── */

$("ds-select").addEventListener("change", (e) => openDataset(e.target.value).catch((er) => msg(er.message, "err")));

function togglePop(id) {
  for (const p of ["new-pop", "bulk-pop", "out-pop"]) $(p).hidden = p === id ? !$(p).hidden : true;
}
$("new-toggle").addEventListener("click", () => { togglePop("new-pop"); $("new-name").focus(); });
$("bulk-toggle").addEventListener("click", () => togglePop("bulk-pop"));
$("out-toggle").addEventListener("click", () => togglePop("out-pop"));

$("new-create").addEventListener("click", async () => {
  const name = $("new-name").value.trim();
  const classes = $("new-classes").value.split(",").map((s) => s.trim()).filter(Boolean);
  try {
    await postJSON("/api/datasets", { name, classes });
    $("new-pop").hidden = true; $("new-name").value = "";
    await loadDatasets(name);
    msg(`데이터셋 ${name} 을 만들었습니다 — 이미지를 올리거나 카메라 프레임을 담으세요`, "ok");
  } catch (e) { msg(e.message, "err"); }
});

$("upload-files").addEventListener("click", () => S.ds ? $("file-input").click() : msg("데이터셋을 먼저 고르세요", "err"));
$("upload-folder").addEventListener("click", () => S.ds ? $("folder-input").click() : msg("데이터셋을 먼저 고르세요", "err"));
for (const id of ["file-input", "folder-input"]) {
  $(id).addEventListener("change", async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    msg(`${files.length}개 올리는 중…`, "busy", true);
    try {
      const r = await api(`/api/datasets/${enc(S.ds)}/upload`, { method: "POST", body: fd });
      await openDataset(S.ds, true);
      msg(`이미지 ${r.added.length}장 더함` + (r.with_label ? ` (라벨 같이 ${r.with_label})` : "")
        + (r.duplicates.length ? ` · 같은 이름이라 건너뜀 ${r.duplicates.length}` : ""), "ok");
    } catch (er) { msg(`올리지 못했습니다: ${er.message}`, "err"); }
  });
}

async function loadCameras() {
  try {
    const r = await api("/api/datasets-cameras");
    $("cam-select").innerHTML = r.items.length
      ? r.items.map((c) => `<option value="${c.id}">카메라 ${c.id} · ${esc(c.name)}${["online", "normal", ""].includes(c.status) ? "" : ` (${esc(c.status)})`}</option>`).join("")
      : `<option value="">카메라 없음</option>`;
    const saved = localGet("label.cam");
    if (saved && r.items.some((c) => String(c.id) === saved)) $("cam-select").value = saved;
  } catch { $("cam-select").innerHTML = `<option value="">카메라 목록 실패</option>`; }
}
$("cam-select").addEventListener("change", (e) => localSet("label.cam", e.target.value));
$("capture").addEventListener("click", async () => {
  const cam = $("cam-select").value;
  if (!S.ds || !cam) { msg(S.ds ? "카메라를 고르세요" : "데이터셋을 먼저 고르세요", "err"); return; }
  msg("프레임을 받는 중…", "busy", true);
  try {
    const r = await postJSON(`/api/datasets/${enc(S.ds)}/capture`, { camera_id: Number(cam) });
    await saveIfDirty();
    const d = await api(`/api/datasets/${enc(S.ds)}`);
    S.items = d.items; renderCounts();
    const i = S.items.findIndex((it) => it.name === r.name);
    S.idx = -1; renderList();
    if (i >= 0) await openImage(i, true);
    msg(`카메라 ${cam} 프레임을 담았습니다: ${r.name}`, "ok");
  } catch (e) { msg(e.message, "err"); }
});

$("bulk-run").addEventListener("click", async () => {
  if (!S.ds) return;
  try {
    await saveIfDirty();
    const r = await postJSON(`/api/datasets/${enc(S.ds)}/draft-all`, {
      min_score: Number($("bulk-score").value), only_unlabeled: $("bulk-scope").value === "unlabeled" });
    if (!r.total) { msg("초안을 만들 이미지가 없습니다", "ok"); return; }
    pollBulk();
  } catch (e) { msg(e.message, "err"); }
});

async function pollBulk() {
  const bar = $("bulk-progress");
  try {
    const j = await api("/api/datasets-job");
    if (j.status === "running") {
      bar.hidden = false;
      bar.firstElementChild.style.width = `${(100 * j.done / Math.max(1, j.total)).toFixed(1)}%`;
      msg(`모델로 초안 만드는 중 ${j.done} / ${j.total} (박스 ${j.boxes})`, "busy", true);
      setTimeout(pollBulk, 1000);
      return;
    }
    bar.hidden = true;
    if (j.status === "error") msg(`초안 실패: ${j.error}`, "err");
    else if (j.status === "done") {
      msg(`초안 ${j.done}장, 박스 ${j.boxes}개를 써 두었습니다 — 한 장씩 보고 고친 뒤 Enter`, "ok");
      if (j.dataset === S.ds) await openDataset(S.ds, true);
    }
  } catch (e) { bar.hidden = true; msg(e.message, "err"); }
}

function updateLinks() {
  $("export-ok").href = S.ds ? `/api/datasets/${enc(S.ds)}/export.zip?status=ok` : "#";
  $("export-all").href = S.ds ? `/api/datasets/${enc(S.ds)}/export.zip?status=all` : "#";
}

$("to-eval").addEventListener("click", async () => {
  const name = $("eval-name").value.trim();
  if (!name) { msg("평가 세트 이름을 적으세요 (예 kaist-100)", "err"); $("eval-name").focus(); return; }
  try {
    await saveIfDirty();
    const r = await postJSON(`/api/datasets/${enc(S.ds)}/to-eval`, { name, status: "ok" });
    msg(`평가 세트 ${r.set} (${r.images}장)을 만들었습니다 — 위쪽 '이미지 판별률 평가' 탭에서 고르세요`, "ok", true);
  } catch (e) { msg(e.message, "err"); }
});

/* ── 기타 ─────────────────────────────────────────────────────────── */

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 못 해도 동작은 같다 */ } }

loadDatasets().catch((e) => msg(`데이터셋 목록을 받지 못했습니다: ${e.message}`, "err"));
loadCameras();
api("/api/datasets-job").then((j) => { if (j.status === "running") pollBulk(); }).catch(() => {});
