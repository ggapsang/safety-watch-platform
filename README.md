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

`http://<PC>:8080`

- 라이브 뷰(MJPEG) + 탐지 박스 + ROI 폴리곤 오버레이
- 결과 코드(0/1/2)와 한글 라벨, 9매트릭스 카테고리, max_count / max_area
- 영상·PLC 연결 상태, 마지막 write 내역, fps / 추론 지연
- 엔드포인트: `/` `/video` `/api/status` `/healthz` (외부 CDN 의존 없음)

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
app/state.py            추론 루프 ↔ 대시보드 공유 상태
app/dashboard.py        FastAPI + MJPEG 대시보드
tests/test_app.py       자체 검증 21건 (외부 의존성 없음)
tools/mock_plc.py       모의 XGT PLC 서버 (실 PLC 없이 왕복 리허설)
```

## 8-1. 현재 검증 상태

| 항목 | 상태 |
|:---|:---|
| 모델 입출력 규격 (`[1,25200,6]`, decoded) | ✅ 그래프 파싱 + `--probe` 실행으로 확인 |
| 전처리·NMS·좌표 역변환·ROI·판정·XGT 프레임 | ✅ 단위 테스트 21건 통과 |
| 영상 → 추론 → 판정 루프 | ✅ 실카메라로 관통 확인 |
| 대시보드 `/` `/video` `/api/status` `/healthz` | ✅ 실행 중 앱에 HTTP 호출로 확인 |
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
