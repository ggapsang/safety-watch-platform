"""인프로세스 이벤트 버스 — 서버 내부 → 브라우저(WebSocket) 통보용.

화면이 폴링으로 새 이벤트를 알아채면 반응이 느리고 DB 를 계속 두드린다.
탐지·카메라 상태 변화가 생긴 쪽에서 여기로 밀면, WS 핸들러가 받아 브라우저로 넘긴다.

브라우저가 브로커에 직결 구독하는 'MQTT 로그' 화면과는 별개다. 그쪽은 원문 확인용이고,
이 버스는 '가공된 이벤트'를 나른다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# 구독자가 느려서 큐가 차면 오래된 메시지부터 버린다. 관제 화면은 최신 상태가 중요하고
# 밀린 메시지를 붙잡고 있느니 끊고 다시 받는 편이 낫다.
QUEUE_SIZE = 100


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, kind: str, data: Any) -> None:
        message = {"kind": kind, "data": data}
        async with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            if q.full():
                try:
                    q.get_nowait()          # 가장 오래된 것 버리기
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:       # 경합 — 이번 건은 포기
                log.debug("이벤트 버스 큐 가득참 — 메시지 폐기")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
