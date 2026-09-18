"""SQLite 로 기능의 get_db 의존성을 대체한다. Postgres 없이 돈다.

세 플레이그라운드(persona·simulation·practice)는 같은 파일(SHARED_DB)을 쓴다 —
persona 에서 만든 페르소나를 simulation·practice 가 바로 골라 쓸 수 있게.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SHARED_DB = Path(__file__).parent.parent / "persona" / "playground.db"


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
