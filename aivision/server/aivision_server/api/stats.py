"""통계 API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import StatsOut
from ..services import stats as svc

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
async def get_stats(
    bucket: str = Query(default="daily", pattern="^(hourly|daily|weekly|monthly)$"),
    start: datetime | None = None,
    end: datetime | None = None,
    cameras: list[int] | None = Query(default=None),
    solutions: list[str] | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> StatsOut:
    tz = svc.local_tz()
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    return await svc.compute(session, bucket=bucket, start=start, end=end,
                             cameras=cameras, solutions=solutions)
