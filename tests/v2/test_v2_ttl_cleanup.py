import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.v2.persona.models import UserPersonaFact
from app.v2.persona.repository import PersonaMemoryRepository
from app.v2.persona.service import cleanup_expired_tentative_facts, reembed_outdated_facts
from tests.conftest import with_db


class FakeEmbedder:
    async def encode_query(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 1023

    async def encode(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 1023

    async def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _ in texts]


def test_cleanup_expired_tentative_facts_and_reembed_outdated():
    async def body(factory):
        async with factory() as db_session:
            now = datetime.now(UTC)
            old_date = now - timedelta(days=35)
            recent_date = now - timedelta(days=10)

            db_session.add_all(
                [
                    UserPersonaFact(user_id="u1", fact="오래된 미확정 사실", status="tentative", created_at=old_date),
                    UserPersonaFact(user_id="u1", fact="최근 미확정 사실", status="tentative", created_at=recent_date),
                    UserPersonaFact(
                        user_id="u1",
                        fact="오래된 확정 사실",
                        status="confirmed",
                        embedding=[0.5] * 1024,
                        embedding_model="old-model-v0",
                        created_at=old_date,
                    ),
                ]
            )
            await db_session.commit()

            deleted = await cleanup_expired_tentative_facts(db_session, ttl_days=30)
            assert deleted == 1

            remaining = list((await db_session.execute(select(UserPersonaFact))).scalars())
            assert {r.fact for r in remaining} == {"최근 미확정 사실", "오래된 확정 사실"}

            repo = PersonaMemoryRepository(db_session)
            reembedded = await reembed_outdated_facts(
                db_session, FakeEmbedder(), target_model_name="nlpai-lab/KURE-v1-onnx-int8"
            )
            assert reembedded == 2  # 남아 있는 2개 행 모두 최신 모델 버전으로 재임베딩됨
            confirmed = await repo.search_confirmed_facts("u1", [1.0] + [0.0] * 1023)
            assert len(confirmed) == 1 and confirmed[0][1] == "오래된 확정 사실"

    asyncio.run(with_db(body))
