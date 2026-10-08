import asyncio

import pytest
from conftest import with_db
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.chat_end.models import ChatEndDraft, ChatEndMessage
from app.features.chat_end.repository import ChatEndRepository

TURNS = ["1번째 답변", "2번째 답변", "3번째 답변"]


async def _draft(repo: ChatEndRepository, *, delegation_id: int = 7001, room_id: int = 5001) -> ChatEndDraft:
    return await repo.save_draft(
        room_id=room_id,
        delegation_id=delegation_id,
        requester_user_id="user-1",
        end_type="GENTLE",
        turns=TURNS,
        source="llm",
    )


def test_tables_have_no_recent_messages_column():
    assert "recent_messages" not in ChatEndDraft.__table__.columns
    assert "recent_messages" not in ChatEndMessage.__table__.columns


def test_save_draft_is_pending_and_message_records_direction():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)
            draft = await _draft(repo)
            msg = await repo.save_message(
                room_id=5001,
                delegation_id=7001,
                user_id="user-1",
                target_user_id="user-2",
                end_type="GENTLE",
                draft_id=draft.id,
                direction=TURNS[0],
                ai_response="고마웠어요.",
                status="SUCCESS",
                end_turns=1,
                end_reason="상호 합의 종료",
                source="llm",
            )
            await db.commit()
            assert len(draft.id) == 32
            assert draft.status == "PENDING" and draft.confirmed_at is None
            assert isinstance(msg.id, int)

        async with factory() as db:
            drafts = (await db.execute(select(ChatEndDraft))).scalars().all()
            msgs = (await db.execute(select(ChatEndMessage))).scalars().all()
            assert [d.turns for d in drafts] == [TURNS]
            assert (msgs[0].draft_id, msgs[0].direction, msgs[0].end_turns) == (drafts[0].id, TURNS[0], 1)
            assert msgs[0].created_at is not None

    asyncio.run(with_db(body))


def test_get_draft_and_confirmed_lookup():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)
            draft = await _draft(repo)
            assert (await repo.get_draft(draft.id)).id == draft.id
            assert await repo.get_draft("0" * 32) is None
            assert await repo.get_confirmed_draft(5001, 7001) is None  # PENDING 은 찾지 못한다
            await repo.confirm_draft(draft)
            found = await repo.get_confirmed_draft(5001, 7001)
            assert found is not None and found.id == draft.id and found.confirmed_at is not None
            assert await repo.get_confirmed_draft(5001, 9999) is None  # 다른 위임
            assert await repo.get_confirmed_draft(1, 7001) is None  # 다른 방

    asyncio.run(with_db(body))


def test_confirming_another_draft_demotes_the_previous_one():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)
            first, second = await _draft(repo), await _draft(repo)
            await repo.confirm_draft(first)
            await repo.confirm_draft(second)
            await db.commit()

        async with factory() as db:
            rows = {r.id: r for r in (await db.execute(select(ChatEndDraft))).scalars().all()}
            statuses = sorted(r.status for r in rows.values())
            assert statuses == ["CONFIRMED", "PENDING"]
            confirmed = await ChatEndRepository(db).get_confirmed_draft(5001, 7001)
            assert confirmed is not None and confirmed.confirmed_at is not None
            demoted = next(r for r in rows.values() if r.status == "PENDING")
            assert demoted.confirmed_at is None

    asyncio.run(with_db(body))


def test_confirmed_drafts_are_independent_per_delegation():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)
            a, b = await _draft(repo, delegation_id=7001), await _draft(repo, delegation_id=7002)
            await repo.confirm_draft(a)
            await repo.confirm_draft(b)
            await db.commit()
            assert (await repo.get_confirmed_draft(5001, 7001)).id == a.id
            assert (await repo.get_confirmed_draft(5001, 7002)).id == b.id

    asyncio.run(with_db(body))


def test_unique_index_rejects_two_confirmed_for_one_delegation():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)
            first, second = await _draft(repo), await _draft(repo)
            first.status = "CONFIRMED"
            second.status = "CONFIRMED"  # repo 를 거치지 않고 직접 어기면 DB 가 막는다
            with pytest.raises(IntegrityError):
                await db.flush()

    asyncio.run(with_db(body))


def test_count_successful_messages_ignores_failed_and_other_delegations():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)

            async def add(delegation_id: int, status: str):
                await repo.save_message(
                    room_id=5001,
                    delegation_id=delegation_id,
                    user_id="u",
                    target_user_id="t",
                    end_type="GENTLE",
                    draft_id=None,
                    direction=TURNS[0],
                    ai_response="x",
                    status=status,
                    end_turns=1 if status == "SUCCESS" else 0,
                    end_reason=None,
                    source="llm",
                )

            await add(7001, "SUCCESS")
            await add(7001, "SUCCESS")
            await add(7001, "FAILED")
            await add(7002, "SUCCESS")
            assert await repo.count_successful_messages(room_id=5001, delegation_id=7001) == 2
            assert await repo.count_successful_messages(room_id=5001, delegation_id=7002) == 1
            assert await repo.count_successful_messages(room_id=5001, delegation_id=1) == 0

    asyncio.run(with_db(body))


def test_large_backend_ids_fit():
    async def body(factory):
        async with factory() as db:
            row = await _draft(ChatEndRepository(db), delegation_id=2**40 + 1, room_id=2**40)
            await db.commit()
            assert row.room_id == 2**40

    asyncio.run(with_db(body))
