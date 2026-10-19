"""연습대화 말 놓기 제안 — 프롬프트 조각과 허락 판정.

흐름: 사용자 발화가 ASK_AFTER_TURNS 번째가 되면 상대가 "말 편하게 해도 될까요?"라고 묻는다(asked).
다음 사용자 답이 긍정이면 casual(반말), 아니면 declined — 이 사용자에게는 다시 묻지 않는다.
허락 전(formal·asked·declined)에는 프로필의 반말 대화 스타일이 있어도 존댓말로 고정한다.
"""

from __future__ import annotations

import logging
import re

from langfuse import observe

from app.features.persona.agents import LLMError, _call_json

logger = logging.getLogger(__name__)

ASK_AFTER_TURNS = 10  # 사용자 발화가 이 횟수 이상이면 제안한다
MODE_FORMAL, MODE_ASKED, MODE_CASUAL, MODE_DECLINED = "formal", "asked", "casual", "declined"

ASK_PHRASE = "말 편하게 해도 될까요?"

_ASKED_RE = re.compile(r"편하게\s*해도\s*(될까|돼|되)")


# 답변이 실제로 말 놓기를 제안했는지. 모델이 지시를 빼먹으면 asked 로 넘어가지 않고 다음 턴에 다시 시도한다
def asks_permission(reply: str) -> bool:
    return bool(_ASKED_RE.search(reply))


FORMAL_ADDON = """\
## 말투 고정 (위의 모든 말투 규칙보다 우선)
아직 {me}님에게 말을 놓아도 된다는 허락을 받지 않았습니다.
- 프로필의 "대화 스타일"이 반말이어도 지금은 존댓말(해요체)로 말합니다. 자주 쓰는 말·웃음은 존댓말에 어울리는 것만 씁니다.
- {me}님이 먼저 반말을 해도 존댓말을 유지합니다.
"""

DECLINED_ADDON = """\
- {me}님이 말을 놓는 것을 원하지 않았습니다. 끝까지 존댓말로 말하고, 말 놓기를 다시 꺼내지 않습니다.
"""

ASK_ADDON = f"""\
## 이번 답변에서 말 놓기 제안
대화가 충분히 오갔습니다. 이번 답변은 평소처럼 짧게 반응하고, 마지막에 "{ASK_PHRASE}"라고 정중하게 묻고 끝냅니다.
- 이 문구를 그대로 포함하세요. 이번 답변에는 다른 질문을 덧붙이지 않습니다.
- 아직 존댓말입니다.
"""

CASUAL_ADDON = """\
## 말투 허락 (위의 모든 말투 규칙보다 우선)
{me}님이 "말 편하게 해도 된다"고 허락했습니다. 지금부터 반말로 말합니다.
- 프로필에 "대화 스타일"이 있으면 그 말투(어미, 자주 쓰는 말, 웃음)를 그대로 쓰고, 없으면 자연스럽고 부드러운 반말로 말합니다.
- 허락 직후의 답변이면 "그럼 편하게 할게 ㅎㅎ" 정도로 한 마디만 받고 이어갑니다. 그 뒤에는 허락 얘기를 다시 꺼내지 않습니다.
- 이모지 금지, 1~3문장 규칙은 그대로입니다.
"""


def speech_addon(*, me: str, mode: str, ask_now: bool) -> str:
    if mode == MODE_CASUAL:
        return CASUAL_ADDON.format(me=me)
    text = FORMAL_ADDON.format(me=me)
    if mode == MODE_DECLINED:
        text += DECLINED_ADDON.format(me=me)
    if ask_now and mode == MODE_FORMAL:  # 제안은 아직 안 물은 formal 에서만
        text += "\n" + ASK_ADDON
    return text


CLASSIFY_SYSTEM = """\
상대가 사용자에게 "말 편하게 해도 될까요?"(서로 말을 놓아도 되는지)라고 물었고, 사용자가 답했습니다.
사용자의 답이 말을 놓아도 좋다는 분명한 허락이면 allow 를 true 로 합니다.
거절이거나, 주저하거나("글쎄요", "조금 더 있다가"), 질문과 상관없는 답이거나, 판단하기 애매하면 false 입니다.
JSON 객체 하나만 출력하세요. 설명·코드펜스 금지.
{"allow": true}
"""


# 사용자의 답이 말 놓기 허락인지. LLM 실패·애매함은 모두 False — 원치 않은 반말이 나가는 것보다 낫다
@observe(name="v2-practice-speech-permission", capture_input=False, capture_output=False)
async def classify_permission(answer: str) -> bool:
    try:
        data = await _call_json(
            system=CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": f"사용자의 답: {answer}"}],
            max_tokens=30,
            timeout=10.0,
            name="v2-practice-speech-permission",
            metadata=None,
        )
    except LLMError as e:
        logger.warning("speech permission classify failed — treating as no: %s", e)
        return False
    return data.get("allow") is True
