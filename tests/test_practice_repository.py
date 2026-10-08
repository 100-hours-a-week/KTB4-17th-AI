"""연습대화 repository — 메시지 user_id 복사."""

import asyncio

from conftest import seed_persona, with_db

from app.features.practice.repository import PracticeRepository


# 세션 하나에 내 발화 n_user 개, 상대 발화 n_persona 개를 쌓는다 (추출 테스트도 쓴다)
async def session_with_messages(db, *, user_id="u-me", n_user=3, n_persona=2, my_nickname="민수"):
    repo = PracticeRepository(db)
    s = await repo.create_session(
        partner_persona_id="partner",
        partner_user_id="u-partner",
        partner_nickname="지수",
        my_persona_id=None,
        user_id=user_id,
        my_nickname=my_nickname,
    )
    for i in range(n_user):
        await repo.add_message(s, "user", f"내 말 {i}")
    for i in range(n_persona):
        await repo.add_message(s, "persona", f"상대 말 {i}", "llm")
    await db.commit()
    return s


def test_add_message_copies_session_user_id_and_starts_unreflected():
    async def body(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="partner", user_id="u-partner", nickname="지수")
            s = await session_with_messages(db)

            assert {m.user_id for m in s.messages} == {"u-me"}
            assert {m.reflected_persona_id for m in s.messages} == {None}

    asyncio.run(with_db(body))
