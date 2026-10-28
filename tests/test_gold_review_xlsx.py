"""사람 검수표(엑셀) → 라벨 시트 → gold_drafts.py build. 검수표와 같은 머리글 구조의 작은 엑셀을 만들어 확인한다. 네트워크 없음."""

import csv
import json

import pytest

openpyxl = pytest.importorskip("openpyxl")

from scripts.gold_drafts import _dims  # noqa: E402
from scripts.gold_drafts import main as gold_main  # noqa: E402
from scripts.gold_review_xlsx import main  # noqa: E402

TAG = "persona_onboarding_tagging-001"  # 휴일에도 작업실에서 혼자 보내는 시간 (avoidance)
TAG2 = "persona_onboarding_tagging-002"
HOLD = "persona_onboarding_tagging-011"
BUILD = "persona_build-001"  # 허용 차원 avoidance, interests, routine


def _workbook(path, reviewer, *, version="v1", choices="AAAAAAAA", tagging=(), hold=(), build=()):
    """검수표 구조: 기준선택(B4 검수자, E4 버전, P행 F열 선택), 검수 시트는 '문항 ID' 머리글 아래에 행."""
    scored, textual = _dims()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "기준선택"
    ws["A4"], ws["B4"], ws["D4"], ws["E4"] = "검수자 ID", reviewer, "합의 기준 버전", version
    for i, choice in enumerate(choices):
        ws.cell(row=8 + i, column=1, value=f"P{i + 1}")
        ws.cell(row=8 + i, column=6, value=choice)
    dim_heads = [f"한글\n{d}" for d in scored + textual]
    tag_head = ["번호", "문항 ID", "질문", "사용자 답변", "off_topic", "검수 상태", "검수 기준 버전"]
    tag_head += ["차원별 근거 원문", "메모", *dim_heads, "입력확인(필수항목)"]
    build_head = ["번호", "문항 ID", "입력의 허용 차원", "대화 원문", "검수 상태", "검수 기준 버전"]
    build_head += ["차원별 근거 원문", "메모", *dim_heads, "입력확인(필수항목)"]
    for title, head, rows in (
        ("태깅검수", tag_head, tagging),
        ("보류검수", tag_head, hold),
        ("추출검수", build_head, build),
    ):
        sheet = wb.create_sheet(title)
        sheet.append(["제목"])
        for _ in range(5):
            sheet.append([])
        sheet.append(head)
        for n, row in enumerate(rows, 1):
            sheet.append([row.get(h.split("\n")[-1], n if h == "번호" else None) for h in head])
    wb.create_sheet("세부점수")
    wb.create_sheet("차원사전")
    wb.save(path)
    return path


def _tag(cid, off="false", status="완료", version="v1", evidence="", **dims):
    return {"문항 ID": cid, "off_topic": off, "검수 상태": status, "검수 기준 버전": version} | (
        {"차원별 근거 원문": evidence} | dims
    )


def _build(cid, status="완료", version="v1", evidence="", **dims):
    allowed = "avoidance, interests, routine"
    return {"문항 ID": cid, "입력의 허용 차원": allowed, "검수 상태": status, "검수 기준 버전": version} | (
        {"차원별 근거 원문": evidence} | dims
    )


@pytest.fixture
def work(tmp_path):
    work = tmp_path / "work"
    for kind in ("tagging", "build"):
        assert gold_main(["export", "--kind", kind, "--work-dir", str(work)]) == 0
    return work


def _sheet(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def test_converts_finished_rows_and_feeds_gold_build(tmp_path, work):
    evidence = "avoidance: 휴일에도 제 작업실에서 혼자 보내는 시간을 꼭 남겨 두고 싶어요."
    a = _workbook(
        tmp_path / "A.xlsx",
        "reviewer-A",
        tagging=[
            _tag(TAG, evidence=evidence, avoidance="primary", routine="secondary"),
            _tag(TAG2, status="작성중"),
        ],
        hold=[_tag(HOLD, off=True)],  # 엑셀이 논리값으로 저장한 off_topic
        build=[
            _build(BUILD, evidence="avoidance: 지도 보는 오전만큼은 혼자", avoidance="high", interests="오래된 지도")
        ],
    )
    b = _workbook(
        tmp_path / "B.xlsx",
        "reviewer-B",
        tagging=[_tag(TAG, evidence=evidence, avoidance="primary", anxiety="생략")],
        build=[
            _build(
                BUILD, evidence="avoidance: 혼자 있고 싶어요", avoidance="high", interests="지도 수집\n주말 아침 산책"
            )
        ],
    )
    assert main([str(a), str(b), "--work-dir", str(work)]) == 0

    labels = work / "labels"
    tag_a = _sheet(labels / "tagging_reviewer-A.csv")
    assert len(tag_a) == 63  # export 목록 전체, 완료한 행만 채운다
    assert tag_a[TAG]["primary"] == "avoidance"
    assert tag_a[TAG]["secondary"] == "routine" and tag_a[TAG]["off_topic"] == "false"
    assert "근거: avoidance" in tag_a[TAG]["note"]
    assert tag_a[TAG2]["off_topic"] == "" and tag_a[HOLD]["off_topic"] == ""  # 작성중·보류 시트(기본 제외)는 빈 칸
    build_b = _sheet(labels / "build_reviewer-B.csv")
    assert build_b[BUILD]["avoidance"] == "high" and build_b[BUILD]["done"] == "y"
    assert build_b[BUILD]["interests"] == "지도 수집;주말 아침 산책"
    report = json.loads((labels / "convert_report.json").read_text())
    assert report["reviewers"]["reviewer-A"]["holdSheetSkipped"] == 1
    assert report["reviewers"]["reviewer-A"]["unfinished"] == [TAG2]

    out = tmp_path / "human"
    args = ["build", "--work-dir", str(work), "--out-dir", str(out), "--reviewers", "reviewer-A,reviewer-B"]
    tag_files = [str(labels / "tagging_reviewer-A.csv"), str(labels / "tagging_reviewer-B.csv")]
    assert gold_main([*args, "--kind", "tagging", "--labels", *tag_files]) == 0
    row = json.loads((out / "persona_onboarding_tagging.jsonl").read_text().splitlines()[0])
    assert row["metadata"]["caseId"] == TAG
    assert row["expectedOutput"]["autoMetrics"]["secondary"]["accepted"] == [["routine"], []]  # secondary 만 갈림
    build_files = [str(labels / "build_reviewer-A.csv"), str(labels / "build_reviewer-B.csv")]
    assert gold_main([*args, "--kind", "build", "--labels", *build_files]) == 0
    row = json.loads((out / "persona_build.jsonl").read_text().splitlines()[0])
    assert row["expectedOutput"]["autoMetrics"]["scoreInRange"] == {"avoidance": [65, 100]}


def test_include_hold_puts_hold_rows_in_tagging_sheet(tmp_path, work):
    a = _workbook(tmp_path / "A.xlsx", "reviewer-A", hold=[_tag(HOLD, off="false")])
    assert main([str(a), "--include-hold", "--work-dir", str(work)]) == 0
    assert _sheet(work / "labels" / "tagging_reviewer-A.csv")[HOLD]["off_topic"] == "false"


def test_row_errors_are_reported_and_left_blank(tmp_path, work, capsys):
    a = _workbook(
        tmp_path / "A.xlsx",
        "reviewer-A",
        tagging=[
            _tag(TAG, avoidance="primary"),  # 근거 없음
            _tag(TAG2, off="true", evidence="x", avoidance="primary"),  # 무관인데 차원
        ],
        build=[_build(BUILD, version="v0", evidence="anxiety: ...", anxiety="low")],  # 버전 다름 + 허용 외 차원
    )
    assert main([str(a), "--work-dir", str(work)]) == 1
    report = json.loads((work / "labels" / "convert_report.json").read_text())["reviewers"]["reviewer-A"]
    errors = {e["caseId"]: " ".join(e["errors"]) for e in report["errors"]}
    assert "근거 원문 없음" in errors[TAG] and "off_topic=true" in errors[TAG2]
    assert "v0" in errors[BUILD] and "허용 외 차원 anxiety" in errors[BUILD]
    assert _sheet(work / "labels" / "tagging_reviewer-A.csv")[TAG]["primary"] == ""
    assert "오류" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("kwargs_b", "message"),
    [
        ({"choices": "AAAABAAA"}, "P5=B"),
        ({"choices": "AAAAAAA"}, "기준 미선택: P8"),
        ({"version": "v2"}, "기준 선택·합의 버전이"),
        ({"reviewer": "reviewer-A"}, "검수자 ID 가 겹침"),
    ],
)
def test_criteria_must_be_complete_and_shared(tmp_path, work, capsys, kwargs_b, message):
    a = _workbook(tmp_path / "A.xlsx", "reviewer-A")
    reviewer = kwargs_b.pop("reviewer", "reviewer-B")
    b = _workbook(tmp_path / "B.xlsx", reviewer, **kwargs_b)
    assert main([str(a), str(b), "--work-dir", str(work)]) == 2
    assert message in capsys.readouterr().err
    assert not (work / "labels").exists()


def test_requires_export_first(tmp_path, capsys):
    a = _workbook(tmp_path / "A.xlsx", "reviewer-A")
    assert main([str(a), "--work-dir", str(tmp_path / "empty")]) == 2
    assert "gold_drafts.py export" in capsys.readouterr().err
