import asyncio

from conftest import with_db
from sqlalchemy import select

from app.features.chat_end.models import ChatEndDraft, ChatEndMessage
from app.features.chat_end.repository import ChatEndRepository


def test_tables_have_no_recent_messages_column():
    assert "recent_messages" not in ChatEndDraft.__table__.columns
    assert "recent_messages" not in ChatEndMessage.__table__.columns


def test_save_draft_and_message():
    async def body(factory):
        async with factory() as db:
            repo = ChatEndRepository(db)
            draft = await repo.save_draft(
                room_id=5001,
                delegation_id=7001,
                requester_user_id="user-1",
                end_type="GENTLE",
                ending_messages=["a", "b", "c"],
                source="llm",
            )
            msg = await repo.save_message(
                room_id=5001,
                delegation_id=7001,
                user_id="user-1",
                target_user_id="user-2",
                end_type="GENTLE",
                ending_messages=["a"],
                ai_response="고마웠어요.",
                status="SUCCESS",
                end_turns=1,
                end_reason="상호 합의 종료",
                source="llm",
            )
            await db.commit()
            assert len(draft.id) == 32
            assert isinstance(msg.id, int)

        async with factory() as db:
            drafts = (await db.execute(select(ChatEndDraft))).scalars().all()
            msgs = (await db.execute(select(ChatEndMessage))).scalars().all()
            assert [d.ending_messages for d in drafts] == [["a", "b", "c"]]
            assert msgs[0].ai_response == "고마웠어요."
            assert msgs[0].end_turns == 1
            assert msgs[0].created_at is not None

    asyncio.run(with_db(body))


def test_large_backend_ids_fit():
    async def body(factory):
        async with factory() as db:
            row = await ChatEndRepository(db).save_draft(
                room_id=2**40,
                delegation_id=2**40 + 1,
                requester_user_id="u",
                end_type="DIRECT",
                ending_messages=["a", "b", "c"],
                source="fallback",
            )
            await db.commit()
            assert row.room_id == 2**40

    asyncio.run(with_db(body))
