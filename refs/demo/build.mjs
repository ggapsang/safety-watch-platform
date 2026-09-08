// web/dist 산출물을 단일 HTML(아티팩트)로 인라이닝한다.
// 실 서버 의존(API·MQTT·WHEP)은 shim.js가 대체한다.
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const DIST = "C:/Users/PC3/Projects/ISECO/web/dist";
const HERE = "C:/Users/PC3/AppData/Local/Temp/claude/C--Users-PC3-Projects-ISECO/081c0475-491c-4fcf-af14-5da0be0c58a3/scratchpad/demo";

// vite 산출물은 파일명에 해시가 붙는다 — 확장자로 찾는다.
const asset = (ext) => {
  const hits = readdirSync(join(DIST, "assets")).filter((f) => f.endsWith(ext));
  if (hits.length !== 1) throw new Error(`assets/*${ext} 가 1개가 아님: ${hits}`);
  return join(DIST, "assets", hits[0]);
};

const b64 = (p, mime) => `data:${mime};base64,${readFileSync(p).toString("base64")}`;

// ── CSS: Pretendard 3종(400/600/700)만 data URI로 인라인, 500·800 face는 제거 ──
let css = readFileSync(asset(".css"), "utf8");
css = css.replace(/@font-face\{[^}]*font-weight:(?:500|800)[^}]*\}/g, "");
for (const [name, w] of [["Regular", 400], ["SemiBold", 600], ["Bold", 700]]) {
  const uri = b64(join(DIST, `fonts/Pretendard-${name}.woff2`), "font/woff2");
  css = css.replace(`url(/fonts/Pretendard-${name}.woff2)`, `url(${uri})`);
  if (!css.includes(uri)) throw new Error(`font ${name} (${w}) not inlined`);
}
if (css.includes("url(/fonts/")) throw new Error("남은 폰트 url() 존재: " + css.match(/url\(\/fonts\/[^)]*\)/g));

// ── JS 번들: 로고 인라인 + 이벤트 캡쳐 URL을 합성 이미지로 치환 ────────────────
let js = readFileSync(asset(".js"), "utf8");
const patch = (from, to) => {
  if (!js.includes(from)) throw new Error(`번들 패치 대상 없음: ${from.slice(0, 60)}`);
  js = js.split(from).join(to);
};
patch('"/daim-logo.png"', JSON.stringify(b64(join(DIST, "daim-logo.png"), "image/png")));
patch("src:`/api/events/${e.id}/snapshot`", "src:window.__DEMO_SNAP__(e.id)");

// ── 사용자 매뉴얼: 스크린샷을 data URI로 바꿔 문자열로 동봉 (오버레이 iframe) ──
let manual = readFileSync(join(DIST, "manual.html"), "utf8");
manual = manual.replace(/src="\/img\/manual\/([^"]+)"/g, (_, f) =>
  `src="${b64(join(DIST, "img/manual", f), "image/png")}"`);
manual = manual.replace(/href="\/"/g, 'href="#top"');
if (manual.includes("/img/manual/")) throw new Error("매뉴얼 이미지 인라인 실패");

const shim = readFileSync(join(HERE, "shim.js"), "utf8");

// 인라인 <script> 안에서 "</script" 시퀀스가 파서를 조기 종료시키지 않도록 무해하게 이스케이프.
// U+FFFD(번들된 string_decoder가 문자열 리터럴로 갖고 있음)는 아티팩트 업로드가 거부하므로
// 같은 값의 JS 유니코드 이스케이프로 바꾼다.
const safeJs = (s) =>
  s.replace(/<\/script/gi, "<\\/script").replace(/�/g, "\\uFFFD");
const safeStr = (s) => JSON.stringify(s).replace(/</g, "\\u003c");

const html = `<title>ISECO 안전관제 대시보드</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;500;600&display=swap" rel="stylesheet" />
<style>
${css}
/* ── 데모 고지 (원본 앱에는 없는 요소) ── */
#demo-note{position:fixed;right:18px;bottom:18px;z-index:9999;display:flex;align-items:center;
  gap:9px;max-width:340px;padding:10px 12px;border:1px solid #e6dfd8;border-radius:10px;
  background:#faf9f5;box-shadow:0 6px 20px rgba(20,20,19,.10);font-size:12px;line-height:1.5;color:#3d3d3a}
#demo-note b{color:#a9583e;font-weight:600}
#demo-note button{flex:0 0 auto;align-self:flex-start;border:0;background:none;color:#8e8b82;
  font-size:14px;line-height:1;cursor:pointer;padding:2px 0 0}
#demo-note button:hover{color:#141413}
#demo-note button:focus-visible{outline:2px solid #cc785c;outline-offset:2px}
#demo-manual{position:fixed;inset:0;z-index:10000;display:none;background:#faf9f5}
#demo-manual iframe{width:100%;height:100%;border:0}
#demo-manual .close{position:fixed;right:18px;top:14px;z-index:1;height:34px;padding:0 14px;
  border:1px solid #e6dfd8;border-radius:8px;background:#faf9f5;color:#3d3d3a;font-size:13px;cursor:pointer}
#demo-manual .close:hover{background:#f5f0e8;color:#141413}
</style>

<div id="root"></div>

<aside id="demo-note">
  <span><b>데모</b> · 실서버 없이 동작하는 UI 데모입니다. 이벤트·카메라 데이터는 합성값이고,
  라이브 영상은 canvas 합성 스트림입니다. 엑셀 내려받기는 미리보기에서 차단됩니다.</span>
  <button type="button" aria-label="데모 안내 닫기" onclick="this.parentNode.remove()">✕</button>
</aside>

<div id="demo-manual" role="dialog" aria-label="사용자 매뉴얼">
  <button type="button" class="close">✕ 닫기</button>
  <iframe title="ISECO 사용자 매뉴얼"></iframe>
</div>

<script>window.__DEMO_MANUAL_HTML__ = ${safeStr(manual)};</script>
<script>
${safeJs(shim)}
</script>
<script type="module">
${safeJs(js)}
</script>
`;

writeFileSync(join(HERE, "iseco-demo.html"), html);
console.log("wrote iseco-demo.html", (Buffer.byteLength(html) / 1048576).toFixed(2), "MB");
