# .pt -> .onnx 변환 전용 이미지.
#
# **제품에는 들어가지 않는다.** 객체감지 모듈(mod-yolo)에는 torch 가 없다 — 학습·변환
# 의존성을 걷어내서 이미지를 GB 단위로 줄였고, 런타임은 onnxruntime 하나다. 24시간 떠
# 있는 추론 컨테이너에 몇 달에 한 번 쓰는 변환 도구를 얹을 이유가 없다.
#
# 그렇다고 매번 받을 이유도 없다. 예전에는 python:3.12-slim 에서 pip install 을 그때그때
# 돌려서, 변환 한 번에 torch 를 통째로 다시 받았다(몇 분). 이미지로 한 번 구워 두면
# 그 다음부터는 몇 초다. 기계마다 한 번만 만들면 된다.
#
#   docker build -t aivision-onnx-export .
#   docker run --rm -v "$PWD:/work" aivision-onnx-export weights.pt 640
#
# 보통은 export_onnx.sh 가 알아서 만들고 돌린다.
#
# 주의: ultralytics 는 AGPL-3.0 이다. 이 변환에만 쓰고 제품 이미지에는 들어가지 않는다 —
# 모듈이 읽는 것은 결과물인 .onnx 파일뿐이다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# opencv 가 libGL 없이는 import 도 안 된다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# CPU 판 torch 로 족하다. 한 번 돌리고 마는 일이라 몇 분 더 걸려도 상관없고, CUDA 판은
# 받는 데만 GB 단위가 든다. 먼저 박아 두면 ultralytics 가 그 위에 얹힌다 — 순서를
# 바꾸면 기본 인덱스에서 CUDA 판이 딸려온다.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu torch torchvision

# ultralytics 가 GUI 판 opencv 를 끌고 온다. 화면 없는 컨테이너에서는 X11 라이브러리를
# 못 찾아 import 에서 죽는다. headless 판으로 갈아 끼운다 — X11 을 깔아 주는 것보다
# 가볍고, 어차피 창을 띄울 일이 없다.
RUN pip install --no-cache-dir ultralytics onnx onnxslim onnxruntime \
    && pip uninstall -y -q opencv-python opencv-contrib-python \
    && pip install --no-cache-dir opencv-python-headless

# 가중치는 굽지 않는다. 볼륨으로 붙인다 — 모델이 바뀔 때마다 이미지를 다시 만들 일이 아니다.
WORKDIR /work
ENTRYPOINT ["python", "export_onnx.py"]
