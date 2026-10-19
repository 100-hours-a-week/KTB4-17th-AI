import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from app.v2.persona.models import MemoryUpdateJob
from app.v2.persona.repository import PersonaMemoryRepository, build_demoted_core_sentences
from tests.conftest import with_db


def test_core_profile_demotion_only_for_objective_slots():
    old_profile = {"region": "서울", "job": "대학생", "personality": "차분한 편"}
    updates = {"region": "부산", "job": "대학생", "personality": "조용하고 차분함"}
    demoted = build_demoted_core_sentences(old_profile, updates)
    assert demoted == ["과거에 서울에 거주했었다"]


def test_add_fact_dedup_and_search_promote():
    async def body(factory):
        async with factory() as db_session:
            repo = PersonaMemoryRepository(db_session)
            vec = [1.0] + [0.0] * 1023
            r1 = await repo.add_fact(
                user_id="u1",
                category="과거이력",
                fact="과거에 서울에 거주했었다",
                confidence=1.0,
                status="confirmed",
                embedding=vec,
                embedding_model="KURE-v1",
            )
            r2 = await repo.add_fact(
                user_id="u1",
                category="과거이력",
                fact="과거에 서울에 거주했었다",
                confidence=1.0,
                status="confirmed",
                embedding=vec,
                embedding_model="KURE-v1",
            )
            assert r1.id == r2.id

            fact_row = await repo.add_fact(
                user_id="user-1",
                category="취미",
                fact="주말마다 등산을 다닌다",
                confidence=0.65,
                status="tentative",
                embedding=vec,
                embedding_model="KURE-v1",
            )
            assert await repo.search_confirmed_facts("user-1", vec) == []

            await repo.apply_reconcile_action(
                action="PROMOTE",
                target_id=fact_row.id,
                user_id="user-1",
                category="취미",
                fact="주말마다 등산을 다닌다",
                confidence=0.85,
                status="confirmed",
                embedding=vec,
                embedding_model="KURE-v1",
            )
            res = await repo.search_confirmed_facts("user-1", vec)
            assert len(res) == 1 and res[0][1] == "주말마다 등산을 다닌다"

    asyncio.run(with_db(body))


def test_active_memory_job_unique_constraint():
    async def body(factory):
        async with factory() as db_session:
            db_session.add(MemoryUpdateJob(user_id="user-1", trigger_reason="turn_count", status="running"))
            await db_session.commit()

            db_session.add(MemoryUpdateJob(user_id="user-1", trigger_reason="signal", status="pending"))
            with pytest.raises(IntegrityError):
                await db_session.commit()

    asyncio.run(with_db(body))
