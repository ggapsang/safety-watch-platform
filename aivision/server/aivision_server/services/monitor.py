"""백그라운드 감시 태스크.

  · 카메라 온·오프라인 판정과 DB 반영 (변화가 있을 때만 기록·통보)
  · 미디어 경로 자가 복구 (미디어 서버가 재시작하면 경로가 날아간다)
  · 요일·시간대 녹화 스케줄 반영 (미디어 서버는 스케줄을 모른다)
  · 상시 녹화 용량 상한 — 카메라별, 그리고 전체
  · MQTT 원문 로그 보존기간 정리

주기 작업을 한 태스크에 모은 이유: 카메라 2대 규모에서 태스크를 여럿 띄울 이유가 없고,
로그가 한 곳에서 나야 무슨 일이 언제 있었는지 읽기 쉽다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

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
# 미디어 경로 점검 주기(초). 개수만 세는 가벼운 호출이지만 5초마다 할 이유는 없다 —
# 미디어 서버가 재시작한 뒤 30초 안에 복구되면 사람이 알아채기 전이다.
HEAL_INTERVAL = 30.0


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


async def _heal_media_paths() -> None:
    """미디어 서버에서 경로가 사라졌으면 다시 밀어 넣는다.

    **왜 필요한가.** 미디어 서버가 재시작하면 경로 설정이 초기화된다(우리가 Control API
    로 만들어 준 것이라 그쪽 설정 파일에는 없다). 그러면 DB 에는 카메라가 멀쩡히 있는데
    당길 곳이 없어져, 화면은 카메라를 보여 주면서 영상만 검게 남는다. 워커는 '영상 소스
    열기 실패' 를 3초마다 반복할 뿐 스스로 벗어나지 못한다 — 자기가 붙을 주소가 사라진
    것이지 주소가 틀린 것이 아니기 때문이다.

    실제로 그 상황을 만났다. 사람이 재시작해야만 복구되는 상태를 남겨 두면 안 된다.

    **싸게 감지한다.** 경로 개수만 센다(health). 경로마다 물어보면 카메라가 늘수록
    5초 주기가 무거워진다. 개수가 모자랄 때만 sync 를 돌린다 — sync 는 없는 것만
    만들므로 여러 번 불러도 해가 없다.
    """
    from ..media import backend as media_backend
    from .recording import apply_all

    media = media_backend()
    if not getattr(media, "supports_recording", False) and media.name == "direct":
        return                      # 미디어 서버를 안 쓰는 배치다. 고칠 경로가 없다

    health = await media.health()
    if not health.available:
        return                      # 미디어 서버 자체가 안 보인다. 복구는 그쪽이 살아난 뒤

    async with sessionmaker()() as session:
        want = int((await session.execute(
            select(func.count(Camera.id)).where(Camera.enabled.is_(True)))).scalar() or 0)
        if want == 0 or health.streams >= want:
            return
        log.warning("미디어 경로가 모자랍니다 (있음 %d · 필요 %d) — 다시 등록합니다",
                    health.streams, want)
        await manager.sync(session)
        # 녹화 설정도 경로에 붙어 있던 것이라 함께 날아간다. force 로 다시 민다.
        await apply_all(session, force=True)


async def _apply_record_schedule() -> None:
    """요일·시간대 스케줄을 미디어 서버에 반영한다.

    미디어 서버는 스케줄을 모른다. 때가 되면 코어가 녹화를 켜고 끈다.

    매번 부르지만 실제 PATCH 는 상태가 바뀔 때만 나간다(recording._applied). 안 그러면
    미디어 서버 로그가 '녹화 켬' 으로 가득 찬다.

    판정은 **현장 시간대**로 한다. 현장 사람이 벽시계를 보고 적은 값이라, 서버를 UTC 로
    띄우는 순간 9시간이 어긋나면 안 된다.
    """
    from .recording import apply_all

    async with sessionmaker()() as session:
        await apply_all(session)


async def run() -> None:
    last_purge = 0.0
    last_heal = 0.0
    last_quota = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await _check_cameras()
            # 경로 복구를 녹화 반영보다 먼저 한다. 경로가 없는 상태에서 녹화를 밀면
            # 미디어 서버가 '없는 경로' 라고 거절하고, 그 실패가 로그만 채운다.
            if loop.time() - last_heal > HEAL_INTERVAL:
                last_heal = loop.time()
                await _heal_media_paths()
            await _apply_record_schedule()
            # 아무도 안 보게 된 고화질 워커를 내린다. 유예가 지난 것만 내려가므로
            # 카메라를 이리저리 눌러 보는 동안에는 붙잡혀 있는다(StreamManager 참조).
            await manager.reap_main()
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
