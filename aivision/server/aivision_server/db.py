"""DB 엔진·세션.

SQLAlchemy 2.0 async. 운영은 PostgreSQL, 개발은 SQLite 로도 뜬다
(DATABASE_URL=sqlite+aiosqlite:///./dev.db).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (AsyncEngine, AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url,
            echo=s.db_echo,
            pool_pre_ping=True,      # 유휴 커넥션이 끊긴 뒤의 첫 쿼리 실패를 막는다
            pool_size=10,
            max_overflow=20,
        )
        if s.database_url.startswith("sqlite"):
            # SQLite 는 외래키를 기본으로 강제하지 않는다. 켜 두지 않으면 카메라를 지워도
            # 이벤트가 남아 '유령 이벤트'가 생긴다(운영 DB 인 PostgreSQL 은 기본 강제).
            @event.listens_for(_engine.sync_engine, "connect")
            def _fk_on(dbapi_conn, _record):                # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성."""
    async with sessionmaker()() as session:
        yield session


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
