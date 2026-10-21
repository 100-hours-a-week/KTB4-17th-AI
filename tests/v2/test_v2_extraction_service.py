"""페르소나 추출 — 테이블 제약, 작업 생성·실행 (in-memory SQLite + 진짜 repository, LLM 만 가짜)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_persona, with_db
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import ConversationStyle
from app.v2.persona_extraction.agents import ExtractionFailed
from app.v2.persona_extraction.models import V2ExtractionJob, V2ImportedUtterance
from app.v2.persona_extraction.parsers import UnknownFormat
from app.v2.persona_extraction.repository import ExtractionRepository
from app.v2.persona_extraction.schemas import ScoreObservation, V2Extraction
from app.v2.persona_extraction.service import (
    ExtractionService,
    JobInProgress,
    NoStyleToDelete,
    PersonaNotFound,
    SpeakerNotFound,
    TooFewUtterances,
)


def test_only_one_active_job_per_user():
    async def body(factory):
        async with factory() as db:
            db.add_all(
                [V2ExtractionJob(user_id="u1", kind="conversation"), V2ExtractionJob(user_id="u1", kind="conversation")]
            )
            with pytest.raises(IntegrityError):
                await db.flush()

    asyncio.run(with_db(body))


def test_finished_jobs_do_not_block_new_ones():
    async def body(factory):
        async with factory() as db:
            db.add_all(
                [
                    V2ExtractionJob(user_id="u1", kind="conversation", status="failed"),
                    V2ExtractionJob(user_id="u1", kind="conversation", status="succeeded"),
                    V2ExtractionJob(user_id="u1", kind="conversation"),
                ]
            )
            await db.flush()

    asyncio.run(with_db(body))


def test_same_utterance_twice_is_rejected():
    async def body(factory):
        async with factory() as db:
            at = datetime(2026, 10, 5, 6, 12, tzinfo=UTC)
            kw = {
                "user_id": "u1",
                "sent_at": at,
                "content": "ㅋㅋ",
                "content_hash": "h",
                "occurrence": 0,
                "job_id": "j",
            }
            db.add_all([V2ImportedUtterance(**kw), V2ImportedUtterance(**kw)])
            with pytest.raises(IntegrityError):
                await db.flush()

    asyncio.run(with_db(body))


def test_existing_identities_finds_already_imported():
    async def body(factory):
        async with factory() as db:
            at = datetime(2026, 10, 5, 6, 12, tzinfo=UTC)
            db.add(
                V2ImportedUtterance(
                    user_id="u1", sent_at=at, content="ㅋㅋ", content_hash="h", occurrence=0, job_id="j"
                )
            )
            await db.flush()

            found = await ExtractionRepository(db).existing_identities("u1", [(at, "h", 0), (at, "h", 1)])

            assert found == {(at, "h", 0)}

    asyncio.run(with_db(body))


# 카카오톡 PC 형식 — 민수 발화 n_mine 개(오후 3:start_minute 부터 1분 간격) + 지은 발화
def _kakao(n_mine, *, start_minute=0, other=2):
    lines = ["--------------- 2026년 10월 5일 일요일 ---------------"]
    for i in range(n_mine):
        lines.append(f"[민수] [오후 3:{start_minute + i:02d}] 내 말 {start_minute + i}")
    for i in range(other):
        lines.append(f"[지은] [오후 4:{i:02d}] 지은 말 {i}")
    return "\n".join(lines) + "\n"


async def _with_me(db):
    await seed_persona(db, persona_id="partner", user_id="u-partner", nickname="지수")
    await seed_persona(
        db,
        persona_id="me",
        user_id="u-me",
        nickname="민수",
        scores={"disclosure": 70},
        confidence={"disclosure": "HIGH"},
    )


def _scenario(steps):
    async def body(factory):
        async with factory() as db:
            await _with_me(db)
        return await steps(factory)

    return asyncio.run(with_db(body))


def test_start_conversation_saves_only_my_new_utterances():
    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
            saved = await ExtractionRepository(db).job_utterances(job.id)
            return job, saved

    job, saved = _scenario(steps)

    assert job.status == "pending" and job.kind == "conversation"
    assert len(saved) == 12 and all("지은" not in u.content for u in saved)


def test_reupload_skips_seen_and_rejects_when_too_few_new():
    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            job.status = "succeeded"
            await db.commit()
        async with factory() as db:
            with pytest.raises(TooFewUtterances) as e:
                await ExtractionService(db).start_conversation("u-me", "민수", _kakao(15))  # 새 발화 3개
            return e.value.count

    assert _scenario(steps) == 3


def test_unknown_speaker_format_and_persona():
    async def steps(factory):
        async with factory() as db:
            svc = ExtractionService(db)
            with pytest.raises(SpeakerNotFound):
                await svc.start_conversation("u-me", "철수", _kakao(12))
            with pytest.raises(UnknownFormat):
                await svc.start_conversation("u-me", "민수", "그냥 텍스트")
            with pytest.raises(PersonaNotFound):
                await svc.start_conversation("u-nobody", "민수", _kakao(12))

    _scenario(steps)


def test_second_request_while_active_is_job_in_progress():
    async def steps(factory):
        async with factory() as db:
            first = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        async with factory() as db:
            with pytest.raises(JobInProgress) as e:
                await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12, start_minute=20))
            return first.id, e.value.job_id

    first_id, blocking_id = _scenario(steps)

    assert first_id == blocking_id


def test_stale_running_job_is_failed_and_does_not_block():
    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            job.status = "running"
            job.started_at = datetime(2026, 1, 1, tzinfo=UTC)  # 아주 오래 전
            await db.commit()
        async with factory() as db:
            new = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12, start_minute=20))
            await db.commit()
            old = await ExtractionService(db).get_job(job.id)
            return old.status, new.status

    assert _scenario(steps) == ("failed", "pending")


class FakeStyle:
    def __init__(self, error=None, scores=None):
        self.error = error
        self.scores = scores if scores is not None else {"disclosure": ScoreObservation(value=40, evidence=[1, 2, 3])}
        self.calls = []

    async def analyze(self, transcript, *, previous, trace_metadata=None):
        self.calls.append({"transcript": transcript, "previous": previous})
        if self.error:
            raise self.error
        return V2Extraction(
            style=ConversationStyle(
                speech_level="반말", frequent_phrases=["내 말", "민수 최고", "010-1"], interjections=["헐"]
            ),
            scores=self.scores,
        )


# 백그라운드 실행을 흉내낸다: 새 세션에서 run → 또 새 세션에서 작업을 읽는다
async def _run(factory, job_id, agent):
    async with factory() as db:
        await ExtractionService(db, agent=agent).run(job_id)
    async with factory() as db:
        return await ExtractionRepository(db).get_job(job_id)


def test_run_conversation_creates_confirmed_version_and_marks_utterances():
    agent = FakeStyle()

    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        done = await _run(factory, job.id, agent)
        async with factory() as db:
            latest = await PersonaRepository(db).latest_persona_for_user("u-me")
            saved = await ExtractionRepository(db).job_utterances(job.id)
        return done, latest, saved

    done, latest, saved = _scenario(steps)

    assert done.status == "succeeded" and done.persona_id == latest.id and done.analyzed_count == 12
    assert latest.source == "kakao" and latest.is_confirmed
    assert latest.scores["disclosure"] == 61  # 70*0.7 + 40*0.3
    assert latest.conversation_style["frequent_phrases"] == ["내 말"]  # 이름·숫자 문구는 걸러짐
    assert latest.conversation_style["interjections"] == []  # 발화에 없는 감탄사(프롬프트 예시 누출)는 버림
    assert all(u.reflected_persona_id == latest.id for u in saved)


def test_run_conversation_analyzes_recent_n_but_marks_all(monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_max_utterances", 5)
    agent = FakeStyle()

    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        done = await _run(factory, job.id, agent)
        async with factory() as db:
            saved = await ExtractionRepository(db).job_utterances(job.id)
        return done, saved

    done, saved = _scenario(steps)

    lines = agent.calls[0]["transcript"].splitlines()
    assert [line for line in lines if line.startswith("나[")] == [
        f"나[{n}]: 내 말 {i}" for n, i in enumerate(range(7, 12), 1)
    ]
    assert "내 말 6" not in agent.calls[0]["transcript"]  # 최근 5개만, 오래된 순
    assert done.analyzed_count == 5 and done.status == "succeeded"
    assert all(u.reflected_persona_id == done.persona_id for u in saved)


def test_run_failure_marks_job_failed_and_keeps_utterances_unreflected():
    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        done = await _run(factory, job.id, FakeStyle(error=ExtractionFailed("timeout")))
        async with factory() as db:
            saved = await ExtractionRepository(db).job_utterances(job.id)
        return done, saved

    done, saved = _scenario(steps)

    assert done.status == "failed" and "timeout" in done.error
    assert len(saved) == 12 and all(u.reflected_persona_id is None for u in saved)


def test_second_extraction_passes_previous_style():
    agent = FakeStyle()

    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        await _run(factory, job.id, FakeStyle())
        async with factory() as db:
            job2 = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12, start_minute=20))
            await db.commit()
        await _run(factory, job2.id, agent)

    _scenario(steps)

    assert agent.calls[0]["previous"].speech_level == "반말"


def test_onboarding_rebuild_drops_style():
    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        await _run(factory, job.id, FakeStyle())
        async with factory() as db:
            repo = PersonaRepository(db)
            session = await repo.get_session_brief("s-me")
            draft, _ = await repo.save_persona(session, {"disclosure": 80}, {}, {})
            # 추출 버전의 confirmed_at(now) 보다 늦어야 최신 확정이 된다
            await repo.save_confirmation(draft, None, datetime.now(UTC) + timedelta(seconds=1))
            await db.commit()
        async with factory() as db:
            latest = await PersonaRepository(db).latest_persona_for_user("u-me")
        return latest

    latest = _scenario(steps)

    assert latest.conversation_style is None and latest.source == "llm"


def test_delete_style_removes_style_and_restores_original_scores():
    agent = FakeStyle()

    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        await _run(factory, job.id, agent)

        # 이제 스타일이 추가된 상태에서 delete_style 호출
        async with factory() as db:
            resp = await ExtractionService(db).delete_style("u-me")
            await db.commit()

        # 다시 delete_style 호출 시 NoStyleToDelete 발생
        async with factory() as db:
            with pytest.raises(NoStyleToDelete):
                await ExtractionService(db).delete_style("u-me")

        return resp

    resp = _scenario(steps)

    assert resp.conversation_style is None
    assert resp.source == "reset"
    assert resp.scores["disclosure"] == 70  # 원래 온보딩 점수로 복원됨


def test_partner_lines_reach_the_llm_as_context_but_are_never_stored():
    agent = FakeStyle()

    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
            stored_context = job.context_payload
        await _run(factory, job.id, agent)
        async with factory() as db:
            saved = await ExtractionRepository(db).job_utterances(job.id)
        return stored_context, saved

    stored_context, saved = _scenario(steps)

    assert "지은 말 0" in stored_context  # 실행 전까지 작업 행에 임시로 있다
    assert "상대: 지은 말 0" in agent.calls[0]["transcript"]
    assert all("지은" not in u.content for u in saved)  # 영구 저장되는 발화는 본인 것뿐


def test_context_is_cleared_after_success_and_after_failure():
    async def steps(factory):
        async with factory() as db:
            ok = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        done_ok = await _run(factory, ok.id, FakeStyle())
        async with factory() as db:
            bad = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12, start_minute=20))
            await db.commit()
        done_bad = await _run(factory, bad.id, FakeStyle(error=ExtractionFailed("timeout")))
        return done_ok, done_bad

    done_ok, done_bad = _scenario(steps)

    assert done_ok.status == "succeeded" and done_ok.context_payload is None
    assert done_bad.status == "failed" and done_bad.context_payload is None


def test_stale_job_clears_context():
    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            job.status = "running"
            job.started_at = datetime(2026, 1, 1, tzinfo=UTC)
            await db.commit()
        async with factory() as db:
            return await ExtractionService(db).get_job(job.id)

    old = _scenario(steps)

    assert old.status == "failed" and old.context_payload is None


def test_scores_with_invented_evidence_are_dropped_and_confidence_follows_evidence_count():
    agent = FakeStyle(
        scores={
            "disclosure": ScoreObservation(value=40, evidence=[1, 2, 3, 4, 5, 6]),  # 기존 70 → 61, 신뢰도 HIGH 로 상승
            "anxiety": ScoreObservation(value=30, evidence=[2, 3]),  # 기존 null → 30, 근거 2건 → LOW
            "openness": ScoreObservation(value=90, evidence=[999]),  # 없는 번호 → 환각, 버림
        }
    )

    async def steps(factory):
        async with factory() as db:
            job = await ExtractionService(db).start_conversation("u-me", "민수", _kakao(12))
            await db.commit()
        await _run(factory, job.id, agent)
        async with factory() as db:
            return await PersonaRepository(db).latest_persona_for_user("u-me")

    latest = _scenario(steps)

    assert latest.scores["disclosure"] == 61 and latest.confidence["disclosure"] == "HIGH"
    assert latest.scores["anxiety"] == 30 and latest.confidence["anxiety"] == "LOW"
    assert latest.scores.get("openness") is None
