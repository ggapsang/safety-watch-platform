# 서버 YOLO 모듈 — 학습과 추론.
#
# 서버 이미지와 분리하는 이유. torch 와 onnxruntime·opencv 만으로 이미지가 GB 단위로
# 불어나고, 추론이나 학습이 죽을 때 웹서버가 같이 죽어서는 안 되며, 모델 로딩이 서버
# 기동을 붙잡아서도 안 된다. 그리고 이 모듈은 모듈 계약의 사용자여야 한다 — 코어 안에
# 두면 계약을 우회하는 특별 통로가 생긴다(매니페스토 1번).
#
# GPU 로 학습할 때:
#   docker compose build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121 mod-yolo
#   그리고 compose 에서 GPU 를 넘긴다(주석 참조). CPU 판으로도 추론은 되지만 학습은
#   현실적으로 불가능하다 — 이 데이터 규모에서 GPU 2시간이 CPU 며칠이 된다.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

# opencv-python-headless 도 libGL 없이는 못 돌고, ffmpeg 는 RTSP 를 읽는 데 필요하다.
# git 은 vendor/yolov7 이 버전 정보를 캐낼 때 부른다(없어도 되지만 경고가 시끄럽다).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        ffmpeg \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG TORCH_INDEX
RUN pip install --no-cache-dir --index-url ${TORCH_INDEX} torch torchvision

COPY yolo/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 빌드 컨텍스트는 modules/ 다. 모듈들이 공유하는 SDK 를 같이 담는다.
COPY _sdk/ /app/_sdk/
COPY yolo/vendor/ /app/vendor/
COPY yolo/web/ /app/web/
COPY yolo/*.py /app/

# 모델·데이터셋·학습 산출물은 이미지에 굽지 않는다. 전부 볼륨이다.
EXPOSE 8000
CMD ["python", "main.py"]
