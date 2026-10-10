from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal


def next_node(
    utterance_count: int,
    turns: int,
    error: str | None = None,
) -> Literal["speak_a", "speak_b", "report", "stop"]:
    """시뮬레이션 발화 순서와 다음 실행할 노드를 결정하는 순수 함수.

    Args:
        utterance_count: 현재까지 누적된 대사 개수 (0부터 시작).
        turns: 시뮬레이션 총 왕복 횟수 (3 이상 15 이하).
        error: 직전 단계 오류 사유 (있으면 중단).

    Returns:
        다음 실행할 노드 이름 ("speak_a", "speak_b", "report", "stop").

    Raises:
        ValueError: turns가 3 미만이거나 15 초과인 경우.
    """
    if turns < 3 or turns > 15:
        raise ValueError(f"turns는 3 이상 15 이하여야 합니다 (현재 {turns}).")

    if error is not None:
        return "stop"

    target_count = turns * 2
    if utterance_count > target_count:
        return "stop"
    if utterance_count == target_count:
        return "report"

    # 다음 index는 utterance_count. 짝수면 speak_a, 홀수면 speak_b
    if utterance_count % 2 == 0:
        return "speak_a"
    return "speak_b"


def closing_hint_for(
    index: int,
    turns: int,
) -> Literal["none", "no_new_question", "close"]:
    """해당 발화 순번(index)의 종료 안내 힌트를 반환한다.

    Args:
        index: 발화 순번 (0부터 turns * 2 - 1).
        turns: 총 왕복 횟수.

    Returns:
        "none", "no_new_question", "close" 중 하나.
    """
    if index == turns * 2 - 2:
        return "no_new_question"
    if index == turns * 2 - 1:
        return "close"
    return "none"


def arc_phase_for(
    index: int,
    turns: int,
) -> Literal["open", "middle", "late", "close_a", "close_b"]:
    """발화 순번이 대화의 어느 구간인지 반환한다.

    앞 구간은 인사와 관심사, 중간은 속도 차이를 한 번만, 뒤 구간은 작별 전이다.
    마지막 두 줄만 close_a, close_b 다.
    """
    if turns < 3 or turns > 15:
        raise ValueError(f"turns는 3 이상 15 이하여야 합니다 (현재 {turns}).")
    if index == turns * 2 - 2:
        return "close_a"
    if index == turns * 2 - 1:
        return "close_b"
    round_index = index // 2
    early_until = max(1, turns // 3)
    middle_until = max(early_until + 1, (2 * turns) // 3)
    if round_index < early_until:
        return "open"
    if round_index < middle_until:
        return "middle"
    return "late"


_FAREWELL_RE = re.compile(
    r"들어가|나중에\s*봐|나중에\s*봬|또\s*봐|연락할(?:게|게요)|연락드릴|잘\s*자|좋은\s*밤|좋은\s*저녁|편안한\s*밤|조심히"
)


def contains_farewell(text: str) -> bool:
    """작별 인사로 대화를 닫는 표현이 있는지 본다."""
    return _FAREWELL_RE.search(text) is not None


def _compact(text: str) -> str:
    compacted = re.sub(r"\s+", "", text)
    for token in ("ㅎㅎ", "ㅋ", "!", ".", "~", "?", "？"):
        compacted = compacted.replace(token, "")
    return compacted


def repeats_previous(text: str, previous: str) -> bool:
    """같은 화자의 직전 문장과 사실상 같은 말인지 본다. 짧은 문장은 완전히 같을 때만."""
    current = _compact(text)
    prior = _compact(previous)
    if len(current) < 12 or len(prior) < 12:
        return False
    if current == prior:
        return True
    return SequenceMatcher(None, current, prior).ratio() >= 0.82


def self_addressed(text: str, nickname: str) -> bool:
    """화자가 자기 닉네임 뒤에 '님'을 붙여 자칭했는지 검사한다.

    닉네임 바로 앞에 한글, 영문, 숫자가 없고 바로 뒤에 '님'이 붙은 경우에만 자칭으로 판정한다 (#77).

    Args:
        text: 검사할 발화 텍스트.
        nickname: 화자 자신의 닉네임.

    Returns:
        닉네임 길이가 2 이상이고 올바른 경계에서 nickname + "님"이 포함되어 있으면 True,
        1글자 닉네임이거나 포함되지 않았으면 False.
    """
    if len(nickname) < 2:
        return False
    pattern = rf"(?<![가-힣A-Za-z0-9]){re.escape(nickname)}님"
    return bool(re.search(pattern, text))


def strip_name_prefix(text: str, nickname: str) -> str:
    """대사 앞머리의 이름표 접두사('민지:', '민지：')를 벗겨낸다.

    앞뒤 공백을 벗긴 뒤, 첫 콜론(ASCII : 또는 전각 ：)까지가 nickname과 같으면
    콜론 뒤를 한 번만 반환한다. 두 번째 콜론은 남긴다. 이름표가 아니면 원문을 반환한다.

    Args:
        text: 모델이 생성한 원문 대사.
        nickname: 화자 닉네임.

    Returns:
        이름표가 벗겨진 대사 또는 원문 대사.
    """
    stripped = text.strip()
    idx_half = stripped.find(":")
    idx_full = stripped.find("：")

    if idx_half != -1 and idx_full != -1:
        colon_idx = min(idx_half, idx_full)
    elif idx_half != -1:
        colon_idx = idx_half
    elif idx_full != -1:
        colon_idx = idx_full
    else:
        return text

    prefix = stripped[:colon_idx].strip()
    if prefix == nickname:
        return stripped[colon_idx + 1 :].strip()

    return text
