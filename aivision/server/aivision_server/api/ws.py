"""WebSocket — 새 이벤트·카메라 상태 변화를 화면에 즉시 밀어준다.

폴링이 아니라 푸시를 쓰는 이유: 관제 화면은 '지금 뜬 이벤트'가 몇 초 늦으면 의미가 준다.
그리고 여기에 바운딩 박스 채널을 함께 태운다 — 영상(MJPEG)과 박스(JSON)를 분리해 두면
나중에 영상 전송 방식을 바꿔도 오버레이 코드는 손대지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.bus import bus

log = logging.getLogger(__name__)
router = APIRouter(tags=["ws"])

PING_INTERVAL = 20.0


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue = await bus.subscribe()
    log.debug("WS 접속 (구독자 %d)", bus.subscriber_count)
    try:
        await ws.send_json({"kind": "hello", "data": {"ok": True}})
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
            except asyncio.TimeoutError:
                # 유휴 연결이 프록시에 끊기지 않도록 주기적으로 신호를 보낸다.
                await ws.send_json({"kind": "ping", "data": {}})
                continue
            await ws.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:                                     # noqa: BLE001
        log.debug("WS 종료", exc_info=True)
    finally:
        await bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await ws.close()
