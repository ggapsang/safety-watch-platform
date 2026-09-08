"""시각 정규화.

주의: DB 방언마다 시간대 취급이 다르다. PostgreSQL 의 timestamptz 는 tz-aware 로 돌아오지만
  SQLite 는 DateTime(timezone=True) 라도 **naive** 로 돌려준다. 그대로 now(UTC) 와 빼면
  TypeError 로 죽는다 — 개발(SQLite)에서만 터지고 운영에서는 안 터지는, 가장 나쁜 종류의 버그다.

그래서 DB 에서 나온 시각은 비교·직렬화 전에 반드시 여기를 거친다.
저장은 항상 UTC 기준이므로, naive 값은 UTC 로 간주하는 것이 맞다.
"""

from __future__ import annotations

from datetime import datetime, timezone


def as_utc(dt: datetime | None) -> datetime | None:
    """naive 면 UTC 로 간주해 tz 를 붙이고, aware 면 UTC 로 변환한다."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def age_sec(dt: datetime | None) -> float:
    """지금으로부터 몇 초 전인지. None 이면 무한대."""
    aware = as_utc(dt)
    if aware is None:
        return float("inf")
    return (now_utc() - aware).total_seconds()
