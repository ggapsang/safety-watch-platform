# ops-watchdog — 스택을 감시·기록하고, 선을 넘으면 되살린다(watchdog.py 참조).
#
# 가볍게 둔다. 감시가 무거우면 감시가 곧 부하가 된다 — 의존성은 도커 SDK 하나뿐이다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY watchdog.py .

CMD ["python", "watchdog.py"]
