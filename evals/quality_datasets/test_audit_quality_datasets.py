"""근사 검사기가 명사 치환은 찾고 짧은 관용 답변은 과대 판정하지 않는지 검증한다."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("quality_audit", Path(__file__).with_name("audit_quality_datasets.py"))
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "요즘 쉬는 날에는 수제 노트를 즐기고 관련 재료를 정돈해요.",
            "요즘 쉬는 날에는 티 블렌딩을 즐기고 관련 재료를 정돈해요.",
        ),
        (
            "다음에도 문화센터에서 이야기 나누되 일정은 서로 확인해요.",
            "다음에도 독립서점에서 이야기 나누되 일정은 서로 확인해요.",
        ),
        (
            "  이번 문화센터 약속에서 서로 편한 조건을 먼저 맞춰 봐요！ ",
            "이번 요리 수업 약속에서 서로 편한 조건을 먼저 맞춰 봐요.",
        ),
        (
            "붐비는 놀이공원에 대해서는 아직 제 선호를 정하지 않았어요.",
            "종이접기에 대해서는 아직 제 선호를 정하지 않았어요.",
        ),
    ],
)
def test_noun_substitution_and_punctuation_are_detected(left, right):
    assert audit_module.approximation(audit_module.normalize(left), audit_module.normalize(right)) is not None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("네", "네"),
        ("그럴 수도 있겠네요.", "그럴 수도 있겠어요."),
        ("차" * 500, "차" * 501),
        (
            "말다툼이 시작되면 혼자 산책하러 나간 뒤 이튿날 대화를 청해요.",
            "모임에서 상대가 직원에게 고맙다고 인사한 모습이 며칠째 떠올라요.",
        ),
    ],
)
def test_short_conventions_boundaries_and_unrelated_scenes_are_not_near_duplicates(left, right):
    assert audit_module.approximation(audit_module.normalize(left), audit_module.normalize(right)) is None


def test_scan_checks_holdout_against_both_training_splits():
    sentence_index = {
        "다음에도 문화센터에서 이야기 나누되 일정은 서로 확인해요": [
            {"caseId": "heldout", "split": "blind_holdout", "path": "history.1.content"}
        ],
        "다음에도 독립서점에서 이야기 나누되 일정은 서로 확인해요": [
            {"caseId": "calibration", "split": "calibration", "path": "history.1.content"},
            {"caseId": "regression", "split": "regression", "path": "history.1.content"},
        ],
    }
    result = audit_module.approximate_leaks(sentence_index)
    assert len(result) == 1
    assert result[0]["holdoutCases"] == ["heldout"]
    assert result[0]["otherCases"] == ["calibration", "regression"]


def test_stimulus_extraction_keeps_short_answers_and_omits_source_topic_contract():
    found = set(audit_module.stimuli({"answer": "음", "topic": {"question": "계약에 있는 고정 질문"}}))
    assert found == {("음", "answer")}


def test_saved_corpus_passes_independent_audit():
    result = audit_module.audit()
    assert result["ok"], result["errors"]
    assert result["rewrittenHoldoutVerification"]["caseCount"] == 21
    assert result["rewrittenHoldoutVerification"]["collisionCount"] == 0
