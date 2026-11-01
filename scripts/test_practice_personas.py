"""적재된 페르소나 유저들로 연습 대화(Practice Conversation) 시뮬레이션 및 테스트."""

import asyncio
from app.core.db import SessionLocal
from app.features.practice.service import PracticeService
from app.features.practice.schemas import PracticeStartRequest
from app.features.persona.schemas import PersonaRef


TEST_CHARACTERS = [
    {
        "user_id": "synth_user_intj_001",
        "name": "서윤",
        "mbti": "INTJ",
        "style": "논리적/신중/짧은 반말",
    },
    {
        "user_id": "synth_user_enfp_002",
        "name": "민재",
        "mbti": "ENFP",
        "style": "공감/활기찬/다정한 어조",
    },
    {
        "user_id": "synth_user_istp_003",
        "name": "도현",
        "mbti": "ISTP",
        "style": "무미건조/단답형/거리두기",
    },
]

USER_TEST_MESSAGE = "너 요즘 왜 이렇게 연락이 뜸해? 나한테 서운한 거 있어?"


async def run_practice_test_for_character(char_info: dict):
    print(f"\n{'='*60}")
    print(f"👉 [테스트 캐릭터: {char_info['name']} ({char_info['mbti']})]")
    print(f"   성향 특징: {char_info['style']}")
    print(f"{'='*60}")

    async with SessionLocal() as db:
        service = PracticeService(db)

        # 1. 세션 시작
        start_req = PracticeStartRequest(
            partner_user_id=char_info["user_id"],
            nickname="지호",
        )
        start_res = await service.start(start_req)
        session_id = start_res.session_id
        print(f"✅ 세션 생성 완료 (session_id: {session_id})")

        # 2. 오프닝 인사 (상대방 첫 발화)
        session = await service.repo.get_session(session_id)
        assert session is not None

        opening_text = ""
        async for event_name, data in service.stream_opening(session):
            if event_name == "delta":
                opening_text += data.text
            elif event_name == "error":
                print(f"❌ 오프닝 에러: {data.message}")

        print(f"\n💬 [{char_info['name']}의 첫 인사]")
        print(f"   \"{opening_text.strip()}\"")

        # 3. 사용자 발화 및 상대방 답변
        print(f"\n👤 [지호 (사용자)]: \"{USER_TEST_MESSAGE}\"")

        # 최신 세션 상태 로드
        session = await service.repo.get_session(session_id)
        assert session is not None

        reply_text = ""
        async for event_name, data in service.stream_reply(session, USER_TEST_MESSAGE):
            if event_name == "delta":
                reply_text += data.text
            elif event_name == "error":
                print(f"❌ 답변 에러: {data.message}")

        print(f"\n💬 [{char_info['name']}의 답변]")
        print(f"   \"{reply_text.strip()}\"")

        # 세션 종료
        session = await service.repo.get_session(session_id)
        assert session is not None
        await service.end(session)
        print(f"🔒 세션 정상 종료\n")


async def main():
    print("🚀 적재된 페르소나 기반 연습대화 테스트 시작...")
    for char in TEST_CHARACTERS:
        await run_practice_test_for_character(char)
    print("🎉 모든 연습대화 테스트 완료!")


if __name__ == "__main__":
    asyncio.run(main())
