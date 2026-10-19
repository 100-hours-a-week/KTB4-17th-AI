"""teacher 라벨 → TagExample 변환. 검수 전 라벨이 학습으로 바로 새지 않는지, 골드셋이 섞이지 않는지 확인한다."""

import json

from training.tagging.data import validate
from training.tagging.from_teacher import convert, main


def teacher_row(**overrides) -> dict:
    row = {
        "id": "synthetic:syn-1-00000:t0",
        "source": "synthetic",
        "input": {"question": "주말엔 뭐 해요?", "answer": "집에서 빵 굽는 게 제일 좋아요"},
        "aggregate": {
            "validSamples": 5,
            "agreement": 0.8,
            "majority": {"primary": ["interests", "routine"], "secondary": ["routine"], "off_topic": False},
        },
    }
    return row | overrides


def test_convert_keeps_majority_and_drops_primary_secondary_overlap():
    example, reason = convert(teacher_row(), min_agreement=0.6)
    assert reason == ""
    assert example.primary == ["interests", "routine"] and example.secondary == []
    assert example.source == "synthetic" and example.reviewed_by is None
    # 형식 규칙은 통과하고, 검수 전이라는 이유로만 막힌다
    errors = validate([example], leaked=set())
    assert errors == [f"[{example.id}] reviewed_by 없음 (사람이 정답을 확인해야 함)"]


def test_convert_skips_gold_failed_and_low_agreement_rows():
    assert convert(teacher_row(source="gold"), min_agreement=0.6) == (None, "합성 데이터가 아님")
    assert convert(teacher_row(aggregate={"validSamples": 0}), min_agreement=0.6)[1] == "다수결 없음"
    low = teacher_row(aggregate={**teacher_row()["aggregate"], "agreement": 0.4})
    assert convert(low, min_agreement=0.6)[1] == "판정 일치율 낮음"


def test_off_topic_majority_clears_tags():
    agg = {"validSamples": 5, "agreement": 1.0, "majority": {"primary": [], "secondary": [], "off_topic": True}}
    example, _ = convert(teacher_row(aggregate=agg), min_agreement=0.6)
    assert example.off_topic and example.primary == [] and example.secondary == []


def test_main_writes_jsonl(tmp_path, capsys):
    src = tmp_path / "tagging.jsonl"
    rows = [teacher_row(), teacher_row(id="gold:x", source="gold")]
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "tagging_examples.jsonl"
    assert main([str(src), "--out", str(out)]) == 0
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    assert json.loads(capsys.readouterr().out)["skipped"] == {"합성 데이터가 아님": 1}
