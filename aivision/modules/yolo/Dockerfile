# 객체감지 모듈 — 추론 전용.
#
# 서버 이미지와 분리하는 이유. 모델 로딩이 웹서버 기동을 붙잡아서는 안 되고, 추론이
# 죽을 때 웹서버가 같이 죽어서도 안 된다. 그리고 이 모듈은 모듈 계약의 사용자여야
# 한다 — 코어 안에 두면 계약을 우회하는 특별 통로가 생긴다(매니페스토 1번).
#
# **학습은 여기 없다.** 예전에는 torch 와 yolov7 을 담아 학습까지 돌렸고 이미지가
# 3GB 를 넘었다. 관제 서버는 24시간 떠 있어야 하는데 학습은 몇 시간짜리 배치 작업이라
# 같이 둘 일이 아니었다. 학습 코드는 refs/yolov7-training/ 에 참조용으로 남겼다.
# 학습은 GPU 워크스테이션에서 돌리고, ONNX 로 내보내 모듈 화면(11990)에서 올린다.
#
# 그래서 런타임은 onnxruntime 하나다. GPU 로 추론하려면 onnxruntime-gpu 로 바꾸고
# compose 에서 장치를 넘긴다(CUDAExecutionProvider 가 없으면 CPU 로 폴백한다).
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

# 빌드 컨텍스트는 modules/ 다. 모듈들이 공유하는 SDK 를 같이 담는다.
COPY _sdk/ /app/_sdk/
COPY yolo/web/ /app/web/
COPY yolo/*.py /app/

# 모델과 이 모듈의 설정(module.json)은 이미지에 굽지 않는다. 볼륨이다.
EXPOSE 8000
CMD ["python", "main.py"]
