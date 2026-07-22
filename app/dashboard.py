"""시연/검수용 웹 대시보드 (FastAPI + MJPEG).

Docker headless 환경이라 화면 직접 출력이 불가하므로 브라우저로 본다.
  GET /            대시보드 HTML (외부 CDN 의존 없음)
  GET /video       MJPEG 스트림
  GET /api/status  현재 상태 JSON
  GET /healthz     헬스체크
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

log = logging.getLogger(__name__)

_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>부적합 탐지 — 아이에스에코솔루션</title>
<style>
:root{--bg:#0f1216;--panel:#171c22;--line:#262d36;--fg:#e6eaef;--muted:#8b96a5;
      --ok:#2fbf71;--warn:#e8b931;--bad:#e5484d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
       align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:999px;
      padding:4px 12px;font-size:12px;color:var(--muted)}
main{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:16px;padding:16px}
@media (max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
         margin:0;padding:12px 14px;border-bottom:1px solid var(--line)}
img{width:100%;display:block;background:#000}
.verdict{padding:22px 16px;text-align:center}
.verdict .code{font-size:56px;font-weight:700;line-height:1}
.verdict .label{font-size:20px;margin-top:6px}
.s0{color:var(--ok)} .s1{color:var(--warn)} .s2{color:var(--bad)}
table{width:100%;border-collapse:collapse}
td{padding:8px 14px;border-bottom:1px solid var(--line)}
td:first-child{color:var(--muted);white-space:nowrap}
td:last-child{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
</style></head><body>
<header>
  <h1>컨베이어 부적합(쓰레기) 탐지 — PC 추론</h1>
  <span class="chip"><span id="vdot" class="dot"></span>영상</span>
  <span class="chip"><span id="pdot" class="dot"></span>PLC</span>
  <span class="chip" id="perf">-</span>
</header>
<main>
  <section class="card"><h2>라이브 뷰</h2><img src="/video" alt="live"></section>
  <div style="display:flex;flex-direction:column;gap:16px">
    <section class="card"><h2>판정 결과</h2>
      <div class="verdict"><div id="code" class="code s0">0</div>
        <div id="label" class="label s0">정상</div>
        <div style="margin-top:6px;font-size:13px" id="act">-</div>
        <div style="color:var(--muted);margin-top:8px" id="reason">-</div></div>
    </section>
    <section class="card"><h2>상세</h2><table>
      <tr><td>9매트릭스</td><td id="cat">-</td></tr>
      <tr><td>max count (2초)</td><td id="mc">-</td></tr>
      <tr><td>max area</td><td id="ma">-</td></tr>
      <tr><td>현재 검출 수</td><td id="dc">-</td></tr>
      <tr><td>PLC 마지막 write</td><td id="pw">-</td></tr>
      <tr><td>PLC write 성공/실패</td><td id="pn">-</td></tr>
      <tr><td>백엔드</td><td id="be">-</td></tr>
    </table></section>
  </div>
</main>
<script>
const $=id=>document.getElementById(id);
// 0 정상 / 1 부분오염 / 2 많이오염 / 9 판정실패
const col=s=>s===9?'var(--bad)':s>=2?'var(--bad)':s>=1?'var(--warn)':'var(--ok)';
const ACT={0:'정회전',1:'알람 + 라인 정지',2:'알람 + 2번 컨베이어 역회전',9:'알람 + 안전 정지'};
async function tick(){
  try{
    const s=await (await fetch('/api/status',{cache:'no-store'})).json();
    const code=s.in_fault?9:s.severity;
    const label=s.in_fault?'판정실패':s.severity_label;
    $('vdot').style.background=s.video_connected?'var(--ok)':'var(--bad)';
    $('pdot').style.background=!s.plc_enabled?'var(--muted)':(s.plc_connected?'var(--ok)':'var(--bad)');
    $('perf').textContent=`${s.fps.toFixed(1)} fps · 추론 ${s.infer_ms.toFixed(1)} ms`;
    $('code').textContent=code; $('label').textContent=label;
    $('code').style.color=col(code); $('label').style.color=col(code);
    $('act').textContent='라인 동작: '+(ACT[code]||'-');
    $('reason').textContent=s.in_fault?s.fault_reason:s.decision_reason;
    $('cat').textContent=s.category;
    $('mc').textContent=s.max_count;
    $('ma').textContent=s.max_area.toFixed(3)+(s.area_unit==='percent'?' %':'');
    $('dc').textContent=s.det_count;
    $('pw').textContent=s.plc_last_error?('오류: '+s.plc_last_error):s.plc_last_write;
    $('pn').textContent=`${s.plc_write_ok} / ${s.plc_write_fail}`;
    $('be').textContent=`${s.backend} · ${s.device} · ${s.infer_mode}`;
  }catch(e){}
}
setInterval(tick,500); tick();
</script></body></html>
"""


def create_app(state, cfg):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

    api = FastAPI(title="부적합 탐지 대시보드", docs_url=None, redoc_url=None)
    interval = 1.0 / max(1.0, cfg.dashboard.stream_fps)

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    @api.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    @api.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse(vars(state.snapshot()))

    @api.get("/video")
    def video() -> StreamingResponse:
        async def gen():
            last = -1
            while True:
                seq, jpeg = state.get_jpeg()
                if jpeg is not None and seq != last:
                    last = seq
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                           + jpeg + b"\r\n")
                await asyncio.sleep(interval)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    return api


def serve_in_thread(state, cfg) -> threading.Thread | None:
    """대시보드를 별도 스레드에서 기동. 의존성이 없으면 경고 후 건너뛴다."""
    try:
        import uvicorn
    except ImportError:
        log.warning("fastapi/uvicorn 미설치 — 대시보드를 건너뜁니다 (pip install fastapi uvicorn)")
        return None

    api = create_app(state, cfg)
    config = uvicorn.Config(api, host=cfg.dashboard.host, port=cfg.dashboard.port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    def run() -> None:
        try:
            server.run()
        except Exception:
            log.exception("대시보드 서버 종료")

    thread = threading.Thread(target=run, name="dashboard", daemon=True)
    thread.start()
    time.sleep(0.3)
    log.info("대시보드: http://localhost:%d", cfg.dashboard.port)
    return thread
