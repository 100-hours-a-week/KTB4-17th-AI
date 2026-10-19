import asyncio
from datetime import UTC, datetime, timedelta

from app.v2.persona.graph import create_memory_graph
from app.v2.persona.models import MemoryUpdateJob
from app.v2.persona.repository import PersonaMemoryRepository
from app.v2.persona.schemas import ExtractedFactItem, ExtractionOutput
from app.v2.persona.service import try_create_memory_job
from app.v2.persona.trigger import should_trigger_memory_update
from tests.conftest import with_db


class FakeEmbedder:
    async def encode_query(self, text: str) -> list[float]:
        return await self.encode(text)

    async def encode(self, text: str) -> list[float]:
        return [1.0, 0.0] + [0.0] * 1022

    async def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.encode(t) for t in texts]


class FakeExtractor:
    def __init__(self, output: ExtractionOutput) -> None:
        self.output = output
        self.called = 0

    async def extract(self, **kwargs) -> ExtractionOutput:
        self.called += 1
        return self.output


class FakeJudge:
    def __init__(self) -> None:
        self.called = 0

    async def judge_batch(self, items):
        self.called += 1
        return [{"action": "UPDATE", "target_id": items[0][3][0][0].id, "updated_fact": items[0][1]["fact"]}]


def test_trivial_batch_skips_llm_and_advances_cursor():
    """사용자 메시지가 전부 단답('ㅋㅋㅋㅋ', 'ㅇㅇ')이면 LLM 호출 없이 커서(last_processed_message_id)만 전진한다."""

    async def body(factory):
        async with factory() as db_session:
            repo = PersonaMemoryRepository(db_session)
            embedder = FakeEmbedder()
            extractor = FakeExtractor(ExtractionOutput())
            judge = FakeJudge()
            graph = create_memory_graph(repo=repo, embedder=embedder, extractor=extractor, judge=judge)

            await graph.ainvoke(
                {
                    "user_id": "u1",
                    "current_date": "2026-10-16",
                    "dialogue_pairs": [
                        {"ai": "반가워요", "user": "ㅋㅋㅋㅋㅋㅋ", "message_id": 11, "is_overlap": False},
                        {"ai": "오늘 날씨 좋죠?", "user": "ㅇㅇ", "message_id": 12, "is_overlap": False},
                    ],
                    "latest_message_id": 12,
                    "existing_core": {},
                }
            )
            assert extractor.called == 0
            core = await repo.get_core("u1")
            assert core is not None
            assert core.last_processed_message_id == 12

    asyncio.run(with_db(body))


def test_numeric_difference_forces_llm_judge_even_when_sim_is_1():
    """코사인 유사도가 1.0(>=0.99)이어도 숫자가 다르면('3회' vs '5회') NOOP이 아니라 Judge를 타야 한다."""

    async def body(factory):
        async with factory() as db_session:
            repo = PersonaMemoryRepository(db_session)
            embedder = FakeEmbedder()
            await repo.add_fact(
                user_id="u1",
                category="일상",
                fact="주 3회 헬스장에 간다",
                confidence=0.9,
                status="confirmed",
                embedding=await embedder.encode("헬스"),
                embedding_model="KURE-v1",
            )
            extractor = FakeExtractor(
                ExtractionOutput(
                    facts=[ExtractedFactItem(category="일상", fact="주 5회 헬스장에 간다", confidence=0.95)]
                )
            )
            judge = FakeJudge()
            graph = create_memory_graph(repo=repo, embedder=embedder, extractor=extractor, judge=judge)

            await graph.ainvoke(
                {
                    "user_id": "u1",
                    "current_date": "2026-10-16",
                    "dialogue_pairs": [
                        {"ai": "운동 자주 하세요?", "user": "요즘 주 5회 헬스장 가요", "message_id": 20}
                    ],
                    "latest_message_id": 20,
                    "existing_core": {},
                }
            )
            assert judge.called == 1
            facts = await repo.search_confirmed_facts("u1", await embedder.encode("헬스"))
            assert facts[0][1] == "주 5회 헬스장에 간다"

            ok, reason = await should_trigger_memory_update(
                db_session, user_id="u1", latest_user_message="저 다음 달에 부산으로 이사해요 기억해줘!"
            )
            assert ok is True and reason == "signal"

    asyncio.run(with_db(body))


def test_stale_job_is_reclaimed_automatically():
    async def body(factory):
        async with factory() as db_session:
            stale_time = datetime.now(UTC) - timedelta(minutes=15)
            old_job = MemoryUpdateJob(
                user_id="u1",
                trigger_reason="turn_count",
                status="running",
                created_at=stale_time,
                started_at=stale_time,
            )
            db_session.add(old_job)
            await db_session.commit()

            new_job = await try_create_memory_job(db_session, "u1", "signal")
            assert new_job is not None
            assert old_job.status == "failed"
            assert "stale" in (old_job.error or "")

    asyncio.run(with_db(body))


def test_onboarding_source_never_advances_cursor():
    """온보딩 turn id 는 practice_messages id 와 다른 번호 공간 — source_type != practice 면 커서를 건드리지 않는다."""

    async def body(factory):
        async with factory() as db_session:
            repo = PersonaMemoryRepository(db_session)
            extractor = FakeExtractor(
                ExtractionOutput(
                    facts=[ExtractedFactItem(category="취미", fact="주말마다 등산을 다닌다", confidence=0.95)]
                )
            )
            graph = create_memory_graph(repo=repo, embedder=FakeEmbedder(), extractor=extractor, judge=FakeJudge())
            await graph.ainvoke(
                {
                    "user_id": "u1",
                    "current_date": "2026-10-17",
                    "source_type": "onboarding",
                    "dialogue_pairs": [{"ai": "취미가 뭐예요?", "user": "주말마다 등산 가요", "message_id": 9999}],
                    "latest_message_id": 9999,
                    "existing_core": {},
                }
            )
            core = await repo.get_core("u1")
            assert core is not None and core.last_processed_message_id == 0
            assert len(await repo.search_confirmed_facts("u1", await FakeEmbedder().encode("등산"))) == 1

    asyncio.run(with_db(body))


def test_llm_garbage_core_keys_and_category_are_sanitized():
    """LLM 이 코어 슬롯 밖의 키, null 값, 정의에 없는 카테고리를 내도 작업이 실패하지 않고 걸러진다."""

    async def body(factory):
        async with factory() as db_session:
            repo = PersonaMemoryRepository(db_session)
            out = ExtractionOutput.model_validate(
                {
                    "core_updates": {"job": "개발자", "hacker_key": "x", "region": None},
                    "facts": [{"category": "???", "fact": "주말마다 등산을 다닌다", "confidence": 0.9}],
                }
            )
            graph = create_memory_graph(
                repo=repo, embedder=FakeEmbedder(), extractor=FakeExtractor(out), judge=FakeJudge()
            )
            await graph.ainvoke(
                {
                    "user_id": "u1",
                    "current_date": "2026-10-17",
                    "dialogue_pairs": [{"ai": "뭐 하세요?", "user": "개발자고 주말엔 등산해요", "message_id": 5}],
                    "latest_message_id": 5,
                    "existing_core": {},
                }
            )
            core = await repo.get_core("u1")
            assert core is not None and core.profile == {"job": "개발자"}
            facts = await repo.search_confirmed_facts("u1", await FakeEmbedder().encode("등산"))
            assert len(facts) == 1

    asyncio.run(with_db(body))
