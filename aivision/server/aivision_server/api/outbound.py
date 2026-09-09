"""아웃바운드 API — 대상 CRUD · 시험 발송 · 전달 이력.

'왜 안 갔나' 를 볼 수 있어야 한다는 요구가 전달 이력 조회로 구현된다.
어드민이 발송 실패를 눈으로 확인하지 못하면, 조용히 안 되는 기능이 된다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..services import cleanup
from ..models import Event, OutboundDelivery, OutboundTarget
from ..schemas import (OutboundTargetCreate, OutboundTargetOut, OutboundTargetPatch,
                       OutboundTestResult)
from ..services import outbound as svc
from ..services import template as tpl
from ..timeutil import as_utc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/outbound", tags=["outbound"])

VALID_KIND = {"mqtt"}


def to_dto(t: OutboundTarget) -> OutboundTargetOut:
    return OutboundTargetOut(
        id=t.id, name=t.name, enabled=t.enabled, kind=t.kind,
        solution_codes=list(t.solution_codes or []),
        camera_ids=list(t.camera_ids or []),
        config=t.config or {},
        payload_template=t.payload_template,
        max_attempts=t.max_attempts, retry_backoff_sec=t.retry_backoff_sec,
        last_sent_at=as_utc(t.last_sent_at), sent_count=t.sent_count,
        fail_count=t.fail_count, last_error=t.last_error,
    )


def _validate(data: dict) -> None:
    kind = data.get("kind")
    if kind is not None and kind not in VALID_KIND:
        raise HTTPException(status_code=400,
                            detail=f"지금 지원하는 종류는 {', '.join(sorted(VALID_KIND))} 뿐입니다")
    topic = (data.get("config") or {}).get("topic_template", "")
    if topic and "{" not in topic and "}" not in topic:
        # 치환 없이 고정 토픽을 쓰는 것도 정상이므로 막지는 않는다.
        log.debug("토픽 템플릿에 치환이 없습니다: %s", topic)


async def _get(session: AsyncSession, target_id: int) -> OutboundTarget:
    t = await session.get(OutboundTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="아웃바운드 대상을 찾을 수 없습니다")
    return t


@router.get("/fields", response_model=list[str])
async def fields() -> list[str]:
    """템플릿에서 쓸 수 있는 이름 목록. 화면이 안내로 보여 준다."""
    return list(tpl.AVAILABLE_FIELDS)


@router.get("/targets", response_model=list[OutboundTargetOut])
async def list_targets(session: AsyncSession = Depends(get_session)) -> list[OutboundTargetOut]:
    rows = (await session.execute(
        select(OutboundTarget).order_by(OutboundTarget.id))).scalars().all()
    return [to_dto(t) for t in rows]


@router.post("/targets", response_model=OutboundTargetOut, status_code=201)
async def create_target(body: OutboundTargetCreate,
                        session: AsyncSession = Depends(get_session)) -> OutboundTargetOut:
    data = body.model_dump()
    _validate(data)
    target = OutboundTarget(**data)
    session.add(target)
    await session.commit()
    await session.refresh(target)
    log.info("아웃바운드 대상 생성: #%d %s (%s)", target.id, target.name, target.kind)
    return to_dto(target)


@router.patch("/targets/{target_id}", response_model=OutboundTargetOut)
async def patch_target(target_id: int, body: OutboundTargetPatch,
                       session: AsyncSession = Depends(get_session)) -> OutboundTargetOut:
    target = await _get(session, target_id)
    patch = body.model_dump(exclude_unset=True)
    _validate(patch)
    for key, value in patch.items():
        setattr(target, key, value)
    await session.commit()
    await session.refresh(target)
    return to_dto(target)


@router.delete("/targets/{target_id}", status_code=204)
async def delete_target(target_id: int, session: AsyncSession = Depends(get_session)) -> None:
    target = await _get(session, target_id)
    # 이 대상으로 보낸 전송 이력도 함께. 대상이 없어진 이력은 읽을 수 없다.
    await cleanup.delete_outbound_target(session, target.id)
    await session.delete(target)
    await session.commit()


@router.post("/targets/{target_id}/test", response_model=OutboundTestResult)
async def test_target(target_id: int, event_code: str | None = None,
                      session: AsyncSession = Depends(get_session)) -> OutboundTestResult:
    """실제 이벤트 하나를 골라 템플릿을 적용해 보고, 바로 발송한다.

    이벤트가 없으면 무엇을 보내려 하는지 미리 볼 수 없으므로, 가장 최근 이벤트를 쓴다.
    """
    target = await _get(session, target_id)

    stmt = select(Event).order_by(Event.ts.desc()).limit(1)
    if event_code:
        stmt = select(Event).where(Event.code == event_code)
    event = (await session.execute(stmt)).scalars().unique().first()
    if event is None:
        raise HTTPException(status_code=409,
                            detail="시험에 쓸 이벤트가 없습니다. 이벤트가 한 건이라도 "
                                   "쌓인 뒤에 시험하세요.")

    ctx = tpl.context_of(event)
    config = target.config or {}
    topic = tpl.render(config.get("topic_template") or tpl.DEFAULT_TOPIC_TEMPLATE, ctx)
    payload = tpl.render_json(target.payload_template or tpl.DEFAULT_PAYLOAD_TEMPLATE, ctx)

    delivery = OutboundDelivery(
        target_id=target.id, event_id=event.id, status="pending",
        next_attempt_at=datetime.now(timezone.utc), topic=topic[:400], payload=payload)
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)

    await svc._send_one(session, delivery, target)           # noqa: SLF001
    await session.commit()

    return OutboundTestResult(
        ok=delivery.status == "sent", event=event.code, topic=topic, payload=payload,
        detail=delivery.error or ("전송했습니다" if delivery.status == "sent" else "실패"))


@router.get("/deliveries")
async def deliveries(target_id: int | None = None, status: str | None = None,
                     limit: int = Query(default=50, le=500),
                     session: AsyncSession = Depends(get_session)) -> list[dict]:
    """전달 이력. 실패 사유를 여기서 본다."""
    stmt = select(OutboundDelivery).order_by(OutboundDelivery.id.desc()).limit(limit)
    if target_id is not None:
        stmt = stmt.where(OutboundDelivery.target_id == target_id)
    if status:
        stmt = stmt.where(OutboundDelivery.status == status)
    rows = (await session.execute(stmt)).scalars().all()

    codes: dict[int, str] = {}
    ids = [r.event_id for r in rows if r.event_id]
    if ids:
        codes = {e.id: e.code for e in (await session.execute(
            select(Event).where(Event.id.in_(ids)))).scalars().unique().all()}

    return [{
        "id": r.id, "target_id": r.target_id, "event": codes.get(r.event_id),
        "status": r.status, "attempt": r.attempt, "topic": r.topic,
        "error": r.error, "created_at": as_utc(r.created_at), "sent_at": as_utc(r.sent_at),
        "next_attempt_at": as_utc(r.next_attempt_at),
    } for r in rows]


@router.post("/drain")
async def drain_now() -> dict:
    """대기 중인 발송을 지금 처리한다(재시도 대기 시간을 기다리지 않고 확인할 때)."""
    handled = await svc.drain()
    return {"handled": handled, "pending": await svc.pending_count()}
