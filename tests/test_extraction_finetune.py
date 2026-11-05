"""추출 파인튜닝 데이터 변환. 학습 자체(torch)는 GPU 서버에서 돌리므로 여기선 다루지 않는다."""

import json

import pytest

from app.features.persona.agents import RUBRIC, ExtractionAgent
from app.features.persona.schemas import RawExtraction
from training.extraction.build_sft import gold_answers, has_gold_answer, main, prompt_sha256, skip_reason, to_record
from training.tagging.data import normalize
from training.tagging.train_lora import assert_fits

HISTORY = [
    {"role": "assistant", "content": "연락은 얼마나 자주 하세요?"},
    {"role": "user", "content": "하루 한두 번이면 충분해요."},
]


def row(**overrides) -> dict:
    fields = {
        "kind": "extraction",
        "id": "synthetic:syn-0-00001",
        "source": "synthetic",
        "familyId": "seed-0-00001",
        "input": {"history": HISTORY, "answered": ["contact_rhythm"]},
        "context": {"seedLevels": {"contact_rhythm": "low"}},
        "output": {
            "contact_rhythm": 20,
            "narrative": {"headline": "연락이 뜸한 타입", "body": "할 말 있을 때 연락하는 편이에요.", "traits": []},
            "summaries": [{"category": "communication", "title": "뜸한 연락 편", "content": "자주 연락하지 않아요."}],
        },
        "schemaError": None,
        "unansweredKeys": [],
        "teacher": {"extractPromptVersion": prompt_sha256()[:12]},
    } | overrides
    return fields


def test_training_prompt_is_exactly_what_serving_sends():
    record = to_record(row())

    content = ExtractionAgent._transcript(HISTORY) + ExtractionAgent._answered_section({"contact_rhythm"})
    assert record["prompt"] == [{"role": "system", "content": RUBRIC}, {"role": "user", "content": content}]
    # 학습 정답은 서빙 스키마를 그대로 통과해야 한다
    assert RawExtraction.model_validate(json.loads(record["completion"][0]["content"])).contact_rhythm == 20


def test_valid_row_is_kept():
    assert skip_reason(row()) == ""


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"source": "gold"}, "합성 데이터가 아님"),
        ({"output": None, "error": "parse: x"}, "호출·파싱 실패"),
        ({"schemaError": "bad"}, "스키마 위반"),
        ({"unansweredKeys": ["anxiety"]}, "답하지 않은 차원에 점수"),
        ({"output": {"narracy": {}, "narrative": {"body": "x"}}}, "스키마에 없는 키"),
        ({"output": {"contact_rhythm": 20}}, "narrative 없음"),
        ({"output": {"narrative": {"body": "서운하실 수 있겠네요"}}}, "짐작 어미"),
        ({"output": {"contact_rhythm": 80, "narrative": {"body": "자주 연락해요"}}}, "시드와 정반대 점수"),
    ],
)
def test_bad_rows_are_skipped(overrides, reason):
    assert skip_reason(row(**overrides)) == reason


def test_mid_seed_never_counts_as_opposite():
    narrative = {"headline": "연락이 잦은 타입", "body": "자주 연락해요", "traits": []}
    kept = row(context={"seedLevels": {"contact_rhythm": "mid"}}, output={"contact_rhythm": 90, "narrative": narrative})
    assert skip_reason(kept) == ""


def test_output_the_service_would_drop_is_skipped():
    narrative = {"headline": "진지한 타입", "body": "가볍게 만나는 쪽을 좋아해요.", "traits": []}
    contradiction = row(
        input={"history": HISTORY, "answered": ["seriousness"]},
        context={},
        output={"seriousness": 90, "narrative": narrative},
    )
    assert skip_reason(contradiction) == "서술과 점수 모순"

    card = {"category": "intimacy", "title": "천천히 가까워지는 편", "content": "서서히 알아가요."}
    output = {**row()["output"], "summaries": [card]}
    assert skip_reason(row(output=output)) == "근거 없는 카드"


class _FakeTokenizer:
    def apply_chat_template(self, messages, tokenize):
        assert tokenize is False
        return " ".join(m["content"] for m in messages)

    def __call__(self, text, add_special_tokens):
        assert add_special_tokens is False
        return {"input_ids": text.split()}


def test_training_refuses_to_truncate_completions():
    rows = [{"prompt": [{"role": "user", "content": "a b c"}], "completion": [{"role": "assistant", "content": "d"}]}]

    assert_fits(rows, _FakeTokenizer(), max_length=4)
    with pytest.raises(SystemExit, match="4 토큰"):
        assert_fits(rows, _FakeTokenizer(), max_length=3)


def test_gold_answers_are_found_in_both_dataset_shapes(tmp_path):
    build = tmp_path / "build.jsonl"
    build.write_text(json.dumps({"input": {"history": HISTORY}}, ensure_ascii=False) + "\n", encoding="utf-8")
    goldset = tmp_path / "goldset.jsonl"
    goldset.write_text(
        json.dumps({"input": {"answers": ["주말엔 쉬어요"]}}, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    gold = gold_answers(build, goldset)

    assert normalize("하루 한두 번이면 충분해요") in gold
    assert normalize("주말엔 쉬어요") in gold
    assert normalize("연락은 얼마나 자주 하세요?") not in gold  # 질문은 답변이 아니다
    assert has_gold_answer(row(), gold)


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_main_splits_by_family_and_writes_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("training.extraction.build_sft.gold_answers", lambda: set())
    rows = [row(id=f"synthetic:{i}", familyId=f"seed-{i}") for i in range(30)] + [row(id="bad", schemaError="x")]
    source = tmp_path / "extraction.jsonl"
    _write(source, rows)
    out = tmp_path / "out"

    assert main([str(source), "--out", str(out), "--allow-unreviewed"]) == 0

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["counts"]["train"] + manifest["counts"]["val"] == 30
    assert manifest["skipped"] == {"스키마 위반": 1}
    assert manifest["prompt_sha256"] == prompt_sha256()
    assert manifest["unreviewed_allowed"] is True


def test_main_refuses_without_explicit_unreviewed_flag(tmp_path):
    source = tmp_path / "extraction.jsonl"
    _write(source, [row()])

    assert main([str(source), "--out", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()


def test_main_refuses_labels_from_another_prompt(tmp_path):
    source = tmp_path / "extraction.jsonl"
    _write(source, [row(teacher={"extractPromptVersion": "000000000000"})])

    assert main([str(source), "--out", str(tmp_path / "out"), "--allow-unreviewed"]) == 1
    assert not (tmp_path / "out").exists()


def test_main_drops_sessions_with_gold_answers(tmp_path, monkeypatch):
    monkeypatch.setattr("training.extraction.build_sft.gold_answers", lambda: {normalize("하루 한두 번이면 충분해요.")})
    source = tmp_path / "extraction.jsonl"
    _write(source, [row()])
    out = tmp_path / "out"

    assert main([str(source), "--out", str(out), "--allow-unreviewed"]) == 0

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["skipped"] == {"평가 데이터셋과 같은 답변": 1}
    assert manifest["counts"] == {"train": 0, "val": 0}
