# 카메라 ONVIF 메타데이터 모듈.
#
# 추론을 하지 않으므로 onnxruntime 도 opencv 도 필요 없다. 필요한 것은 RTSP 데이터 트랙을
# 읽을 ffmpeg 와 발행용 paho 뿐이라 이미지가 작다. 무거운 모델 컨테이너와 분리해 두면
# 이 모듈만 따로 재시작해도 다른 것이 흔들리지 않는다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY onvif_meta/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 빌드 컨텍스트는 modules/ 다. SDK 를 같이 담는다.
COPY _sdk/ /app/_sdk/
COPY onvif_meta/*.py /app/

CMD ["python", "main.py"]
