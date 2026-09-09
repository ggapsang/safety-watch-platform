"""분석 모듈 API — 모듈이 플랫폼에 자기를 붙이는 통로.

모듈 입장에서 필요한 것은 셋뿐이다.
  1. 등록한다                POST /api/modules
  2. 무엇을 볼지 물어본다     GET  /api/modules/{id}/work   -> 카메라 목록 + 스트림 주소 + 옵션
  3. 살아 있다고 알린다       POST /api/modules/{id}/heartbeat

판정 결과는 여기로 보내지 않는다. MQTT 로 발행하거나 /api/ingest 로 밀어넣으면 인바운드
바인딩을 지나 이벤트가 된다 — 카메라 엣지든 우리 모듈이든 같은 문을 쓴다(매니페스토 4번).

코어는 모듈이 어디서 도는지 모른다. `kind` 는 화면에 보여 주려고 받아 둘 뿐 동작을 바꾸지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..services import cleanup
from ..models import AnalyticsModule, Camera, ModuleAssignment
from ..schemas import (AssignmentCreate, AssignmentOut, ModuleOut, ModuleRegister,
                       ModuleWork, ModuleWorkItem)
from ..streaming.manager import manager
from ..timeutil import age_sec, as_utc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/modules", tags=["modules"])

VALID_KIND = {"internal", "sidecar", "remote", "edge"}
# 이 시간 이상 heartbeat 가 없으면 '응답 없음'으로 본다.
STALE_SEC = 90.0


def to_dto(m: AnalyticsModule) -> ModuleOut:
    return ModuleOut(
        id=m.id, name=m.name, kind=m.kind, description=m.description,
        capabilities=list(m.capabilities or []), endpoint=m.endpoint, enabled=m.enabled,
        last_seen_at=as_utc(m.last_seen_at),
        alive=age_sec(m.last_seen_at) < STALE_SEC,
        last_status=m.last_status or {},
        assignments=[AssignmentOut(id=a.id, module_id=a.module_id, camera_id=a.camera_id,
                                   options=a.options or {}, enabled=a.enabled)
                     for a in m.assignments],
    )


async def _get(session: AsyncSession, module_id: str) -> AnalyticsModule:
    m = (await session.execute(
        select(AnalyticsModule).where(AnalyticsModule.id == module_id)
    )).scalars().unique().first()
    if m is None:
        raise HTTPException(status_code=404, detail="모듈을 찾을 수 없습니다")
    return m


@router.get("", response_model=list[ModuleOut])
async def list_modules(session: AsyncSession = Depends(get_session)) -> list[ModuleOut]:
    rows = (await session.execute(
        select(AnalyticsModule).order_by(AnalyticsModule.id))).scalars().unique().all()
    return [to_dto(m) for m in rows]


@router.post("", response_model=ModuleOut, status_code=201)
async def register(body: ModuleRegister,
                   session: AsyncSession = Depends(get_session)) -> ModuleOut:
    """등록. 같은 id 로 다시 부르면 갱신한다 — 모듈이 재시작할 때마다 실패하면 곤란하다."""
    if body.kind not in VALID_KIND:
        raise HTTPException(status_code=400,
                            detail=f"kind 는 {', '.join(sorted(VALID_KIND))} 중 하나입니다")
    existing = (await session.execute(
        select(AnalyticsModule).where(AnalyticsModule.id == body.id)
    )).scalars().unique().first()

    if existing is not None:
        existing.name = body.name or existing.name
        existing.kind = body.kind
        existing.description = body.description
        existing.capabilities = body.capabilities
        existing.endpoint = body.endpoint
        existing.last_seen_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(existing)
        log.info("모듈 재등록: %s (%s)", existing.id, existing.kind)
        return to_dto(existing)

    module = AnalyticsModule(
        id=body.id, name=body.name or body.id, kind=body.kind,
        description=body.description, capabilities=body.capabilities,
        endpoint=body.endpoint, last_seen_at=datetime.now(timezone.utc))
    session.add(module)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 모듈 id 입니다") from None
    await session.refresh(module)
    log.info("모듈 등록: %s (%s)", module.id, module.kind)
    return to_dto(module)


@router.delete("/{module_id}", status_code=204)
async def unregister(module_id: str, session: AsyncSession = Depends(get_session)) -> None:
    module = await _get(session, module_id)
    # 할당만 함께 정리한다. 이 모듈이 만든 이벤트는 남긴다 — module_id 는 '무엇이
    # 판정했나' 의 기록이지 소유 관계가 아니다.
    await cleanup.delete_module(session, module.id)
    await session.delete(module)
    await session.commit()
    log.info("모듈 해제: %s", module_id)


@router.post("/{module_id}/heartbeat", response_model=ModuleOut)
async def heartbeat(module_id: str, body: dict | None = None,
                    session: AsyncSession = Depends(get_session)) -> ModuleOut:
    """살아 있다는 신호. 본문은 자유 형식이라 그대로 보관해 화면에 보여 준다."""
    module = await _get(session, module_id)
    module.last_seen_at = datetime.now(timezone.utc)
    module.last_status = body or {}
    await session.commit()
    await session.refresh(module)
    return to_dto(module)


@router.get("/{module_id}/work", response_model=ModuleWork)
async def work(module_id: str, session: AsyncSession = Depends(get_session)) -> ModuleWork:
    """모듈이 '무엇을 볼지' 물어보는 곳.

    카메라마다 **영상 주소를 함께 준다.** 모듈은 카메라 IP·계정을 알 필요가 없고,
    미디어 서버에서 가져가므로 카메라 부하도 늘지 않는다.
    """
    module = await _get(session, module_id)
    if not module.enabled:
        return ModuleWork(module_id=module_id, items=[])

    cam_ids = [a.camera_id for a in module.assignments if a.enabled]
    if not cam_ids:
        return ModuleWork(module_id=module_id, items=[])

    cams = {c.id: c for c in (await session.execute(
        select(Camera).where(Camera.id.in_(cam_ids)))).scalars().unique().all()}

    items: list[ModuleWorkItem] = []
    for a in module.assignments:
        cam = cams.get(a.camera_id)
        if not a.enabled or cam is None or not cam.enabled:
            continue
        info = manager.stream_info(cam.id)
        items.append(ModuleWorkItem(
            camera_id=cam.id, camera_name=cam.name, location=cam.location,
            rtsp=getattr(info, "rtsp", "") or "",
            rtsp_sub=getattr(info, "rtsp_sub", "") or "",
            snapshot=f"/api/stream/{cam.id}/snapshot.jpg",
            options=a.options or {},
        ))
    return ModuleWork(module_id=module_id, items=items)


# ────────────────────────────────────────────────────────────── 할당

@router.post("/{module_id}/assignments", response_model=AssignmentOut, status_code=201)
async def assign(module_id: str, body: AssignmentCreate,
                 session: AsyncSession = Depends(get_session)) -> AssignmentOut:
    await _get(session, module_id)
    if await session.get(Camera, body.camera_id) is None:
        raise HTTPException(status_code=400, detail="없는 카메라입니다")
    row = ModuleAssignment(module_id=module_id, camera_id=body.camera_id,
                           options=body.options, enabled=body.enabled)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409,
                            detail="이미 이 카메라에 할당된 모듈입니다") from None
    await session.refresh(row)
    return AssignmentOut(id=row.id, module_id=row.module_id, camera_id=row.camera_id,
                         options=row.options or {}, enabled=row.enabled)


@router.delete("/{module_id}/assignments/{assignment_id}", status_code=204)
async def unassign(module_id: str, assignment_id: int,
                   session: AsyncSession = Depends(get_session)) -> None:
    row = await session.get(ModuleAssignment, assignment_id)
    if row is None or row.module_id != module_id:
        raise HTTPException(status_code=404, detail="할당을 찾을 수 없습니다")
    await session.delete(row)
    await session.commit()
