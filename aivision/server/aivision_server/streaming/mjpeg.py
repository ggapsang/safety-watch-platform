"""MJPEG 응답 생성.

왜 MJPEG 인가: 카메라 2대, 동시 시청자 1~2명 규모다. WebRTC(WHEP) 는 지연이 짧지만
시그널링 서버·STUN·미디어 서버가 붙어 운영 요소가 늘어난다. MJPEG 은 <img src> 하나로
끝나고 프록시·방화벽을 타지 않으며, 인코딩도 카메라당 1회로 공유된다.

대역폭 감각(1280px, q75 기준): 프레임당 약 60~120KB × 12fps ≒ 6~12Mbps/스트림.
LAN 에서 2대 × 2명이면 여유가 있다. 이 전제가 깨지면(카메라 증설·원격 접속) 다시 검토한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .manager import manager

BOUNDARY = "aivisionframe"


async def mjpeg_response(camera_id: int) -> StreamingResponse:
    worker = manager.get(camera_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="스트리밍 중인 카메라가 아닙니다")

    async def gen() -> AsyncIterator[bytes]:
        last = -1
        try:
            while True:
                # wait_jpeg 은 블로킹이므로 스레드로 넘긴다(이벤트 루프를 막지 않도록).
                seq, jpeg = await asyncio.to_thread(worker.wait_jpeg, last, 2.0)
                if jpeg is None:
                    await asyncio.sleep(0.2)
                    continue
                if seq == last:
                    continue          # 타임아웃 — 새 프레임 없음
                last = seq
                yield (
                    b"--" + BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        except asyncio.CancelledError:      # 브라우저가 탭을 닫으면 여기로 온다
            raise

    return StreamingResponse(
        gen(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",     # 앞단에 nginx 가 붙어도 버퍼링하지 않도록
        },
    )
