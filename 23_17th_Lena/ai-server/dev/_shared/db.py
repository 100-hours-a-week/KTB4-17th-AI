"""SQLite 로 기능의 get_db 의존성을 대체한다. Postgres 없이 돈다."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class SQLite:
    def __init__(self, path: Path) -> None:
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def get_db(self):
        """`app.dependency_overrides[feature_api.get_db] = sqlite.get_db`"""
        async with self.sessions() as db:
            yield db

    async def create_tables(self, base) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
