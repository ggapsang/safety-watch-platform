"""도움말(사용 설명서) 페이지 — 대시보드의 '도움말' 버튼으로 새 창에 표시.

비전공자(현장 담당자)가 Docker Desktop 설치부터 실행·사용까지 혼자 따라 할 수 있도록
차분하게 풀어 쓴 단일 HTML 문서. 대시보드와 마찬가지로 외부 CDN 의존이 없다.
`app/dashboard.py` 의 `GET /help` 가 이 문자열을 그대로 반환한다.

배포 전제: 모델·설정을 이미지에 구운 '단일 이미지' 를 전달하고, 현장에서는 Docker Desktop
GUI 로 실행(CPU 추론). 따라서 폴더/.env 편집 단계가 없다.
"""

from __future__ import annotations

# 이미지/포트는 배포 방식과 일치시킨다.
#   image : daim/trash-detector:0.1.0
#   host port 1235 -> container 8080 (GUI Run 시 Host port 에 1235 입력)
HELP_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>설치 · 사용 설명서 — 컨베이어 부적합 탐지</title>
<style>
:root{--bg:#0f1216;--panel:#171c22;--panel2:#1b222b;--line:#262d36;--fg:#e6eaef;
      --muted:#93a0b0;--ok:#2fbf71;--warn:#e8b931;--bad:#e5484d;--accent:#4c8dff;
      --code:#0c1015}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);
     font:16px/1.75 "Segoe UI",system-ui,-apple-system,"Malgun Gothic",sans-serif}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
.top{position:sticky;top:0;z-index:20;background:rgba(15,18,22,.92);
     backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
     padding:12px 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.top h1{font-size:16px;margin:0;font-weight:700;white-space:nowrap}
.top .sub{color:var(--muted);font-size:13px}
.top .sp{flex:1}
.top a.btn{border:1px solid var(--line);border-radius:8px;padding:6px 13px;color:var(--fg);
     font-size:13px} .top a.btn:hover{border-color:var(--accent);text-decoration:none}
.wrap{display:grid;grid-template-columns:250px minmax(0,1fr);gap:34px;
      max-width:1180px;margin:0 auto;padding:26px 22px 90px}
nav{position:sticky;top:74px;align-self:start;font-size:14px;
    border:1px solid var(--line);border-radius:10px;background:var(--panel);padding:12px 8px;
    max-height:calc(100vh - 96px);overflow:auto}
nav a{display:block;color:var(--muted);padding:6px 12px;border-radius:7px;border-left:2px solid transparent}
nav a:hover{color:var(--fg);background:var(--panel2);text-decoration:none}
nav a.on{color:var(--fg);background:var(--panel2);border-left-color:var(--accent)}
nav .g{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:#5c6774;
       padding:12px 12px 4px}
main{min-width:0}
section{margin:0 0 40px;scroll-margin-top:80px}
h2{font-size:23px;margin:8px 0 6px;padding-bottom:8px;border-bottom:1px solid var(--line);
   display:flex;align-items:baseline;gap:10px}
h2 .n{color:var(--accent);font-size:15px;font-weight:700;
      border:1px solid var(--accent);border-radius:999px;padding:1px 10px;flex:none}
h3{font-size:17px;margin:26px 0 6px}
p{margin:9px 0}
ul,ol{margin:9px 0;padding-left:24px} li{margin:5px 0}
strong{color:#fff}
kbd{background:var(--panel2);border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;
    padding:1px 7px;font:13px/1 "Segoe UI",sans-serif}
code{background:var(--panel2);border:1px solid var(--line);border-radius:5px;padding:1px 6px;
     font:13.5px Consolas,"Courier New",monospace;color:#e8d8a8;word-break:break-all}
.cmd{position:relative;margin:12px 0}
.cmd pre{background:var(--code);border:1px solid var(--line);border-radius:9px;
     padding:14px 92px 14px 15px;margin:0;overflow-x:auto;
     font:14px/1.6 Consolas,"Courier New",monospace;color:#d7e2ef}
.cmd .copy{position:absolute;top:9px;right:9px;background:var(--panel);color:var(--muted);
     border:1px solid var(--line);border-radius:7px;padding:4px 11px;font-size:12px;cursor:pointer}
.cmd .copy:hover{border-color:var(--accent);color:var(--fg)}
.cmd .copy.done{color:var(--ok);border-color:var(--ok)}
.box{border:1px solid var(--line);border-left-width:4px;border-radius:9px;
     padding:11px 15px;margin:14px 0;background:var(--panel)}
.box .h{font-weight:700;margin-bottom:2px;display:flex;align-items:center;gap:7px}
.box p{margin:4px 0}
.box.tip{border-left-color:var(--accent)} .box.tip .h{color:var(--accent)}
.box.ok{border-left-color:var(--ok)} .box.ok .h{color:var(--ok)}
.box.warn{border-left-color:var(--warn)} .box.warn .h{color:var(--warn)}
.box.bad{border-left-color:var(--bad)} .box.bad .h{color:var(--bad)}
table{width:100%;border-collapse:collapse;margin:14px 0;font-size:14.5px}
th,td{border:1px solid var(--line);padding:8px 11px;text-align:left;vertical-align:top}
th{background:var(--panel2);color:var(--muted);font-weight:600;white-space:nowrap}
td code{white-space:nowrap}
.step{list-style:none;padding-left:0;counter-reset:s}
.step>li{counter-increment:s;position:relative;padding:2px 0 12px 46px;margin:0}
.step>li::before{content:counter(s);position:absolute;left:0;top:0;width:30px;height:30px;
     border-radius:50%;background:var(--accent);color:#fff;font-weight:700;
     display:flex;align-items:center;justify-content:center;font-size:15px}
.step>li::after{content:"";position:absolute;left:15px;top:32px;bottom:-2px;width:2px;
     background:var(--line)}
.step>li:last-child::after{display:none}
.lead{color:var(--muted);font-size:16px}
.pill{display:inline-block;border-radius:6px;padding:1px 9px;font-weight:700;color:#0b0e12;font-size:13px}
.foot{color:var(--muted);font-size:13px;text-align:center;border-top:1px solid var(--line);
      padding-top:20px;margin-top:30px}
@media (max-width:820px){.wrap{grid-template-columns:1fr} nav{display:none}}
</style></head><body>

<div class="top">
  <h1>설치 · 사용 설명서</h1>
  <span class="sub">컨베이어 부적합(쓰레기) 탐지 — PC 추론 프로그램</span>
  <span class="sp"></span>
  <a class="btn" href="/" target="_blank" rel="noopener">대시보드 열기</a>
</div>

<div class="wrap">
<nav id="toc">
  <div class="g">시작하기</div>
  <a href="#intro">소개</a>
  <a href="#req">0. 준비물 확인</a>
  <div class="g">설치</div>
  <a href="#docker">1. Docker Desktop 설치</a>
  <a href="#load">2. 프로그램 불러오기</a>
  <div class="g">운영</div>
  <a href="#run">3. 실행 / 종료</a>
  <a href="#dash">4. 대시보드 사용법</a>
  <a href="#codes">5. 판정 코드 의미</a>
  <div class="g">기타</div>
  <a href="#trouble">6. 문제 해결</a>
  <a href="#criteria">7. 부적합 판정 기준</a>
</nav>

<main>

<section id="intro">
  <h2><span class="n">소개</span></h2>
  <p class="lead">컨베이어를 비추는 카메라 영상을 이 PC가 실시간으로 분석해, 쓰레기(부적합물)를
  발견하면 PLC에 신호를 보내 라인을 제어하는 프로그램입니다. 담당자가 코드를 다룰 필요 없이,
  아래 순서대로 <strong>설치 → 이미지 불러오기 → 실행</strong> 만 하면 됩니다.</p>
  <div class="cmd"><pre>[카메라] --영상--> [이 PC 추론] --신호--> [PLC] --> [컨베이어 제어]</pre></div>
</section>

<section id="req">
  <h2><span class="n">0</span>준비물 확인</h2>
  <p>시작하기 전에 아래를 확인하세요.</p>
  <table>
    <tr><th>항목</th><th>필요 조건</th></tr>
    <tr><td>운영체제</td><td><strong>Windows 10 (64비트, 2004 버전 이상)</strong> 또는 <strong>Windows 11</strong></td></tr>
    <tr><td>그래픽카드</td><td>없어도 됩니다 — <strong>CPU로 동작</strong>합니다. (NVIDIA GPU가 있으면 더 빠르게 돌릴 수 있으나 선택 사항)</td></tr>
    <tr><td>메모리</td><td>8GB 이상 (16GB 권장)</td></tr>
    <tr><td>네트워크</td><td>이 PC가 <strong>카메라·PLC와 같은 랜(같은 대역)</strong>에 연결돼 있어야 함</td></tr>
    <tr><td>권한</td><td>프로그램 설치를 위한 <strong>관리자 권한</strong></td></tr>
    <tr><td>전달받은 파일</td><td>프로그램 이미지 파일 <code>trash-detector.tar</code> <strong>하나</strong> (모델·설정이 이미 안에 들어 있음)</td></tr>
  </table>
  <div class="box tip"><div class="h">이 PC 네트워크 설정</div>
    <p>이 PC의 IP를 Windows 네트워크 설정에서 <code>192.168.0.11 / 255.255.255.0</code> 으로 맞춰,
    카메라·PLC와 같은 <code>192.168.0.x</code> 대역에 두세요.
    카메라·PLC 주소는 프로그램에 미리 설정돼 있으며, 대시보드 <strong>설정</strong> 창에서 확인·변경할 수 있습니다.</p></div>
</section>

<section id="docker">
  <h2><span class="n">1</span>Docker Desktop 설치</h2>
  <p class="lead">Docker는 이 프로그램을 담고 있는 '상자'를 실행해 주는 무료 프로그램입니다. 딱 한 번만 설치하면 됩니다.</p>
  <ol class="step">
    <li><strong>내려받기</strong> — 웹 브라우저에서
      <a href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noopener">docker.com/products/docker-desktop</a>
      에 접속해 <strong>Download for Windows (AMD64)</strong> 버튼을 누릅니다.</li>
    <li><strong>설치 실행</strong> — 내려받은 <code>Docker Desktop Installer.exe</code> 를 더블클릭합니다.
      설정 화면에서 <strong>“Use WSL 2 instead of Hyper-V”</strong> 체크는 <strong>켠 채로</strong> 두고
      <kbd>OK</kbd> 를 눌러 설치합니다. (몇 분 걸립니다.)</li>
    <li><strong>재시작</strong> — 설치가 끝나면 안내에 따라 PC를 <strong>재부팅</strong>합니다.</li>
    <li><strong>첫 실행</strong> — 바탕화면의 <strong>Docker Desktop</strong> 아이콘을 실행합니다.
      약관 동의(Accept) 화면이 나오면 동의하고, 로그인/계정 화면은 <strong>Skip(건너뛰기)</strong> 해도 됩니다.</li>
    <li><strong>준비 완료 확인</strong> — 창 왼쪽 아래의 <strong>고래 아이콘이 초록색</strong>이 되고
      <strong>“Engine running”</strong> 이라고 표시되면 준비가 끝난 것입니다.</li>
  </ol>
  <div class="box warn"><div class="h">“WSL 2 installation is incomplete” 메시지가 뜨면</div>
    <p>화면에 나온 링크로 <strong>WSL2 커널 업데이트</strong>를 설치한 뒤 Docker Desktop을 다시 켜면 됩니다.
    그래도 안 되면, <kbd>Windows</kbd> 키 → “PowerShell” 을 <strong>관리자 권한으로 실행</strong> 후 아래를 입력하고 재부팅하세요.</p>
    <div class="cmd"><pre>wsl --install</pre></div></div>
  <div class="box bad"><div class="h">“Virtualization not enabled / 가상화 사용 안 함”</div>
    <p>PC의 <strong>BIOS에서 가상화 기능(Intel VT-x 또는 AMD SVM)이 꺼져</strong> 있는 경우입니다.
    BIOS 진입 방법은 PC 제조사마다 다르므로, 이 경우에는 <strong>IT 담당자에게 “가상화 기능 활성화”를 요청</strong>하세요.</p></div>
</section>

<section id="load">
  <h2><span class="n">2</span>프로그램 불러오기</h2>
  <p class="lead">전달받은 이미지 파일 하나를 Docker에 등록하는 단계입니다.
  모델과 설정이 이미 파일 안에 들어 있어, 따로 넣거나 고칠 것이 없습니다.</p>
  <ol class="step">
    <li><strong>파일 배치</strong> — 받은 <code>trash-detector.tar</code> 를 원하는 위치(예: <code>C:\\trash</code>)에 둡니다.</li>
    <li><strong>그 폴더에서 터미널 열기</strong> — 파일이 있는 폴더를 열고, 위쪽 <strong>주소 표시줄</strong>을 클릭해
      <code>powershell</code> 입력 후 <kbd>Enter</kbd>. (검은 창이 뜹니다.)</li>
    <li><strong>이미지 등록</strong> — 아래를 입력하고 <kbd>Enter</kbd>. (파일명은 실제 이름에 맞추세요.)
      <div class="cmd"><pre>docker load -i trash-detector.tar</pre></div>
      잠시 뒤 <code>Loaded image: daim/trash-detector:0.1.0</code> 가 나오면 성공입니다.</li>
  </ol>
  <div class="box ok"><div class="h">한 번만 하면 됩니다</div>
    <p>이미지 등록은 최초 1회면 됩니다. 이후에는 아래 3번처럼 실행/정지만 반복하면 됩니다.</p></div>
</section>

<section id="run">
  <h2><span class="n">3</span>실행 / 종료</h2>
  <p class="lead">Docker Desktop 화면에서 버튼으로 실행·정지합니다. (명령어로 하는 방법은 아래 참고)</p>
  <h3>실행하기 — 최초 1회</h3>
  <ol class="step">
    <li><strong>Docker Desktop 실행</strong> 후 왼쪽 <strong>Images</strong> 탭을 엽니다.</li>
    <li><code>daim/trash-detector</code> 행 오른쪽의 <strong>Run</strong> 버튼을 누릅니다.</li>
    <li><strong>Optional settings</strong> 를 펼치고, <strong>Ports</strong> 의 <strong>Host port</strong> 칸에 <code>1235</code> 를 입력합니다.
      (컨테이너 포트 <code>8080</code> 에 연결됩니다.) 이름(Container name)에 <code>trash-detector</code> 를 넣어두면 나중에 찾기 쉽습니다.</li>
    <li><strong>Run</strong> 을 누르면 컨테이너가 시작됩니다.</li>
    <li>웹 브라우저에서 <a href="http://localhost:1235" target="_blank" rel="noopener"><code>http://localhost:1235</code></a> 로 접속하면 화면이 나옵니다.
      (다른 PC에서 볼 때는 <code>http://이PC의IP:1235</code>)</li>
  </ol>
  <h3>정지 · 다시 시작 (2회차부터)</h3>
  <p>Docker Desktop 왼쪽 <strong>Containers</strong> 탭에서 <code>trash-detector</code> 행의
  <strong>정지(Stop)</strong> / <strong>시작(Start)</strong> 버튼만 누르면 됩니다. 한 번 만들어두면 이후엔 이 버튼만 쓰면 됩니다.</p>
  <div class="box tip"><div class="h">명령어로 하고 싶다면 (선택)</div>
    <p>PowerShell에서도 실행할 수 있습니다.</p>
    <div class="cmd"><pre>docker run -d --name trash-detector --restart unless-stopped -p 1235:8080 daim/trash-detector:0.1.0</pre></div>
    <p>정지 <code>docker stop trash-detector</code> · 시작 <code>docker start trash-detector</code> ·
    기록 보기 <code>docker logs -f trash-detector</code></p></div>
  <div class="box tip"><div class="h">PC를 켜면 자동으로 돌게 하기 (무인 운전)</div>
    <p>위 <strong>명령어 방식</strong>으로 한 번 실행해두면(<code>--restart unless-stopped</code> 포함) 재부팅 후에도 자동으로 다시 시작됩니다.
    단, Docker Desktop이 켜져 있어야 하며, <strong>Settings → General → “Start Docker Desktop when you sign in”</strong> 을 켜두면 전원만 넣어도 스스로 올라옵니다.</p></div>
</section>

<section id="dash">
  <h2><span class="n">4</span>대시보드 사용법</h2>
  <p class="lead">브라우저 화면(<code>http://localhost:1235</code>) 상단 버튼들의 역할입니다.</p>
  <table>
    <tr><th>버튼 / 영역</th><th>설명</th></tr>
    <tr><td><strong>시작 / 중지</strong></td><td>자동 판정과 PLC 신호 전송을 켜고 끕니다. 평소 운전 중에는 <strong>시작</strong> 상태로 둡니다.</td></tr>
    <tr><td><strong>PLC 테스트</strong></td><td>실제 투입물과 무관하게 코드(0·1·2·9)를 <strong>직접</strong> 보내 배선을 점검합니다. 이 모드에선 자동 판정이 잠시 멈춥니다. <strong>Conveyor RUN 상태 확인</strong> 버튼으로 PLC의 컨베이어 가동(D8001) 상태를 읽어볼 수 있습니다(전송 없음).</td></tr>
    <tr><td><strong>녹화</strong></td><td>최대 60초 영상을 녹화합니다. 정지 후 목록에서 mp4로 내려받을 수 있습니다.</td></tr>
    <tr><td><strong>설정</strong></td><td>카메라·PLC 주소, 판정 기준(민감도·쿨다운), 사용할 모델을 재시작 없이 바꿉니다. 저장하면 유지됩니다.</td></tr>
    <tr><td><strong>도움말</strong></td><td>지금 보고 있는 이 설명서입니다.</td></tr>
    <tr><td colspan="2" style="background:var(--panel2)"><strong>상단 표시등</strong> — 초록이면 정상 연결, 빨강이면 연결 끊김입니다. (영상 / PLC)</td></tr>
  </table>
  <h3>화면 구성</h3>
  <ul>
    <li><strong>왼쪽 — 라이브 뷰</strong>: 카메라 영상 위에 탐지 박스가 표시됩니다.</li>
    <li><strong>가운데 — 판정 결과</strong>: 현재 코드(0/1/2/9)와 라인 동작, 아래에 현재 값(검출 수·면적·컨베이어 RUN 상태·PLC 전송 성공/실패·사용 중인 모델)과 시스템 로그.</li>
    <li><strong>오른쪽 — 녹화 · 최근 판독</strong>: 녹화 파일과 최근에 잡힌 판독 캡처 8장.</li>
  </ul>
  <div class="box warn"><div class="h">컨베이어가 멈춰 있으면 신호를 보내지 않습니다</div>
    <p>PLC의 <strong>Conveyor RUN(D8001)</strong> 이 가동 중일 때만 판정 결과를 PLC로 보냅니다.
    컨베이어가 정지 상태면 화면 분석은 계속하되 결과는 전송하지 않습니다(오작동 방지).</p></div>
</section>

<section id="codes">
  <h2><span class="n">5</span>판정 코드 의미</h2>
  <p>프로그램이 PLC로 보내는 결과 코드와 라인 동작입니다.</p>
  <table>
    <tr><th>코드</th><th>판정</th><th>라인 동작</th></tr>
    <tr><td><span class="pill" style="background:var(--ok)">0</span></td><td>정상</td><td>정회전 (정상 가동)</td></tr>
    <tr><td><span class="pill" style="background:var(--warn)">1</span></td><td>부분 오염</td><td>알람 + 라인 정지 → 작업자 이물 제거 → 재시작</td></tr>
    <tr><td><span class="pill" style="background:var(--bad)">2</span></td><td>많이 오염</td><td>알람 + 2번 컨베이어 역회전 → NG 배출 → 재시작</td></tr>
    <tr><td><span class="pill" style="background:var(--bad)">9</span></td><td>판정 실패</td><td>화면에 표시만 (카메라·추론 오류 시) — <strong>PLC로는 전송하지 않음</strong></td></tr>
  </table>
  <div class="box tip"><div class="h">민감도 조절</div>
    <p>오탐/미탐이 잦으면 <strong>설정</strong> 창의 판정 기준을 조정하세요. 각 값의 자세한 의미는
    아래 <a href="#criteria">7. 부적합 판정 기준</a> 을 참고하세요. 재시작 없이 바로 반영됩니다.</p></div>
</section>

<section id="trouble">
  <h2><span class="n">6</span>문제 해결</h2>
  <table>
    <tr><th>증상</th><th>확인 / 조치</th></tr>
    <tr><td>대시보드가 안 열림</td>
        <td>① Docker Desktop 고래 아이콘이 <strong>초록(Engine running)</strong>인지 확인 ·
            ② <strong>Containers</strong> 탭에서 <code>trash-detector</code> 가 <strong>running(초록)</strong>인지 확인 ·
            ③ 주소가 <code>http://localhost:1235</code> 맞는지 확인.</td></tr>
    <tr><td>느리게 도는 것 같음</td>
        <td>이 프로그램은 <strong>CPU로 동작</strong>하므로 GPU PC보다 fps가 낮습니다(정상). 화면이 조금 느려도 판정은 정상 동작합니다.</td></tr>
    <tr><td>영상 표시등이 빨강</td>
        <td>카메라 IP·아이디·비밀번호·스트림 경로를 <strong>설정</strong> 창에서 재확인. 같은 망인지
            (<code>ping</code>)와 카메라 전원을 확인.</td></tr>
    <tr><td>PLC 표시등이 빨강</td>
        <td>PLC IP·포트(2004), 랜선/스위치, 같은 대역인지 확인. <strong>PLC 테스트 → Conveyor RUN 상태 확인</strong>으로 통신 점검.</td></tr>
    <tr><td>포트 충돌(<code>port is already allocated</code>)</td>
        <td>실행할 때 <strong>Host port</strong> 를 다른 값(예: <code>1236</code>)으로 바꿔 다시 Run.</td></tr>
    <tr><td>그 밖의 원인 파악</td>
        <td><strong>Containers</strong> 탭 → <code>trash-detector</code> 클릭 → <strong>Logs</strong> 탭에서 실시간 기록을 보면
            대부분 원인이 메시지로 나옵니다. (명령어: <code>docker logs -f trash-detector</code>)</td></tr>
  </table>
</section>

<section id="criteria">
  <h2><span class="n">7</span>부적합 판정 기준</h2>
  <p class="lead">프로그램은 최근 2초 동안 화면에서 (1) 검출된 쓰레기 <strong>개수(count)</strong>와
  (2) 쓰레기가 차지한 <strong>면적 비율(area, %)</strong> 을 봅니다. 두 값을 각각 등급으로 나누고,
  <strong>둘 중 더 심한 쪽</strong>으로 최종 판정합니다.</p>
  <table>
    <tr><th>기준</th><th>정상 (0)</th><th>부분오염 (1)</th><th>많이오염 (2)</th></tr>
    <tr><td><strong>개수</strong> (count)</td><td>low 미만</td><td>low 이상 ~ high 미만</td><td>high 이상</td></tr>
    <tr><td><strong>면적 %</strong> (area)</td><td>low 미만</td><td>low 이상 ~ high 이하</td><td>high 초과</td></tr>
  </table>
  <p>예를 들어 개수 기준이 low=1·high=2 이면 → <strong>0개는 정상</strong>, <strong>1개는 부분오염</strong>, <strong>2개 이상은 많이오염</strong>입니다.
  개수 판정과 면적 판정이 다르면 <strong>더 심한 쪽</strong>을 따릅니다 (예: 개수=부분오염, 면적=많이오염 → 최종 <strong>많이오염</strong>).</p>

  <h3>설정 창의 값 (설정 → 판정 기준)</h3>
  <table>
    <tr><th>항목</th><th>뜻</th><th>기본값</th></tr>
    <tr><td>confidence 임계값</td><td>이 값 이상 확신하는 검출만 인정 (높일수록 덜 민감)</td><td>0.25</td></tr>
    <tr><td>부분오염 기준 — count low</td><td>이 개수부터 ‘부분오염’으로 본다</td><td>1</td></tr>
    <tr><td>많이오염 기준 — count high</td><td>이 개수부터 ‘많이오염’으로 본다</td><td>2</td></tr>
    <tr><td>부분오염 기준 — area low (%)</td><td>이 면적%부터 ‘부분오염’으로 본다</td><td>0.3</td></tr>
    <tr><td>많이오염 기준 — area high (%)</td><td>이 면적%부터 ‘많이오염’으로 본다</td><td>3.0</td></tr>
    <tr><td>cooldown (초)</td><td>한 번 신호를 보낸 뒤, 다음 신호까지 최소 대기 시간</td><td>8</td></tr>
    <tr><td>부분오염 확인 대기 (초)</td><td>부분오염 감지 후 이 시간 동안 더 지켜봐서 ‘많이오염’이 나오면 격상해 전송</td><td>0.5</td></tr>
  </table>
  <div class="box tip"><div class="h">언제 조정하나요</div>
    <p>작은 부스러기까지 잡혀 오탐이 잦으면 <code>confidence</code> 나 <code>area low</code> 를 <strong>올리고</strong>,
    반대로 실제 쓰레기를 놓치면 <strong>낮추세요</strong>. 라인이 너무 자주 멈추면
    <code>count high</code>·<code>area high</code> 를 올려 ‘많이오염’을 덜 민감하게 만들 수 있습니다.
    저장하면 재시작 없이 즉시 반영됩니다.</p></div>
  <div class="foot">컨베이어 부적합 탐지 — PC 추론 · 문제가 지속되면 이 화면(로그 포함)을 캡처해 담당자에게 전달하세요.</div>
</section>

</main>
</div>

<script>
// 코드 블록 복사 버튼
document.querySelectorAll('.cmd').forEach(function(box){
  var pre=box.querySelector('pre'); if(!pre) return;
  var b=document.createElement('button'); b.className='copy'; b.textContent='복사';
  b.onclick=function(){
    navigator.clipboard.writeText(pre.textContent).then(function(){
      b.textContent='복사됨'; b.classList.add('done');
      setTimeout(function(){b.textContent='복사'; b.classList.remove('done');},1200);
    });
  };
  box.appendChild(b);
});
// 목차 현재 위치 강조
var links=[].slice.call(document.querySelectorAll('#toc a'));
var map={}; links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a;});
var obs=new IntersectionObserver(function(es){
  es.forEach(function(e){
    if(e.isIntersecting){
      links.forEach(function(a){a.classList.remove('on');});
      var a=map[e.target.id]; if(a) a.classList.add('on');
    }
  });
},{rootMargin:'-45% 0px -50% 0px'});
document.querySelectorAll('main section').forEach(function(s){obs.observe(s);});
</script>
</body></html>
"""
