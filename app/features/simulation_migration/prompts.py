from __future__ import annotations

import json
from typing import Literal

from app.features.persona.profile import describe
from app.features.persona.schemas import PersonaResponse
from app.features.simulation_migration.order import arc_phase_for

OPENER = (
    "메신저로 처음 대화를 시작한 상황이다. 인사부터 자연스럽게. "
    "상대 닉네임으로 부르고, 처음 인사한다는 말을 넣어라. "
    "오늘 하루를 가볍게 물을 수 있다. "
    "이 줄에서는 장소, 취미, 약속, 계획을 꺼내지 마라."
)
HINT_NO_NEW_QUESTION = "이번이 마지막 왕복이다. 새 질문과 새 화제를 열지 않는다."
HINT_CLOSE = "방금까지의 대화에 대한 마지막 답이다. 다음 약속이나 짧은 인사로 끝낸다. 새 주제나 새 질문을 열지 않는다."
HINT_OPEN = (
    "지금은 대화의 앞부분이다. 가볍게 이어 가며 주말과 관심사로 자연스럽게 넘어간다. "
    "관심사와 데이트는 네 프로필에 있는 것만 네 이야기로 말한다. "
    "상대가 말하지 않은 장소나 일정을 상대의 계획으로 묻지 마라. "
    "약속과 작별 인사는 하지 마라."
)
HINT_MIDDLE = "성향이나 속도가 다르면 한 번만 드러내라. 같은 제안과 같은 거절을 되풀이하지 마라."
HINT_LATE = "아직 마지막이 아니다. 들어가세요, 나중에 봐요, 연락할게요, 잘 자요 같은 작별 문장은 쓰지 마라."
FACT_LIMIT = (
    "프로필에 없는 직업, 나이, 거주지는 만들지 마라. "
    "관심사와 데이트는 네 프로필에 있는 것만 네 얘기로 말해라. "
    "상대의 취향은 이 대화에서 상대가 말한 것만 언급하라. "
    "프로필의 설명이나 특징 문장은 상대가 한 말이 아니다."
)
NO_REPEAT = "이미 한 합의와 이미 한 작별을 다시 쓰지 마라. 이번 줄에는 새 정보나 새 반응을 하나만 담아라."
COMMON_STYLE = (
    "존댓말 1~3문장, 이모지 없음, 가벼운 ㅎㅎ 만 허용, AI·챗봇·페르소나 연기 금지, 자기 닉네임에 님을 붙이지 않음."
)
OUTPUT_FORMAT = '응답은 JSON 하나만 보낸다. 형식은 {"text": "대사"} 다. 키는 text 하나다.'


def speaker_messages(
    *,
    speaker: Literal["a", "b"],
    persona: PersonaResponse,
    nickname_self: str,
    nickname_other: str,
    transcript: list[tuple[Literal["a", "b"], str]],
    closing_hint: Literal["none", "no_new_question", "close"],
    regen_rule_id: str | None = None,
    index: int | None = None,
    turns: int | None = None,
) -> list[dict[str, str]]:
    """화자 도구의 LLM 프롬프트 메시지 목록을 생성한다.

    상대 페르소나는 넣지 않는다. nickname_other 는 부를 이름만 알린다.
    시스템 프롬프트에는 화자 자신의 describe 프로필, 호칭, 공통 스타일, JSON 출력 형식이 들어간다.

    Args:
        speaker: 현재 발화자 ("a" 또는 "b").
        persona: 현재 발화자의 페르소나 응답 객체.
        nickname_self: 현재 발화자의 닉네임.
        nickname_other: 상대방의 닉네임. 호칭으로만 넣고 프로필은 넣지 않는다.
        transcript: 이전까지 누적된 대사 목록 [(speaker, text), ...].
        closing_hint: 대화 종료 힌트 ("none", "no_new_question", "close").
        regen_rule_id: 가드레일 등 재시도 시 위반된 규칙 ID.

    Returns:
        LLM 호출용 메시지 딕셔너리 리스트 (role은 'system', 'user', 'assistant'만 사용).

    Raises:
        ValueError: transcript가 비어 있는데 speaker가 'b'인 경우.
    """
    if not transcript and speaker == "b":
        raise ValueError("speaker b의 대화 시작 시 transcript는 비어 있을 수 없습니다.")

    is_first_a = not transcript and speaker == "a"
    system_parts = [
        describe(nickname_self, persona),
        address_line(nickname_other),
        COMMON_STYLE,
        FACT_LIMIT,
        NO_REPEAT,
        OUTPUT_FORMAT,
    ]

    phase = arc_phase_for(index, turns) if index is not None and turns is not None else None
    if is_first_a:
        system_parts.append(OPENER)
    elif phase == "open":
        system_parts.append(HINT_OPEN)
    elif phase == "middle":
        system_parts.append(HINT_MIDDLE)
    elif phase == "late":
        system_parts.append(HINT_LATE)
    elif phase == "close_a" or (phase is None and closing_hint == "no_new_question"):
        system_parts.append(HINT_NO_NEW_QUESTION)
    elif (phase == "close_b" or (phase is None and closing_hint == "close")) and speaker != "a":
        system_parts.append(HINT_CLOSE)

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "\n\n".join(system_parts),
        }
    ]

    for spk, text in transcript:
        role = "assistant" if spk == speaker else "user"
        messages.append({"role": role, "content": text})

    partner_line = next((text for spk, text in reversed(transcript) if spk != speaker), None)
    if partner_line is not None:
        messages.append({"role": "user", "content": reply_anchor(partner_line)})

    if regen_rule_id is not None:
        extra = ""
        if regen_rule_id == "EARLY-FAREWELL":
            extra = " 작별 문장을 빼라."
        elif regen_rule_id == "REPEAT":
            extra = " 직전 네 말과 다르게 써라."
        messages.append(
            {
                "role": "user",
                "content": f"규칙 {regen_rule_id} 에 걸렸다. 네 말만 다시 써라.{extra}",
            }
        )

    return messages


def address_line(nickname_other: str) -> str:
    """상대 프로필 없이, 부를 닉네임만 알려 준다."""
    return (
        # 닉네임 뒤에 "다"를 붙이면 모델이 "셰일다님"처럼 이름의 일부로 베낀다. 따옴표로 감싼다
        f'상대의 닉네임: "{nickname_other}". '
        "상대를 부를 땐 이 닉네임에 님을 붙여 부른다. "
        "자기 닉네임으로 상대를 부르지 마라. "
        "이 닉네임 외에 상대의 관심사, 일상, 성향은 모른다."
    )


def reply_anchor(text: str) -> str:
    """상대의 마지막 문장만 다시 보여 주고, 그 문장에 답하라고 지시한다."""
    return (
        f"상대가 방금 한 말: {text}\n"
        "이 문장에만 답하라. 네 직전 말의 변명을 덧붙이지 마라. "
        "상대가 하지 않은 감정이나 거절은 전제로 쓰지 마라."
    )


def parse_speaker_json(raw: str) -> str:
    """화자 도구의 JSON 출력을 파싱하여 text를 반환한다.

    Args:
        raw: 모델이 출력한 JSON 문자열.

    Returns:
        파싱 및 검증된 대사 문자열.

    Raises:
        ValueError: JSON 형식이 아니거나, 키가 'text' 하나가 아니거나,
                    문자열이 아니거나, 빈 문자열/공백만이거나, 300자를 초과하는 경우.
    """
    try:
        data = json.loads(raw)
    except Exception as e:
        raise ValueError(f"유효한 JSON이 아닙니다: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("JSON 객체여야 합니다.")

    if list(data.keys()) != ["text"]:
        raise ValueError("JSON 객체에는 'text' 키 하나만 존재해야 합니다.")

    val = data["text"]
    if not isinstance(val, str):
        raise ValueError("'text' 필드의 값은 문자열이어야 합니다.")

    if not val.strip():
        raise ValueError("대사가 비어 있거나 공백만 포함되어 있습니다.")

    if len(val) > 300:
        raise ValueError(f"대사는 300자를 초과할 수 없습니다 (현재 {len(val)}자).")

    return val
