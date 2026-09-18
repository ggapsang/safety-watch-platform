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
# **torch 는 한 번만 받는다.** 예전에는 python:3.12-slim 에 그때그때 pip install 을
# 돌려서 변환할 때마다 torch 를 통째로 다시 받았다(몇 분). 지금은 Dockerfile 로 한 번
# 구워 두고 그 이미지를 다시 쓴다 — 두 번째부터는 몇 초다.
#
# 사용:
#   bash export_onnx.sh [입력.pt] [이미지크기]
#   bash export_onnx.sh weights.pt 640
#   bash export_onnx.sh --rebuild weights.pt 640   변환용 이미지를 새로 만든다
#
# 결과는 같은 폴더의 .onnx 다. 모듈 화면(11990)에서 올리면 클래스 표까지 만들어 준다.
#
# **.pt 는 저장소에 없다**(.gitignore). 학습 쪽에서 받아 이 폴더에 두고 돌리면 된다 —
# 남겨 두는 것은 변환 방법과 결과물(.onnx)이고, 40MB 짜리 가중치가 버전마다 쌓이면
# 클론이 무거워진다.
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
IMAGE="aivision-onnx-export:latest"

REBUILD=0
if [ "${1:-}" = "--rebuild" ]; then REBUILD=1; shift; fi

WEIGHTS="${1:-weights.pt}"
IMGSZ="${2:-640}"

if [ ! -f "$HERE/$WEIGHTS" ]; then
  echo "가중치를 찾을 수 없습니다: $HERE/$WEIGHTS" >&2
  exit 1
fi

# 변환용 이미지를 한 번만 만들고 다시 쓴다. 이 검사가 없으면 변환할 때마다 torch 를
# 통째로 다시 받는다 — 실제로 그렇게 몇 분씩 썼다.
if [ "$REBUILD" = "1" ]; then
  docker image rm -f "$IMAGE" >/dev/null 2>&1 || true
fi
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "변환용 이미지를 다시 씁니다: $IMAGE"
else
  echo "변환용 이미지를 만듭니다: $IMAGE"
  echo "처음 한 번만입니다. torch 를 받느라 몇 분 걸립니다 — 다음부터는 몇 초입니다."
  MSYS_NO_PATHCONV=1 docker build -t "$IMAGE" "$HERE"
fi

echo "변환을 시작합니다: $WEIGHTS (img-size $IMGSZ)"

MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$HERE:/work" \
  "$IMAGE" "$WEIGHTS" "$IMGSZ"

OUT="$HERE/${WEIGHTS%.pt}.onnx"
if [ -f "$OUT" ]; then
  echo "완료: $OUT ($(du -h "$OUT" | cut -f1))"
else
  echo "변환은 끝났는데 결과 파일이 없습니다. 위 로그를 보십시오." >&2
  exit 1
fi
