"""이벤트 서비스 — 탐지 신호를 이벤트로 승격한다.

여기가 '탐지 소스가 무엇이든 뒤가 같아지는' 합류점이다.

승격 규칙
  · state='active' 로 바뀌는 순간만 이벤트가 된다. inactive 는 상태 해제일 뿐이다.
  · 같은 (카메라, 탐지항목) 조합은 dedup 창(기본 20초) 안에서 하나로 묶는다.
    실장비가 active 를 여러 번 쏘거나, 한 상황이 잠깐 끊겼다 이어져도 이벤트는 1건이다.
  · 승격 시점에 해당 카메라의 현재 프레임을 스냅샷으로 남긴다(있으면).

이벤트에 심각도나 처리 상태를 매기지 않는다. 그 기준이 아직 정해지지 않았고, 임의로 정해
두면 화면에 근거 없는 값이 뜬다. 기준이 서면 그때 컬럼과 화면을 함께 붙인다.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..detection.base import DetectionSignal
from ..models import Camera, Event, EventBox, Solution
from ..schemas import EventOut
from ..timeutil import as_utc
from .bus import bus

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────── 승격

async def ingest_signal(session: AsyncSession, signal: DetectionSignal) -> Event | None:
    """탐지 신호 → 이벤트(필요할 때만). 승격되면 Event, 아니면 None."""
    if not signal.is_active:
        # 해제 신호. 지금은 별도 상태 테이블을 두지 않고 로그만 남긴다.
        log.debug("탐지 해제: cam=%s sol=%s", signal.camera_id, signal.solution_code)
        return None

    # 라이브 오버레이 전용 신호는 적재하지 않는다. 초당 여러 번 흐르는 데이터를
    # 이벤트 파이프에 태우면 DB 가 감당하지 못한다. 화면으로 통과만 시킨다.
    if signal.live_only:
        await bus.publish("live-boxes", {
            "camera_id": signal.camera_id,
            "item": signal.solution_code,
            "ts": signal.ts.isoformat(),
            "boxes": [{"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2,
                       "label": b.label, "score": b.score} for b in signal.boxes],
        })
        return None

    s = get_settings()
    solution = await session.get(Solution, signal.solution_code)
    if solution is None or not solution.enabled:
        log.warning("알 수 없거나 비활성인 탐지 항목: %s", signal.solution_code)
        return None
    camera = await session.get(Camera, signal.camera_id)
    if camera is None:
        return None

    # ── 중복 억제 ────────────────────────────────────────────────────
    since = signal.ts - timedelta(seconds=s.event_dedup_sec)
    dup = (await session.execute(
        select(Event)
        .where(Event.camera_id == signal.camera_id,
               Event.solution_code == signal.solution_code,
               Event.ts >= since)
        .order_by(Event.ts.desc())
        .limit(1)
    )).scalars().first()
    if dup is not None:
        log.debug("중복 억제(%.0fs 창): %s / %s", s.event_dedup_sec, dup.code, signal.solution_code)
        return None

    event = Event(
        code="",                                  # flush 후 id 로 채운다
        ts=signal.ts,
        camera_id=camera.id,
        solution_code=solution.code,
        event_type=solution.event_type,
        source=signal.source,
        module_id=signal.module_id[:64],
        binding_id=signal.binding_id,
        confidence=signal.confidence,
        raw_topic=signal.raw_topic[:300],
        raw_payload=signal.raw_payload,
    )
    session.add(event)
    await session.flush()
    event.code = f"EVT-{event.id:05d}"

    for b in signal.boxes:
        session.add(EventBox(event_id=event.id, x1=b.x1, y1=b.y1, x2=b.x2, y2=b.y2,
                             label=b.label, score=b.score))

    if s.snapshot_on_event:
        path = _save_snapshot(camera.id, event.code)
        if path:
            event.snapshot_path = path

    await session.commit()
    await session.refresh(event)
    log.info("이벤트 발생 %s | %s / %s  <- %s(%s)", event.code, camera.location,
             solution.short_name, signal.module_id or "?", signal.source)

    # 사고 영상은 코어 자산이다. 뒤쪽 여유분이 녹화될 때까지 기다렸다 잘라 온다.
    # 실패해도 이벤트는 이미 살아 있다 — 클립은 참고 자료다.
    from .recording import schedule_event_clip

    schedule_event_clip(event.id, camera.id, event.ts)

    await bus.publish("event", to_dto(event).model_dump(mode="json"))
    return event


def _save_snapshot(camera_id: int, code: str) -> str:
    """이벤트 발생 시점의 프레임을 파일로 남긴다.

    주의: 어떤 이유로 실패하든(영상 미수신, 디코더 오류, 디스크 문제) 이벤트 적재는 계속돼야
      한다. 캡쳐는 참고 자료이고 이벤트가 본체다.
    """
    from ..streaming.manager import manager

    try:
        worker = manager.get(camera_id)
        if worker is None:
            return ""
        jpeg = worker.snapshot_jpeg()
        if not jpeg:
            return ""
        path = get_settings().snapshot_path / f"{code}.jpg"
        path.write_bytes(jpeg)
        return str(path)
    except Exception:                                        # noqa: BLE001
        log.warning("스냅샷 저장 실패(%s) — 이벤트는 정상 기록합니다", code, exc_info=True)
        return ""


# ────────────────────────────────────────────────────────────── DTO

def to_dto(event: Event, *, has_clip: bool = False) -> EventOut:
    cam = event.camera
    return EventOut(
        id=event.code,
        ts=as_utc(event.ts),
        cam=event.camera_id,
        cam_name=cam.name if cam else "",
        cam_location=cam.location if cam else "",
        sol=event.solution_code,
        type=event.event_type,
        source=event.source,
        confidence=event.confidence,
        has_snapshot=bool(event.snapshot_path),
        has_clip=has_clip,
        boxes=[{"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2,
                "label": b.label, "score": b.score} for b in event.boxes],
    )
