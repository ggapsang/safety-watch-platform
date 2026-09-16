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

from ..services import viewers
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
        # 브라우저가 '지금 이 카메라를 보고 있다' 고 알려 오는 것을 받는다. 이미 열려
        # 있는 소켓을 쓴다 — 이것 때문에 REST 를 5초마다 치면 연결만 늘어난다.
        reader = asyncio.create_task(_read_viewing(ws))
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
            except asyncio.TimeoutError:
                # 유휴 연결이 프록시에 끊기지 않도록 주기적으로 신호를 보낸다.
                await ws.send_json({"kind": "ping", "data": {}})
                if reader.done():        # 상대가 끊었다. 여기서 알아채고 나간다
                    break
                continue
            await ws.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:                                     # noqa: BLE001
        log.debug("WS 종료", exc_info=True)
    finally:
        reader.cancel()
        await bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await ws.close()


async def _read_viewing(ws: WebSocket) -> None:
    """브라우저가 보내는 것을 읽는다. 지금은 '보고 있는 카메라' 하나뿐이다.

    받은 것을 그대로 믿지 않는다 — 소켓은 누구나 열 수 있고, 이 값은 추론 부하를
    좌우한다. 숫자가 아닌 것과 터무니없이 긴 목록은 버린다.

    끊기면 조용히 끝낸다. 브라우저는 탭을 닫으며 '이제 안 봅니다' 를 보낼 수 없으므로,
    끝을 알리는 신호가 아니라 **소식이 끊기는 것**으로 판단한다(services/viewers.py).
    """
    try:
        while True:
            raw = await ws.receive_json()
            if not isinstance(raw, dict) or raw.get("kind") != "viewing":
                continue
            ids = raw.get("camera_ids")
            if not isinstance(ids, list) or len(ids) > 64:
                continue
            viewers.report([int(c) for c in ids if isinstance(c, (int, float))])
    except (WebSocketDisconnect, RuntimeError, ValueError, TypeError):
        return
    except Exception:                                     # noqa: BLE001
        log.debug("WS 수신 종료", exc_info=True)
