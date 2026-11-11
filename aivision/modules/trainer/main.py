"""학습·평가 플러그인 — 데이터셋·라벨링과 이미지 판별률 평가.

객체감지 플러그인 안에 있던 것을 따로 뺐다. 실시간 감시(모델 켜기·민감도·카메라별 모델)와
데이터를 만들고 재는 일은 쓰는 때도 사람도 다르다 — 한 화면에 둘 이유가 없다.

**영상을 보지 않는 플러그인이다.** 카메라를 할당받지 않고 브로커에도 붙지 않는다. 플랫폼에는
화면 주소만 등록하고, 살아 있다는 것만 알린다(heartbeat 의 `uses_cameras: false` — 플러그인
탭이 '이 플러그인이 볼 카메라' 칸을 그리지 않는다).

**판독은 객체감지와 똑같이 한다.** 평가 점수와 모델 초안이 실제 관제 화면과 달라지면 쓸모가
없다. 그래서 객체감지의 판독 코드(inference·config·settings)를 이미지에 그대로 담고, 모델
폴더(/models — 모델 파일과 객체감지 설정 module.json)를 **읽기 전용으로** 붙인다. 평가·초안을
돌릴 때마다 그 설정을 새로 읽으므로, 객체감지 화면에서 모델을 바꾸면 다음 평가부터 따라간다.

GPU 는 평가·초안을 돌릴 때만 잡는다(ONNX 세션을 그때 연다).

구성
  · API·화면     FastAPI/uvicorn (eval_api.py, dataset_api.py, web/)
  · 등록·heartbeat  백그라운드 스레드
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "/app")

from _sdk import BaseConfig, configure_logging, env, num  # noqa: E402
from _sdk.platform import Platform  # noqa: E402

log = logging.getLogger("trainer")
WEB = Path(__file__).resolve().parent / "web"


@dataclass
class Config(BaseConfig):
    module_id: str = "train-eval"
    module_name: str = "학습·평가"
    public_url: str = "http://localhost:12020"
    serve_port: int = 8000


def load_config() -> Config:
    cfg = Config()
    cfg.load_base()
    cfg.public_url = env("PUBLIC_URL", cfg.public_url).rstrip("/")
    cfg.serve_port = int(num("SERVE_PORT", 8000))
    return cfg


def load_yolo_cfg():
    """객체감지 플러그인의 설정을 지금 상태로 읽는다(모델 폴더의 module.json + 같은 env).

    객체감지 config.load 는 '연결된 탐지 항목이 없다' 같은 실시간 쪽 경고를 남긴다. 평가할
    때마다 그것이 이 플러그인 로그에 쌓이면 읽을 것이 묻히므로 그동안만 누른다.
    """
    import config as yolo_config

    quiet = logging.getLogger(yolo_config.__name__)
    level = quiet.level
    quiet.setLevel(logging.ERROR)
    try:
        return yolo_config.load()
    finally:
        quiet.setLevel(level)


def keep_registered(cfg: Config, stop: threading.Event) -> None:
    """등록하고 heartbeat 를 보낸다. 플랫폼이 재시작해도 다시 등록한다."""
    platform = Platform(cfg.platform_url, cfg.module_id)
    registered = False
    while not stop.is_set():
        if not registered:
            registered = platform.register(
                cfg.module_name, [], "sidecar",
                "데이터셋·라벨링과 이미지 판별률 평가", cfg.public_url)
            if registered:
                log.info("등록 완료: %s (%s)", cfg.module_id, cfg.public_url)
        if registered:
            # 등록이 지워졌으면(플랫폼 DB 를 새로 만든 경우) heartbeat 가 404 로 돌아온다.
            # _call 이 None 을 돌려주므로 다음 주기에 다시 등록한다.
            registered = platform.heartbeat({"uses_cameras": False})
        stop.wait(cfg.heartbeat_sec if registered else 5.0)


def create_app(cfg: Config):
    from fastapi import FastAPI
    from fastapi.responses import FileResponse

    import dataset_api
    import eval_api

    app = FastAPI(title="AI Safety Watcher — 학습·평가 모듈", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    eval_api.mount(app, load_yolo_cfg)
    dataset_api.mount(app, cfg, load_yolo_cfg)

    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(WEB / "index.html"), headers=no_cache)

    @app.get("/{name}.js")
    async def script(name: str) -> FileResponse:
        if name not in ("label", "eval"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return FileResponse(str(WEB / f"{name}.js"), media_type="application/javascript",
                            headers=no_cache)

    @app.get("/api/state")
    async def state() -> dict:
        return {"module": {"id": cfg.module_id, "name": cfg.module_name}}

    return app


def main() -> int:
    configure_logging()
    cfg = load_config()
    stop = threading.Event()
    threading.Thread(target=keep_registered, args=(cfg, stop), name="register",
                     daemon=True).start()

    import uvicorn

    try:
        uvicorn.run(create_app(cfg), host="0.0.0.0", port=cfg.serve_port, log_level="warning")
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
