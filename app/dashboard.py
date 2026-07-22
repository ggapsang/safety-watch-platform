"""시연/검수용 웹 대시보드 (FastAPI + MJPEG).

Docker headless 환경이라 화면 직접 출력이 불가하므로 브라우저로 본다.

  GET  /                   대시보드 HTML (외부 CDN 의존 없음)
  GET  /video              MJPEG 스트림
  GET  /api/status         현재 상태 JSON
  GET  /api/logs?since=N   시스템 로그(증분)
  GET  /api/captures       최근 판독 캡처 메타 8장(최신순)
  GET  /capture/{id}.jpg   캡처 이미지
  GET  /api/settings       현재 설정
  POST /api/settings       설정 변경(카메라/PLC/판정 기준) — 즉시 반영 + 파일 저장
  POST /api/record/start   녹화 시작 (최대 60초, 도달 시 자동 정지)
  POST /api/record/stop    녹화 정지
  GET  /api/recordings     녹화 파일 목록
  GET  /recording/{name}   녹화 파일 다운로드(mp4)
  GET  /healthz            헬스체크
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
      --ok:#2fbf71;--warn:#e8b931;--bad:#e5484d;--accent:#4c8dff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;
       align-items:center;gap:12px;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600;white-space:nowrap}
.spacer{flex:1}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:999px;
      padding:4px 12px;font-size:12px;color:var(--muted);white-space:nowrap}
button{font:inherit;background:var(--panel);color:var(--fg);border:1px solid var(--line);
       border-radius:8px;padding:6px 14px;cursor:pointer}
button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
main{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,1fr) minmax(210px,.62fr);
     gap:14px;padding:14px}
@media (max-width:1400px){main{grid-template-columns:minmax(0,1.4fr) minmax(260px,1fr)}
  .caps{grid-column:1/-1}}
@media (max-width:900px){main{grid-template-columns:1fr}}
.col{display:flex;flex-direction:column;gap:14px;min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
      overflow:hidden;display:flex;flex-direction:column;min-height:0}
.card h2{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
         margin:0;padding:10px 13px;border-bottom:1px solid var(--line);
         display:flex;justify-content:space-between;align-items:center}
img.live{width:100%;display:block;background:#000}
.verdict{padding:18px 14px;text-align:center}
.verdict .code{font-size:52px;font-weight:700;line-height:1}
.verdict .label{font-size:19px;margin-top:4px}
table{width:100%;border-collapse:collapse}
td{padding:6px 13px;border-bottom:1px solid var(--line)}
td:first-child{color:var(--muted);white-space:nowrap}
td:last-child{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
#logbox{font:12px/1.55 Consolas,"Courier New",monospace;padding:8px 12px;overflow-y:auto;
        max-height:340px;min-height:180px}
#logbox div{white-space:pre-wrap;word-break:break-all;border-bottom:1px solid #1e242c;padding:2px 0}
.lv-ERROR{color:var(--bad)} .lv-WARNING{color:var(--warn)} .lv-DET{color:var(--accent)}
.t{color:var(--muted)}
.caps{overflow-y:auto;max-height:calc(100vh - 120px)}
.cap{border-bottom:1px solid var(--line);padding:8px}
.cap img{width:100%;border-radius:6px;display:block;background:#000}
.cap .meta{display:flex;justify-content:space-between;font-size:11px;margin-top:5px;gap:6px}
.badge{border-radius:5px;padding:1px 7px;font-weight:700;color:#0b0e12}
.empty{color:var(--muted);padding:16px;text-align:center;font-size:12px}
dialog{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:12px;
       padding:0;width:min(560px,94vw)}
dialog::backdrop{background:rgba(0,0,0,.6)}
dialog h3{margin:0;padding:14px 18px;border-bottom:1px solid var(--line);font-size:15px}
.form{padding:14px 18px;display:grid;gap:12px;max-height:70vh;overflow-y:auto}
fieldset{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:0}
legend{color:var(--muted);font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:0 6px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
label{display:block;font-size:11px;color:var(--muted);margin-bottom:3px}
input{width:100%;background:#0f1216;color:var(--fg);border:1px solid var(--line);
      border-radius:6px;padding:6px 9px;font:inherit}
input:focus{outline:none;border-color:var(--accent)}
.actions{display:flex;gap:8px;justify-content:flex-end;padding:12px 18px;border-top:1px solid var(--line)}
.hint{font-size:11px;color:var(--muted)}
#saveMsg{font-size:12px;margin-right:auto;align-self:center}
</style></head><body>
<header>
  <h1>컨베이어 부적합(쓰레기) 탐지 — PC 추론</h1>
  <span class="chip"><span id="vdot" class="dot"></span>영상</span>
  <span class="chip"><span id="pdot" class="dot"></span>PLC</span>
  <span class="chip" id="perf">-</span>
  <span class="spacer"></span>
  <button id="btnRec">● 녹화</button>
  <button id="btnCfg">⚙ 설정</button>
</header>

<main>
  <div class="col">
    <section class="card"><h2>라이브 뷰</h2><img class="live" src="/video" alt="live"></section>
  </div>

  <div class="col">
    <section class="card"><h2>판정 결과</h2>
      <div class="verdict"><div id="code" class="code">0</div>
        <div id="label" class="label">정상</div>
        <div style="margin-top:5px;font-size:13px" id="act">-</div>
        <div style="color:var(--muted);margin-top:6px;font-size:12px" id="reason">-</div></div>
    </section>
    <section class="card"><h2>현재 값</h2><table>
      <tr><td>9매트릭스</td><td id="cat">-</td></tr>
      <tr><td>max count (2초)</td><td id="mc">-</td></tr>
      <tr><td>max area</td><td id="ma">-</td></tr>
      <tr><td>현재 검출 수</td><td id="dc">-</td></tr>
      <tr><td>PLC 마지막 write</td><td id="pw">-</td></tr>
      <tr><td>PLC write 성공/실패</td><td id="pn">-</td></tr>
      <tr><td>백엔드</td><td id="be">-</td></tr>
    </table></section>
    <section class="card"><h2>시스템 로그<span class="hint">conf · 후처리 전 라벨 포함</span></h2>
      <div id="logbox"></div></section>
  </div>

  <div class="col">
    <section class="card"><h2>녹화 <span class="hint" id="recstate">-</span></h2>
      <div id="recbox"><div class="empty">녹화 파일이 없습니다</div></div></section>
    <section class="card caps"><h2>최근 판독 <span class="hint">최신 8장</span></h2>
      <div id="capbox"><div class="empty">아직 판독 기록이 없습니다</div></div></section>
  </div>
</main>

<dialog id="cfg"><h3>설정</h3>
  <form method="dialog" class="form" id="cfgForm">
    <fieldset><legend>카메라 (RTSP)</legend>
      <div class="grid2">
        <div><label>카메라 IP</label><input id="c_ip" placeholder="192.168.5.52"></div>
        <div><label>포트</label><input id="c_port" type="number" placeholder="554"></div>
        <div><label>ID</label><input id="c_user" placeholder="admin"></div>
        <div><label>비밀번호</label><input id="c_pw" type="password"></div>
      </div>
      <div style="margin-top:8px"><label>스트림 경로</label>
        <input id="c_path" placeholder="/profile2/media.smp"></div>
      <div class="hint" style="margin-top:6px">저장하면 영상 수신이 새 주소로 재연결됩니다.</div>
    </fieldset>
    <fieldset><legend>PLC (XGT)</legend>
      <div class="grid2">
        <div><label>PLC IP</label><input id="p_host" placeholder="192.168.5.199"></div>
        <div><label>포트</label><input id="p_port" type="number" placeholder="2004"></div>
      </div>
      <div class="hint" style="margin-top:6px">D8100 PC Ready · D8101 결과코드(0/1/2/9) write 전용</div>
    </fieldset>
    <fieldset><legend>판정 기준</legend>
      <div class="grid2">
        <div><label>confidence 임계값</label><input id="j_conf" type="number" step="0.01" min="0" max="1"></div>
        <div><label>NMS IoU</label><input id="j_iou" type="number" step="0.01" min="0" max="1"></div>
        <div><label>부분오염 기준 — count low</label><input id="j_cl" type="number" step="1" min="0"></div>
        <div><label>많이오염 기준 — count high</label><input id="j_ch" type="number" step="1" min="0"></div>
        <div><label>부분오염 기준 — area low (%)</label><input id="j_al" type="number" step="0.1" min="0"></div>
        <div><label>많이오염 기준 — area high (%)</label><input id="j_ah" type="number" step="0.1" min="0"></div>
        <div><label>cooldown (초)</label><input id="j_cd" type="number" step="1" min="1"></div>
      </div>
      <div class="hint" style="margin-top:6px">
        count: &lt;low → 정상 / ≥high → 많이오염 / 그 외 부분오염<br>
        area: &lt;low → 정상 / &gt;high → 많이오염 / 그 외 부분오염 · 최종 = 둘 중 높은 쪽
      </div>
    </fieldset>
  </form>
  <div class="actions"><span id="saveMsg"></span>
    <button id="btnCancel">취소</button>
    <button id="btnSave" class="primary">저장 및 적용</button></div>
</dialog>

<script>
const $=id=>document.getElementById(id);
// 0 정상 / 1 부분오염 / 2 많이오염 / 9 판정실패
const col=c=>c===9||c>=2?'var(--bad)':c>=1?'var(--warn)':'var(--ok)';
const ACT={0:'정회전',1:'알람 + 라인 정지',2:'알람 + 2번 컨베이어 역회전',9:'알람 + 안전 정지'};
const NAME={0:'정상',1:'부분오염',2:'많이오염',9:'판정실패'};

async function tick(){
  try{
    const s=await (await fetch('/api/status',{cache:'no-store'})).json();
    const code=s.in_fault?9:s.severity;
    $('vdot').style.background=s.video_connected?'var(--ok)':'var(--bad)';
    $('pdot').style.background=!s.plc_enabled?'var(--muted)':(s.plc_connected?'var(--ok)':'var(--bad)');
    $('perf').textContent=`${s.fps.toFixed(1)} fps · 추론 ${s.infer_ms.toFixed(1)} ms`;
    $('code').textContent=code; $('label').textContent=s.in_fault?'판정실패':s.severity_label;
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

let logSeq=0;
async function pollLogs(){
  try{
    const rows=await (await fetch('/api/logs?since='+logSeq,{cache:'no-store'})).json();
    if(rows.length){
      const box=$('logbox');
      const stick=box.scrollTop+box.clientHeight>=box.scrollHeight-30;
      for(const r of rows){
        logSeq=r.seq;
        const d=document.createElement('div');
        d.className='lv-'+r.level;
        d.innerHTML='<span class="t">'+r.t+'</span> '+
          r.msg.replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
        box.appendChild(d);
      }
      while(box.childElementCount>400) box.removeChild(box.firstChild);
      if(stick) box.scrollTop=box.scrollHeight;
    }
  }catch(e){}
}

let capIds='';
async function pollCaps(){
  try{
    const caps=await (await fetch('/api/captures',{cache:'no-store'})).json();
    const key=caps.map(c=>c.id).join(',');
    if(key===capIds) return;
    capIds=key;
    const box=$('capbox');
    if(!caps.length){box.innerHTML='<div class="empty">아직 판독 기록이 없습니다</div>';return;}
    box.innerHTML=caps.map(c=>{
      const t=new Date(c.ts*1000).toLocaleTimeString('ko-KR',{hour12:false});
      const raw=c.raw_label?(' · '+c.raw_label):'';
      return `<div class="cap"><img src="/capture/${c.id}.jpg" alt="capture">
        <div class="meta">
          <span><span class="badge" style="background:${col(c.code)}">${c.code} ${NAME[c.code]||''}</span></span>
          <span class="t">${t}</span>
        </div>
        <div class="meta"><span class="t">det=${c.det_count} · conf=${c.top_score.toFixed(3)}${raw}</span>
          <span class="t">${c.category}</span></div></div>`;
    }).join('');
  }catch(e){}
}

// ── 녹화 ──────────────────────────────────────────────────────────
let recActive=false;
function fmtSize(b){return b>=1048576?(b/1048576).toFixed(1)+' MB':(b/1024).toFixed(0)+' KB';}
$('btnRec').onclick=async()=>{
  const url=recActive?'/api/record/stop':'/api/record/start';
  try{
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
                             body:JSON.stringify({seconds:60,overlay:true})});
    const j=await r.json();
    if(!r.ok) $('recstate').textContent='실패: '+(j.detail||'');
  }catch(e){}
  pollRecs();
};
async function pollRecs(){
  try{
    const j=await (await fetch('/api/recordings',{cache:'no-store'})).json();
    recActive=j.status.active;
    $('btnRec').textContent=recActive?'■ 정지':'● 녹화';
    $('btnRec').style.borderColor=recActive?'var(--bad)':'';
    $('btnRec').style.color=recActive?'var(--bad)':'';
    $('recstate').textContent=recActive
      ? `녹화 중 ${j.status.elapsed.toFixed(0)}s / ${j.status.limit.toFixed(0)}s`
      : (j.status.error||'최대 60초');
    const box=$('recbox');
    if(!j.files.length){box.innerHTML='<div class="empty">녹화 파일이 없습니다</div>';return;}
    box.innerHTML='<table>'+j.files.map(f=>{
      const t=new Date(f.ts*1000).toLocaleTimeString('ko-KR',{hour12:false});
      const busy=f.recording?' <span style="color:var(--bad)">●</span>':'';
      return `<tr><td><a href="/recording/${f.name}" download
                style="color:var(--accent);text-decoration:none">${f.name}</a>${busy}</td>
              <td>${fmtSize(f.bytes)}<br><span class="t">${t}</span></td></tr>`;
    }).join('')+'</table>';
  }catch(e){}
}

// ── 설정 ──────────────────────────────────────────────────────────
const dlg=$('cfg');
$('btnCfg').onclick=async()=>{
  const s=await (await fetch('/api/settings',{cache:'no-store'})).json();
  $('c_ip').value=s.camera.ip; $('c_port').value=s.camera.port;
  $('c_user').value=s.camera.user; $('c_pw').value=s.camera.password;
  $('c_path').value=s.camera.path;
  $('p_host').value=s.plc.host; $('p_port').value=s.plc.port;
  $('j_conf').value=s.judge.conf; $('j_iou').value=s.judge.iou;
  $('j_cl').value=s.judge.count_low; $('j_ch').value=s.judge.count_high;
  $('j_al').value=s.judge.area_low; $('j_ah').value=s.judge.area_high;
  $('j_cd').value=s.judge.cooldown;
  $('saveMsg').textContent=''; dlg.showModal();
};
$('btnCancel').onclick=()=>dlg.close();
$('btnSave').onclick=async()=>{
  const body={
    camera:{ip:$('c_ip').value.trim(),port:+$('c_port').value||554,
            user:$('c_user').value,password:$('c_pw').value,path:$('c_path').value.trim()},
    plc:{host:$('p_host').value.trim(),port:+$('p_port').value||2004},
    judge:{conf:+$('j_conf').value,iou:+$('j_iou').value,
           count_low:+$('j_cl').value,count_high:+$('j_ch').value,
           area_low:+$('j_al').value,area_high:+$('j_ah').value,cooldown:+$('j_cd').value}
  };
  $('saveMsg').textContent='저장 중…';
  try{
    const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
                                         body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok){$('saveMsg').style.color='var(--bad)';$('saveMsg').textContent='실패: '+(j.detail||j.error);return;}
    $('saveMsg').style.color='var(--ok)';
    $('saveMsg').textContent=j.changed.length?('적용됨: '+j.changed.join(', ')):'변경 없음';
    setTimeout(()=>dlg.close(),700);
  }catch(e){$('saveMsg').style.color='var(--bad)';$('saveMsg').textContent='실패: '+e;}
};

setInterval(tick,500); setInterval(pollLogs,700); setInterval(pollCaps,1500);
setInterval(pollRecs,1000);
tick(); pollLogs(); pollCaps(); pollRecs();
</script></body></html>
"""


def create_app(state, cfg, on_settings=None, recorder=None):
    # ⚠ 이 모듈은 `from __future__ import annotations` 를 쓴다. FastAPI 는 타입 힌트를
    #   모듈 전역에서 해석하므로, 여기서 지역 import 한 이름(예: Request)을 애노테이션에
    #   쓰면 인식하지 못하고 쿼리 파라미터로 오인한다(422). 요청 본문은 builtin 인
    #   dict 로 받는다.
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                                   Response, StreamingResponse)

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

    @api.get("/api/logs")
    def logs(since: int = 0) -> JSONResponse:
        return JSONResponse(state.logs_since(since))

    @api.get("/api/captures")
    def captures() -> JSONResponse:
        return JSONResponse(state.capture_list())

    @api.get("/capture/{capture_id}.jpg")
    def capture(capture_id: int) -> Response:
        data = state.capture_jpeg(capture_id)
        if data is None:
            raise HTTPException(status_code=404, detail="캡처를 찾을 수 없습니다")
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=300"})

    @api.get("/api/settings")
    def get_settings() -> JSONResponse:
        from .config import settings_dict

        return JSONResponse(settings_dict(cfg))

    @api.post("/api/settings")
    def post_settings(patch: dict) -> JSONResponse:
        if on_settings is None:
            raise HTTPException(status_code=503, detail="설정 변경이 활성화되어 있지 않습니다")
        try:
            changed = on_settings(patch)
        except ValueError as exc:                                # cfg.validate() 위반 등
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "changed": sorted(changed)})

    def _need_recorder():
        if recorder is None:
            raise HTTPException(status_code=503, detail="녹화 기능이 비활성 상태입니다")
        return recorder

    @api.post("/api/record/start")
    def record_start(body: dict | None = None) -> JSONResponse:
        rec = _need_recorder()
        body = body or {}
        snap = state.snapshot()
        try:
            st = rec.start(seconds=float(body.get("seconds", 60)),
                           fps=snap.fps or cfg.dashboard.stream_fps,
                           overlay=bool(body.get("overlay", True)))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(st)

    @api.post("/api/record/stop")
    def record_stop() -> JSONResponse:
        return JSONResponse(_need_recorder().stop())

    @api.get("/api/recordings")
    def recordings() -> JSONResponse:
        if recorder is None:
            return JSONResponse({"status": {"active": False, "elapsed": 0, "limit": 0,
                                            "error": "비활성"}, "files": []})
        return JSONResponse({"status": recorder.status(), "files": recorder.listing()})

    @api.get("/recording/{name}")
    def recording(name: str) -> FileResponse:
        path = _need_recorder().file_path(name)
        if path is None:
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
        return FileResponse(str(path), media_type="video/mp4", filename=name)

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


def serve_in_thread(state, cfg, on_settings=None, recorder=None) -> threading.Thread | None:
    """대시보드를 별도 스레드에서 기동. 의존성이 없으면 경고 후 건너뛴다."""
    try:
        import uvicorn
    except ImportError:
        log.warning("fastapi/uvicorn 미설치 — 대시보드를 건너뜁니다 (pip install fastapi uvicorn)")
        return None

    api = create_app(state, cfg, on_settings, recorder)
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
