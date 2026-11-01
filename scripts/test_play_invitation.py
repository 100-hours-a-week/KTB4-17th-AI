"""'야 내일 뭐해? 나랑 같이 연극보러갈래?' 문구로 5개 캐릭터 연습대화 단일 턴 테스트."""

import asyncio
from app.core.db import SessionLocal
from app.features.practice.service import PracticeService
from app.features.practice.schemas import PracticeStartRequest


CHARACTERS = [
    {"user_id": "synth_user_intj_001", "name": "서윤", "mbti": "INTJ", "archetype": "논리·신중형"},
    {"user_id": "synth_user_enfp_002", "name": "민재", "mbti": "ENFP", "archetype": "공감·활기형"},
    {"user_id": "synth_user_istp_003", "name": "도현", "mbti": "ISTP", "archetype": "독립·단답형"},
    {"user_id": "synth_user_isfj_004", "name": "하은", "mbti": "ISFJ", "archetype": "다정·배려형"},
    {"user_id": "synth_user_entj_005", "name": "태오", "mbti": "ENTJ", "archetype": "주도·리더형"},
]

USER_PROMPT = "야 내일 뭐해? 나랑 같이 연극보러갈래?"


async def test_character(char_info: dict) -> str:
    async with SessionLocal() as db:
        service = PracticeService(db)

        # 세션 생성
        start_req = PracticeStartRequest(
            partner_user_id=char_info["user_id"],
            nickname="친구",
        )
        start_res = await service.start(start_req)
        session_id = start_res.session_id

        session = await service.repo.get_session(session_id)
        assert session is not None

        # 오프닝 인사 건너뛰고 바로 사용자 메시지로 첫 턴 시작 (또는 자연스러운 첫 대화)
        reply_text = ""
        async for event_name, data in service.stream_reply(session, USER_PROMPT):
            if event_name == "delta":
                reply_text += data.text
            elif event_name == "error":
                reply_text = f"[에러: {data.message}]"

        # 세션 종료
        session = await service.repo.get_session(session_id)
        if session:
            await service.end(session)

        return reply_text.strip()


async def main():
    print(f"\n==================================================")
    print(f"👤 사용자: \"{USER_PROMPT}\"")
    print(f"==================================================\n")

    for char in CHARACTERS:
        reply = await test_character(char)
        print(f"🎭 [{char['name']} ({char['mbti']} / {char['archetype']})]")
        print(f"👉 \"{reply}\"\n")


if __name__ == "__main__":
    asyncio.run(main())
