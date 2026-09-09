# 서버 YOLO 사이드카.
#
# 서버 이미지와 분리하는 이유가 여기 있다. onnxruntime 과 opencv 만으로도 수백 MB 이고,
# GPU 를 쓰려면 onnxruntime-gpu 가 몇 GB 를 더 붙인다. 웹서버 이미지가 그걸 같이 질 이유가
# 없고, 추론이 죽을 때 웹서버가 같이 죽을 이유도 없다.
#
# GPU 를 쓸 때: requirements.txt 의 onnxruntime 을 onnxruntime-gpu 로 바꾸고
# compose 에서 deploy.resources.reservations.devices 로 GPU 를 넘긴다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

# opencv-python-headless 도 libGL 없이는 못 돌고, ffmpeg 는 RTSP 를 읽는 데 필요하다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY yolo/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 빌드 컨텍스트는 modules/ 다. 모듈들이 공유하는 SDK 를 같이 담는다.
COPY _sdk/ /app/_sdk/
COPY yolo/*.py /app/

# 모델은 이미지에 굽지 않는다. 볼륨으로 넣는다 — 모델이 바뀔 때 이미지를 다시 만들 이유가 없다.
CMD ["python", "main.py"]
