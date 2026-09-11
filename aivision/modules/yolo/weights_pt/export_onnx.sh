#!/usr/bin/env bash
#
# weights.pt -> weights.onnx  (일회성 변환)
#
# **왜 스크립트가 여기 있나.** 객체감지 모듈은 ONNX 만 받는다. torch 와 학습 코드를
# 담으면 이미지가 3GB 를 넘어서 걷어냈기 때문이다(refs/yolov7-training 참조). 그런데
# .pt 를 ONNX 로 바꾸려면 바로 그 torch 가 필요하다. 그래서 **모듈 이미지가 아니라
# 일회용 컨테이너**에서 돌린다. 이 PC 에 torch 를 설치하지 않아도 되고, 다 쓰면 흔적
# 없이 사라진다.
#
# 실제 변환은 export_onnx.py 가 한다. 이 파일은 그것을 돌릴 환경을 만들 뿐이다.
#
# 사용:
#   bash export_onnx.sh [입력.pt] [이미지크기]
#   bash export_onnx.sh weights.pt 640
#
# 결과는 같은 폴더의 .onnx 다. 모듈 화면(11990)에서 올리면 클래스 표까지 만들어 준다.
#
# **yolov7 계열 가중치라면** 이 길이 아니다. 그쪽은 체크포인트가 yolov7 클래스를
# 참조하는 피클이라 그 소스가 있어야 열린다. export_onnx.py 가 알아보고 알려 주며,
# 그때는 아래처럼 돌린다(저장소 루트에서):
#
#   docker run --rm -v "$PWD:/repo" -w /repo/refs/yolov7-training/vendor/yolov7 \
#     python:3.12-slim bash -lc "
#       pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision &&
#       pip install 'numpy<3' opencv-python-headless PyYAML tqdm requests matplotlib \
#                   scipy pandas seaborn onnx onnx-simplifier &&
#       python export.py --weights /repo/<가중치경로> --img-size 640 640 \
#                        --batch-size 1 --grid --simplify --device cpu"

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEIGHTS="${1:-weights.pt}"
IMGSZ="${2:-640}"

if [ ! -f "$HERE/$WEIGHTS" ]; then
  echo "가중치를 찾을 수 없습니다: $HERE/$WEIGHTS" >&2
  exit 1
fi

# CPU 판 torch 로 족하다. 한 번 돌리고 마는 일이라 몇 분이 더 걸려도 상관없고,
# CUDA 판은 받는 데만 GB 단위가 든다.
#
# ultralytics 가 torch 를 끌고 오는데, 기본 인덱스로 두면 CUDA 판(수 GB)이 딸려온다.
# CPU 판을 먼저 박아 두면 그 위에 얹힌다.
echo "변환을 시작합니다: $WEIGHTS (img-size $IMGSZ)"
echo "일회용 컨테이너에서 돕니다. 처음 한 번은 torch 를 받느라 몇 분 걸립니다."

MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$HERE:/work" \
  -w /work \
  python:3.12-slim \
  bash -lc "
    set -e
    apt-get update -qq && apt-get install -y -qq --no-install-recommends libglib2.0-0 >/dev/null
    pip install --quiet --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu torch torchvision
    pip install --quiet --no-cache-dir ultralytics onnx onnxslim onnxruntime
    # ultralytics 가 GUI 판 opencv 를 끌고 온다. 화면 없는 컨테이너에서는 X11
    # 라이브러리(libxcb)를 못 찾아 import 에서 죽는다. headless 판으로 갈아 끼운다 —
    # X11 을 깔아 주는 것보다 가볍고, 어차피 창을 띄울 일이 없다.
    pip uninstall -y -q opencv-python opencv-contrib-python 2>/dev/null || true
    pip install --quiet --no-cache-dir opencv-python-headless
    python export_onnx.py '$WEIGHTS' '$IMGSZ'
  "

OUT="$HERE/${WEIGHTS%.pt}.onnx"
if [ -f "$OUT" ]; then
  echo "완료: $OUT ($(du -h "$OUT" | cut -f1))"
else
  echo "변환은 끝났는데 결과 파일이 없습니다. 위 로그를 보십시오." >&2
  exit 1
fi
