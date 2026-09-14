# 충돌 위험 모듈 — 규칙 판정 전용.
#
# 이 이미지에는 모델도 디코더도 없다. 입력이 브로커에서 오는 박스뿐이기 때문이다
# (계약 3장). 객체감지 모듈이 ffmpeg 와 onnxruntime 을 담는 것과 대조된다 — 같은 계약을
# 지키는 플러그인이라도 안에 무엇을 담을지는 각자의 사정이다.
#
# 그래서 apt 로 설치할 것이 없고, 이미지는 python:slim + numpy 수준으로 끝난다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

COPY collision/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 빌드 컨텍스트는 modules/ 다. 모듈들이 공유하는 SDK 를 같이 담는다.
COPY _sdk/ /app/_sdk/
COPY collision/web/ /app/web/
COPY collision/*.py /app/

# 설정(호모그래피 4점·임계값·라벨 매핑)과 증거 스냅샷은 이미지에 굽지 않는다. 볼륨이다.
# 코어에 저장하는 길은 없고, 있어서도 안 된다(계약 4장).
VOLUME ["/config"]

EXPOSE 8000
CMD ["python", "main.py"]
