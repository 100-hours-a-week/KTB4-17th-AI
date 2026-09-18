"""프로젝트 공통 DB 세션. 기능별 api.py 는 여기 get_db 를 import 해서 Depends 에 건다.

커밋은 라우트가 직접 한다(`await db.commit()`). 여기서는 세션을 열고 닫기만 한다.
플레이그라운드(dev/)는 `app.dependency_overrides[get_db] = ...` 로 SQLite 로 갈아끼운다.

Base 도 여기 둔다. 기능마다 Base 를 따로 만들면 simulation → personas 처럼
기능 간 FK 를 걸 때 MetaData 가 달라 테이블을 못 찾는다. 모든 features/*/models.py 가 이 Base 를 쓴다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,  # 끊긴 커넥션을 풀에서 꺼내 쓰다 죽는 걸 막는다
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        yield db
