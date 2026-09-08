"""아웃바운드 — 이벤트를 밖으로 내보낸다.

동작
  1. 이벤트 버스를 구독한다(`services/bus.py`). 이벤트가 승격될 때마다 신호가 온다.
  2. 조건에 맞는 대상을 골라 **outbox 에 적재만** 한다. 여기까지가 동기 구간이다.
  3. 워커 루프가 outbox 를 훑어 실제로 보낸다. 실패하면 백오프를 두고 재시도한다.

왜 이렇게 나누는가 — 발송이 이벤트 적재를 막아서는 안 된다. 수신 측이 느리거나 죽어 있을 때
`ingest_signal` 이 그 대기 시간을 물려받으면 탐지가 밀린다. `_save_snapshot` 과 같은 원칙이다.

왜 메모리 큐가 아니라 테이블인가
  · 수신 측이 죽어 있어도 이벤트를 잃지 않는다.
  · **서버를 재시작해도 대기분이 살아남는다.** 메모리 큐면 통째로 사라진다.
  · '왜 안 갔나' 를 어드민이 볼 수 있다(시도 횟수·실패 사유).

주의: 자기가 발행한 토픽에 인바운드 바인딩을 만들면 무한 루프가 된다. 서버는 `#` 을
구독하기 때문이다. 화면에서 경고하고, 여기서도 우리 아웃바운드 토픽을 로그로 남겨 둔다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import sessionmaker
from ..models import Event, OutboundDelivery, OutboundTarget
from ..mqtt.publisher import publisher
from . import template as tpl
from .bus import bus

log = logging.getLogger(__name__)

POLL_INTERVAL = 2.0          # outbox 확인 주기(초)
BATCH = 20                   # 한 번에 처리할 발송 수
MAX_BACKOFF_SEC = 300.0      # 재시도 간격 상한
RETENTION_DAYS = 14          # 전달 이력 보존


# ────────────────────────────────────────────────────────────── 대상 매칭

def matches(target: OutboundTarget, solution_code: str, camera_id: int) -> bool:
    """비어 있는 필터는 '전부'를 뜻한다."""
    sols = target.solution_codes or []
    cams = target.camera_ids or []
    if sols and solution_code not in sols:
        return False
    if cams and camera_id not in cams:
        return False
    return True


async def enqueue_for_event(session: AsyncSession, event: Event) -> int:
    """이벤트 하나에 대해 보낼 대상들을 outbox 에 적재한다. 적재 건수를 반환."""
    targets = (await session.execute(
        select(OutboundTarget).where(OutboundTarget.enabled.is_(True))
    )).scalars().all()

    ctx = tpl.context_of(event)
    made = 0
    for target in targets:
        if not matches(target, event.solution_code, event.camera_id):
            continue
        config = target.config or {}
        topic = tpl.render(config.get("topic_template") or tpl.DEFAULT_TOPIC_TEMPLATE, ctx)
        payload = tpl.render_json(target.payload_template or tpl.DEFAULT_PAYLOAD_TEMPLATE, ctx)
        session.add(OutboundDelivery(
            target_id=target.id, event_id=event.id, status="pending",
            next_attempt_at=datetime.now(timezone.utc),
            topic=topic[:400], payload=payload,
        ))
        made += 1
    if made:
        await session.commit()
    return made


# ────────────────────────────────────────────────────────────── 발송

async def _send_one(session: AsyncSession, delivery: OutboundDelivery,
                    target: OutboundTarget) -> None:
    """한 건 발송하고 결과를 기록한다. 예외를 밖으로 내지 않는다."""
    delivery.attempt += 1
    now = datetime.now(timezone.utc)
    config = target.config or {}

    try:
        if target.kind == "mqtt":
            await publisher.publish(delivery.topic, delivery.payload,
                                    qos=int(config.get("qos", 0)),
                                    retain=bool(config.get("retain", False)))
        else:
            # kind 를 늘릴 자리. 여기 한 줄만 추가하면 웹훅·알림·PLC 가 붙는다.
            raise NotImplementedError(f"지원하지 않는 대상 종류: {target.kind}")
    except Exception as exc:                                 # noqa: BLE001
        reason = str(exc)[:300]
        if delivery.attempt >= max(1, target.max_attempts):
            delivery.status = "expired"
            delivery.next_attempt_at = None
            log.warning("아웃바운드 포기(%d회 시도): %s -> %s : %s",
                        delivery.attempt, target.name, delivery.topic, reason)
        else:
            delivery.status = "failed"
            backoff = min(target.retry_backoff_sec * 2 ** (delivery.attempt - 1),
                          MAX_BACKOFF_SEC)
            delivery.next_attempt_at = now + timedelta(seconds=backoff)
            log.info("아웃바운드 실패(%d회) — %.0fs 후 재시도: %s : %s",
                     delivery.attempt, backoff, target.name, reason)
        delivery.error = reason
        target.fail_count += 1
        target.last_error = reason
        return

    delivery.status = "sent"
    delivery.sent_at = now
    delivery.next_attempt_at = None
    delivery.error = ""
    target.sent_count += 1
    target.last_sent_at = now
    target.last_error = ""
    log.info("아웃바운드 전송: %s -> %s", target.name, delivery.topic)


async def drain() -> int:
    """보낼 때가 된 것들을 처리한다. 처리한 건수를 반환."""
    now = datetime.now(timezone.utc)
    async with sessionmaker()() as session:
        rows = (await session.execute(
            select(OutboundDelivery)
            .where(OutboundDelivery.status.in_(("pending", "failed")),
                   OutboundDelivery.next_attempt_at <= now)
            .order_by(OutboundDelivery.next_attempt_at)
            .limit(BATCH)
        )).scalars().all()
        if not rows:
            return 0

        targets = {t.id: t for t in (await session.execute(
            select(OutboundTarget).where(
                OutboundTarget.id.in_({r.target_id for r in rows}))
        )).scalars().all()}

        for delivery in rows:
            target = targets.get(delivery.target_id)
            if target is None or not target.enabled:
                # 대상이 사라졌거나 꺼졌다. 붙잡고 있을 이유가 없다.
                delivery.status = "expired"
                delivery.next_attempt_at = None
                delivery.error = "대상이 삭제되었거나 비활성입니다"
                continue
            await _send_one(session, delivery, target)

        await session.commit()
        return len(rows)


# ────────────────────────────────────────────────────────────── 수명주기

_task: asyncio.Task | None = None


async def _on_event(message: dict) -> None:
    """버스에서 온 이벤트를 outbox 에 적재한다."""
    if message.get("kind") != "event":
        return
    code = (message.get("data") or {}).get("id")
    if not code:
        return
    async with sessionmaker()() as session:
        event = (await session.execute(
            select(Event).where(Event.code == code))).scalars().unique().first()
        if event is None:
            return
        made = await enqueue_for_event(session, event)
    if made:
        log.debug("아웃바운드 적재: %s -> %d건", code, made)


async def _loop() -> None:
    queue = await bus.subscribe()
    last_purge = 0.0
    loop = asyncio.get_running_loop()
    try:
        while True:
            # 버스 메시지를 기다리되, 없어도 주기적으로 outbox 를 훑는다.
            # (재시도 대기 중인 건은 새 이벤트 없이도 보내야 한다.)
            with contextlib.suppress(asyncio.TimeoutError):
                message = await asyncio.wait_for(queue.get(), timeout=POLL_INTERVAL)
                await _on_event(message)
            try:
                await drain()
            except Exception:                                # noqa: BLE001
                log.exception("아웃바운드 처리 오류 — 계속 진행합니다")

            if loop.time() - last_purge > 3600:
                last_purge = loop.time()
                with contextlib.suppress(Exception):
                    await _purge()
    except asyncio.CancelledError:
        raise
    finally:
        await bus.unsubscribe(queue)


async def _purge() -> None:
    """오래된 전달 이력 정리. 이벤트 × 대상 수로 늘어나므로 방치하면 커진다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    async with sessionmaker()() as session:
        result = await session.execute(
            delete(OutboundDelivery).where(OutboundDelivery.created_at < cutoff,
                                           OutboundDelivery.status.in_(("sent", "expired"))))
        await session.commit()
    if result.rowcount:
        log.info("아웃바운드 이력 정리: %d건 삭제 (보존 %d일)", result.rowcount, RETENTION_DAYS)


def start() -> asyncio.Task:
    global _task
    _task = asyncio.create_task(_loop(), name="outbound")
    return _task


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
        _task = None
    await publisher.close()


async def pending_count() -> int:
    """대기 중인 발송 수. 시스템 상태 화면에 보여 준다."""
    async with sessionmaker()() as session:
        rows = (await session.execute(
            select(OutboundDelivery.id).where(
                OutboundDelivery.status.in_(("pending", "failed"))))).scalars().all()
        return len(rows)
