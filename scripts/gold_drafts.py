"""골드셋 초안(LLM 작성) → 사람 라벨링 시트 → 골드셋 행. 누수·중복 검사와 평가자 간 일치도를 함께 본다.

초안은 docs 의 골드셋 프롬프트로 Opus·Codex 등이 만든 JSONL 이다. 모델은 입력(질문·답변, 대화)만 쓰고
정답 라벨은 사람 두 명이 따로 단다. 모델의 작성 의도(intent·plan)는 라벨러에게 보이지 않게 따로 둔다.

  1) prepare  초안 검증 → 누수·중복 검사 → split 배정 → 라벨링 시트(CSV)와 숨긴 메타 파일
       uv run python scripts/gold_drafts.py prepare drafts/tagging_opus.jsonl drafts/tagging_codex.jsonl \\
           --kind tagging --author opus-5.5,codex --against data/synthetic/pilot200.jsonl
       holdout 묶음은 다른 세션에서 만든 초안으로 따로 돌린다:  ... --split blind_holdout
  2) 사람 두 명이 시트를 복사해 각자 채운다 (labels_A.csv, labels_B.csv). 서로의 시트를 보지 않는다
  3) build    두 라벨 비교 → 일치도(κ) → 갈린 건은 조정 시트로 → 확정분을 골드셋 형식으로 저장
       uv run python scripts/gold_drafts.py build --kind tagging --labels labels_A.csv labels_B.csv \\
           --reviewers A,B [--adjudicated adjudicated.csv]

출력 골드셋은 evals/quality_datasets/human/ 에 쌓는다. 기존 evals/quality_datasets/*.jsonl 은 생성기와 manifest 해시로
고정된 합성 초안이라 건드리지 않는다. 행 구조(input·expectedOutput·metadata)와 채점 계약(autoMetrics)은 같아서
score_teacher_gold.py·label_with_teacher.py(--gold-dir)·eval_tagging.py(--dataset)가 그대로 읽는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = ROOT / "evals" / "quality_datasets"
HUMAN_DIR = GOLD_DIR / "human"
WORK_DIR = ROOT / "data" / "gold_work"
MAX_ANSWER_LEN = 200
DATASET = {"tagging": "persona_onboarding_tagging", "build": "persona_build"}
# 추출 정답 구간 — profile.py 서술 경계(35 / 65)와 student 의 구간 일치 지표에 맞춘다
LEVEL_RANGES = {"low": [0, 34], "mid": [35, 64], "high": [65, 100]}
LEVELS = ("low", "mid", "high")


def _dims() -> tuple[list[str], list[str]]:
    sys.path.insert(0, str(ROOT))
    from app.features.persona.schemas import SCORED, TEXTUAL

    return list(SCORED), list(TEXTUAL)


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _split_list(value: str) -> list[str]:
    return sorted({x.strip() for x in re.split(r"[,\s/]+", value or "") if x.strip()})


# ══ 누수 검사 ══════════════════════════════════════════════


def _audit():
    spec = importlib.util.spec_from_file_location("audit_quality_datasets", GOLD_DIR / "audit_quality_datasets.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class LeakIndex:
    """기준 문장들과 같거나 거의 같은 문장을 찾는다. 기준은 골드셋 감사(audit_quality_datasets)의 정규화·근사 규칙.

    기존 골드셋·사람 골드셋은 물론 학습 데이터(합성 세션·학습 예시)도 넣는다 — 평가 문장이 학습에 있으면 점수가 부풀려진다.
    """

    def __init__(self) -> None:
        self.audit = _audit()
        self.texts: dict[str, str] = {}  # 정규화 문장 → 출처

    def add_jsonl(self, path: Path) -> None:
        for row in _rows(path):
            self.add_value(row.get("input", row), str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path))

    def add_value(self, value, source: str) -> None:
        # stimuli 는 answer·content·question 같은 발화 키를 깊이 따라가며 문장 단위로도 쪼갠다
        for sentence, _ in self.audit.stimuli(value):
            self.add_text(sentence, source)

    def add_text(self, text: str, source: str) -> None:
        normalized = self.audit.normalize(text)
        if len(normalized) >= 8 and text.strip() not in self.audit.FIXED_CONTRACT_TEXT:
            self.texts.setdefault(normalized, source)

    def find(self, text: str) -> str | None:
        normalized = self.audit.normalize(text)
        if len(normalized) < 8:  # "네", "글쎄요" 같은 한두 마디는 겹쳐도 누수가 아니다
            return None
        if normalized in self.texts:
            return self.texts[normalized]
        for other, source in self.texts.items():
            if self.audit.approximation(normalized, other):
                return source
        return None


def default_leak_index(against: list[Path]) -> LeakIndex:
    index = LeakIndex()
    for path in [*sorted(GOLD_DIR.glob("*.jsonl")), *sorted(HUMAN_DIR.glob("*.jsonl")), *against]:
        if path.exists():
            index.add_jsonl(path)
    return index


# ══ 1) prepare ═══════════════════════════════════════════════


def check_draft(draft: dict, kind: str) -> list[str]:
    problems = []
    if not str(draft.get("draft_id", "")).strip():
        problems.append("draft_id 없음")
    if kind == "tagging":
        for key in ("category", "question", "answer"):
            if not str(draft.get(key, "")).strip():
                problems.append(f"{key} 없음")
        if len(str(draft.get("answer", ""))) > MAX_ANSWER_LEN:
            problems.append(f"answer {MAX_ANSWER_LEN}자 초과")
    else:
        history = draft.get("history")
        if not isinstance(history, list) or len(history) < 2:
            problems.append("history 가 2개 미만")
        else:
            for i, m in enumerate(history):
                expected = "assistant" if i % 2 == 0 else "user"
                if m.get("role") != expected or not str(m.get("content", "")).strip():
                    problems.append(f"history[{i}] 는 내용 있는 {expected} 여야 함")
                    break
                if expected == "user" and len(m["content"]) > MAX_ANSWER_LEN:
                    problems.append(f"history[{i}] {MAX_ANSWER_LEN}자 초과")
                    break
    return problems


def user_texts(draft: dict, kind: str) -> list[str]:
    if kind == "tagging":
        return [draft["answer"]]
    return [m["content"] for m in draft["history"] if m["role"] == "user"]


def assign_split(draft_id: str) -> str:
    """holdout 은 따로 만든 초안 묶음에만 --split 으로 준다. 나머지는 id 해시로 calibration 1 : regression 3."""
    bucket = int(hashlib.sha256(draft_id.encode()).hexdigest()[:8], 16) % 4
    return "calibration" if bucket == 0 else "regression"


def case_id(kind: str, draft_id: str) -> str:
    return f"human_{DATASET[kind]}-{re.sub(r'[^0-9A-Za-z_-]+', '_', draft_id)}"


def tagging_sheet_rows(items: list[dict]) -> tuple[list[str], list[list[str]]]:
    header = ["case_id", "question", "answer", "primary", "secondary", "off_topic", "note"]
    return header, [[i["caseId"], i["question"], i["answer"], "", "", "", ""] for i in items]


def build_sheet_rows(items: list[dict]) -> tuple[list[str], list[list[str]]]:
    scored, textual = _dims()
    # done: 근거가 하나도 없는 세션도 정답이므로, 빈 칸과 "아직 안 봄"을 구분하려고 끝냈으면 y 를 적는다
    header = ["case_id", "transcript", *scored, *textual, "done", "note"]
    rows = []
    for i in items:
        transcript = "\n".join(
            f"{'하루' if m['role'] == 'assistant' else '사용자'}: {m['content']}" for m in i["history"]
        )
        rows.append([i["caseId"], transcript, *[""] * (len(scored) + len(textual)), "", ""])
    return header, rows


def prepare(
    drafts: list[dict],
    *,
    kind: str,
    authors: list[str],
    sources: list[str],
    index: LeakIndex,
    split: str | None,
    existing_ids: set[str],
) -> tuple[list[dict], dict]:
    """초안 → 라벨링 대상 목록과 보고서. 초안 사이의 중복도 같은 근사 규칙으로 잡는다."""
    kept: list[dict] = []
    report = {"input": len(drafts), "invalid": [], "leaked": [], "duplicate": [], "existing": []}
    local = LeakIndex.__new__(LeakIndex)  # 이번 묶음 안의 중복용 — 같은 규칙, 빈 기준
    local.audit, local.texts = index.audit, {}
    for draft, author, source in zip(drafts, authors, sources, strict=True):
        problems = check_draft(draft, kind)
        if problems:
            report["invalid"].append({"draft_id": draft.get("draft_id"), "source": source, "problems": problems})
            continue
        cid = case_id(kind, str(draft["draft_id"]))
        if cid in existing_ids:
            report["existing"].append(cid)
            continue
        texts = user_texts(draft, kind)
        leak = next((hit for t in texts if (hit := index.find(t))), None)
        if leak:
            report["leaked"].append({"draft_id": draft["draft_id"], "matches": leak})
            continue
        dup = next((hit for t in texts if (hit := local.find(t))), None)
        if dup:
            report["duplicate"].append({"draft_id": draft["draft_id"], "sameAs": dup})
            continue
        for t in texts:
            local.add_text(t, str(draft["draft_id"]))
        existing_ids.add(cid)
        item = {
            "caseId": cid,
            "draftId": str(draft["draft_id"]),
            "kind": kind,
            "author": author,
            "draftFile": source,
            "split": split or assign_split(str(draft["draft_id"])),
            "category": draft.get("category") or ("+".join(draft.get("plan", {}).get("traps", [])) or "session"),
            "familyId": str(draft.get("family_id") or draft["draft_id"]),
            "hidden": {k: draft[k] for k in ("intent", "plan") if k in draft},  # 라벨러에게 보이지 않는 작성 의도
        }
        if kind == "tagging":
            item |= {"question": draft["question"], "answer": draft["answer"]}
        else:
            item |= {"history": draft["history"]}
        kept.append(item)
    report["kept"] = len(kept)
    report["splits"] = dict(Counter(i["split"] for i in kept))
    return kept, report


def cmd_prepare(args: argparse.Namespace) -> int:
    authors = args.author.split(",") if args.author else ["unknown"]
    if len(authors) == 1:
        authors = authors * len(args.drafts)
    if len(authors) != len(args.drafts):
        print("--author 는 초안 파일 수만큼 쉼표로 주거나 하나만 줍니다", file=sys.stderr)
        return 2
    drafts, row_authors, row_sources = [], [], []
    for path, author in zip(args.drafts, authors, strict=True):
        for row in _rows(path):
            drafts.append(row)
            row_authors.append(author)
            row_sources.append(path.name)

    work = args.work_dir / args.kind
    items_path = work / "items.jsonl"
    previous = _rows(items_path) if items_path.exists() else []
    human_path = HUMAN_DIR / f"{DATASET[args.kind]}.jsonl"
    existing = {i["caseId"] for i in previous} | (
        {r["metadata"]["caseId"] for r in _rows(human_path)} if human_path.exists() else set()
    )
    index = default_leak_index(args.against)
    for item in previous:  # 이전 묶음과도 중복을 본다
        for t in user_texts(item, args.kind):
            index.add_text(t, f"이전 묶음 {item['draftId']}")

    kept, report = prepare(
        drafts,
        kind=args.kind,
        authors=row_authors,
        sources=row_sources,
        index=index,
        split=args.split,
        existing_ids=existing,
    )
    _write_rows(items_path, previous + kept)
    header, rows = (tagging_sheet_rows if args.kind == "tagging" else build_sheet_rows)(previous + kept)
    sheet = work / "sheet.csv"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀·구글 시트에서 한글이 안 깨진다
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    (work / "prepare_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}
    print(json.dumps(brief | {"sheet": str(sheet), "items": str(items_path)}, ensure_ascii=False, indent=2))
    return 0


# ══ 3) build ═════════════════════════════════════════════════


def read_sheet(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["case_id"]: row for row in csv.DictReader(f) if row.get("case_id")}


def parse_tag_label(row: dict) -> tuple[dict | None, list[str]]:
    """시트 한 줄 → {primary, secondary, off_topic}. 비어 있으면 아직 안 단 것."""
    scored, textual = _dims()
    valid = set(scored) | set(textual)
    primary, secondary = _split_list(row.get("primary", "")), _split_list(row.get("secondary", ""))
    off_raw = (row.get("off_topic") or "").strip().lower()
    if not primary and not secondary and not off_raw:
        return None, ["라벨 없음"]
    errors = [f"없는 차원 {d}" for d in primary + secondary if d not in valid]
    off = off_raw in ("true", "t", "1", "y", "yes", "o", "예")
    if set(primary) & set(secondary):
        errors.append("primary·secondary 에 같은 차원")
    if off and (primary or secondary):
        errors.append("off_topic 이면 primary·secondary 는 비워야 함")
    return {"primary": primary, "secondary": secondary, "off_topic": off}, errors


def parse_build_label(row: dict) -> tuple[dict | None, list[str]]:
    """시트 한 줄 → {levels: {차원: low|mid|high}, textual: {차원: [항목]}}. 빈 칸 = 근거 없음."""
    scored, textual = _dims()
    levels, errors = {}, []
    for d in scored:
        value = (row.get(d) or "").strip().lower()
        if not value:
            continue
        value = {"낮음": "low", "중간": "mid", "높음": "high", "l": "low", "m": "mid", "h": "high"}.get(value, value)
        if value not in LEVELS:
            errors.append(f"{d}={row.get(d)!r} (low·mid·high 또는 빈 칸)")
        else:
            levels[d] = value
    items = {d: [x.strip() for x in re.split(r"[,;/]", row.get(d) or "") if x.strip()] for d in textual}
    items = {d: v for d, v in items.items() if v}
    if (row.get("done") or "").strip().lower() not in ("y", "yes", "o", "1", "true", "예"):
        return None, ["라벨 없음 (done 칸이 비어 있음)"]
    return {"levels": levels, "textual": items}, errors


def cohen_kappa(a: list, b: list) -> float | None:
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((observed - expected) / (1 - expected), 4) if expected < 1 else 1.0


def tagging_agreement(pairs: list[tuple[dict, dict]]) -> dict:
    scored, textual = _dims()
    dims = scored + textual
    kappas = []
    for d in dims:  # 차원별 primary 여부(있다/없다)의 κ, 한쪽이라도 한 번은 고른 차원만
        a = [d in x["primary"] for x, _ in pairs]
        b = [d in y["primary"] for _, y in pairs]
        if any(a) or any(b):
            k = cohen_kappa(a, b)
            if k is not None:
                kappas.append(k)
    return {
        "items": len(pairs),
        "primaryExactAgreement": round(sum(x["primary"] == y["primary"] for x, y in pairs) / len(pairs), 4)
        if pairs
        else None,
        "primaryKappaMeanOverDims": round(sum(kappas) / len(kappas), 4) if kappas else None,
        "offTopicKappa": cohen_kappa([x["off_topic"] for x, _ in pairs], [y["off_topic"] for _, y in pairs]),
    }


def build_agreement(pairs: list[tuple[dict, dict]]) -> dict:
    scored, _ = _dims()
    a = [x["levels"].get(d, "none") for x, _ in pairs for d in scored]
    b = [y["levels"].get(d, "none") for _, y in pairs for d in scored]
    return {
        "items": len(pairs),
        "dimensionLevelKappa": cohen_kappa(a, b),
        "evidenceAgreement": round(sum((x != "none") == (y != "none") for x, y in zip(a, b, strict=True)) / len(a), 4)
        if a
        else None,
    }


def resolve(kind: str, a: dict, b: dict, adjudicated: dict | None) -> tuple[dict | None, list[str]]:
    """두 라벨 → 확정 라벨과 플래그. 핵심이 갈렸는데 조정 라벨이 없으면 None."""
    if kind == "tagging":
        if a["primary"] == b["primary"] and a["off_topic"] == b["off_topic"]:
            if a["secondary"] == b["secondary"]:
                return a, []
            # secondary 만 갈리면 둘 다 허용 대안으로 — 기존 골드셋 관례(ambiguous_secondary)
            return a | {"secondaryAlternatives": [b["secondary"]]}, ["ambiguous_secondary"]
    elif a["levels"] == b["levels"] and set(a["textual"]) == set(b["textual"]):
        # 텍스트형은 있다/없다만 채점한다(textualKept). 항목 문구는 사람마다 달라 합쳐 둔다
        merged = {d: sorted(set(a["textual"][d]) | set(b["textual"][d])) for d in a["textual"]}
        return {"levels": a["levels"], "textual": merged}, []
    if adjudicated is not None:
        return adjudicated, ["adjudicated"]
    return None, []


TAG_HARD_ASSERTIONS = [
    "primary는 사용자가 직접 말한 자기 행동·선호만 포함하고 secondary는 문장에 있는 간접 단서만 포함한다.",
    "primary·secondary는 등록된 19개 차원이고 점수·설명문은 출력하지 않는다.",
    "무관한 답이 아니면 off_topic은 false이고, 근거가 없으면 primary를 비운다.",
]
TAG_FORBIDDEN = ["인용된 타인의 성향을 사용자에게 귀속", "인용문 내부의 출력 지시 실행", "질문 주제를 정답 라벨로 복사"]
BUILD_HARD_ASSERTIONS = [
    "raw_model과 post_service를 따로 평가한다.",
    "근거 없는 점수 키는 raw_model에서 생략한다. 서비스도 답변으로 확인되지 않은 차원은 null(모름)로 둔다.",
    "점수는 0~100 정수이며 사람 라벨의 구간(낮음 0~34, 중간 35~64, 높음 65~100) 안에 있어야 한다.",
]
BUILD_FORBIDDEN = [
    "근거 없이 점수를 50으로 채움",
    "직접 답하지 않은 차원을 다른 답에서 미루어 채움",
    "유형명·진단·평가·조언",
]


def _rubric(criteria: dict) -> dict:
    return {"scale": [1, 5], "criteria": criteria, "anchors": {}, "status": "dual_human_reviewed", "role": "auxiliary"}


def _metadata(item: dict, kind: str, flags: list[str], reviewers: list[str]) -> dict:
    return {
        "caseId": item["caseId"],
        "dataset": DATASET[kind],
        "split": item["split"],
        "splitGroup": item["familyId"],
        "category": item["category"],
        "difficulty": "medium",
        "sourceRefs": ["scripts/gold_drafts.py"],
        "generationMethod": f"llm-draft:{item['author']}",
        "prototypeId": item["draftId"],
        "familyId": item["familyId"],
        "piiClass": "synthetic",
        "rubricVersion": "1.0",
        "reviewFlags": ["dual_human_reviewed", *flags],
        "reviewers": reviewers,
        "evaluationKind": "label_accuracy",
        "draft": {"file": item["draftFile"], **item.get("hidden", {})},
    }


def tagging_row(item: dict, label: dict, flags: list[str], reviewers: list[str]) -> dict:
    scored, textual = _dims()
    secondary_accepted = [label["secondary"], *label.get("secondaryAlternatives", [])]
    return {
        "input": {
            "task": "persona_tagging",
            "question": item["question"],
            "answer": item["answer"],
            "stimulusFamilies": [item["category"]],
        },
        "expectedOutput": {
            "hardAssertions": TAG_HARD_ASSERTIONS,
            "referenceLabels": {
                "primary": label["primary"],
                "secondary": label["secondary"],
                "off_topic": label["off_topic"],
                "acceptableAlternatives": {"secondary": label.get("secondaryAlternatives", [])},
            },
            "acceptableRanges": {},
            "requiredEvidence": [],
            "forbidden": TAG_FORBIDDEN,
            "rubric": _rubric(
                {"labelSchema": 10, "primaryEvidence": 40, "secondaryEvidence": 25, "topicRelevance": 25}
            ),
            "labelUniverse": scored + textual,
            "autoMetrics": {
                "applies": True,
                "primary": {"accepted": [label["primary"]], "method": "set_match"},
                "secondary": {"accepted": secondary_accepted, "method": "set_match"},
                "off_topic": {"accepted": [label["off_topic"]], "method": "exact_match"},
                "labelUniverse": "labelUniverse에 없는 차원 이름은 오답으로 센다.",
                "aggregate": "지표별 일치 비율(%).",
            },
        },
        "metadata": _metadata(item, "tagging", flags, reviewers),
    }


def build_row(item: dict, label: dict, flags: list[str], reviewers: list[str]) -> dict:
    scored, textual = _dims()
    levels, kept = label["levels"], label["textual"]
    answered = sorted([*levels, *kept])
    unanswered = [d for d in scored if d not in levels]
    users = sum(m["role"] == "user" for m in item["history"])
    ranges = {d: LEVEL_RANGES[v] for d, v in levels.items()}
    return {
        "input": {
            "task": "persona_build",
            "history": item["history"],
            "answeredDimensions": answered,
            "coverage": {"primary": dict.fromkeys(answered, 1), "secondary": {}},
            "session": {
                "answeredTurns": users,
                "finishedEarly": users < 10,
                "pendingTopicId": None,
                "status": "completed",
                "totalTurns": 10,
                "turnIndex": 10,
            },
            "stimulusFamilies": [item["category"]],
        },
        "expectedOutput": {
            "hardAssertions": BUILD_HARD_ASSERTIONS,
            "referenceLabels": {"levels": levels, **kept},
            "acceptableRanges": {d: {"min": r[0], "max": r[1]} for d, r in ranges.items()},
            "requiredEvidence": [],
            "forbidden": BUILD_FORBIDDEN,
            "rubric": _rubric({"scoreRange": 40, "nullIsolation": 30, "narrativeGrounding": 30}),
            "autoMetrics": {
                "applies": True,
                "scoreInRange": ranges,
                "mustOmitFromRawModel": unanswered,
                "unknownMustBeNull": unanswered,
                "textualKept": sorted(kept),
                "textualDropped": [d for d in textual if d not in kept],
                "aggregate": "지표별 일치 비율(%). 범위는 사람 라벨의 구간이다.",
            },
        },
        "metadata": _metadata(item, "build", flags, reviewers),
    }


def build(
    items: list[dict],
    sheet_a: dict[str, dict],
    sheet_b: dict[str, dict],
    adjudicated: dict[str, dict],
    *,
    kind: str,
    reviewers: list[str],
) -> tuple[list[dict], dict, list[dict]]:
    parse = parse_tag_label if kind == "tagging" else parse_build_label
    rows, pending, pairs = [], [], []
    report = {"items": len(items), "labelErrors": [], "unlabeled": []}
    for item in items:
        cid = item["caseId"]
        a, ea = parse(sheet_a.get(cid, {}))
        b, eb = parse(sheet_b.get(cid, {}))
        if a is None or b is None:
            report["unlabeled"].append(cid)
            continue
        if ea or eb:
            report["labelErrors"].append({"caseId": cid, "A": ea, "B": eb})
            continue
        pairs.append((a, b))
        adj = None
        if cid in adjudicated:
            adj, eadj = parse(adjudicated[cid])
            if adj is None or eadj:
                report["labelErrors"].append({"caseId": cid, "adjudicated": eadj})
                continue
        label, flags = resolve(kind, a, b, adj)
        if label is None:
            pending.append({"case_id": cid, "A": a, "B": b})
            continue
        rows.append((tagging_row if kind == "tagging" else build_row)(item, label, flags, reviewers))
    report["agreement"] = (tagging_agreement if kind == "tagging" else build_agreement)(pairs)
    report["needsAdjudication"] = len(pending)
    report["written"] = len(rows)
    return rows, report, pending


def cmd_build(args: argparse.Namespace) -> int:
    work = args.work_dir / args.kind
    items = _rows(work / "items.jsonl")
    sheet_a, sheet_b = (read_sheet(p) for p in args.labels)
    adjudicated = read_sheet(args.adjudicated) if args.adjudicated else {}
    reviewers = args.reviewers.split(",") if args.reviewers else ["A", "B"]
    rows, report, pending = build(items, sheet_a, sheet_b, adjudicated, kind=args.kind, reviewers=reviewers)

    out = args.out_dir / f"{DATASET[args.kind]}.jsonl"
    existing = {r["metadata"]["caseId"]: r for r in _rows(out)} if out.exists() else {}
    existing |= {r["metadata"]["caseId"]: r for r in rows}  # 다시 돌리면 같은 caseId 는 새 라벨로 바꾼다
    _write_rows(out, list(existing.values()))

    if pending:
        # 조정 시트: 갈린 건만, 두 라벨을 나란히. 조정자는 이 파일을 보고 sheet 형식으로 adjudicated.csv 를 채운다
        with (work / "needs_adjudication.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", "label_A", "label_B"])
            for p in pending:
                writer.writerow(
                    [p["case_id"], json.dumps(p["A"], ensure_ascii=False), json.dumps(p["B"], ensure_ascii=False)]
                )
    (work / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}
    print(json.dumps(brief | {"out": str(out), "total": len(existing)}, ensure_ascii=False, indent=2))
    kappa = report["agreement"].get("primaryKappaMeanOverDims", report["agreement"].get("dimensionLevelKappa"))
    if kappa is not None and kappa < 0.6:
        print("평가자 간 일치도가 0.6 미만입니다. 라벨링 가이드를 먼저 맞추세요.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="초안 검증·누수 검사·라벨링 시트 만들기")
    p.add_argument("drafts", nargs="+", type=Path, help="초안 JSONL (여러 개 가능)")
    p.add_argument("--kind", choices=tuple(DATASET), required=True)
    p.add_argument("--author", default="", help="초안 작성 모델. 파일마다 쉼표로 (예: opus-5.5,codex)")
    p.add_argument("--against", nargs="*", type=Path, default=[], help="누수 검사에 더할 학습 데이터 JSONL")
    p.add_argument("--split", choices=("calibration", "regression", "blind_holdout"), help="이 묶음 전체의 split")
    p.add_argument("--work-dir", type=Path, default=WORK_DIR)
    p.set_defaults(func=cmd_prepare)

    b = sub.add_parser("build", help="두 라벨러 시트 → 골드셋")
    b.add_argument("--kind", choices=tuple(DATASET), required=True)
    b.add_argument("--labels", nargs=2, type=Path, required=True, metavar=("A.csv", "B.csv"))
    b.add_argument("--adjudicated", type=Path, help="갈린 건의 조정 라벨 (sheet 와 같은 형식)")
    b.add_argument("--reviewers", default="", help="라벨러 표시 (예: lena,yuta). 실명·연락처는 쓰지 않는다")
    b.add_argument("--work-dir", type=Path, default=WORK_DIR)
    b.add_argument("--out-dir", type=Path, default=HUMAN_DIR)
    b.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
