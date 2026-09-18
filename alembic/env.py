"""Alembic 마이그레이션 환경.

접속 URL 은 alembic.ini 가 아니라 app.core.config(.env 의 DATABASE_URL) 에서 가져오고,
테이블 정의는 app.core.db.Base.metadata 하나로 넘긴다 (모든 기능이 같은 Base).
새 기능에 models.py 가 생기면 아래 import 에 추가한다 — import 해야 테이블이 metadata 에 등록된다.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

load_dotenv()

# 테이블은 import 되어야 Base.metadata 에 등록된다. 새 기능에 models.py 가 생기면 여기에 추가.
import app.features.persona.models  # noqa: E402, F401
import app.features.practice.models  # noqa: E402, F401
import app.features.simulation.models  # noqa: E402, F401
from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# alembic.ini 에서 logging 섹션을 뺐다 (앱 로깅과 따로 관리). 섹션이 없으면 fileConfig 가 KeyError 를 내므로 건너뛴다.
if config.config_file_name is not None and config.file_config.has_section("formatters"):
    fileConfig(config.config_file_name)

# 모든 기능이 app.core.db.Base 를 공유하므로 metadata 는 하나다
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB 없이 SQL 만 출력한다 (`alembic upgrade head --sql`)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # 컬럼 타입 변경도 autogenerate 가 잡게
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
