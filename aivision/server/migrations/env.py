"""Alembic 실행 환경.

접속 주소는 `DATABASE_URL` 환경변수에서 읽는다. alembic.ini 에 적어 두면 자격증명이
파일에 남고, 컨테이너와 로컬이 서로 다른 주소를 써야 하는 상황도 감당하지 못한다.

서버가 기동할 때 `upgrade head` 를 자동으로 돌린다(main.py). 사람이 잊어버려서
스키마가 어긋난 채 뜨는 일을 막기 위해서다.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 모델을 모두 import 해야 metadata 가 채워진다.
from aivision_server import models  # noqa: F401
from aivision_server.db import Base

config = context.config

# 주의: fileConfig 는 프로세스 전역 로깅을 통째로 재설정한다. 서버가 기동 중에 이 파일을
#   부를 때 그대로 두면 alembic.ini 의 root=WARNING 이 적용되어 **앱 로그가 전부 사라진다.**
#   (실제로 카메라 등록·미디어 경로 로그가 안 보여 원인을 한참 찾았다.)
#   그래서 CLI 로 직접 돌릴 때만 설정한다. 임베드 실행은 main.py 가 attributes 로 알려 준다.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL 이 없습니다. 마이그레이션 대상 DB 를 알 수 없습니다.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata,
                      literal_binds=True, compare_type=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
