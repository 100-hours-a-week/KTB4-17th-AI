"""골드셋 초안 → 라벨링 시트 → 골드셋. 누수·중복 검사, 일치도, 조정, 기존 채점기와의 호환을 확인한다. 네트워크 없음."""

import csv
import importlib.util
import json

from scripts.gold_drafts import (
    LeakIndex,
    _audit,
    cohen_kappa,
    default_leak_index,
    main,
    parse_build_label,
    parse_tag_label,
    prepare,
)

GOLD_ANSWER = "휴일에도 제 작업실에서 혼자 보내는 시간을 꼭 남겨 두고 싶어요."  # 기존 골드셋 tagging-001


def _draft(i, answer, **extra):
    return {
        "draft_id": f"d-{i:03d}",
        "category": "direct",
        "question": "연락은 자주 하는 편이 좋아요?",
        "answer": answer,
    } | extra


def _empty_index():
    index = LeakIndex.__new__(LeakIndex)
    index.audit, index.texts = _audit(), {}
    return index


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _fill(sheet, out, labels):
    """prepare 가 만든 시트를 복사해 라벨을 채운다. labels: case_id → {열: 값}."""
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow(row | labels.get(row["case_id"], {}))


def test_prepare_drops_invalid_leaked_and_duplicate_drafts():
    index = default_leak_index([])
    drafts = [
        _draft(1, "할 말 있을 때만 연락하는 게 편해요. 하루 종일 톡 하면 금방 지치더라고요."),
        _draft(2, "가" * 201),  # 길이 초과
        _draft(3, GOLD_ANSWER.replace(".", "!")),  # 기존 골드셋과 문장부호만 다름
        _draft(4, "할 말 있을 때만 연락하는 게 편해요. 하루 종일 톡 하면 금방 지치더라구요."),  # 1번과 거의 같음
        _draft(5, "네"),  # 짧은 관용 답은 누수 검사 대상이 아니다
    ]
    kept, report = prepare(
        drafts,
        kind="tagging",
        authors=["opus"] * 5,
        sources=["a.jsonl"] * 5,
        index=index,
        split=None,
        existing_ids=set(),
    )
    assert [k["draftId"] for k in kept] == ["d-001", "d-005"]
    assert [x["draft_id"] for x in report["invalid"]] == ["d-002"]
    assert [x["draft_id"] for x in report["leaked"]] == ["d-003"]
    assert [x["draft_id"] for x in report["duplicate"]] == ["d-004"]
    assert all(k["split"] in ("calibration", "regression") for k in kept)  # holdout 은 따로 만든 묶음에만


def test_prepare_checks_training_data_and_forced_holdout(tmp_path):
    train = tmp_path / "tagging.jsonl"
    _write(train, [{"id": "x", "question": "q", "answer": "퇴근하면 무조건 혼자 러닝 한 시간은 하고 들어가요"}])
    index = default_leak_index([train])
    drafts = [
        _draft(1, "퇴근하면 무조건 혼자 러닝 한 시간은 하고 들어가요!"),
        _draft(2, "주말엔 본가 가서 강아지 산책시켜요"),
    ]
    kept, report = prepare(
        drafts,
        kind="tagging",
        authors=["codex"] * 2,
        sources=["b.jsonl"] * 2,
        index=index,
        split="blind_holdout",
        existing_ids=set(),
    )
    assert len(report["leaked"]) == 1 and report["leaked"][0]["matches"].endswith("tagging.jsonl")
    assert kept[0]["split"] == "blind_holdout"


def test_parse_tag_label_validation():
    assert parse_tag_label({"primary": "", "secondary": "", "off_topic": ""}) == (None, ["라벨 없음"])
    label, errors = parse_tag_label({"primary": "avoidance, mbti", "secondary": "avoidance", "off_topic": "false"})
    assert "없는 차원 mbti" in errors and "primary·secondary 에 같은 차원" in errors
    label, errors = parse_tag_label({"primary": "", "secondary": "", "off_topic": "false"})
    assert errors == [] and label == {"primary": [], "secondary": [], "off_topic": False}


def test_parse_build_label_requires_done_and_valid_levels():
    assert parse_build_label({"avoidance": "high"})[0] is None  # done 없음 → 아직 안 봄
    label, errors = parse_build_label(
        {"avoidance": "높음", "anxiety": "x", "interests": "러닝, 필름카메라", "done": "y"}
    )
    assert label["levels"] == {"avoidance": "high"} and errors == ["anxiety='x' (low·mid·high 또는 빈 칸)"]
    assert label["textual"] == {"interests": ["러닝", "필름카메라"]}
    empty, errors = parse_build_label({"done": "y"})  # 근거가 하나도 없는 세션도 정답
    assert empty == {"levels": {}, "textual": {}} and errors == []


def test_cohen_kappa():
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0


def test_tagging_end_to_end_with_adjudication_and_existing_scorers(tmp_path, capsys):
    from scripts.label_with_teacher import gold_items
    from scripts.score_teacher_gold import load_gold, score_tagging

    drafts = tmp_path / "drafts.jsonl"
    _write(
        drafts,
        [
            _draft(
                1,
                "할 말 있을 때만 연락하는 게 편해요. 하루 종일 톡 하면 금방 지쳐요",
                intent={"target_dimension": "contact_rhythm"},
            ),
            _draft(2, "답장 늦으면 내가 뭘 잘못했나 대화창을 몇 번이고 다시 열어 봐요"),
            _draft(3, "아 오늘 점심 뭐 먹지 진짜 고민되네요"),
        ],
    )
    work, out = tmp_path / "work", tmp_path / "human"
    assert main(["prepare", str(drafts), "--kind", "tagging", "--author", "opus-5.5", "--work-dir", str(work)]) == 0
    sheet = work / "tagging" / "sheet.csv"
    assert "contact_rhythm" not in sheet.read_text(encoding="utf-8-sig")  # 작성 의도는 라벨러에게 숨긴다

    ids = [json.loads(x)["caseId"] for x in (work / "tagging" / "items.jsonl").read_text().splitlines()]
    a, b, adj = tmp_path / "A.csv", tmp_path / "B.csv", tmp_path / "adj.csv"
    _fill(
        sheet,
        a,
        {
            ids[0]: {"primary": "contact_rhythm", "secondary": "", "off_topic": "false"},
            ids[1]: {"primary": "anxiety", "secondary": "contact_rhythm", "off_topic": "false"},
            ids[2]: {"primary": "", "secondary": "", "off_topic": "true"},
        },
    )
    _fill(
        sheet,
        b,
        {
            ids[0]: {"primary": "contact_rhythm", "secondary": "", "off_topic": "false"},
            ids[1]: {"primary": "anxiety", "secondary": "", "off_topic": "false"},  # secondary 만 다름
            ids[2]: {"primary": "", "secondary": "", "off_topic": "false"},  # off_topic 이 다름 → 조정
        },
    )
    args = ["build", "--kind", "tagging", "--labels", str(a), str(b), "--reviewers", "lena,yuta"]
    assert main([*args, "--work-dir", str(work), "--out-dir", str(out)]) == 0
    assert (work / "tagging" / "needs_adjudication.csv").exists()
    rows = [json.loads(x) for x in (out / "persona_onboarding_tagging.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    ambiguous = next(r for r in rows if r["metadata"]["caseId"] == ids[1])
    assert "ambiguous_secondary" in ambiguous["metadata"]["reviewFlags"]
    assert ambiguous["expectedOutput"]["autoMetrics"]["secondary"]["accepted"] == [["contact_rhythm"], []]

    _fill(sheet, adj, {ids[2]: {"primary": "", "secondary": "", "off_topic": "true"}})
    assert main([*args, "--adjudicated", str(adj), "--work-dir", str(work), "--out-dir", str(out)]) == 0
    rows = [json.loads(x) for x in (out / "persona_onboarding_tagging.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and "adjudicated" in rows[-1]["metadata"]["reviewFlags"]
    assert rows[0]["metadata"]["generationMethod"] == "llm-draft:opus-5.5"
    assert rows[0]["metadata"]["draft"]["intent"] == {"target_dimension": "contact_rhythm"}

    # 기존 채점기가 그대로 읽는다
    if importlib.util.find_spec("scripts.eval_tagging"):  # V2 에만 있는 운영 태깅 평가 스크립트
        from scripts.eval_tagging import score

        ok = score({"primary": ["contact_rhythm"], "secondary": [], "off_topic": False}, rows[0]["expectedOutput"])
        assert ok["exact"]
    gold_tags, _ = load_gold(out)
    teacher_row = {
        "id": f"gold:{ids[0]}",
        "samples": [
            {
                "primary": ["contact_rhythm"],
                "secondary": [],
                "off_topic": False,
                "invalidDimensions": [],
                "extraKeys": [],
            }
        ],
        "aggregate": {
            "validSamples": 1,
            "majority": {"primary": ["contact_rhythm"], "off_topic": False},
            "agreement": 1.0,
        },
    }
    assert score_tagging([teacher_row], gold_tags)["exactMatch"] == 1.0
    tags, extracts = gold_items(out, include_holdout=False)
    assert len(tags) == 3 and extracts == []


def test_build_end_to_end(tmp_path):
    from scripts.score_teacher_gold import load_gold, score_extraction

    history = [
        {"role": "assistant", "content": "안녕하세요, 저는 하루예요. 연인과 시간은 어떻게 나누고 싶어요?"},
        {"role": "user", "content": "평일 저녁 하루는 꼭 혼자 영화 보러 가요. 그 시간은 연애해도 지키고 싶어요"},
        {"role": "assistant", "content": "답장이 늦으면 어때요?"},
        {"role": "user", "content": "음 그건 생각해 본 적이 별로 없어요"},
    ]
    drafts = tmp_path / "sessions.jsonl"
    _write(drafts, [{"draft_id": "b-001", "history": history, "plan": {"evidence": {"avoidance": "high"}}}])
    work, out = tmp_path / "work", tmp_path / "human"
    assert main(["prepare", str(drafts), "--kind", "build", "--work-dir", str(work)]) == 0
    sheet = work / "build" / "sheet.csv"
    cid = json.loads((work / "build" / "items.jsonl").read_text().splitlines()[0])["caseId"]
    a, b = tmp_path / "A.csv", tmp_path / "B.csv"
    _fill(sheet, a, {cid: {"avoidance": "high", "interests": "영화", "done": "y"}})
    _fill(sheet, b, {cid: {"avoidance": "높음", "interests": "혼자 영화 보기", "done": "y"}})
    assert (
        main(["build", "--kind", "build", "--labels", str(a), str(b), "--work-dir", str(work), "--out-dir", str(out)])
        == 0
    )

    row = json.loads((out / "persona_build.jsonl").read_text().splitlines()[0])
    metrics = row["expectedOutput"]["autoMetrics"]
    assert metrics["scoreInRange"] == {"avoidance": [65, 100]}
    assert "anxiety" in metrics["mustOmitFromRawModel"] and metrics["textualKept"] == ["interests"]
    assert row["expectedOutput"]["referenceLabels"]["interests"] == ["영화", "혼자 영화 보기"]  # 항목 문구는 합친다
    assert row["input"]["answeredDimensions"] == ["avoidance", "interests"]

    _, gold_builds = load_gold(out)
    teacher_row = {
        "id": f"gold:{cid}",
        "input": row["input"] | {"answered": ["avoidance", "interests"]},
        "output": {"avoidance": 80, "interests": ["영화"]},
    }
    e = score_extraction([teacher_row], gold_builds)
    assert e["inRange"] == "1/1" and e["hallucinationRate"] == 0.0
