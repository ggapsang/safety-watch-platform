"""운영 설정 저장소 (settings 테이블).

환경변수는 '배포하면 고정'인 값이고, 여기 있는 것은 '운영 중 화면에서 바꾸는' 값이다.
경계를 흐리면 재시작해야 반영되는 값이 생겨서 현장에서 혼란이 생긴다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting

KEY_RUNTIME = "runtime"                      # 보존기간·스냅샷·중복억제 등


async def get_value(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(Setting, key)
    return row.value if row is not None else (default if default is not None else {})


async def set_value(session: AsyncSession, key: str, value: dict) -> dict:
    row = await session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        merged = dict(row.value or {})
        merged.update(value)
        row.value = merged
        value = merged
    await session.commit()
    return value


async def get_runtime(session: AsyncSession) -> dict[str, Any]:
    return dict(await get_value(session, KEY_RUNTIME, {}) or {})


async def all_settings(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(Setting))).scalars().all()
    return {r.key: r.value for r in rows}


async def replace_value(session: AsyncSession, key: str, value: dict) -> dict:
    """통째로 갈아 끼운다. set_value 와 달리 병합하지 않는다 — 지운 키는 지워진다.

    설정 파일 편집기가 쓴다. 편집기는 '보이는 것이 곧 전부' 라야 하는데 병합하면
    화면에서 지운 항목이 DB 에 남아 다음 조회에 되살아난다.
    """
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.flush()
    return value
