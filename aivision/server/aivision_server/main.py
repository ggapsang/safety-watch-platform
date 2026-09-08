"""FastAPI 진입점.

기동 순서
  1. 미디어 백엔드 상태 확인
  2. 스키마 마이그레이션(upgrade head)
  3. 미디어 경로 등록 + 카메라 프레임 워커 기동, 녹화 정책 반영
  4. 인바운드 탐지 소스 기동 (MQTT 구독 -> 바인딩)
  5. 감시 태스크(카메라 상태·로그 정리) + 아웃바운드 워커 기동
  6. 빌드된 SPA 서빙

스키마는 Alembic 마이그레이션으로 관리한다. 기동할 때 자동으로 `upgrade head` 를 돌린다 —
사람이 잊어버려서 스키마가 어긋난 채 서버가 뜨는 일을 막기 위해서다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import (admin, bindings, cameras, events, ingest, modules, outbound,
                  recordings, stats, stream, ws)
from .config import get_settings
from .db import dispose, sessionmaker
from .media import backend as media_backend, dispose as media_dispose
from .detection.registry import registry
from .seed import seed
from .services import monitor
from .services import outbound as outbound_service
from .services import recording as recording_service
from .streaming.manager import manager

log = logging.getLogger(__name__)


def configure_logging() -> None:
    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 유휴 시에도 매 요청을 찍어 로그가 묻히는 것을 막는다.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    s = get_settings()
    log.info("%s v%s 기동", s.app_name, __version__)

    health = await media_backend().health()
    log.info("미디어 백엔드 %s: %s", health.name,
             health.detail if health.available else f"사용 불가 — {health.detail}")

    await _migrate()
    async with sessionmaker()() as session:
        await seed(session)
        await manager.sync(session)

    async with sessionmaker()() as session:
        await recording_service.apply_all(session)   # 카메라별 녹화 정책을 미디어 서버에 반영

    await registry.start_all()
    monitor_task = monitor.start()
    # 아웃바운드 워커. 재시작 전에 남아 있던 outbox 대기분을 이어서 보낸다.
    outbound_service.start()

    try:
        yield
    finally:
        log.info("종료 중…")
        await monitor.stop(monitor_task)
        await outbound_service.stop()
        await registry.stop_all()
        await manager.shutdown()
        await media_dispose()
        await dispose()
        log.info("종료 완료")


async def _migrate() -> None:
    """스키마를 최신으로 올린다.

    alembic 의 env.py 는 자체 이벤트 루프를 쓰므로(asyncio.run) 별도 스레드에서 부른다.
    실패하면 서버를 띄우지 않는다 — 스키마가 어긋난 채 도는 것이 더 위험하다.
    """
    import asyncio
    from pathlib import Path as _Path

    from alembic import command
    from alembic.config import Config

    root = _Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    # alembic 이 우리 로깅을 덮어쓰지 않게 한다(env.py 주석 참조).
    cfg.attributes["configure_logger"] = False
    log.info("스키마 마이그레이션 확인")
    await asyncio.to_thread(command.upgrade, cfg, "head")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.include_router(cameras.router)
    app.include_router(events.router)
    app.include_router(stats.router)
    app.include_router(stream.router)
    app.include_router(recordings.router)
    app.include_router(modules.router)
    app.include_router(ingest.router)
    app.include_router(outbound.router)
    app.include_router(bindings.router)
    app.include_router(admin.router)
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "version": __version__,
            "detection_sources": registry.statuses(),
            "streams": len(manager.statuses()),
        })

    # ── 빌드된 SPA ──────────────────────────────────────────────────
    # 단일 오리진으로 서빙한다(CORS 설정이 필요 없고, MJPEG 도 같은 호스트라 단순하다).
    static = s.static_dir
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            # 없는 API 경로까지 index.html 로 받아 주면, 프론트가 오타 난 주소를 호출해도
            # 200 + HTML 이 돌아와 원인을 찾기 어려워진다. API 는 확실히 404 로 끝낸다.
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="없는 API 경로입니다")
            candidate = static / path
            if path and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(static / "index.html"))     # 클라이언트 라우팅
    else:
        log.warning("빌드된 프론트엔드(%s)가 없습니다 — API 만 제공합니다. "
                    "개발 중이면 web/ 에서 `npm run dev` 를 쓰세요.", static)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    configure_logging()
    uvicorn.run("aivision_server.main:app", host="0.0.0.0", port=8000, reload=True)
