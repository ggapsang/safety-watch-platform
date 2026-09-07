연결 IP / PORT
- 카메라 : 192.168.100 / 554
- PLC : 192.168.0.10 / 2004
- PC :192.168.0.11 (255.255.255.0)l


# 컨베이어 부적합(쓰레기) 탐지 — PC 추론 앱

아이에스에코솔루션 사이트용. 카메라는 RTSP 영상만 보내고, PC(RTX 4060)가 추론·판정하여
결과 코드(0/1/2)를 **XGT 전용 프로토콜(TCP 2004)** 로 LS PLC 에 전달한다.

```
[카메라] --RTSP(H.264)--> [PC/GPU] --XGT(TCP 2004)--> [PLC XBC-DN64H] --> [컨베이어]
                             ├ (1) 영상 수신 (최신 프레임 우선, 자동 재연결)
                             ├ (2) 전처리 (letterbox 640, /255, RGB, CHW)
                             ├ (3) 추론 (ONNX Runtime CUDA | TorchScript CUDA)
                             ├ (4) 후처리 (conf + NMS + ROI 필터)
                             ├ (5) 판정 (2초 window + 9매트릭스 + cooldown)
                             ├ (6) PLC 제어 (D8100 PC Ready, D8101 결과 코드)
                             └ (7) 대시보드 (라이브뷰 + 결과 + PLC 상태)
```

배경·결정 히스토리는 `문서/00_프로젝트_컨텍스트_및_히스토리.md`, 요구사항은
`문서/아이에스에코솔루션_PC추론_탐지앱_상세기획안.md` 참조.

---

## 1. 모델 규격 (파일에서 직접 검증됨)

`model_files/best_trash.torchscript.onnx` 그래프를 파싱해 확인한 결과:

| 항목 | 값 |
|:---|:---|
| 입력 | `images` · **float32** · `[1, 3, 640, 640]` (배치 1 고정) → `/255` 정규화 필요 |
| Detect | `model.105` 에 **Sigmoid + grid decode 포함** → `Concat_6` = `[1, 25200, 9]` (nc=4) |
| 라벨 통일 | `Slice([..., :5])` + `ReduceMax(4클래스 score, keepdims)` → `Concat(axis=-1)` |
| 출력 | `output` · float32 · **`[1, 25200, 6]`** = `[cx, cy, w, h, obj, cls]`, 입력(640) 픽셀 좌표 |

**→ `infer_mode=decoded` 확정.** 앱은 conf 필터 + NMS 만 하면 되고 anchor decode 는 불필요하다.
4물체 → `trash` 단일 라벨 통일도 그래프 안(ReduceMax)에서 끝나 있어 앱은 단일 클래스로만 처리한다.
(raw logit 모델을 나중에 받을 경우를 대비해 `MODEL_INFER_MODE=raw` 경로도 구현해 두었다.)

---

## 2. 빠른 시작 (로컬)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # GPU 없으면 onnxruntime-gpu 대신 onnxruntime
copy .env.example .env                   # 카메라 URL / PLC IP 채우기

python main.py --probe                   # ① 모델 로드 + 출력 shape 진단 (카메라·PLC 불필요)
python main.py --source sample.mp4 --no-plc --show    # ② 영상만으로 파이프라인 확인
python main.py                           # ③ 상시 운전 (대시보드 http://localhost:8080)
```

자체 검증 테스트(외부 의존성 없이 numpy 만으로 동작):

```powershell
python -m unittest discover -s tests -v
```

실제 PLC 없이 PLC 경로를 리허설하려면 모의 XGT 서버를 띄운다:

```powershell
python tools\mock_plc.py --port 20004          # 터미널 A
$env:PLC_HOST="127.0.0.1"; $env:PLC_PORT="20004"
python main.py --source sample.mp4             # 터미널 B
```

모의 서버는 Company ID / source 바이트 / BCC / body 레이아웃을 실제로 검증한 뒤 응답하므로,
앱이 보내는 바이트가 규격과 다르면 그 자리에서 드러난다.

## 3. Docker (운영 배포)

```bash
cp .env.example .env        # 값 채우기
docker compose up -d --build
docker compose logs -f
```

- `network_mode: host` — 카메라/PLC 가 같은 산업망이므로 NAT 회피.
- `./model_files` 를 read-only 볼륨으로 주입(이미지에 모델을 굽지 않음).
- `restart: unless-stopped` + `/healthz` 헬스체크.

---

## 4. 설정

전부 환경변수(`.env`)로 주입한다. 전체 목록과 설명은 `.env.example` 참조. 주요 항목:

| 변수 | 기본값 | 설명 |
|:---|:---|:---|
| `VIDEO_SOURCE` | — | RTSP URL / 파일 / 웹캠 인덱스. **자격증명은 여기에만** |
| `MODEL_BACKEND` | `onnx` | `onnx` \| `torchscript` |
| `MODEL_DEVICE` | `cuda` | CUDA 불가 시 자동 CPU 폴백 |
| `MODEL_INFER_MODE` | `decoded` | `decoded` \| `raw` \| `auto`(첫 프레임 자동 판별) |
| `CONF_THRESHOLD` / `IOU_THRESHOLD` | `0.25` / `0.45` | |
| `COUNT_LOW` / `COUNT_HIGH` | `1` / `2` | |
| `AREA_LOW` / `AREA_HIGH` | `0.3` / `3.0` | **단위 %** — 아래 주의 |
| `COOLDOWN_SEC` | `8` | 최소 1초 |
| `WRITE_ZERO_ON_NORMAL` | `true` | 정상 복귀 시 0(정회전) write |
| `FAULT_AFTER_SEC` | `5` | 이 시간 이상 영상/추론 실패 시 코드 9 |
| `ROI_POLYGON` | 없음 | `x1,y1;x2,y2;…` (0~1 정규화 또는 픽셀) |
| `PLC_ENABLED` / `PLC_HOST` | `true` / `192.168.5.199` | |
| `DASHBOARD_PUBLISH_PORT` | `1235` | 호스트 게시 포트 |

> **⚠ `AREA_*` 단위**: 설계 문서의 기본값 `low=0.3, high=3.0` 은 0~1 비율로 해석하면
> 30% / 300% 가 되어 `H` 가 영원히 나오지 않는다. 따라서 이 앱은 `area` 를 **퍼센트(%)** 로
> 계산한다(`Σbbox면적 / 분모 × 100`). 원본 daim_detector 가 비율(0~1)을 썼다면
> `AREA_UNIT=ratio` 로 바꾸고 임계값을 맞춰야 한다. **현장 튜닝 전에 확인 필요.**

---

## 5. 판정 로직 (daim_detector 이식)

```
count = 검출 수,  area = Σbbox면적 / (ROI 또는 프레임 면적) × 100 [%]
→ 최근 2초 sliding window(시간 기반, fps 무관) 의 max_count, max_area
→ count: <low → L / >=high → H / 그 외 M      (상한 경계 '포함')
   area: <low → L / > high → H / 그 외 M      (상한 경계 '미포함')
→ 9매트릭스 "L_L".."H_H"
→ severity = max(count_level, area_level) = 0 정상 / 1 부분오염 / 2 많이오염
→ cooldown(8초) 판단, 단 카테고리 변경 시 우회 → D8101 write
```

`L_L`(정상)은 기본적으로 **write 스킵**(설계 문서 원칙, PLC 는 직전 코드 유지).
`WRITE_ZERO_ON_NORMAL=true` 로 두면 정상 복귀 시 **1회만** 0 을 쓴다.

## 6. PLC IF 맵 (`문서/폐기물 분리기_PC_PLC_IF_map_Rev_260703.xlsx` 기준)

**PC 는 write 만 한다.** `D8000`/`D8001` 읽기는 사용하지 않는다.

| 방향 | 디바이스 | 이름 | 값 |
|:---:|:---:|:---|:---|
| PC → PLC (write) | `%DW8100` | PC Ready | 앱 기동 1 / 종료 0 |
| PC → PLC (write) | `%DW8101` | 판정 결과 코드 | 아래 표 |

| 코드 | 판정 | 라인 동작 |
|:---:|:---|:---|
| 0 | 정상 | 정회전 |
| 1 | 부분오염 | 알람 + 라인 정지 → 작업자 이물 제거 → 재시작 |
| 2 | 많이오염 | 알람 + 2번 컨베이어 역회전 → NG 받이 배출 → 재시작 |
| 9 | 판정실패(카메라 오류) | 알람 + 안전 정지 또는 수동 확인 |

- **코드 0 은 '정회전'이라는 능동 명령**이다. 따라서 정상 복귀 시 0 을 반드시 쓴다
  (`WRITE_ZERO_ON_NORMAL=true` 가 기본). 안 쓰면 라인이 정지/역회전 상태에 물린 채 남는다.
- **코드 9** 는 영상 수신이나 추론이 `FAULT_AFTER_SEC`(기본 5초) 이상 실패하면 **1회만** 전송한다.
  알람 코드라 반복 전송하지 않으며, 복구되면 다음 판정을 즉시 전송해 라인을 되살린다.

XGT 프레임(헤더 20byte + BCC, Read `0x5400` / Write `0x5800`, Word `0x0200`)은
`app/xgt_client.py` 에 구현했고 바이트 레이아웃은 `tests/test_app.py` 에서 검증한다.
IO 실패 시 close 후 **1회 자동 재연결**, `ECONNREFUSED` 는 20→200ms backoff.

---

## 7. 대시보드

`http://<PC>:1235` (호스트 게시 포트 = `DASHBOARD_PUBLISH_PORT`)

3단 구성이며 외부 CDN 의존이 없다.

| 영역 | 내용 |
|:---|:---|
| **좌** | 라이브 뷰(MJPEG) + 탐지 박스 + ROI 폴리곤 + 결과 오버레이 |
| **중앙 위** | 판정 결과 코드(0/1/2/9) · 한글 라벨 · 라인 동작 · 현재 값(9매트릭스, max_count, max_area, PLC write 성공/실패) |
| **중앙 아래** | **시스템 로그** — PLC write, 판정, 재연결 + 검출 상세 |
| **우** | **녹화**(최대 60초, 다운로드) · **최근 판독 캡처 8장**(최신순, 오래된 것부터 밀어냄) |
| **우상단** | ⚙ **설정** — 카메라/PLC/판정 기준을 재시작 없이 변경 |

### 7.1 검출 상세 로그 — '후처리 전에 무엇으로 봤는지'

```
#1 conf=0.293 (obj=0.293 cls=0.999) box=(751,1595)-(1189,2150)
   | 후처리전=cls3 [cls0=0.000 cls1=0.002 cls2=0.004 cls3=0.999]
```

- `conf` = 최종 confidence = `obj × cls`
- 이 모델은 그래프 안에서 **4클래스를 `ReduceMax` 로 합쳐** trash 단일 출력을 만든다.
  최종 출력만으로는 원래 어떤 물체였는지 알 수 없다.
- 그래서 앱은 기동 시 **`ReduceMax` 직전 텐서(`[1,25200,9]`)를 그래프 출력에 추가**해
  4클래스 원본 점수와 argmax 를 되살린다 (`EXPOSE_RAW_CLASSES=true`, 기본값).
  - `onnx` 패키지가 없거나 텐서를 못 찾으면 경고만 남기고 이 표시만 생략된다.
  - 클래스 이름은 모델에 없다. `CLASS_NAMES=병,캔,비닐,종이` 로 지정하면 그 이름으로 표시된다.
  - TorchScript 백엔드에서는 지원하지 않는다(ONNX 전용).

### 7.2 설정 (재시작 불필요)

| 그룹 | 항목 |
|:---|:---|
| 카메라 | IP · 포트 · ID · 비밀번호 · 스트림 경로 → 저장 시 RTSP 재연결 |
| PLC | IP · 포트 → 저장 시 재접속 후 `D8100=1` 재전송 |
| 판정 | confidence · NMS IoU · count low/high · area low/high(%) · **cooldown(초)** |

- 저장하면 `runtime/settings.json`(볼륨 마운트)에 남아 **재시작 후에도 유지**된다.
- 잘못된 값(`count_low > count_high` 등)은 400 으로 거부되고 기존 설정이 유지된다.
- `GET /api/settings` 응답에는 RTSP 비밀번호를 담지 않는다(대시보드에 인증이 없다).
  비밀번호 칸을 비워두고 저장하면 기존 값이 유지된다.

### 7.3 녹화

- ● 녹화 버튼 → **최대 60초**, 도달 시 자동 정지. 정지 후 목록에서 mp4 다운로드.
- 1280 폭으로 축소 저장(4K 원본은 파일이 과도하게 커진다), 코덱 `mp4v`, 오버레이 포함.
- 파일은 호스트 `recordings/` 에 그대로 쌓이며 **최근 5개**만 보관한다.

### 7.4 엔드포인트

```
GET  /                  대시보드            GET  /video             MJPEG 스트림
GET  /api/status        현재 상태           GET  /api/logs?since=N  시스템 로그(증분)
GET  /api/captures      캡처 메타 8장       GET  /capture/{id}.jpg  캡처 이미지
GET  /api/settings      현재 설정           POST /api/settings      설정 변경
POST /api/record/start  녹화 시작           POST /api/record/stop   녹화 정지
GET  /api/recordings    녹화 목록/상태      GET  /recording/{name}  mp4 다운로드
GET  /healthz           헬스체크
```

> ⚠ 대시보드에는 **인증이 없다.** 설정 변경·녹화 API 가 열려 있으므로 신뢰된 산업망
> 안에서만 노출할 것. 외부에 열어야 한다면 리버스 프록시로 인증을 앞단에 둘 것.

---

## 8. 파일 구성

```
main.py                 진입점 · 추론 루프 · 오버레이 · CLI
app/config.py           환경변수 설정 (+ .env 로더)
app/video.py            RTSP 수신 스레드 (최신 프레임 우선, 재연결)
app/detector.py         백엔드 추상화 · letterbox/stretch · NMS · ROI · raw decode
app/trash_logic.py      2초 sliding window · 9매트릭스 · cooldown
app/xgt_client.py       XGT 전용 프로토콜 클라이언트
app/plc.py              IF 맵 래퍼 (D8000/8001/8100/8101)
app/state.py            공유 상태 · 로그 링버퍼 · 캡처 8장 링버퍼
app/recorder.py         최대 60초 mp4 녹화
app/dashboard.py        FastAPI 대시보드 (MJPEG · 로그 · 캡처 · 설정 · 녹화)
tests/test_app.py       자체 검증 37건 (외부 의존성 없음)
tools/mock_plc.py       모의 XGT PLC 서버 (실 PLC 없이 왕복 리허설)
runtime/settings.json   대시보드에서 저장한 설정 (볼륨, gitignore)
recordings/             녹화 mp4 (볼륨, 최근 5개, gitignore)
```

## 8-1. 현재 검증 상태

| 항목 | 상태 |
|:---|:---|
| 모델 입출력 규격 (`[1,25200,6]`, decoded) | ✅ 그래프 파싱 + `--probe` 실행으로 확인 |
| 전처리·NMS·좌표 역변환·ROI·판정·XGT 프레임 | ✅ 단위 테스트 21건 통과 |
| 영상 → 추론 → 판정 루프 | ✅ 실카메라로 관통 확인 |
| 대시보드 전체 엔드포인트(로그/캡처/설정/녹화 포함) | ✅ 실행 중 컨테이너에 HTTP 호출로 확인 |
| 후처리 전 4클래스 노출 | ✅ `final.cls == max(raw[5:9])` 대조 + 실운전 로그 확인 |
| 녹화 mp4 | ✅ 1280×720 21.8fps 재생 확인, 호스트 `recordings/` 에 생성 |
| XGT 프레임/재연결 | ✅ 모의 PLC 서버 + 실 PLC 로 확인 |
| **실제 RTSP 카메라 수신** | ✅ 192.168.5.52 profile2, 3840×2160 @30fps (Digest 인증 필요) |
| **실제 PLC write** | ✅ 192.168.5.199:2004, D8100/D8101 write 성공 (실패 0) |
| **GPU(CUDA) 추론 · Docker** | ✅ RTX 4060, CUDAExecutionProvider, 약 20fps / 추론 22~24ms |
| **현장 컨베이어 정확도** | ⬜ 미검증 — 카메라가 아직 컨베이어를 보고 있지 않음 |

---

## 9. 현장 통합 전 확인 사항

1. **`--probe` 로그**로 출력 shape 재확인 (`[1, 25200, 6]`, obj 범위 0~1 → decoded).
2. **`AREA_UNIT`** — % 인지 비율인지 원본 daim_detector 값과 대조 (4장 경고 참조).
3. **`WRITE_ZERO_ON_NORMAL`** — 현장 PLC 가 D8101 을 latch 로 소비하는지 확인 후 확정.
4. **박스 위치가 어긋나면** `MODEL_LETTERBOX`(letterbox↔stretch) → `MODEL_INFER_MODE` 순으로 의심.
5. **네트워크** — 컨테이너(host 모드)에서 카메라 RTSP·PLC 2004 포트 도달 가능한지.
6. **ROI** — 미설정 시 area 분모가 프레임 전체 면적이다. ROI 를 넣으면 분모가 폴리곤 면적으로
   바뀌므로 `AREA_*` 임계값을 다시 튜닝해야 한다.
