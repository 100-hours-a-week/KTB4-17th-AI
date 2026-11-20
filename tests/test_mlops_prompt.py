"""말투 어댑터 프롬프트가 학습 때(persona-mlops prompt_builder)와 글자 단위로 같은지.

기대값은 persona-mlops 의 원본 빌더로 만든 tests/fixtures/mlops_prompt_golden.json 이다 (원본을 import 하지 않는다).
"""

import json
from pathlib import Path

import pytest

from app.features.mlops.prompt import Turn, build_messages, build_system, persona_name, split_reply, strip_reply_label

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "mlops_prompt_golden.json").read_text(encoding="utf-8"))


def _turns(rows: list[dict]) -> list[Turn]:
    return [Turn(r["speaker"], r["text"], r.get("name")) for r in rows]


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_messages_match_mlops_builder(case):
    got = build_messages(
        GOLDEN["personas"][case["persona"]],
        _turns(case["history"]),
        case["memory"],
        partner_name=case["partner_name"],
    )
    assert got == case["expected"]


def test_system_with_memory_matches_mlops_builder():
    assert build_system(GOLDEN["personas"]["지민"], ["메모 하나"]) == GOLDEN["system_with_memory_only"]
    assert "## 기억" in GOLDEN["system_with_memory_only"]


@pytest.mark.parametrize("row", GOLDEN["replies"], ids=[r["text"][:20] for r in GOLDEN["replies"]])
def test_reply_label_split_matches_mlops(row):
    assert split_reply(row["text"], row["name"], row["partner_name"]) == (row["reply"], row["tail"])
    assert strip_reply_label(row["text"], row["name"], row["partner_name"]) == row["reply"]


def test_persona_name_reads_profile_first_line():
    assert persona_name(GOLDEN["personas"]["지민"]) == "지민"
    assert persona_name(GOLDEN["personas"]["서윤"]) == "서윤"
    assert persona_name("이름 줄이 없는 프로필") is None


def test_empty_profile_and_string_memory_are_rejected():
    with pytest.raises(ValueError):
        build_system("   ")
    with pytest.raises(TypeError):
        build_system("### 지민", "문자열 하나")  # type: ignore[arg-type]
