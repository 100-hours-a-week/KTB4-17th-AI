"""teacher 추출 라벨 → 학습 데이터 — 서빙 입력과 같은지, 평가셋 누수·프롬프트 불일치를 막는지 고정한다."""

import json

from app.features.persona.agents import RUBRIC, ExtractionAgent
from scripts.label_with_teacher import prompt_version
from training.extraction import from_teacher as ft

HISTORY = [{"role": "assistant", "content": "요즘 어때요?"}, {"role": "user", "content": "조약돌 모아요."}]


def _row(row_id="synthetic:s1", family="f1", output=None, answered=("anxiety", "interests"), **extra):
    return {
        "kind": "extraction",
        "id": row_id,
        "source": "synthetic",
        "familyId": family,
        "input": {"history": HISTORY, "answered": list(answered)},
        "output": {"anxiety": 80} if output is None else output,
        "schemaError": None,
        "teacher": {"extractPromptVersion": prompt_version(RUBRIC)},
        **extra,
    }


def test_prompt_is_exactly_what_serving_sends():
    record, reason = ft.convert(_row(), leaked=set())

    assert reason == ""
    assert record["prompt"][0] == {"role": "system", "content": RUBRIC}
    expected = ExtractionAgent._transcript(HISTORY) + ExtractionAgent._answered_section({"anxiety", "interests"})
    assert record["prompt"][1] == {"role": "user", "content": expected}


def test_target_drops_unanswered_and_unknown_keys():
    raw = {
        "anxiety": 82,
        "avoidance": 50,  # answered 밖 → 지움
        "interests": ["조약돌 분류"],
        "routine": ["저녁 정리"],  # answered 밖 → 지움
        "made_up": 1,  # 스키마에 없음 → 지움
        "narrative": {"headline": "h"},
    }

    record, _ = ft.convert(_row(output=raw), leaked=set())

    assert json.loads(record["completion"][0]["content"]) == {
        "anxiety": 82,
        "interests": ["조약돌 분류"],
        "narrative": {"headline": "h"},
    }


def test_gold_failed_and_schema_error_rows_are_skipped():
    failed = _row()
    del failed["output"]

    assert ft.convert(_row(source="gold"), leaked=set())[1] == "합성 데이터가 아님"
    assert ft.convert(failed, leaked=set())[1] == "호출·파싱 실패"
    assert ft.convert(_row(schemaError="range"), leaked=set())[1] == "스키마 오류"


def test_session_with_an_eval_answer_is_skipped():
    leaked = {"조약돌모아요"}  # normalize("조약돌 모아요.")

    assert ft.convert(_row(), leaked=leaked)[1] == "평가 데이터셋과 같은 답변 (평가 누수)"


def test_prompt_drift_is_detected():
    assert not ft.prompt_drift([_row()])
    assert ft.prompt_drift([_row(teacher={"extractPromptVersion": "000000000000"})]) == {"000000000000"}


def test_main_splits_by_family_and_blocks_drift(tmp_path):
    rows = [_row(f"synthetic:s{i}", f"family-{i % 20}") for i in range(60)]
    src = tmp_path / "extraction.jsonl"
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    assert ft.main([str(src), "--out", str(tmp_path / "out"), "--val-ratio", "0.3"]) == 0

    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert manifest["counts"]["train"] + manifest["counts"]["val"] == 60
    assert manifest["counts"]["val"] % 3 == 0  # 한 family(3세션)는 통째로 한쪽에만
    assert manifest["unreviewed_allowed"] is True
    assert manifest["prompt_sha256"] == ft.prompt_sha256()

    stale = tmp_path / "stale.jsonl"
    stale.write_text(json.dumps(_row(teacher={"extractPromptVersion": "000000000000"})) + "\n")
    assert ft.main([str(stale), "--out", str(tmp_path / "out2")]) == 1
    assert ft.main([str(stale), "--out", str(tmp_path / "out2"), "--allow-prompt-drift"]) == 0
