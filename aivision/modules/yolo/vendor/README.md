# vendor/yolov7 — 외부 소스 (수정 최소)

학습·평가·ONNX 내보내기의 실행 주체입니다. 원본은
[WongKinYiu/yolov7](https://github.com/WongKinYiu/yolov7) 이고, `refs/YOLO_코드공유/05_yolov7_source`
(사내에서 py3.13 / torch2.6 / numpy2.x 환경에 맞춰 패치한 판)에서 **학습·평가·내보내기에
실제로 필요한 것만** 옮겼습니다. 27개 파일 · 567KB.

가져오지 않은 것: `cfg/baseline`·`cfg/deploy` (쓰지 않는 모델 구조), `detect.py`·`hubconf.py`
(추론은 우리 `inference.py` 가 함), `train_aux.py`, `scripts/`, `utils/aws`,
`utils/google_app_engine`, 데이터셋 yaml (데이터셋은 사용자가 볼륨에 넣습니다).

`utils/wandb_logging` 은 쓰지 않지만 `train.py` 가 최상단에서 import 하므로 남겨 두었습니다.
wandb 패키지가 없으면 스스로 비활성화됩니다.

## 우리가 손대지 않는다

이 폴더는 **가져온 그대로 둡니다.** 고쳐야 할 것이 생기면 우리 코드(`training.py`,
`inference.py`)에서 감싸고, 그래도 안 되면 여기를 고친 뒤 무엇을 왜 고쳤는지 아래에 적습니다.
그렇게 해야 나중에 원본을 새 버전으로 갈아 끼울 수 있습니다.

`_compat.py` 는 원본에 없는 파일입니다(사내 패치). `train.py`·`test.py`·`export.py` 최상단에서
import 되며 세 가지를 되돌립니다 — numpy 2.x 에서 삭제된 `np.int/float/bool` 별칭,
`np.trapz` -> `np.trapezoid`(mAP 적분에서 필요), torch 2.6 의 `torch.load(weights_only=True)`
기본값. 이 패치 없이는 학습이 시작되지 않거나 평가 단계에서 죽습니다.

## 이 소스가 요구하는 것

`requirements.txt` 에 있는 것 중 vendor 때문에 들어간 것들이다.

| 패키지 | 왜 |
|---|---|
| `torch` · `torchvision` | 학습·평가 본체. Dockerfile 에서 CPU/CUDA 판을 골라 설치한다 |
| `requests` | `models/common.py` 가 최상단에서 import 한다 (가중치 자동 다운로드) |
| `thop` | FLOPs 표시. 없어도 돌지만 학습 로그에 모델 크기가 안 찍힌다 |
| `PyYAML` · `tqdm` · `matplotlib` · `scipy` · `pandas` · `seaborn` | 학습 루프·플롯·지표 |
| `tensorboard` | `train.py` 가 SummaryWriter 를 무조건 만든다 |
| `onnx` · `onnx-simplifier` | `export.py` |

`onnx_graphsurgeon` 은 없어도 된다. `export.py` 가 END2END(NMS 내장) 옵션에만 쓰고,
우리는 `--grid` 만 쓰기 때문이다. 없으면 경고 한 줄이 찍히고 넘어간다.

## 라이선스

YOLOv7 은 **GPL-3.0** 입니다. `LICENSE.md` 를 함께 두었습니다. 이 모듈을 별도 컨테이너로
분리해 두는 이유 중 하나이기도 합니다 — 플랫폼 코어와는 프로세스가 다르고 MQTT·REST 로만
만납니다. 외부에 납품·배포할 때는 이 부분을 먼저 확인해야 합니다.
