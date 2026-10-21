"""onboarding_turns / practice_messages 를 memory_graph 입력 쌍으로 변환한다."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_pairs_from_onboarding_turns(turns: list[Any]) -> list[dict[str, Any]]:
    """OnboardingTurn 리스트에서 skipped=False 이고 off_topic 이 아닌 질문-답변 쌍만 추출한다."""
    pairs: list[dict[str, Any]] = []
    for t in turns:
        if getattr(t, "skipped", False) or not getattr(t, "answer", None):
            continue
        tags = getattr(t, "tags", None) or {}
        if isinstance(tags, dict) and tags.get("off_topic") is True:
            continue
        pairs.append({"ai": t.question, "user": t.answer, "message_id": t.id, "is_overlap": False})
    return pairs


def build_pairs_from_practice_messages(
    messages: list[Any],
    *,
    last_processed_id: int = 0,
    overlap: int = 2,
) -> list[dict[str, Any]]:
    """PracticeMessage 리스트를 session_id 별로 분리해 직전 AI(persona) 발화와 사용자 발화를 쌍으로 묶는다."""
    by_session: dict[str, list[Any]] = defaultdict(list)
    for m in messages:
        by_session[getattr(m, "session_id", "default")].append(m)

    result: list[dict[str, Any]] = []
    for sess_msgs in by_session.values():
        sess_pairs: list[dict[str, Any]] = []
        last_ai_text = ""
        for m in sess_msgs:
            if m.role == "persona":
                last_ai_text = m.content
            elif m.role == "user":
                sess_pairs.append(
                    {
                        "ai": last_ai_text,
                        "user": m.content,
                        "message_id": m.id,
                        "is_overlap": m.id <= last_processed_id,
                    }
                )
        first_new = next((i for i, p in enumerate(sess_pairs) if not p["is_overlap"]), None)
        if first_new is None:
            continue
        start_idx = max(0, first_new - overlap)
        result.extend(sess_pairs[start_idx:])
    return result
