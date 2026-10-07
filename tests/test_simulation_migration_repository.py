"""시뮬레이션 마이그레이션 리포지토리 단위 테스트."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_persona, with_db
from sqlalchemy.exc import IntegrityError

import app.features.simulation_migration.models  # noqa: F401  Base.metadata에 테이블 등록
from app.features.simulation_migration.models import SimulationMigrationUtterance
from app.features.simulation_migration.repository import MigrationRepository, pair_key


def test_pair_key_ordering() -> None:
    """pair_key는 페르소나 ID 순서와 무관하게 사전순 min:max로 생성된다."""
    assert pair_key("p-alice", "p-bob") == "p-alice:p-bob"
    assert pair_key("p-bob", "p-alice") == "p-alice:p-bob"


def test_conditional_update_different_attempt_returns_false_and_status_unchanged() -> None:
    """attempt가 다른 conditional_update는 False이고 status가 바뀌지 않는다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id

            # attempt mismatch: 2 != 1
            updated = await repo.conditional_update(
                run_id,
                2,
                ["running"],
                status="reporting",
            )
            assert updated is False

            fetched = await repo.get_run(run_id)
            assert fetched is not None
            assert fetched.status == "running"

    asyncio.run(with_db(scenario))


def test_insert_utterance_if_owner_different_attempt_returns_lost_and_no_utterance() -> None:
    """attempt가 다른 insert_utterance_if_owner는 'lost'이고 utterance 행이 없다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id

            # attempt mismatch: 2 != 1
            result = await repo.insert_utterance_if_owner(
                run_id,
                attempt=2,
                index=0,
                speaker="a",
                text="안녕하세요",
            )
            assert result == "lost"

            utterances = await repo.list_utterances(run_id)
            assert utterances == []

    asyncio.run(with_db(scenario))


def test_insert_utterance_owner_inserted_and_same_index_recalled_exists_and_status_running() -> None:
    """소유 attempt의 insert는 'inserted'이고 같은 index 재호출은 'exists'이며 status는 running이다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id

            # 1. 첫 발화 삽입
            r1 = await repo.insert_utterance_if_owner(
                run_id,
                attempt=1,
                index=0,
                speaker="a",
                text="안녕하세요",
            )
            assert r1 == "inserted"

            # 2. 동일 index 재호출
            r2 = await repo.insert_utterance_if_owner(
                run_id,
                attempt=1,
                index=0,
                speaker="a",
                text="안녕하세요 (중복)",
            )
            assert r2 == "exists"

            # 3. 상태 확인: running 유지, error_reason 없음
            fetched = await repo.get_run(run_id)
            assert fetched is not None
            assert fetched.status == "running"
            assert fetched.error_reason is None

            utterances = await repo.list_utterances(run_id)
            assert len(utterances) == 1
            assert utterances[0].index == 0
            assert utterances[0].text == "안녕하세요"

    asyncio.run(with_db(scenario))


def test_same_pair_key_running_second_raises_integrity_error_but_done_allows_new_running() -> None:
    """같은 pair_key의 running 두 번째는 IntegrityError. status가 done인 행이 있으면 같은 pair의 running 하나는 허용."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)

            # 첫 번째 running 실행 생성 후 커밋하여 보존
            run1 = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run1_id = run1.id
            assert run1.status == "running"
            await db.commit()

            # 동일 pair_key로 두 번째 running 생성 시 partial unique 위반
            with pytest.raises(IntegrityError):
                await repo.insert_run(
                    persona_a_id="pb",
                    persona_b_id="pa",
                    user_id_a="ub",
                    user_id_b="ua",
                    nickname_a="B",
                    nickname_b="A",
                    turns=3,
                )
            await db.rollback()

            # run1을 done으로 전이
            updated = await repo.conditional_update(
                run1_id,
                1,
                ["running"],
                status="done",
            )
            assert updated is True
            await db.commit()

            # done인 행이 있으므로 같은 pair의 새로운 running 하나는 허용
            run2 = await repo.insert_run(
                persona_a_id="pb",
                persona_b_id="pa",
                user_id_a="ub",
                user_id_b="ua",
                nickname_a="B",
                nickname_b="A",
                turns=3,
            )
            assert run2.status == "running"

    asyncio.run(with_db(scenario))


def test_same_user_id_a_running_second_raises_integrity_error() -> None:
    """같은 user_id_a의 running 두 번째는 IntegrityError."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="u-same", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            await seed_persona(db, persona_id="pc", user_id="uc", nickname="C")
            repo = MigrationRepository(db)

            await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="u-same",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )

            # 같은 user_id_a로 두 번째 running 생성 시 partial unique 위반
            with pytest.raises(IntegrityError):
                await repo.insert_run(
                    persona_a_id="pa",
                    persona_b_id="pc",
                    user_id_a="u-same",
                    user_id_b="uc",
                    nickname_a="A",
                    nickname_b="C",
                    turns=3,
                )

    asyncio.run(with_db(scenario))


def test_speaker_parity_check_constraint_rejects_invalid_speaker() -> None:
    """speaker가 index 짝수인데 b이면 거부 (체크 제약 조건 검증)."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id

            # 짝수 index (0)에 speaker='b' -> IntegrityError (CheckConstraint)
            with pytest.raises(IntegrityError):
                await repo.insert_utterance_if_owner(
                    run_id,
                    attempt=1,
                    index=0,
                    speaker="b",
                    text="짝수에 b 발화 시도",
                )

            # 홀수 index (1)에 speaker='a' -> IntegrityError (CheckConstraint)
            with pytest.raises(IntegrityError):
                await repo.insert_utterance_if_owner(
                    run_id,
                    attempt=1,
                    index=1,
                    speaker="a",
                    text="홀수에 a 발화 시도",
                )

            # 직접 DB 모델 추가 시에도 동일하게 거부
            with pytest.raises(IntegrityError):
                db.add(
                    SimulationMigrationUtterance(
                        run_id=run_id,
                        index=2,
                        speaker="b",
                        text="직접 삽입 위반",
                    )
                )
                await db.flush()

    asyncio.run(with_db(scenario))


def test_abort_stale_only_aborts_stale_running_and_fresh_running_remains() -> None:
    """abort_stale은 heartbeat가 stale_before보다 오래된 running만 aborted로 바꾸고, fresh running은 그대로다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            await seed_persona(db, persona_id="pc", user_id="uc", nickname="C")
            await seed_persona(db, persona_id="pd", user_id="ud", nickname="D")
            repo = MigrationRepository(db)

            now = datetime.now(UTC)
            stale_time = now - timedelta(seconds=200)
            fresh_time = now - timedelta(seconds=10)
            stale_before = now - timedelta(seconds=120)

            # 1. stale run
            stale_run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            stale_id = stale_run.id
            # heartbeat_at 과거로 수정
            stale_run.heartbeat_at = stale_time
            await db.flush()

            # 2. fresh run
            fresh_run = await repo.insert_run(
                persona_a_id="pc",
                persona_b_id="pd",
                user_id_a="uc",
                user_id_b="ud",
                nickname_a="C",
                nickname_b="D",
                turns=3,
            )
            fresh_id = fresh_run.id
            fresh_run.heartbeat_at = fresh_time
            await db.flush()

            # stale_run abort 시도 -> 성공
            aborted = await repo.abort_stale(stale_id, stale_before=stale_before)
            assert aborted is True

            r_stale = await repo.get_run(stale_id)
            assert r_stale is not None
            assert r_stale.status == "aborted"
            assert r_stale.error_reason == "aborted"

            # fresh_run abort 시도 -> 실패 (fresh 유지)
            fresh_aborted = await repo.abort_stale(fresh_id, stale_before=stale_before)
            assert fresh_aborted is False

            r_fresh = await repo.get_run(fresh_id)
            assert r_fresh is not None
            assert r_fresh.status == "running"
            assert r_fresh.error_reason is None

    asyncio.run(with_db(scenario))


def test_conditional_update_reporting_then_different_attempt_reporting_to_done_fails() -> None:
    """conditional_update(running -> reporting) 성공 뒤 다른 attempt의 reporting -> done은 False."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id

            # 1. attempt 1로 running -> reporting 성공
            ok1 = await repo.conditional_update(
                run_id,
                1,
                ["running"],
                status="reporting",
            )
            assert ok1 is True

            # 2. 다른 attempt (2)의 reporting -> done은 False
            ok2 = await repo.conditional_update(
                run_id,
                2,
                ["reporting"],
                status="done",
            )
            assert ok2 is False

            # 상태는 reporting 유지
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "reporting"

    asyncio.run(with_db(scenario))


def test_find_active_by_pair_and_listing() -> None:
    """find_active_by_pair, list_for_user, list_for_persona 동작 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)

            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id
            p_key = pair_key("pa", "pb")

            # 1. find_active_by_pair (running일 때 조회됨)
            active = await repo.find_active_by_pair(p_key)
            assert active is not None
            assert active.id == run_id

            # 2. list_for_user (ua, ub 모두 조회됨)
            user_a_list = await repo.list_for_user("ua")
            assert len(user_a_list) == 1
            assert user_a_list[0].id == run_id

            user_b_list = await repo.list_for_user("ub")
            assert len(user_b_list) == 1
            assert user_b_list[0].id == run_id

            # 3. list_for_persona (pa, pb 모두 조회됨)
            pa_list = await repo.list_for_persona("pa")
            assert len(pa_list) == 1
            assert pa_list[0].id == run_id

            pb_list = await repo.list_for_persona("pb")
            assert len(pb_list) == 1
            assert pb_list[0].id == run_id

            # 4. done으로 변경 후 find_active_by_pair는 None
            await repo.conditional_update(run_id, 1, ["running"], status="done")
            active_done = await repo.find_active_by_pair(p_key)
            assert active_done is None

    asyncio.run(with_db(scenario))


def test_conditional_update_keyword_attempt_bump() -> None:
    """위치 인자 attempt와 from_statuses를 유지하면서 키워드 인자 attempt로 컬럼을 갱신할 수 있다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)

            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id
            await repo.conditional_update(run_id, 1, ["running"], status="failed")
            await db.commit()

            # 1. attempt 불일치 시 키워드 attempt 갱신 실패
            wrong_res = await repo.conditional_update(
                run_id,
                999,
                ["failed"],
                status="running",
                attempt=2,
            )
            assert wrong_res is False

            r_unchanged = await repo.get_run(run_id)
            assert r_unchanged is not None
            assert r_unchanged.attempt == 1
            assert r_unchanged.status == "failed"

            # 2. 현재 attempt와 일치할 때 키워드 attempt=2 로 성공적 갱신
            ok_res = await repo.conditional_update(
                run_id,
                1,
                ["failed"],
                status="running",
                attempt=2,
            )
            assert ok_res is True

            r_updated = await repo.get_run(run_id)
            assert r_updated is not None
            assert r_updated.attempt == 2
            assert r_updated.status == "running"

    asyncio.run(with_db(scenario))


def test_abort_stale_rowcount_zero_keeps_in_memory_status_n1() -> None:
    """[N1] abort_stale의 UPDATE가 rowcount 0일 때 세션 메모리 객체의 status를 aborted로 덮어쓰지 않는지 검증."""

    async def scenario(factory):
        now = datetime.now(UTC)
        stale_time = now - timedelta(seconds=200)

        async with factory() as db_setup:
            await seed_persona(db_setup, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db_setup, persona_id="pb", user_id="ub", nickname="B")
            repo_setup = MigrationRepository(db_setup)
            run = await repo_setup.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            # stale 상태로 만들기 위해 heartbeat_at을 과거로 설정
            run.heartbeat_at = stale_time
            await db_setup.commit()
            run_id = run.id

        stale_before = now - timedelta(seconds=120)

        # 세션 A에서 stale 상태인 run을 메모리에 로드
        async with factory() as session_a:
            repo_a = MigrationRepository(session_a)
            run_a = await repo_a.get_run(run_id)
            assert run_a is not None
            assert run_a.status == "running"
            assert run_a.heartbeat_at.replace(tzinfo=UTC) == stale_time

            # 세션 B에서 run의 status를 done으로 커밋 (경쟁 상황 시뮬레이션)
            async with factory() as session_b:
                repo_b = MigrationRepository(session_b)
                updated = await repo_b.conditional_update(
                    run_id,
                    1,
                    ["running"],
                    status="done",
                )
                assert updated is True
                await session_b.commit()

            # 이제 세션 A에서 abort_stale 호출:
            # DB에는 이미 status="done"이므로 WHERE status IN ('running', 'reporting') 조건에 맞지 않아 rowcount == 0 반환
            aborted = await repo_a.abort_stale(run_id, stale_before=stale_before)
            assert aborted is False

            # n1 핵심 검증: synchronize_session=False이므로 세션 A의 인메모리 객체 run_a의 status는 aborted로 오염되지 않음!
            assert run_a.status != "aborted"
            assert run_a.error_reason != "aborted"
            assert run_a.status == "running"

        # 세션 C에서 DB 확인: 세션 B가 커밋한 "done" 유지
        async with factory() as session_c:
            repo_c = MigrationRepository(session_c)
            run_c = await repo_c.get_run(run_id)
            assert run_c is not None
            assert run_c.status == "done"

    asyncio.run(with_db(scenario))
