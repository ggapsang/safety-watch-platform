"""통계 집계.

시각은 DB 에 UTC 로 저장하고, 집계·표시는 **현장 로컬 시간대**로 한다.
(현장에서 '어제 몇 건'은 당연히 로컬 날짜 기준이다. UTC 로 끊으면 오전 9시 이전 건이
 전날로 밀려 통계가 어긋난다.)

구간 집계는 파이썬에서 한다. date_trunc / strftime 은 DB 방언마다 다르고,
이 규모(카메라 2대 · 연간 수천 건)에서는 SQL 로 내릴 이득이 없다.
데이터가 수십만 건 단위로 커지면 그때 구간별 집계 테이블을 두는 것이 맞다.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, timedelta, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Camera, Event, Solution
from ..schemas import CameraCount, SeriesPoint, SolutionCount, StatsOut

log = logging.getLogger(__name__)

BUCKETS = ("hourly", "daily", "weekly", "monthly")


@lru_cache(maxsize=4)
def local_tz() -> tzinfo:
    """현장 로컬 시간대.

    주의: Windows 에는 IANA 시간대 DB 가 없어 ZoneInfo("Asia/Seoul") 이 실패한다.
      (requirements 의 tzdata 패키지가 이를 채워 준다.) 그래도 못 찾으면 통계 화면 전체가
      500 으로 죽는 것보다 시스템 로컬 시간대로 내려가는 편이 낫다.
    """
    key = os.environ.get("TZ", "Asia/Seoul")
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        fallback = datetime.now().astimezone().tzinfo
        log.warning("시간대 '%s' 를 찾을 수 없습니다 — 시스템 로컬(%s)로 대체합니다. "
                    "정확한 집계를 위해 tzdata 설치를 권장합니다.", key, fallback)
        return fallback or ZoneInfo("UTC")


def default_range(bucket: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """구간별 기본 조회 범위. 화면 탭을 누르면 이 범위가 쓰인다."""
    tz = local_tz()
    now = (now or datetime.now(tz)).astimezone(tz)
    end = now
    if bucket == "hourly":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif bucket == "daily":
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif bucket == "weekly":
        monday = now - timedelta(days=now.weekday())
        start = (monday - timedelta(weeks=3)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = first
        for _ in range(5):
            start = (start - timedelta(days=1)).replace(day=1)
    return start, end


def _bucket_keys(bucket: str, start: datetime, end: datetime) -> list[tuple[str, str]]:
    """(정렬키, 표시라벨) 목록. 발생이 0인 구간도 빠지지 않게 미리 만든다."""
    tz = local_tz()
    start, end = start.astimezone(tz), end.astimezone(tz)
    out: list[tuple[str, str]] = []
    if bucket == "hourly":
        cur = start.replace(minute=0, second=0, microsecond=0)
        while cur <= end:
            out.append((cur.strftime("%Y-%m-%dT%H"), f"{cur.hour:02d}시"))
            cur += timedelta(hours=1)
    elif bucket == "daily":
        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cur.date() <= end.date():
            out.append((cur.strftime("%Y-%m-%d"), f"{cur.month}/{cur.day}"))
            cur += timedelta(days=1)
    elif bucket == "weekly":
        cur = (start - timedelta(days=start.weekday())).replace(hour=0, minute=0, second=0,
                                                                microsecond=0)
        while cur <= end:
            out.append((cur.strftime("%G-W%V"), f"{cur.month}/{cur.day}주"))
            cur += timedelta(weeks=1)
    else:
        cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= end:
            out.append((cur.strftime("%Y-%m"), f"{cur.month}월"))
            cur = (cur.replace(day=28) + timedelta(days=5)).replace(day=1)
    return out


def _key_of(ts: datetime, bucket: str) -> str:
    ts = ts.astimezone(local_tz())
    if bucket == "hourly":
        return ts.strftime("%Y-%m-%dT%H")
    if bucket == "daily":
        return ts.strftime("%Y-%m-%d")
    if bucket == "weekly":
        return ts.strftime("%G-W%V")
    return ts.strftime("%Y-%m")


def apply_filters(stmt, *, start: datetime, end: datetime,
                  cameras: list[int] | None = None,
                  solutions: list[str] | None = None):
    """화면의 필터 규칙: 같은 항목 안에서는 OR, 항목끼리는 AND."""
    stmt = stmt.where(Event.ts >= start, Event.ts <= end)
    if cameras:
        stmt = stmt.where(Event.camera_id.in_(cameras))
    if solutions:
        stmt = stmt.where(Event.solution_code.in_(solutions))
    return stmt


async def compute(session: AsyncSession, *, bucket: str = "daily",
                  start: datetime | None = None, end: datetime | None = None,
                  cameras: list[int] | None = None,
                  solutions: list[str] | None = None,
                  top_cameras: int = 10) -> StatsOut:
    if bucket not in BUCKETS:
        bucket = "daily"
    if start is None or end is None:
        start, end = default_range(bucket)

    def scoped(stmt):
        return apply_filters(stmt, start=start, end=end, cameras=cameras, solutions=solutions)

    # ── 시계열 ──────────────────────────────────────────────────────
    rows = (await session.execute(scoped(select(Event.ts)))).scalars().all()
    counts = Counter(_key_of(ts, bucket) for ts in rows)
    series = [SeriesPoint(key=key, label=label, count=counts.get(key, 0))
              for key, label in _bucket_keys(bucket, start, end)]

    # ── 솔루션별 ────────────────────────────────────────────────────
    sol_rows = (await session.execute(
        scoped(select(Event.solution_code, func.count(Event.id))).group_by(Event.solution_code)
    )).all()
    sol_counts = {code: int(n) for code, n in sol_rows}
    all_solutions = (await session.execute(
        select(Solution).where(Solution.enabled.is_(True)).order_by(Solution.sort_order)
    )).scalars().all()
    by_solution = [SolutionCount(code=s.code, short_name=s.short_name, color=s.color,
                                 count=sol_counts.get(s.code, 0)) for s in all_solutions]

    # ── 카메라별 상위 ───────────────────────────────────────────────
    cam_rows = (await session.execute(
        scoped(select(Event.camera_id, func.count(Event.id)))
        .group_by(Event.camera_id)
        .order_by(func.count(Event.id).desc())
        .limit(top_cameras)
    )).all()
    cam_map = {c.id: c for c in (await session.execute(select(Camera))).scalars().all()}
    by_camera = [
        CameraCount(camera_id=cid,
                    name=cam_map[cid].name if cid in cam_map else f"카메라 {cid}",
                    location=cam_map[cid].location if cid in cam_map else "",
                    count=int(n))
        for cid, n in cam_rows
    ]

    return StatsOut(
        bucket=bucket, start=start, end=end, total=len(rows), series=series,
        by_solution=by_solution, by_camera=by_camera,
    )
