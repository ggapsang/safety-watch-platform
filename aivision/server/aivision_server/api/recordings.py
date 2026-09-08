"""녹화 API — 타임라인 조회 · 구간 재생 · 클립 내려받기.

상시 녹화 세그먼트는 미디어 서버가 진실의 원천이라 여기서는 조회만 중계한다.
반면 이벤트 클립은 코어 자산이라 우리 파일을 직접 내보낸다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..media import backend as media_backend
from ..models import Camera, Event, Recording
from ..schemas import SegmentOut
from ..services import recording as svc
from ..services.stats import local_tz

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recordings"])


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    tz = local_tz()
    now = datetime.now(tz)
    end = end or now
    start = start or (end - timedelta(hours=6))
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    return start, end


@router.get("/cameras/{camera_id}/timeline", response_model=list[SegmentOut])
async def timeline(camera_id: int, start: datetime | None = None, end: datetime | None = None,
                   session: AsyncSession = Depends(get_session)) -> list[SegmentOut]:
    """구간 안에 녹화가 있는 시각들. 되감기 UI 가 이걸로 타임라인을 그린다."""
    if await session.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다")
    lo, hi = _range(start, end)
    segs = await svc.timeline(camera_id, lo, hi)
    return [SegmentOut(start=s.start, duration_sec=s.duration_sec, url=s.url) for s in segs]


@router.get("/cameras/{camera_id}/playback")
async def playback(camera_id: int, start: datetime,
                   duration: float = Query(default=60.0, gt=0, le=3600),
                   session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    """구간 재생. 미디어 서버가 직접 흘려보내는 편이 빠르므로 주소만 넘긴다.

    (프록시하면 서버가 영상 트래픽을 통째로 짊어진다.)
    """
    if await session.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다")
    media = media_backend()
    url = getattr(media, "playback_url", None)
    if url is None:
        raise HTTPException(status_code=503,
                            detail=f"미디어 백엔드 '{media.name}' 는 재생을 지원하지 않습니다")
    if start.tzinfo is None:
        start = start.replace(tzinfo=local_tz())
    return RedirectResponse(url(camera_id, start, duration))


@router.get("/events/{code}/clip")
async def event_clip(code: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    """이벤트 클립 파일. 코어가 소유하므로 세그먼트가 회전 삭제돼도 남아 있다."""
    event = (await session.execute(
        select(Event).where(Event.code == code))).scalars().unique().first()
    if event is None:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    rec = await svc.clip_of(session, event.id)
    if rec is None or not rec.path:
        raise HTTPException(status_code=404,
                            detail="이 이벤트에는 클립이 없습니다 (상시 녹화가 꺼져 있었거나 "
                                   "해당 구간 녹화가 없습니다)")
    path = Path(rec.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="클립 파일이 삭제되었습니다")
    return FileResponse(str(path), media_type="video/mp4", filename=f"{code}.mp4")


@router.get("/recordings")
async def list_recordings(camera_id: int | None = None, limit: int = Query(default=50, le=500),
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    """코어가 소유한 영상 자산 목록(이벤트 클립 등)."""
    stmt = select(Recording).order_by(Recording.start_at.desc()).limit(limit)
    if camera_id is not None:
        stmt = stmt.where(Recording.camera_id == camera_id)
    rows = (await session.execute(stmt)).scalars().all()
    codes = {}
    if rows:
        ev_ids = [r.event_id for r in rows if r.event_id]
        if ev_ids:
            codes = {e.id: e.code for e in (await session.execute(
                select(Event).where(Event.id.in_(ev_ids)))).scalars().unique().all()}
    return [{
        "id": r.id, "camera_id": r.camera_id, "kind": r.kind,
        "event": codes.get(r.event_id), "start_at": r.start_at,
        "duration_sec": r.duration_sec, "size_bytes": r.size_bytes, "note": r.note,
    } for r in rows]
