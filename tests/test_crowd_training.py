"""다수결 → 태깅 학습 데이터 · 다수결 평가 채점 · 게이트 · Pod 파이프라인 단계. 네트워크 · GPU 없음."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.features.sentence_cards.models import Sentence
from scripts.eval_crowd_tagging import predicted_label, summarize
from scripts.export_crowd_tagging import build, correct_tags, load_sentences, write
from training.pipeline import gate, run_tagging
from training.tagging.data import validate

T0 = datetime(2026, 11, 11, tzinfo=UTC)
CHAMPION = {"primary": ["contact_rhythm"], "secondary": ["avoidance"], "off_topic": False}


def _sentence(sid: str, dimension: str, verdict: str, consensus: str | None, **extra) -> Sentence:
    values = {
        "id": sid,
        "family_id": "fam-1",
        "sentence": "답장은 생각날 때 몰아서 하는 편이에요",
        "context_question": "연락은 자주 하는 편이에요?",
        "source": "team_written",
        "question_type": "AXIS",
        "dimension": dimension,
        "expected_champion": "direct",
        "champion_tags": CHAMPION,
        "verdict": verdict,
        "consensus": consensus,
        "created_at": T0,
        "active": True,
        "is_check": False,
    }
    return Sentence(**(values | extra))


# ── 한 차원 교정 ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("judged", "expected"),
    [
        ({"contact_rhythm": "inferred"}, {"primary": [], "secondary": ["avoidance", "contact_rhythm"]}),
        ({"contact_rhythm": "none"}, {"primary": [], "secondary": ["avoidance"]}),
        ({"avoidance": "direct"}, {"primary": ["contact_rhythm", "avoidance"], "secondary": []}),
        ({"disclosure": "inferred"}, {"primary": ["contact_rhythm"], "secondary": ["avoidance", "disclosure"]}),
    ],
)
def test_correct_tags_changes_only_judged_dimensions(judged, expected):
    assert correct_tags(CHAMPION, judged) == expected | {"off_topic": False}


def test_correct_tags_clears_off_topic_when_crowd_finds_a_dimension():
    off = {"primary": [], "secondary": [], "off_topic": True}
    assert correct_tags(off, {"avoidance": "inferred"})["off_topic"] is False
    assert correct_tags(off, {"avoidance": "none"})["off_topic"] is True


# ── 학습 · 평가 데이터 만들기 ─────────────────────────────


def test_build_merges_dimensions_of_same_input_and_records_reviewers():
    sentences = [
        _sentence("a", "contact_rhythm", "DISAGREE", "inferred", reviewed_by="lena"),
        _sentence("b", "avoidance", "AGREE", "inferred", created_at=T0 + timedelta(minutes=1)),
        _sentence("c", "disclosure", "AMBIGUOUS", "none"),  # 그 차원은 champion 출력 그대로
        _sentence("d", "openness", "DISAGREE", "direct"),  # 사람 확인 전 — 쓰지 않는다
    ]
    examples, eval_rows = build(sentences, eval_ratio=0.0)
    assert eval_rows == []
    (ex,) = examples
    assert (ex.question, ex.answer, ex.source, ex.group) == (
        "연락은 자주 하는 편이에요?",
        "답장은 생각날 때 몰아서 하는 편이에요",
        "crowd",
        "fam-1",
    )
    assert (ex.primary, ex.secondary, ex.off_topic) == ([], ["avoidance", "contact_rhythm"], False)
    assert ex.reviewed_by == "crowd+lena"
    assert validate(examples, leaked=set()) == []


def test_build_skips_inputs_without_usable_judgement():
    examples, _ = build([_sentence("a", "contact_rhythm", "AMBIGUOUS", "none")], eval_ratio=0.0)
    assert examples == []


def test_eval_families_are_held_out_with_ambiguous_rows():
    sentences = [
        _sentence("a", "contact_rhythm", "AGREE", "direct"),
        _sentence("b", "avoidance", "AMBIGUOUS", "unsure"),
        _sentence("c", "disclosure", "DISAGREE", "none"),  # 사람 확인 전 — 평가에도 안 쓴다
    ]
    examples, eval_rows = build(sentences, eval_ratio=1.0)
    assert examples == []  # 평가 묶음 문장은 학습에 들어가지 않는다
    assert [(r["dimension"], r["label"], r["verdict"]) for r in eval_rows] == [
        ("contact_rhythm", "direct", "AGREE"),
        ("avoidance", None, "AMBIGUOUS"),
    ]


def test_load_sentences_reads_only_usable_axis_rows(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all(
                [
                    _sentence("ok", "contact_rhythm", "AGREE", "direct"),
                    _sentence("pending", "avoidance", "PENDING", None),
                    _sentence("check", "avoidance", "AGREE", "direct", is_check=True, check_answer="direct"),
                    _sentence("off", "avoidance", "AGREE", "direct", active=False),
                    _sentence("old", "avoidance", "AGREE", "direct", champion_tags=None),
                    _sentence("dir", "avoidance", "AGREE", "high", question_type="DIRECTION"),
                ]
            )
            await db.commit()
            return [s.id for s in await load_sentences(db)]

    assert asyncio.run(run()) == ["ok"]
    asyncio.run(engine.dispose())


def test_write_outputs_files_and_manifest(tmp_path):
    examples, _ = build([_sentence("a", "contact_rhythm", "AGREE", "direct")], eval_ratio=0.0)
    manifest = write(tmp_path, examples, [{"id": "e", "verdict": "AGREE"}], trigger_seq=3)
    assert manifest["trigger_seq"] == 3 and manifest["train"]["count"] == 1
    row = json.loads((tmp_path / "train.jsonl").read_text())
    assert row["source"] == "crowd" and "consent_basis" not in row
    assert json.loads((tmp_path / "manifest.json").read_text())["eval"]["by_verdict"] == {"AGREE": 1}


# ── 다수결 평가 채점 ──────────────────────────────────────


def test_crowd_eval_scores_only_the_judged_dimension():
    out = {"primary": ["avoidance"], "secondary": ["contact_rhythm"], "off_topic": False}
    assert predicted_label(out, "avoidance") == "direct"
    assert predicted_label(out, "contact_rhythm") == "inferred"
    assert predicted_label(out, "openness") == "none"
    assert predicted_label(None, "openness") is None

    results = [
        {"label": "direct", "predicted": "direct"},
        {"label": "none", "predicted": "inferred"},
        {"label": None, "predicted": "direct"},
        {"label": None, "predicted": "none"},
        {"label": "none", "predicted": None},
    ]
    assert summarize(results) == {
        "rows": 5,
        "labeled": 3,
        "accuracy_pct": 33.3,
        "ambiguous": 2,
        "ambiguous_confident_pct": 50.0,
        "failure_pct": 20.0,
    }


# ── 게이트 ────────────────────────────────────────────────


def _gold(exact, primary=50.0, failure=0.0):
    return {"summary": {"exact_pct": exact, "primary_pct": primary, "failure_pct": failure}}


def _crowd(accuracy, ambiguous=10.0, failure=0.0):
    return {"summary": {"accuracy_pct": accuracy, "ambiguous_confident_pct": ambiguous, "failure_pct": failure}}


def test_gate_passes_when_nothing_gets_worse():
    result = gate.checks(_gold(40.0, failure=2.0), _gold(40.0, failure=0.5), _crowd(70.0), _crowd(75.0, 5.0))
    assert all(c.passed for c in result)


@pytest.mark.parametrize(
    ("candidate_gold", "candidate_crowd", "failed"),
    [
        (_gold(39.9), _crowd(75.0), "gold_exact_pct"),
        (_gold(40.0, failure=1.5), _crowd(75.0), "gold_failure_pct"),
        (_gold(40.0), _crowd(69.0), "crowd_accuracy_pct"),
        (_gold(40.0), _crowd(75.0, ambiguous=12.0), "crowd_ambiguous_confident_pct"),
    ],
)
def test_gate_fails_on_any_regression(candidate_gold, candidate_crowd, failed):
    result = gate.checks(_gold(40.0), candidate_gold, _crowd(70.0), candidate_crowd)
    assert [c.name for c in result if not c.passed] == [failed]


def test_gate_cli_exit_code(tmp_path):
    files = {}
    for name, data in {
        "gold-champion": _gold(40.0),
        "gold-candidate": _gold(30.0),
        "crowd-champion": _crowd(70.0),
        "crowd-candidate": _crowd(70.0),
    }.items():
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(data))
    argv = [arg for name, path in files.items() for arg in (f"--{name}", str(path))]
    assert gate.main([*argv, "--out", str(tmp_path / "gate.json")]) == 3
    assert json.loads((tmp_path / "gate.json").read_text())["passed"] is False


# ── Pod 파이프라인 ────────────────────────────────────────


def test_pipeline_trains_on_the_served_base_model():
    model, revision = run_tagging.served_base_model()
    assert model == "kakaocorp/kanana-2-3b-instruct" and len(revision) == 40


def test_pipeline_dry_run_lists_every_stage(capsys, tmp_path):
    code = run_tagging.main(
        [
            *("--crowd-repo", "org/crowd", "--crowd-revision", "crowd-20261111-t1"),
            *("--champion-repo", "org/tagging-lora", "--champion-revision", "prod-v1"),
            *("--base-data", "base.jsonl", "--work-dir", str(tmp_path), "--dry-run"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    for stage in ("download org/crowd@crowd-20261111-t1", "build_sft.py", "train kakaocorp/kanana-2-3b-instruct"):
        assert stage in out
    assert out.count("eval_tagging.py") == 2 and out.count("eval_crowd_tagging.py") == 2
    assert "gate.py" in out and "org/tagging-lora@candidate-crowd-20261111-t1" in out
    assert not any(tmp_path.iterdir())  # dry-run 은 아무것도 만들지 않는다


def test_pipeline_requires_champion_adapter(monkeypatch):
    monkeypatch.delenv("TAGGING_ADAPTER_REPO", raising=False)
    monkeypatch.delenv("TAGGING_ADAPTER_REVISION", raising=False)
    with pytest.raises(SystemExit):
        run_tagging.main(["--crowd-repo", "org/crowd", "--crowd-revision", "r", "--dry-run"])


def test_merge_and_result_message(tmp_path):
    base, crowd = tmp_path / "base.jsonl", tmp_path / "crowd.jsonl"
    base.write_text('{"id": "b1"}\n\n{"id": "b2"}\n')
    crowd.write_text('{"id": "c1"}\n')
    assert run_tagging.merge_training_data([base], crowd, tmp_path / "out.jsonl") == 3

    report = {
        "passed": False,
        "checks": [{"name": "gold_exact_pct", "champion": 40.0, "candidate": 39.0, "passed": False}],
    }
    message = run_tagging.result_message("crowd-r", report, None)
    assert "게이트 실패" in message and "✗ gold_exact_pct: champion 40.0 → candidate 39.0" in message
