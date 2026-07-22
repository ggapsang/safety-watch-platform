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

# 모델은 이미지에 굽지 않고 볼륨으로 주입한다 (./model_files -> /app/model_files)
ENV MODEL_ONNX_PATH=/app/model_files/best_trash.torchscript.onnx \
    MODEL_TORCHSCRIPT_PATH=/app/model_files/best_trash.torchscript.pt \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)" || exit 1

ENTRYPOINT ["python3", "main.py"]
