from types import SimpleNamespace

from app.v2.persona.pairs import (
    build_pairs_from_onboarding_turns,
    build_pairs_from_practice_messages,
)
from scripts.eval_v2_memory_rag import evaluate_retrieval_metrics


def test_build_pairs_from_onboarding_excludes_skipped_and_off_topic():
    turns = [
        SimpleNamespace(id=1, question="취미가 뭐예요?", answer="주말마다 등산 가요", skipped=False, tags={}),
        SimpleNamespace(id=2, question="주말엔 뭐 해요?", answer=None, skipped=True, tags={}),
        SimpleNamespace(id=3, question="연락 빈도는요?", answer="배고파요", skipped=False, tags={"off_topic": True}),
    ]
    pairs = build_pairs_from_onboarding_turns(turns)
    assert len(pairs) == 1
    assert pairs[0]["user"] == "주말마다 등산 가요"


def test_build_pairs_from_practice_messages_respects_session_boundary():
    msgs = [
        SimpleNamespace(id=1, session_id="s1", role="persona", content="세션1 질문"),
        SimpleNamespace(id=2, session_id="s1", role="user", content="세션1 과거답변"),
        SimpleNamespace(id=3, session_id="s2", role="user", content="세션2 첫말"),  # s1의 질문이 붙으면 안 됨
    ]
    pairs = build_pairs_from_practice_messages(msgs, last_processed_id=2, overlap=1)
    assert len(pairs) == 1
    assert pairs[0]["message_id"] == 3
    assert pairs[0]["ai"] == ""


def test_evaluate_retrieval_metrics_with_cosine_distribution():
    sample = [
        ("주말마다 등산을 다닌다", ["주말마다 등산을 다닌다", "커피를 좋아한다"]),
        ("부산에 거주한다", ["운동을 좋아한다", "부산에 거주한다"]),
    ]
    metrics = evaluate_retrieval_metrics(sample, distances=[0.12, 0.28, 0.44], k=5)
    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr"] == 0.75
    assert metrics["dist_p50"] == 0.28
