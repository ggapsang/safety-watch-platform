"""백그라운드 감시 태스크.

  · 카메라 온·오프라인 판정과 DB 반영 (변화가 있을 때만 기록·통보)
  · MQTT 원문 로그 보존기간 정리

주기 작업을 한 태스크에 모은 이유: 카메라 2대 규모에서 태스크를 여럿 띄울 이유가 없고,
로그가 한 곳에서 나야 무슨 일이 언제 있었는지 읽기 쉽다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..config import get_settings
from ..db import sessionmaker
from ..models import Camera, MqttMessage
from ..streaming.manager import manager
from ..timeutil import age_sec
from .bus import bus
from .settings_store import get_runtime

log = logging.getLogger(__name__)

CHECK_INTERVAL = 5.0            # 카메라 상태 점검 주기(초)
PURGE_INTERVAL = 3600.0         # 로그 정리 주기(초)


async def _check_cameras() -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    changed: list[dict] = []

    async with sessionmaker()() as session:
        cams = (await session.execute(select(Camera))).scalars().unique().all()
        for cam in cams:
            worker = manager.get(cam.id)
            video_ok = bool(worker and worker.connected and worker.stale_sec < s.camera_stale_sec)
            mqtt_ok = age_sec(cam.last_seen_at) < s.heartbeat_stale_sec
            online = cam.enabled and (video_ok or mqtt_ok)
            error = worker.last_error if (worker and not video_ok) else ""

            if online != cam.online or error != cam.last_error:
                cam.online = online
                cam.last_error = error[:300]
                if video_ok:
                    cam.last_seen_at = now
                changed.append({"camera_id": cam.id, "name": cam.name,
                                "location": cam.location,
                                "status": "normal" if online else "offline",
                                "last_error": cam.last_error})
                log.info("카메라 상태 변화: #%d %s → %s%s", cam.id, cam.name,
                         "정상" if online else "오프라인",
                         f" ({cam.last_error})" if cam.last_error else "")
        if changed:
            await session.commit()

    for item in changed:
        await bus.publish("camera-status", item)


async def _purge_logs() -> None:
    s = get_settings()
    async with sessionmaker()() as session:
        runtime = await get_runtime(session)
        keep = int(runtime.get("mqtt_log_retention_days", s.mqtt_log_retention_days))
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, keep))
        result = await session.execute(delete(MqttMessage).where(MqttMessage.ts < cutoff))
        await session.commit()
    if result.rowcount:
        log.info("MQTT 원문 로그 정리: %d건 삭제 (보존 %d일)", result.rowcount, keep)


async def _enforce_record_quota() -> None:
    """상시 녹화가 상한을 넘었으면 오래된 세그먼트부터 지운다.

    미디어 서버는 시간 기반 회전까지만 할 수 있어 용량 상한은 코어가 지킨다.
    설정을 화면에서 바꿀 수 있으므로 매번 읽는다 — 5분에 한 번이라 부담이 없다.
    """
    from .recording import enforce_quota

    s = get_settings()
    async with sessionmaker()() as session:
        runtime = await get_runtime(session)
    max_gb = float(runtime.get("record_max_gb", s.record_max_gb) or 0)
    if max_gb <= 0:
        return
    await enforce_quota(max_gb)


async def run() -> None:
    last_purge = 0.0
    last_quota = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await _check_cameras()
            if loop.time() - last_purge > PURGE_INTERVAL:
                last_purge = loop.time()
                await _purge_logs()
            if loop.time() - last_quota > get_settings().record_quota_interval_sec:
                last_quota = loop.time()
                await _enforce_record_quota()
        except asyncio.CancelledError:
            raise
        except Exception:                                    # noqa: BLE001
            log.exception("감시 태스크 오류 — 계속 진행합니다")
        await asyncio.sleep(CHECK_INTERVAL)


def start() -> asyncio.Task:
    return asyncio.create_task(run(), name="monitor")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
