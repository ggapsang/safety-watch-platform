# 학습·평가 모듈 — 데이터셋·라벨링과 이미지 판별률 평가.
#
# 영상을 보지 않는다. 그래도 객체감지와 **같은 판독**을 해야 하므로(평가 점수와 모델 초안이
# 관제 화면과 같아야 한다) 런타임은 객체감지와 똑같이 담는다 — onnxruntime-gpu 와 CUDA.
#
# 아래 기반 단계는 yolo/Dockerfile 과 한 줄도 다르지 않게 둔다. 그래야 도커가 같은 층을
# 다시 쓰고, GB 단위의 pip 설치를 두 번 받지 않는다. 한쪽을 고치면 다른 쪽도 고친다.
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

# CUDA 라이브러리를 로더에게 알린다.
#
# onnxruntime-gpu[cuda,cudnn] 이 CUDA·cuDNN 을 pip 패키지로 받아 site-packages 아래에
# 두는데, 거기는 기본 검색 경로가 아니다. 이것을 빼면 provider 목록에는 CUDA 가 보이는데
# 세션을 여는 순간 'libcublasLt.so.13: cannot open shared object file' 로 실패하고
# 조용히 CPU 로 내려간다 — 로그를 안 보면 GPU 를 쓰는 줄 안다. 실제로 두 번 그랬다.
#
# 경로를 박아 두지 않고 찾아서 만든다. 패키지 구조가 버전마다 달라진다(cu12/cu13,
# nvidia/<pkg>/lib) — 박아 두면 다음 버전에서 같은 증상으로 되돌아온다.
RUN find /usr/local/lib/python3.12/site-packages/nvidia -name 'lib' -type d \
      > /etc/ld.so.conf.d/nvidia-pip.conf \
    && ldconfig \
    && ldconfig -p | grep -q libcublasLt \
    || (echo "CUDA 라이브러리를 찾지 못했습니다 — CPU 로 돕니다" >&2; true)

# 빌드 컨텍스트는 modules/ 다. SDK 와, 객체감지의 판독 코드를 그대로 담는다 — 따로 쓰면
# 언젠가 둘이 다르게 판독한다. 모델과 객체감지 설정은 compose 에서 /models 로 읽기 전용.
COPY _sdk/ /app/_sdk/
COPY yolo/inference.py yolo/config.py yolo/settings.py /app/
COPY trainer/web/ /app/web/
COPY trainer/*.py /app/

EXPOSE 8000
CMD ["python", "main.py"]
