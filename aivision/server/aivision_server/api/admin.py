"""어드민 API — 솔루션·탐지규칙·운영설정·시스템 상태.

카메라 등록은 /api/cameras 에 있다(일반 화면과 같은 자원이라 경로를 나누지 않았다).

주의: 인증이 없다. 폐쇄망 전제이기 때문이다. 외부에서 접근 가능한 망에 올릴 거라면
  이 라우터부터 보호해야 한다(로그인 도입 시 여기에 의존성 하나만 추가하면 되도록
  경로를 /api/admin 으로 묶어 뒀다).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..detection.registry import registry
from ..media import backend as media_backend
from ..models import MqttMessage, Solution
from ..schemas import SettingsOut, SettingsPatch, SolutionOut, SolutionPatch
from ..services import settings_store as store
from ..streaming.manager import manager

router = APIRouter(prefix="/api", tags=["admin"])


# ────────────────────────────────────────────────────────────── 솔루션

@router.get("/solutions", response_model=list[SolutionOut])
async def list_solutions(session: AsyncSession = Depends(get_session)) -> list[Solution]:
    return (await session.execute(
        select(Solution).order_by(Solution.sort_order, Solution.code))).scalars().all()


@router.patch("/solutions/{code}", response_model=SolutionOut)
async def patch_solution(code: str, body: SolutionPatch,
                         session: AsyncSession = Depends(get_session)) -> Solution:
    sol = await session.get(Solution, code)
    if sol is None:
        raise HTTPException(status_code=404, detail="솔루션을 찾을 수 없습니다")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(sol, key, value)
    await session.commit()
    await session.refresh(sol)
    return sol


# ────────────────────────────────────────────────────────────── 운영 설정

@router.get("/settings", response_model=SettingsOut)
async def get_settings_api(session: AsyncSession = Depends(get_session)) -> SettingsOut:
    s = get_settings()
    runtime = await store.get_runtime(session)
    return SettingsOut(
        mqtt_ws_url=s.mqtt_ws_url,
        mqtt_log_retention_days=int(runtime.get("mqtt_log_retention_days",
                                                s.mqtt_log_retention_days)),
        snapshot_on_event=bool(runtime.get("snapshot_on_event", s.snapshot_on_event)),
        event_dedup_sec=float(runtime.get("event_dedup_sec", s.event_dedup_sec)),
    )


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsPatch,
                       session: AsyncSession = Depends(get_session)) -> SettingsOut:
    runtime = {k: v for k, v in body.model_dump(exclude_unset=True).items()
               if v is not None}
    if runtime:
        await store.set_value(session, store.KEY_RUNTIME, runtime)
    return await get_settings_api(session)


# ────────────────────────────────────────────────────────────── 시스템 상태

@router.get("/system")
async def system_status(session: AsyncSession = Depends(get_session)) -> dict:
    """어드민 화면 상단. '지금 무엇이 살아 있나'를 한 눈에 본다."""
    s = get_settings()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = int((await session.execute(
        select(func.count(MqttMessage.id)).where(MqttMessage.ts >= since))).scalar() or 0)
    media = await media_backend().health()
    from ..services import outbound as outbound_service

    return {
        "outbound": {"pending": await outbound_service.pending_count()},
        "detection_sources": registry.statuses(),
        "streams": manager.statuses(),
        "media": {"name": media.name, "available": media.available,
                  "detail": media.detail, "paths": media.streams, **media.extra},
        "mqtt": {"host": s.mqtt_host, "port": s.mqtt_port, "ws_url": s.mqtt_ws_url,
                 "subscribe": s.mqtt_subscribe, "messages_1h": recent},
        "stream": {"fps": s.stream_fps, "jpeg_quality": s.jpeg_quality,
                   "max_width": s.stream_max_width},
    }


@router.post("/system/mqtt-log/purge")
async def purge_mqtt_log(days: int = 0, session: AsyncSession = Depends(get_session)) -> dict:
    """MQTT 원문 로그 정리. days=0 이면 설정된 보존기간을 쓴다."""
    s = get_settings()
    runtime = await store.get_runtime(session)
    keep = days or int(runtime.get("mqtt_log_retention_days", s.mqtt_log_retention_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep)
    result = await session.execute(delete(MqttMessage).where(MqttMessage.ts < cutoff))
    await session.commit()
    return {"deleted": result.rowcount or 0, "kept_days": keep}


@router.get("/mqtt-log")
async def mqtt_log(limit: int = 200, topic: str | None = None,
                   session: AsyncSession = Depends(get_session)) -> list[dict]:
    """서버가 적재해 둔 MQTT 원문(사후 조회용).

    실시간 화면은 브로커에 직결 구독하므로 이 API 를 쓰지 않는다. 여기는
    '어제 그 시간에 무슨 메시지가 왔었나'를 되짚을 때 쓴다.
    """
    stmt = select(MqttMessage).order_by(MqttMessage.ts.desc()).limit(min(limit, 1000))
    if topic:
        stmt = stmt.where(MqttMessage.topic.ilike(f"%{topic}%"))
    rows = (await session.execute(stmt)).scalars().all()
    return [{"id": r.id, "ts": r.ts, "topic": r.topic, "payload": r.payload,
             "camera_id": r.camera_id, "matched": r.matched} for r in rows]
