"""HTTP 인바운드 어댑터 — MQTT 를 못 쓰는 모듈이 결과를 밀어넣는 통로.

**MQTT 와 완전히 같은 바인딩 층을 지난다.** 전송 방식이 다르다고 특별 대우를 받지 않는다
(매니페스토 4번). 바인딩의 `transport` 를 `http` 로 두면 이 경로로 들어온 것에만 걸린다.

토픽이 없는 전송이라 `topic` 을 함께 받는다. 그래야 바인딩의 토픽 패턴을 그대로 쓸 수 있고,
MQTT 로 보내던 모듈이 HTTP 로 옮겨 와도 규칙을 다시 짤 필요가 없다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import sessionmaker
from ..services import raw_log
from ..services.binding import engine
from ..services.events import ingest_signal

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class IngestBody(BaseModel):
    """토픽 + 페이로드. MQTT 메시지와 같은 모양으로 받는다."""

    topic: str = Field(min_length=1, max_length=400)
    payload: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    accepted: bool
    matched: int = 0
    events: list[str] = Field(default_factory=list)
    detail: str = ""


@router.post("", response_model=IngestResult)
async def ingest(body: IngestBody) -> IngestResult:
    """판정 결과를 밀어넣는다. 바인딩에 걸리면 이벤트가 된다.

    안 걸려도 200 을 준다 — 보낸 쪽 잘못이 아니라 아직 규칙이 없는 것일 수 있다.
    """
    signals = engine.apply(body.topic, body.payload, transport="http")

    # 원문 적재는 정책을 따른다(기본은 남기지 않음). MQTT 인바운드와 같은 규칙이다 —
    # 전송이 다르다고 다르게 굴면 어드민이 두 가지를 외워야 한다.
    await raw_log.store_message(body.topic, body.payload, None, matched=bool(signals))
    if signals:
        async with sessionmaker()() as session:
            await engine.note_matches(session, [s.binding_id for s in signals if s.binding_id])

    if not signals:
        return IngestResult(accepted=True, matched=0,
                            detail="걸리는 바인딩이 없어 이벤트로 만들지 않았습니다. "
                                   "관리자에서 바인딩을 추가하세요.")

    codes: list[str] = []
    for signal in signals:
        async with sessionmaker()() as session:
            event = await ingest_signal(session, signal)
            if event is not None:
                codes.append(event.code)
    return IngestResult(accepted=True, matched=len(signals), events=codes)


@router.post("/test", response_model=list[dict])
async def test(body: IngestBody) -> list[dict]:
    """적재하지 않고 어느 바인딩이 걸리는지만 확인한다(모듈 개발용)."""
    results = engine.explain(body.topic, body.payload, transport="http")
    if not results:
        raise HTTPException(status_code=404,
                            detail="transport=http 인 바인딩이 없습니다")
    return [{"binding": r.binding_name, "matched": r.matched, "reason": r.reason,
             **r.detail} for r in results]
