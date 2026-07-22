# 아이에스에코솔루션 부적합(쓰레기) 탐지 — PC 추론 애플리케이션 상세 기획안

문서 버전: v1.0 · 작성일: 2026-07-22 · 대상 사이트: 아이에스에코솔루션(컨베이어 부적합 탐지)
배포 형태: **Docker 컨테이너 / RTX 4060 GPU 상시 가동** (카메라 임베디드 아님)

---

## 1. 배경 및 목적

### 1.1 문제

- 아이에스에코솔루션용 쓰레기 검출 모델을 카메라 NPU용 `.nb`로 변환하면 **양자화가 붕괴**한다. confidence가 0/100 극단으로만 튀어 실사용 불가.
- 해결 가능성 불투명 + 시연/검수 일정 확정 → 카메라 온디바이스 경로를 포기하고 우회한다.

### 1.2 결정

- 추론을 **카메라 → PC(GPU)** 로 이관한다. 카메라는 RTSP 영상만 송출, PC가 추론·판정·PLC 제어를 담당.
- **본 시스템은 카메라에 탑재되지 않는다.** Docker 컨테이너로 패키징하여 **RTX 4060 GPU가 장착된 PC에서 상시 가동**한다. 임베디드 NPU의 메모리/연산 제약이 없으므로 **전·후처리를 정확도 우선으로 충분히 무겁게 구성**할 수 있다.

### 1.3 목적

- 컨베이어 위 부적합(이물/쓰레기)을 검출하여 오염도 등급(0/1/2)을 산출하고 PLC에 전달, 컨베이어 제어에 활용.
- 검수/시연 시 대시보드로 탐지 동작을 실시간 시연.

---

## 2. 범위

### 2.1 포함 (In-scope)

- 카메라 RTSP 영상 수신
- GPU 추론(ONNX-CUDA 또는 TorchScript-CUDA)
- 전·후처리(letterbox, NMS, ROI 필터 등)
- 판정 로직(2초 window + 9매트릭스 + cooldown) — 기존 daim_detector 검증본 이식
- XGT LS 전용 프로토콜 PLC 제어(D8100/D8101)
- Docker 패키징 및 GPU 배포 구성
- 시연/검수용 모니터링 화면

### 2.2 제외 (Out-of-scope)

- 카메라 온디바이스(NPU) 추론 — 본 건 전환의 원인
- SUNAPI/IVA SUNAPI 연동, MQTT publish, ONVIF 메타데이터, 카메라 rule engine/OSD, self-restart 등 카메라 앱 전용 장치
- 쓰레기 모델의 NPU 양자화 안정화(별도 트랙)

---

## 3. 배포 환경 (핵심 전제)

| 항목 | 내용 |
|:---|:---|
| 실행 형태 | Docker 컨테이너 (상시 가동, `restart: unless-stopped`) |
| GPU | NVIDIA RTX 4060 (Ada, sm_89, VRAM 8GB) |
| CUDA/cuDNN | CUDA 12.4 + cuDNN 9 (베이스 이미지) |
| 컨테이너 GPU 접근 | nvidia-container-toolkit (`--gpus` / compose device reservation) |
| 네트워크 | 카메라(RTSP) · PLC가 동일 산업망 → host 네트워크 권장(NAT/포트 회피) |
| 설정 주입 | 환경변수 기반(카메라 URL, 모델 경로, PLC IP, 임계값 등) |
| 모델 주입 | 볼륨 마운트(`./models` → 컨테이너) |

> **함의**: 8GB VRAM + Ada 세대이므로 640 입력 단일 스트림 추론은 여유가 큼. FP16 추론, 프레임 스킵 없는 매 프레임 추론, 정밀 letterbox, 필요 시 상위 입력 해상도까지 감당 가능. 전·후처리를 임베디드용으로 축소할 이유가 없다.

---

## 4. 시스템 아키텍처

```
[카메라] --RTSP(H.264)--> [Docker/RTX 4060 PC] --XGT(TCP 2004)--> [PLC] --> [컨베이어]
                              │
                              ├─ (1) 영상 수신 (OpenCV/FFmpeg, 최신 프레임 우선)
                              ├─ (2) 전처리 (letterbox 640, /255, RGB, CHW)
                              ├─ (3) GPU 추론 (ONNX-CUDA | TorchScript-CUDA)
                              ├─ (4) 후처리 (conf 필터 + NMS + ROI 필터)
                              ├─ (5) 판정 (2초 window + 9매트릭스 + cooldown)
                              ├─ (6) PLC 제어 (D8100 PC Ready, D8101 결과코드)
                              └─ (7) 모니터링 (시연/검수용 화면)
```

- 카메라는 dumb sensor(영상 소스). 판단·제어의 두뇌는 전부 PC 컨테이너.
- 판정 로직·PLC 통신은 신규 설계가 아니라 **daim_detector 검증본을 이식**.

---

## 5. 기능 요구사항 (상세)

### F1. 영상 수신

- 입력: 카메라 RTSP 스트림(H.264).
- OpenCV FFmpeg 백엔드 사용. 수신 버퍼 최소화하여 **최신 프레임 우선**(지연 누적 방지).
- 수신 실패/끊김 시 자동 재연결(주기적 재시도).
- 요구: 실시간성 > 무손실. 프레임 드랍 허용.

### F2. 전처리 (GPU 여유 → 정밀도 우선)

- **letterbox 리사이즈** 640×640(비율 유지 + 패딩) 기본. 좌표 역변환 정확 처리.
  - 대안 stretch(직접 resize)도 설정으로 지원(카메라 앱과 동일 방식 비교용).
- RGB 변환, `/255` 정규화(모델 입력 dtype에 맞춤: float32 표준, uint8이면 정규화 생략).
- 색공간/채널 순서(CHW)·배치 차원 정리.
- (선택) FP16 입력/추론으로 처리량 향상 — 4060에서 정확도 손실 미미, 필요 시 활성화.

### F3. GPU 추론 — 백엔드 선택형

두 백엔드를 지원하고 설정으로 전환(6장 상세):

- **ONNX Runtime + CUDAExecutionProvider**: `best_trash.onnx` 사용.
- **TorchScript + CUDA**: `best_trash.torchscript.pt`를 `torch.jit.load` 후 GPU 로드. yolov7 레포 불필요.

공통 요구: CUDA 사용 불가 시 CPU로 자동 폴백(개발/장애 상황 대비).

### F4. 후처리

- 출력 파싱: **decoded 모드**(표준 YOLOv7 export, `[1,N,5+nc]` grid-decode+sigmoid 완료) → conf 필터 + NMS.
- **raw 모드**(raw logit `[1,25200,5+nc]`) → sigmoid + anchor decode 후 NMS(카메라 .nb 후처리 동일).
  - 최초 통합 시 출력 shape/샘플을 로그로 확인해 모드 확정(11장 참조).
- **ROI 필터**(선택): 컨베이어 영역 폴리곤 밖 검출 제거(point-in-polygon). 좌표는 설정값으로 관리(SUNAPI 미사용).
- 클래스는 단일(`trash`)로 취급(모델이 4물체를 내부 통합 완료).

### F5. 판정 로직 (설계 문서 그대로 이식 — 8장 상세)

- 매 프레임 `count`(검출 수), `area_ratio`(bbox 총면적 / ROI 또는 프레임 면적) 산출.
- **2초 sliding window**에서 `max(count)`, `max(area_ratio)`.
- count/area 각각 L/M/H 분류 → 9매트릭스 → `severity = max(count_level, area_level)` = 0/1/2.
- cooldown + 카테고리 변경 시 우회.

### F6. PLC 제어 (XGT — 9장 상세)

- XGT LS 전용 프로토콜(TCP 2004)로 `%DW` word write/read.
- 발송 시 `D8101`에 결과 코드(1/2) write. 앱 기동/정지 시 `D8100`(PC Ready) 1/0.
- (선택) `D8000`(PLC Ready)·`D8001`(Conveyor RUN)을 읽어 게이트 조건으로 사용.
- **정상(L_L) 처리 방침 확정 필요**: 설계 문서 기본은 write 스킵(PLC 직전값 유지). 컨베이어 정상 복귀 위해 0 write가 필요한지 현장 PLC 로직 확인 후 결정.

### F7. 모니터링 (시연/검수)

- 라이브 영상 + 탐지 박스 오버레이 + 현재 결과 코드(정상/부분오염/많이오염) + PLC 연결 상태.
- Docker headless 환경 → 화면 출력 방식 확정 필요(웹 대시보드 권장, 아래 12장).

---

## 6. 추론 백엔드 설계 (선택 사항)

| 기준 | ONNX Runtime (CUDA EP) | TorchScript (CUDA) |
|:---|:---|:---|
| 모델 파일 | `best_trash.onnx` | `best_trash.torchscript.pt` |
| 로딩 | ORT InferenceSession | `torch.jit.load` 한 줄 |
| 레포 의존 | 없음 | 없음(torch만) |
| 출력 형태 | export 옵션 따라 decoded/raw | 대개 decoded(Detect 포함) |
| 리스크 | export 시 decode/양자화 옵션 불일치 가능 | 상대적으로 export 변수 적음 |
| 이미지 크기 | onnxruntime-gpu만 | torch 포함(용량 큼) |

- 두 경로 모두 GPU에서 정상 동작. **TorchScript가 export 변수가 적어 초기 안정성 유리**, ONNX가 이미지가 가벼움.
- 권고: **초기 통합은 TorchScript로 빠르게 동작 확인 → 이후 운영 백엔드 확정.** (최종 선택은 통합 결과로 판단)
- 두 경로의 후처리는 동일 로직 공유(출력을 공통 배열로 정규화 후 처리).

---

## 7. 전·후처리 설계 상세 (정확도 우선)

- **letterbox 역변환**: 검출 좌표를 `(coord - pad) / ratio`로 원본 프레임 좌표계로 정확 복원. clip으로 프레임 경계 보정.
- **NMS**: 단일 클래스 기준 IoU NMS. 임계값 설정화(기본 0.45).
- **conf 임계값**: 기본 0.25(설정화). GPU 여유로 낮은 임계값 + NMS로 회수율 우선 후, 판정 로직의 window/cooldown이 노이즈 흡수.
- **ROI 필터(선택)**: 폴리곤 면적은 Shoelace로 계산, `area_ratio` 분모로 사용(미사용 시 프레임 전체 면적).
- (확장 여지) 4060 여유 활용: FP16 추론, 필요 시 입력 해상도 상향, 프레임 스킵 없는 매 프레임 처리. 정확도 이슈 시 카드가 있는 옵션들.

---

## 8. 판정 로직 상세 (daim_detector 2026-06-30 설계 + 07-01 개정 이식)

### 8.1 데이터 흐름

```
프레임 검출 결과
  → count = 검출 수
  → area_ratio = Σbbox면적 / (ROI 또는 프레임 면적)
  → 2초 sliding window 에 (count, area_ratio) 누적
  → max_count, max_area_ratio
  → count/area 각각 L/M/H 분류 → 9매트릭스 → severity(0/1/2)
  → cooldown 판단 → 발송 시 D8101 write
```

### 8.2 분류 규칙

- **count**: `< low` → L / `>= high` → H / 그 외 M. (2026-07-01: 상한 경계 포함 `>=`)
- **area**: `< low` → L / `> high` → H / 그 외 M. (경계 미포함 유지)
- 기본 임계값: count `low=1, high=2` / area `low=0.3, high=3.0`(전부 설정화).

### 8.3 severity 축약 및 코드

- `severity = max(count_level, area_level)`
- `0`=L_L(정상, skip) / `1`=하나라도 M(H 없음) / `2`=하나라도 H
- 결과 코드 의미: **0=정상 / 1=부분오염 / 2=많이오염**

### 8.4 cooldown

- 별도 `trash_cooldown_sec`(기본 8초, 최소 1초).
- L_L → 항상 skip + prev 초기화.
- 9매트릭스 카테고리 변경 → cooldown 우회(위협 수준 변화 즉시 반영).
- cooldown 중 + 동일 카테고리 → skip.

### 8.5 fps-윈도우 이식 주의

- 카메라는 5fps라 "10프레임 = 2초"였으나, PC fps는 다를 수 있음.
- → 윈도우를 **프레임 수 고정이 아닌 "최근 2초 시간 기반"**으로 구현하여 '2초' 의미 보존.

---

## 9. PLC 인터페이스 규격 (XGT LS)

### 9.1 IF 맵

| 방향 | 디바이스 | 이름 | 값/의미 |
|:---:|:---:|:---|:---|
| PLC → PC (read) | D8000 | PLC Ready | 0/1 |
| PLC → PC (read) | D8001 | Conveyor RUN | 0/1 |
| PC → PLC (write) | D8100 | PC Ready | 0/1 (앱 기동/정지) |
| PC → PLC (write) | D8101 | 결과 코드 | 0=정상 / 1=부분오염 / 2=많이오염 |

### 9.2 프로토콜

- XGT 전용 프로토콜, **TCP 2004**. XGB 계열 접속은 CPU_ANY 권장.
- Company Header(20 byte, BCC 포함) + Body(Read `0x5400`/Write `0x5800`, Word `0x0200`), `%DW####` 주소 표기.
- IO 실패 시 1회 자동 재연결. `ECONNREFUSED` backoff.
- (daim_detector `xgt_client.cc` 검증본을 그대로 이식.)

---

## 10. 비기능 요구사항

- **성능**: 640 입력 단일 스트림, 매 프레임 추론(프레임 스킵 없음) 목표. 4060 기준 충분.
- **안정성/복원력**: RTSP 끊김 자동 재연결, PLC IO 실패 재연결, CUDA 불가 시 CPU 폴백, 컨테이너 자동 재시작.
- **관측성**: fps/검출 수/판정 코드/PLC write 결과 로그. 최초 프레임 출력 shape 진단 로그.
- **설정성**: 카메라/모델/PLC/임계값 전부 환경변수로 주입(재빌드 없이 변경).
- **보안**: RTSP 자격증명은 환경변수/시크릿으로 관리(소스 하드코딩 금지).

---

## 11. 리스크 및 착수 전 확인 사항

1. **모델 출력 형태**(decoded vs raw): 첫 통합 시 출력 shape/값 확인 → `infer_mode` 확정. (표준 export면 decoded=NMS만.)
2. **전처리 정합**: onnx/torchscript가 float `/255`·letterbox 전제인지 확인. 박스 위치 어긋나면 전처리부터 점검.
3. **정상(L_L) write 방침**: PLC의 D8101 소비 로직 확인 → 스킵 유지 vs 0 write 확정.
4. **모니터링 방식**: Docker headless라 화면 직접 출력 불가 → 웹 대시보드 방식 확정 필요.
5. **네트워크 구성**: host 네트워크로 카메라·PLC 접근 가능한지(방화벽/서브넷) 확인.
6. **백엔드 확정**: TorchScript/ONNX 중 운영 백엔드 초기 통합 결과로 결정.

---

## 12. 개발 단계 / 마일스톤

| 단계 | 내용 | 산출물 |
|:---:|:---|:---|
| M1 | 파이프라인 관통: RTSP 수신 → GPU 추론 → 박스 출력(로그/로컬) | 동작 확인 |
| M2 | XGT PLC 연동: 더미 코드(0/1/2) D8101 write ↔ read-back 왕복 검증 | PLC 통신 검증 |
| M3 | 판정 로직 이식: 2초 window + 9매트릭스 + cooldown → 실코드 산출 | 판정 검증 |
| M4 | Docker/GPU 패키징: 컨테이너 빌드, GPU 접근, 환경변수/볼륨 구성 | 배포 이미지 |
| M5 | 모니터링(웹 대시보드): 라이브뷰 + 코드 + PLC 상태 | 시연 화면 |
| M6 | 현장 튜닝 + 시연 리허설: ROI/임계값/cooldown 조정 | 검수 대응 |

---

## 13. 향후 확장 여지

- FP16/TensorRT 등 가속 최적화(현재는 불필요 수준의 여유).
- 다중 카메라/다중 컨베이어 확장(스트림별 판정 인스턴스).
- 이벤트 이미지 저장/갤러리(감사 추적용, 필요 시).
- 쓰레기 모델 NPU 양자화 안정화 재도전(성공 시 온디바이스 회귀 옵션).
