# 1단계: 프론트엔드 빌드 (Vite + React)
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

# 2단계: 서버 런타임
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Seoul

# opencv-python-headless 가 RTSP 를 열려면 ffmpeg 런타임 라이브러리가 필요하다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 ffmpeg tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./
# 빌드된 SPA 를 서버가 그대로 서빙한다(단일 오리진 — CORS 불필요).
COPY --from=web /web/dist ./static

RUN mkdir -p /data/snapshots
EXPOSE 8000
CMD ["uvicorn", "aivision_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
