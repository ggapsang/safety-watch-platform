"""영상 API — MJPEG 스트림과 단일 프레임 스냅샷."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from ..streaming.manager import manager
from ..streaming.mjpeg import mjpeg_response

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/{camera_id}")
async def stream(camera_id: int) -> StreamingResponse:
    """MJPEG 스트림. 화면에서는 <img src="/api/stream/1"> 한 줄로 끝난다.

    주의: 브라우저는 같은 호스트에 대한 동시 연결 수가 제한된다(HTTP/1.1 기준 6개).
      MJPEG 은 연결을 붙잡고 있으므로, 한 화면에 스트림을 6개 이상 띄우면 나머지 API 가
      막힌다. 카메라가 늘어나면 화면당 동시 재생 수를 제한하거나 HTTP/2 를 앞단에 둘 것.
    """
    return await mjpeg_response(camera_id)


@router.get("/{camera_id}/snapshot.jpg")
async def snapshot(camera_id: int) -> Response:
    """현재 프레임 1장. 목록 썸네일처럼 상시 스트림이 필요 없는 곳에서 쓴다."""
    worker = manager.get(camera_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="스트리밍 중인 카메라가 아닙니다")
    jpeg = worker.snapshot_jpeg()
    if jpeg is None:
        raise HTTPException(status_code=503, detail="아직 수신된 프레임이 없습니다")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
