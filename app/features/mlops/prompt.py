"""말투 어댑터에 보내는 프롬프트 — persona-mlops 의 src/persona/prompt_builder.py 를 추론에 필요한 만큼 옮겼다.

어댑터는 이 틀로 만든 학습 샘플로 학습했다. 글자 하나라도 다르면 학습한 말투가 실제 호출에서 흐려진다.
그래서 문구와 규칙을 원본 그대로 두고, 원본으로 만든 기대값과 글자 단위로 비교한다
(tests/fixtures/mlops_prompt_golden.json, tests/test_mlops_prompt.py). 원본이 바뀌면 이 파일과 기대값을 같이 바꾼다.

messages 는 [system, user] 두 개다.
  system  역할 + "## 당신의 프로필" + (기억이 있으면 "## 기억") + 내용 규칙 + 맥락 + 금지사항.
          말투 지시(길이 규칙, 이모지 금지 등)는 넣지 않는다 — 말투는 어댑터 몫이다 (원본의 style_rules=False).
  user    이름 라벨 대화 기록 하나 + 지금 할 일.
            [대화 기록]
            지민(나): 안녕하세요!
            민수(상대): 주말에는 뭐 하세요?

            [지금 할 일]
            마지막 말은 민수 님이 했다. 지민 본인으로 한 번만 답한다.
모델은 "{이름}(나): 답" 형식으로 답하고, 호출한 쪽이 strip_reply_label 로 라벨을 뗀다.

옮기지 않은 것: 학습 샘플 조립(build_training_messages), 대화 기록 되읽기(parse_history), 말투 지시를 넣는
style_rules=True 틀 (어댑터가 없는 사용자는 이 서비스의 기존 프롬프트로 답한다).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Message = dict[str, str]

SELF = "self"
OTHER = "other"

# 문맥 턴 수. 학습 샘플(SampleConfig.max_context_turns)과 같다
DEFAULT_MAX_TURNS = 20

HISTORY_HEADER = "[대화 기록]"
TASK_HEADER = "[지금 할 일]"
EMPTY_HISTORY = "(아직 대화 없음)"

SELF_ROLE = "나"
PARTNER_ROLE = "상대"
SELF_FALLBACK = "본인"  # 프로필에 "### 이름" 이 없을 때 본인 라벨
PARTNER_FALLBACK = "상대방"  # 상대 이름을 모를 때 상대 라벨

# {me} = "{이름} 본인" 또는 "본인", {you} = "{상대 이름} 님" 또는 "상대방". 둘 다 받침으로 끝나 조사가 고정이다.
REPLY_TASK = "마지막 말은 {you}이 했다. {me}으로 한 번만 답한다."
CONTINUE_TASK = "마지막 말은 {me}이 했다. {you}의 답장은 아직 없다. {me}으로 이어서 한 번만 더 보낸다."
OPENING_TASK = (
    "매칭 후 첫 메시지를 보낸다. 인사하고, 내 프로필의 관심사나 일상 하나로 가볍게 문을 열고, "
    "답하기 쉬운 질문 하나로 끝낸다. {me}으로 한 번만 쓴다."
)


@dataclass(frozen=True)
class Turn:
    """대화의 턴 하나. speaker 가 self 면 페르소나 주인(어댑터 주인) 본인이다.

    name 은 상대 턴을 말한 사람의 이름 (선택). 있으면 그 턴의 라벨은 "{name}(상대)" 다.
    """

    speaker: Literal["self", "other"]
    text: str
    name: str | None = None


# ══ system 프롬프트 조각 (원본과 같은 문구) ═══════════════════════

_ROLE_NAMED = """\
당신은 "{name}" 본인입니다. 상대방과 메신저로 1:1로 대화합니다.
아래 프로필은 당신의 연애 성향입니다. 이 사람으로서 말하고 행동하세요.
목표는 상대방이 "이 사람이랑 말이 잘 통한다"고 느끼는 것입니다 — 성향은 지키되, 상대에게 맞춰 대화가 이어지게."""

_ROLE_UNNAMED = """\
당신은 아래 프로필의 주인 본인입니다. 상대방과 메신저로 1:1로 대화합니다.
아래 프로필은 당신의 연애 성향입니다. 이 사람으로서 말하고 행동하세요.
목표는 상대방이 "이 사람이랑 말이 잘 통한다"고 느끼는 것입니다 — 성향은 지키되, 상대에게 맞춰 대화가 이어지게."""

_PROFILE_HEADER = "## 당신의 프로필 (이대로 말하고 행동합니다)"

_MEMORY_HEADER = "## 기억 (알고 있는 사실. 대화에 필요할 때만 자연스럽게 꺼냅니다)"

_SPEAKING_CONTENT_ONLY = """\
## 대화하는 방식
- 질문을 받았으면 그 질문에 프로필대로 솔직하게 답합니다.
- 프로필에 없는 사실(직업·나이·사는 곳 등)은 지어내지 않고, 물어보면 자연스럽게 넘깁니다."""

_CONTEXT_RECALL_NO_MEMORY = """\
- 이 대화에서 상대가 실제로 말한 것만 기억했다가 꺼냅니다. 대화에 없는 취미·사실을 "하셨잖아요"로 꺼내거나
  "저번에" 처럼 이전 만남이 있었던 듯 말하지 않습니다. 같은 질문을 두 번 하지 않습니다."""
_CONTEXT_RECALL_WITH_MEMORY = """\
- 이 대화에서 상대가 실제로 말한 것과 위 기억에 있는 것만 꺼냅니다. 대화에도 기억에도 없는 취미·사실을
  "하셨잖아요"로 꺼내지 않습니다. 같은 질문을 두 번 하지 않습니다."""
_CONTEXT_REST = """\
- 상대가 짧게 답하면 답하기 쉬운 가벼운 질문으로, 길게 답하면 그중 하나를 집어서 깊게.
- 상대가 화제를 바꾸면 따라갑니다. 어색하면 어색한 대로 — 성향이 안 맞는 지점은 감추지 않아도 됩니다."""

_NEVER_HEADER = "## 절대 하지 않는 것"
_NEVER_JUDGE = '- 평가·진단·조언 ("잘 하고 계세요", "회피형이시네요" ✕). 상대 말을 요약하지 않기.'
_NEVER_IDENTITY_NAMED = """\
- AI·인공지능·챗봇·언어 모델·페르소나·연기라는 단어를 답변에 넣지 않기. 누가 되냐고 물으면
  "{name}{ieyo}"처럼 1인칭으로 답하고, 프로필에 있는 관심사나 일상 하나만 짧게 얹습니다.
  "AI야?", "인공지능이야?"라고 물어도 동의하지 않습니다. "네"로 받지 않습니다.
  자신을 "{name}님은"처럼 제3자로 부르지 않습니다."""
_NEVER_IDENTITY_UNNAMED = """\
- AI·인공지능·챗봇·언어 모델·페르소나·연기라는 단어를 답변에 넣지 않기. 누가 되냐고 물으면
  1인칭으로 답하고, 프로필에 있는 관심사나 일상 하나만 짧게 얹습니다.
  "AI야?", "인공지능이야?"라고 물어도 동의하지 않습니다. "네"로 받지 않습니다.
  자신을 제3자로 부르지 않습니다."""
_NEVER_LIST = "- 프로필 내용을 목록처럼 읊기. 대화하듯 한 번에 하나씩."

_NAME_LINE = re.compile(r"^###\s+(?P<name>\S.*?)\s*$")


def persona_name(persona_prompt: str) -> str | None:
    """프로필 블록 첫 줄 "### 이름" 의 이름. 그 형식이 아니면 None."""
    for line in persona_prompt.splitlines():
        if line.strip():
            m = _NAME_LINE.match(line.strip())
            return m.group("name") if m else None
    return None


def _ieyo(name: str) -> str:
    """이름 마지막 글자에 받침이 있으면 "이에요", 없으면 "예요"."""
    code = ord(name[-1])
    has_batchim = 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0
    return "이에요" if has_batchim else "예요"


def _memory_items(memory: Sequence[str]) -> list[str]:
    if isinstance(memory, str):
        raise TypeError("memory 는 문자열 하나가 아니라 사실 문장의 목록이어야 합니다")
    items = []
    for m in memory:
        line = " ".join(m.split())  # 한 항목은 한 줄로
        if line:
            items.append(line)
    return items


def build_system(persona_prompt: str, memory: Sequence[str] = ()) -> str:
    """system 프롬프트 (원본 build_system 의 style_rules=False). memory 가 비어 있으면 "## 기억" 블록이 없다."""
    profile = persona_prompt.strip()
    if not profile:
        raise ValueError("페르소나 프롬프트가 비어 있습니다")
    items = _memory_items(memory)
    name = persona_name(profile)

    role = _ROLE_NAMED.format(name=name) if name else _ROLE_UNNAMED
    identity = _NEVER_IDENTITY_NAMED.format(name=name, ieyo=_ieyo(name)) if name else _NEVER_IDENTITY_UNNAMED

    sections = [role, _PROFILE_HEADER + "\n" + profile]
    if items:
        sections.append(_MEMORY_HEADER + "\n" + "\n".join(f"- {m}" for m in items))
    sections.append(_SPEAKING_CONTENT_ONLY)
    recall = _CONTEXT_RECALL_WITH_MEMORY if items else _CONTEXT_RECALL_NO_MEMORY
    sections.append("## 맥락\n" + recall + "\n" + _CONTEXT_REST)
    sections.append("\n".join([_NEVER_HEADER, _NEVER_JUDGE, identity, _NEVER_LIST]))
    return "\n\n".join(sections)


# ══ 대화 기록 (이름 라벨) ════════════════════════════════════════


def _clean_name(name: str | None) -> str | None:
    """이름의 공백을 정리한다. 비었으면 None (= 모름)."""
    if name is None:
        return None
    cleaned = " ".join(name.split())
    return cleaned or None


def self_label(name: str | None) -> str:
    """본인 라벨 "{이름}(나)". 이름을 모르면 "본인(나)"."""
    return f"{_clean_name(name) or SELF_FALLBACK}({SELF_ROLE})"


def partner_label(partner_name: str | None) -> str:
    """상대 라벨 "{이름}(상대)". 이름을 모르면 "상대방(상대)"."""
    return f"{_clean_name(partner_name) or PARTNER_FALLBACK}({PARTNER_ROLE})"


def _partner_of(turn: Turn, partner_name: str | None) -> str | None:
    """상대 턴의 이름: 턴의 이름 → partner_name. 둘 다 없으면 None (= "상대방")."""
    return _clean_name(turn.name) or _clean_name(partner_name)


def _task(recent: Sequence[Turn], name: str | None, partner_name: str | None) -> str:
    me = f"{n} 본인" if (n := _clean_name(name)) else SELF_FALLBACK
    if not recent:
        return OPENING_TASK.format(me=me)
    last = recent[-1]
    if last.speaker == SELF:
        # 답장을 기다리는 상대: 보여 준 기록에서 가장 최근 상대 턴. 상대 턴이 없으면 partner_name
        others = [t for t in recent if t.speaker == OTHER]
        p = _partner_of(others[-1], partner_name) if others else _clean_name(partner_name)
        return CONTINUE_TASK.format(me=me, you=f"{p} 님" if p else PARTNER_FALLBACK)
    p = _partner_of(last, partner_name)
    return REPLY_TASK.format(me=me, you=f"{p} 님" if p else PARTNER_FALLBACK)


def build_user_message(
    history: Sequence[Turn],
    name: str | None,
    partner_name: str | None = None,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """history → user 메시지 하나.

    1. 내용이 빈 턴은 버리고, 남은 턴 중 최근 max_turns 개만 쓴다. 턴 내용은 앞뒤 공백만 걷는다.
    2. 턴마다 "{라벨}: {내용}". 같은 화자가 연달아 말해도 합치지 않는다. 내용 안의 줄바꿈은 그대로 둔다.
    3. 본인은 "{이름}(나)", 상대는 턴의 이름 → partner_name → "상대방" 순으로 "{이름}(상대)".
    4. 지금 할 일은 마지막 화자로 정한다 (상대면 답하기, 본인이면 이어서 보내기, 대화가 없으면 첫 메시지).
    """
    if max_turns < 0:
        raise ValueError(f"max_turns 는 0 이상이어야 합니다: {max_turns}")
    turns = [t for t in history if t.text.strip()]
    recent = turns[-max_turns:] if max_turns else []  # [-0:] 은 전체가 되므로 0 은 따로 처리

    me = self_label(name)
    lines = [
        f"{me if t.speaker == SELF else partner_label(_partner_of(t, partner_name))}: {t.text.strip()}" for t in recent
    ]
    log = "\n".join(lines) if lines else EMPTY_HISTORY
    return f"{HISTORY_HEADER}\n{log}\n\n{TASK_HEADER}\n{_task(recent, name, partner_name)}"


def build_messages(
    persona_prompt: str,
    history: Sequence[Turn],
    memory: Sequence[str] = (),
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    partner_name: str | None = None,
) -> list[Message]:
    """추론용 messages [system, user]. partner_name 은 상대 이름 (모르면 None → "상대방")."""
    system = build_system(persona_prompt, memory)
    user = build_user_message(history, persona_name(persona_prompt), partner_name, max_turns=max_turns)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ══ 모델 출력 정리 ══════════════════════════════════════════════
# 출력 맨 앞의 본인 라벨. 이름이 틀려도 "(나)" 가 붙었으면 본인 라벨로 본다
_ANY_SELF_LEAD = re.compile(r"^\s*[^\s:：()]{1,20}\s*\(나\)\s*[:：]\s*")
# 어디서든 "이름(나):" / "이름(상대):" — 모델이 대화 기록 형식대로 다른 대사를 이어 쓴 것
_ANY_LABEL = re.compile(r"[^\s:：()]{1,20}[ \t]*\((?:나|상대)\)[ \t]*[:：]")


def split_reply(text: str, name: str | None, partner_name: str | None = None) -> tuple[str, str]:
    """모델 출력 → (답, 잘라낸 뒷부분). 규칙은 strip_reply_label."""
    me = _clean_name(name) or SELF_FALLBACK
    you = _clean_name(partner_name) or PARTNER_FALLBACK
    own_lead = re.compile(r"^\s*" + re.escape(me) + r"\s*(?:\(나\))?\s*[:：]\s*")
    rest = text
    while m := (own_lead.match(rest) or _ANY_SELF_LEAD.match(rest)):  # 라벨을 두 번 쓴 경우까지
        rest = rest[m.end() :]
    stops = [f"\n{you}(", f"\n{me}(", f"{you}({PARTNER_ROLE})", f"{me}({SELF_ROLE})"]
    cuts = [i for s in stops if (i := rest.find(s)) >= 0]
    if m := _ANY_LABEL.search(rest):
        cuts.append(m.start())
    cut = min(cuts, default=len(rest))
    return rest[:cut].strip(), rest[cut:].strip()


def strip_reply_label(text: str, name: str | None, partner_name: str | None = None) -> str:
    """모델 출력에서 맨 앞 본인 라벨("{이름}(나):")을 떼고, 다른 대사 라벨이 나오면 그 앞까지만 남긴다.

    출력이 상대 라벨로 시작하면(상대 대사를 대신 쓴 출력) 빈 문자열이 된다.
    """
    return split_reply(text, name, partner_name)[0]
