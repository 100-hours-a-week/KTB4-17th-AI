"""연습대화 말투 어댑터 경로 — 페르소나 주인의 어댑터로 답한다 (MLOps 설계서 7-4절).

라우팅 규칙
  - 세션을 시작할 때 페르소나 주인(partner_user_id)의 ACTIVE 어댑터를 고른다. 연습하는 사람(user_id)의 것이 아니다.
    고르면 세션의 adapter_model 에 "{user_id}-v{n}" 을 남긴다 (app/features/mlops/serving.select_adapter).
    [임시 시험용] 상대 페르소나 닉네임이 MLOPS_ADAPTER_TEST_NICKNAME_MAP 에 있으면 그 어댑터 이름이 먼저다.
  - 어댑터 세션은 턴마다 어댑터로 먼저 답한다. 실패하면(연결 실패·타임아웃·모르는 이름·빈 답) 그 턴만 기존 경로
    (세션에 정한 9칸 또는 메인 LLM)로 다시 만든다. 세션을 내리지 않아 다음 턴은 다시 어댑터로 간다.
  - 도중에 MLOPS_ADAPTER_ENABLED 를 끄면 다음 턴부터 기존 경로로 답한다. 허용 목록은 세션을 시작할 때만 본다.

프롬프트는 어댑터를 학습할 때와 같은 틀이다 (app/features/mlops/prompt.py). 이 서비스의 값은 이렇게 옮긴다.
  - 프로필 블록: v1 app/features/persona/profile.describe(상대 닉네임, 상대 페르소나). persona-mlops 의 프로필 블록
    (profile.json 의 profile_block, "KTB-17DEV describe()")이 이 함수로 만든 것이다. v2 describe 는
    "사실 사용 기준"·관심사·일상·데이트 줄이 없고 기억된 사실 블록이 붙어 학습 때와 달라진다.
  - 본인(나) = 페르소나 주인 = role "persona" 메시지, 상대 = 연습하는 사람 = role "user" 메시지.
    상대 라벨의 이름은 세션의 my_nickname 이다.
  - 대화 기록은 세션 메시지 전체를 넘기고, 빌더가 최근 20턴만 쓴다 (학습 샘플과 같다).
    메인 LLM 용 _history 는 쓰지 않는다 — 인사 지시문을 user 메시지로 끼워 넣어 상대 대사처럼 보이게 된다.

옮길 값이 없거나 일부러 넣지 않는 것
  - 기억("## 기억" 블록): 학습 샘플이 모두 빈 기억으로 만들어졌다 (persona-mlops build_samples). 그래서 비워 보낸다.
    상대 주인의 확정 사실(v2 메인 경로의 "기억된 사실")과 연습하는 사람의 메모리도 넣지 않는다.
  - 연습하는 사람의 프로필(메인 경로의 ME_SECTION, MBTI): mlops 틀에 자리가 없다.
  - 가드레일 지시(cove addon): 학습에 없던 입력이라 넣지 않는다. 답한 뒤의 가드레일 검사는 그대로 돈다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.observability import LangfuseMetadata
from app.features.mlops import serving
from app.features.mlops.prompt import OTHER, SELF, Message, Turn, build_messages, persona_name, strip_reply_label
from app.features.persona.profile import describe
from app.features.persona.schemas import PersonaResponse
from app.features.practice.models import PracticeMessage, PracticeSession

# practice_messages.source 값 (varchar 8). API 응답의 source 는 기존대로 "llm"
SOURCE = "adapter"
CALL_NAME = "practice-reply-adapter"


def usable(session: PracticeSession) -> bool:
    """이번 턴을 어댑터로 먼저 답하는가. 어댑터는 세션을 시작할 때 정했고, 여기서는 켜져 있는지만 다시 본다."""
    return bool(session.adapter_model) and serving.enabled()


def turns(messages: Sequence[PracticeMessage]) -> list[Turn]:
    return [Turn(SELF if m.role == "persona" else OTHER, m.content) for m in messages]


@dataclass(frozen=True)
class AdapterPrompt:
    messages: list[Message]
    me: str | None  # 본인 라벨의 이름 (프로필 첫 줄 "### 이름")
    partner: str | None  # 상대 라벨의 이름


def build(session: PracticeSession, partner: PersonaResponse) -> AdapterPrompt:
    """학습 때와 같은 [system, user]. 상대(페르소나 주인)가 본인이고, 연습하는 사람이 상대다."""
    profile = describe(session.partner_nickname, partner)
    messages = build_messages(profile, turns(session.messages), partner_name=session.my_nickname)
    return AdapterPrompt(messages=messages, me=persona_name(profile), partner=session.my_nickname)


async def reply(model: str, prompt: AdapterPrompt, *, metadata: LangfuseMetadata | None = None) -> str:
    """어댑터 답 하나. 이름 라벨을 떼고, 남는 게 없으면 AdapterError (상대 대사를 대신 쓴 출력 등)."""
    raw = await serving.complete(prompt.messages, model=model, name=CALL_NAME, metadata=metadata)
    text = strip_reply_label(raw, prompt.me, prompt.partner)
    if not text:
        raise serving.AdapterError("empty reply after stripping the name label")
    return text
