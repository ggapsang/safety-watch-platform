"""이벤트 API — 목록·상세·스냅샷·CSV 내려받기."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Event, Recording
from ..schemas import EventOut, EventPage, SummaryOut
from ..services import events as svc
from ..services.stats import apply_filters, local_tz

router = APIRouter(prefix="/api/events", tags=["events"])


def _parse_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    tz = local_tz()
    now = datetime.now(tz)
    if end is None:
        end = now
    if start is None:
        start = now.replace(year=now.year - 1)
    # 날짜만 넘어오면(시각 없음) 로컬 자정 기준으로 해석한다.
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    return start, end


def _scoped(stmt, *, start, end, cameras, solutions, q):
    stmt = apply_filters(stmt, start=start, end=end, cameras=cameras, solutions=solutions)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Event.code.ilike(like) | Event.event_type.ilike(like))
    return stmt


@router.get("", response_model=EventPage)
async def list_events(
    start: datetime | None = None,
    end: datetime | None = None,
    cameras: list[int] | None = Query(default=None),
    solutions: list[str] | None = Query(default=None),
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> EventPage:
    start, end = _parse_range(start, end)
    kw = dict(start=start, end=end, cameras=cameras, solutions=solutions, q=q)
    total = int((await session.execute(
        _scoped(select(func.count(Event.id)), **kw))).scalar() or 0)
    rows = (await session.execute(
        _scoped(select(Event), **kw).order_by(Event.ts.desc(), Event.id.desc())
        .limit(limit).offset(offset)
    )).scalars().unique().all()
    clips = await _clip_ids(session, [e.id for e in rows])
    return EventPage(items=[svc.to_dto(e, has_clip=e.id in clips) for e in rows],
                     total=total, limit=limit, offset=offset)


async def _clip_ids(session: AsyncSession, event_ids: list[int]) -> set[int]:
    """클립이 있는 이벤트 id 집합. 목록마다 이벤트별로 조회하면 N+1 이 된다."""
    if not event_ids:
        return set()
    rows = (await session.execute(
        select(Recording.event_id).where(Recording.event_id.in_(event_ids),
                                         Recording.kind == "event"))).scalars().all()
    return {r for r in rows if r is not None}


@router.get("/summary", response_model=SummaryOut)
async def summary(session: AsyncSession = Depends(get_session)) -> SummaryOut:
    """대시보드 상단 KPI. 화면이 카메라·이벤트를 전부 내려받아 세지 않도록 서버가 준다."""
    from ..models import Camera
    from .cameras import to_dto as cam_dto

    cams = (await session.execute(select(Camera))).scalars().unique().all()
    dtos = [cam_dto(c) for c in cams]
    normal = sum(1 for d in dtos if d.status == "normal")

    midnight = datetime.now(local_tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    today = int((await session.execute(
        select(func.count(Event.id)).where(Event.ts >= midnight))).scalar() or 0)

    return SummaryOut(cameras_total=len(dtos), cameras_normal=normal,
                      cameras_offline=len(dtos) - normal, events_today=today)


@router.get("/export.csv")
async def export_csv(
    start: datetime | None = None,
    end: datetime | None = None,
    cameras: list[int] | None = Query(default=None),
    solutions: list[str] | None = Query(default=None),
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """엑셀 내려받기. UTF-8 BOM 을 붙여야 엑셀이 한글을 깨뜨리지 않는다."""
    start, end = _parse_range(start, end)
    rows = (await session.execute(
        _scoped(select(Event), start=start, end=end, cameras=cameras, solutions=solutions, q=q)
        .order_by(Event.ts.desc())
    )).scalars().unique().all()

    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(["이벤트 ID", "발생시간", "카메라", "설치 위치", "이벤트", "탐지 소스"])
    tz = local_tz()
    for e in rows:
        writer.writerow([
            e.code, e.ts.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
            e.camera.name if e.camera else "", e.camera.location if e.camera else "",
            e.event_type, e.source,
        ])
    buf.seek(0)
    filename = f"이벤트목록_{datetime.now(tz):%Y%m%d}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


async def _get(session: AsyncSession, code: str) -> Event:
    event = (await session.execute(
        select(Event).where(Event.code == code))).scalars().unique().first()
    if event is None:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    return event


@router.get("/{code}", response_model=EventOut)
async def get_event(code: str, session: AsyncSession = Depends(get_session)) -> EventOut:
    event = await _get(session, code)
    clips = await _clip_ids(session, [event.id])
    return svc.to_dto(event, has_clip=event.id in clips)


@router.get("/{code}/snapshot")
async def snapshot(code: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    event = await _get(session, code)
    if not event.snapshot_path:
        raise HTTPException(status_code=404, detail="캡쳐 없음")
    path = Path(event.snapshot_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="캡쳐 파일이 삭제되었습니다")
    return FileResponse(str(path), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})
