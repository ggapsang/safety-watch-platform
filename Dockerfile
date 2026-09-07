# RTX 4060 (Ada, sm_89) 상시 가동용. CUDA 12.4 + cuDNN 9.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
        libgl1 libglib2.0-0 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY main.py ./
COPY tests/ ./tests/

# 단일 이미지 배포: 모델·현재 설정을 이미지에 굽는다(외부 폴더/.env/볼륨 없이 실행).
COPY model_files/ ./model_files/
COPY .env ./.env
COPY runtime/ ./runtime/
RUN mkdir -p /app/recordings

# MODEL_DEVICE=cpu 를 기본값으로 — GPU 없이(Docker Desktop GUI 실행) 곧장 CPU 추론.
#   (GPU 로 돌릴 때는 실행 시 -e MODEL_DEVICE=cuda 로 덮어쓴다. dev 는 compose 가 cuda 로 덮음.)
ENV MODEL_ONNX_PATH=/app/model_files/best_trash.torchscript.onnx \
    MODEL_TORCHSCRIPT_PATH=/app/model_files/best_trash.torchscript.pt \
    MODEL_DEVICE=cpu \
    RUNTIME_CONFIG_PATH=/app/runtime/settings.json \
    RECORDINGS_DIR=/app/recordings \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)" || exit 1

ENTRYPOINT ["python3", "main.py"]
