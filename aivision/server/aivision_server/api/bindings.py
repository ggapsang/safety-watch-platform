"""인바운드 바인딩 API.

어드민이 여기서 "어떤 토픽·페이로드를 어떤 탐지 항목의 이벤트로 볼지"를 정한다.
코드 수정 없이 새 소스를 받아들일 수 있어야 하므로, 이 API 가 플랫폼의 확장 지점이다.

'시험' 엔드포인트가 핵심이다. 실제 메시지를 붙여 넣으면 어느 바인딩이 걸리고 왜 안 걸리는지
바로 보여 준다. 이게 없으면 어드민은 표현식을 손으로 맞춰 보며 추측해야 한다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..detection.registry import registry
from ..models import Camera, InboundBinding, Solution
from ..mqtt import onvif
from ..schemas import (BindingCreate, BindingOut, BindingPatch, BindingTestRequest,
                       BindingTestResult)
from ..services.binding import engine

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bindings", tags=["bindings"])

VALID_PROFILE = {"raw", "onvif"}
VALID_TRANSPORT = {"mqtt", "http"}
VALID_CAMERA_FROM = {"topic_mac", "topic_segment", "payload", "fixed"}
VALID_ITEM_FROM = {"fixed", "payload"}
VALID_BOX_FORMAT = {"xyxy_norm", "xyxy_px", "xywh_px", "cxcywh_norm"}


async def _validate(session: AsyncSession, data: dict) -> None:
    """저장 전에 걸러 낸다. 잘못된 바인딩은 조용히 안 걸릴 뿐이라 원인 찾기가 어렵다."""
    def check(field: str, allowed: set[str]) -> None:
        value = data.get(field)
        if value is not None and value not in allowed:
            raise HTTPException(status_code=400,
                                detail=f"{field}: '{value}' 은(는) 허용되지 않습니다 "
                                       f"({', '.join(sorted(allowed))})")

    check("transport", VALID_TRANSPORT)
    check("payload_profile", VALID_PROFILE)
    check("camera_from", VALID_CAMERA_FROM)
    check("item_from", VALID_ITEM_FROM)
    check("boxes_format", VALID_BOX_FORMAT)

    if data.get("camera_from") == "fixed" and data.get("camera_id") is None:
        raise HTTPException(status_code=400, detail="camera_from=fixed 이면 카메라를 지정해야 합니다")
    if data.get("camera_id") is not None:
        if await session.get(Camera, data["camera_id"]) is None:
            raise HTTPException(status_code=400, detail="없는 카메라입니다")

    if data.get("item_from") == "fixed":
        code = data.get("solution_code") or data.get("item_expr")
        if not code:
            raise HTTPException(status_code=400,
                                detail="item_from=fixed 이면 탐지 항목을 지정해야 합니다")
        if await session.get(Solution, code) is None:
            raise HTTPException(status_code=400, detail=f"없는 탐지 항목입니다: {code}")
    if data.get("item_from") == "payload" and not (data.get("item_expr") or "").strip():
        raise HTTPException(status_code=400,
                            detail="item_from=payload 이면 항목 표현식이 필요합니다")

    if not (data.get("topic_pattern") or "").strip() and data.get("transport", "mqtt") == "mqtt":
        raise HTTPException(status_code=400, detail="토픽 패턴이 비어 있습니다")


async def _after_change() -> None:
    """저장 즉시 반영. 바인딩 캐시를 갈아 끼운다."""
    await engine.reload()
    await registry.reload_all()


@router.get("", response_model=list[BindingOut])
async def list_bindings(session: AsyncSession = Depends(get_session)) -> list[InboundBinding]:
    return (await session.execute(
        select(InboundBinding).order_by(InboundBinding.priority, InboundBinding.id)
    )).scalars().all()


@router.post("", response_model=BindingOut, status_code=201)
async def create_binding(body: BindingCreate,
                         session: AsyncSession = Depends(get_session)) -> InboundBinding:
    data = body.model_dump()
    await _validate(session, data)
    binding = InboundBinding(**data)
    session.add(binding)
    await session.commit()
    await session.refresh(binding)
    await _after_change()
    log.info("바인딩 생성: #%d %s (%s)", binding.id, binding.name, binding.topic_pattern)
    return binding


@router.patch("/{binding_id}", response_model=BindingOut)
async def patch_binding(binding_id: int, body: BindingPatch,
                        session: AsyncSession = Depends(get_session)) -> InboundBinding:
    binding = await session.get(InboundBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="바인딩을 찾을 수 없습니다")
    patch = body.model_dump(exclude_unset=True)
    merged = {c.name: getattr(binding, c.name) for c in InboundBinding.__table__.columns}
    merged.update(patch)
    await _validate(session, merged)
    for key, value in patch.items():
        setattr(binding, key, value)
    await session.commit()
    await session.refresh(binding)
    await _after_change()
    return binding


@router.delete("/{binding_id}", status_code=204)
async def delete_binding(binding_id: int, session: AsyncSession = Depends(get_session)) -> None:
    binding = await session.get(InboundBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="바인딩을 찾을 수 없습니다")
    await session.delete(binding)
    await session.commit()
    await _after_change()


@router.post("/test", response_model=list[BindingTestResult])
async def test_bindings(body: BindingTestRequest) -> list[BindingTestResult]:
    """실제 메시지를 넣어 어느 바인딩이 걸리는지, 안 걸리면 왜인지 본다.

    MQTT 로그 화면에서 본 메시지를 그대로 붙여 넣어 확인하는 용도다.
    """
    payload = onvif.parse_payload(body.payload)
    results = engine.explain(body.topic, payload, transport=body.transport)
    return [BindingTestResult(
        binding_id=r.binding_id, binding_name=r.binding_name, matched=r.matched,
        reason=r.reason,
        camera_id=r.detail.get("camera_id"), item=r.detail.get("item"),
        state=r.detail.get("state"), boxes=r.detail.get("boxes", 0),
    ) for r in results]
