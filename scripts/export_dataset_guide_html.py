# ruff: noqa: UP031
"""품질 데이터셋 설명 문서(단일 self-contained HTML)를 만든다.

숫자·목록은 실행 시점에 evals/quality_datasets 아래 파일에서 읽는다.
표준 라이브러리만 쓰고 네트워크를 호출하지 않는다. 이 스크립트는 읽기 전용으로
데이터를 다루며 출력 HTML 한 파일만 쓴다.

    python3 scripts/export_dataset_guide_html.py
    python3 scripts/export_dataset_guide_html.py --output /tmp/guide.html
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "evals" / "quality_datasets"
REVIEW_DIR = DATA_DIR / "reviews"
PLAN_PATH = ROOT / "docs" / "v1docs" / "langfuse-quality-dataset-plan.md"
REVIEW_HTML = ROOT / "evals" / "review" / "quality_dataset_review.html"
DEFAULT_OUTPUT = ROOT / "evals" / "review" / "quality_dataset_guide.html"

FAIL_TEXT = "파싱 실패"
SPLIT_ORDER = ["calibration", "regression", "blind_holdout"]
SPLIT_KO = {
    "calibration": "calibration (보정)",
    "regression": "regression (회귀)",
    "blind_holdout": "blind_holdout (최종 확인)",
}
# 검토 통과 기준(사용자 지침에 명시된 값)
PASS_SCORE = 90

# 표시용 이름 사전. 숫자는 없고 파일에 없는 데이터셋은 파일명을 그대로 쓴다.
DS_INFO = {
    "persona_onboarding_conversation": ("온보딩 발화", "페르소나 온보딩: 다음 질문 한 턴 생성"),
    "persona_onboarding_tagging": ("온보딩 태깅", "페르소나 온보딩: 답변에서 차원 라벨 추출"),
    "persona_build": ("페르소나 build", "페르소나 build: 온보딩 대화에서 점수·서술 생성"),
    "practice_reply": ("연습대화", "연습대화: 저장된 페르소나로 다음 답변 생성"),
    "simulation_run": ("시뮬레이션 대본", "시뮬레이션: 두 페르소나의 대본과 리포트"),
    "simulation_report_preview": ("리포트 preview", "시뮬레이션: 고정 대화록에 대한 리포트 서술"),
}
COMMON_EXPECTED = {
    "hardAssertions",
    "forbidden",
    "rubric",
    "referenceLabels",
    "acceptableRanges",
    "requiredEvidence",
    "runtime",
}

esc = html.escape


# --------------------------------------------------------------------------
# 읽기 도우미
# --------------------------------------------------------------------------
def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_json(path: Path) -> dict:
    try:
        data = json.loads(read_text(path))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def ver_key(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", ver))


def short_ver(ver: str) -> str:
    parts = ver.split(".")
    return "v" + ".".join(parts[:2]) if len(parts) >= 2 else "v" + ver


def sha256_of(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# 최소 마크다운 변환 (문단, 목록, 표만)
# --------------------------------------------------------------------------
def md_inline(text: str) -> str:
    out = esc(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def md_blocks(text: str, limit: int = 0) -> str:
    """limit가 0이 아니면 목록 항목·표 행을 그 수까지만 보여준다."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            i += 1
            continue
        if stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            out.append(table_from_md(block))
            continue
        if re.match(r"^(?:[-*]|\d+\.)\s+", stripped):
            items = []
            while i < len(lines):
                cur = lines[i].strip()
                m = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", cur)
                if m:
                    items.append(m.group(1))
                    i += 1
                elif cur and lines[i].startswith("  ") and items:
                    items[-1] += " " + cur
                    i += 1
                else:
                    break
            shown = items[:limit] if limit else items
            lis = "".join("<li>" + md_inline(x) + "</li>" for x in shown)
            more = ""
            if limit and len(items) > limit:
                more = '<li class="muted">… 외 %d개</li>' % (len(items) - limit)
            out.append("<ul>" + lis + more + "</ul>")
            continue
        if stripped.startswith("#"):
            out.append("<h4>" + md_inline(stripped.lstrip("# ").strip()) + "</h4>")
            i += 1
            continue
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(\||#|```|(?:[-*]|\d+\.)\s)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + md_inline(" ".join(para)) + "</p>")
    return "\n".join(out)


def table_from_md(block: list[str]) -> str:
    rows = []
    for ln in block:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    th = "".join("<th>" + md_inline(c) + "</th>" for c in head)
    tr = "".join("<tr>" + "".join("<td>" + md_inline(c) + "</td>" for c in r) + "</tr>" for r in body)
    return '<div class="scroll"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (th, tr)


def md_sections(text: str, level: int) -> dict[str, str]:
    """지정 레벨('##' 등) 제목별 본문을 순서대로 돌려준다."""
    marker = "#" * level + " "
    out: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith(marker) and not line.startswith(marker + "#"):
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur = line[len(marker) :].strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def find_section(text: str, level: int, keyword: str) -> str:
    for title, body in md_sections(text, level).items():
        if keyword in title:
            return body
    return ""


# --------------------------------------------------------------------------
# HTML 조각
# --------------------------------------------------------------------------
def scroll_table(headers: list[str], rows: list[list[str]], num_cols: tuple[int, ...] = ()) -> str:
    th = "".join('<th class="%s">%s</th>' % ("num" if i in num_cols else "", h) for i, h in enumerate(headers))
    body = ""
    for r in rows:
        body += (
            "<tr>"
            + "".join('<td class="%s">%s</td>' % ("num" if i in num_cols else "", c) for i, c in enumerate(r))
            + "</tr>"
        )
    return '<div class="scroll"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (th, body)


def bar_rows(items: list[tuple[str, int]], total: int, cls: str = "") -> str:
    if not items:
        return '<p class="muted">데이터 없음</p>'
    peak = max(n for _, n in items) or 1
    out = ['<div class="bars %s">' % cls]
    for label, n in items:
        pct = 100.0 * n / peak
        share = (100.0 * n / total) if total else 0.0
        out.append(
            '<div class="bar-row"><span class="bar-label">%s</span>'
            '<span class="bar-track"><span class="bar-fill" style="width:%.1f%%"></span></span>'
            '<span class="bar-num">%d <small>(%.0f%%)</small></span></div>' % (esc(label), pct, n, share)
        )
    out.append("</div>")
    return "".join(out)


def stack_bar(counts: dict[str, int]) -> str:
    total = sum(counts.values()) or 1
    segs = []
    for sp in SPLIT_ORDER:
        n = counts.get(sp, 0)
        segs.append(
            '<span class="seg s-%s" style="width:%.2f%%" title="%s %d">%d</span>' % (sp, 100.0 * n / total, sp, n, n)
        )
    return '<div class="stack">%s</div>' % "".join(segs)


def details(summary: str, inner: str, open_: bool = False) -> str:
    return "<details%s><summary>%s</summary>%s</details>" % (" open" if open_ else "", summary, inner)


def pre_json(obj) -> str:
    return "<pre><code>%s</code></pre>" % esc(json.dumps(obj, ensure_ascii=False, indent=2))


def shrink(value, depth: int = 0):
    """긴 값을 줄여 보여준다. 원본은 바꾸지 않는다."""
    if isinstance(value, str):
        return value if len(value) <= 90 else value[:90] + "…(%d자)" % len(value)
    if isinstance(value, list):
        head = [shrink(v, depth + 1) for v in value[:3]]
        if len(value) > 3:
            head.append("… 외 %d개" % (len(value) - 3))
        return head
    if isinstance(value, dict):
        if depth >= 3:
            return "{…키 %d개}" % len(value)
        return {k: shrink(v, depth + 1) for k, v in value.items()}
    return value


def clip(text, n: int = 150) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n] + "…"


# --------------------------------------------------------------------------
# 데이터 수집
# --------------------------------------------------------------------------
def collect_datasets(manifest: dict) -> list[dict]:
    names = list((manifest.get("datasets") or {}).keys())
    for p in sorted(DATA_DIR.glob("*.jsonl")):
        if p.stem not in names:
            names.append(p.stem)
    out = []
    for name in names:
        rows = load_rows(DATA_DIR / (name + ".jsonl"))
        if not rows and name not in DS_INFO:
            continue
        meta = [r.get("metadata") or {} for r in rows]
        splits = Counter(m.get("split") for m in meta)
        info = DS_INFO.get(name, (name, name))
        out.append(
            {
                "name": name,
                "label": info[0],
                "feature": info[1],
                "rows": rows,
                "count": len(rows),
                "splits": {sp: splits.get(sp, 0) for sp in SPLIT_ORDER},
                "categories": Counter(m.get("category") for m in meta),
                "difficulties": Counter(m.get("difficulty") for m in meta),
                "families": {m.get("familyId") for m in meta},
            }
        )
    return out


def check_integrity(datasets: list[dict]) -> dict:
    fam_splits: dict[str, set] = {}
    seen_ids: Counter = Counter()
    seen_inputs: Counter = Counter()
    for d in datasets:
        for r in d["rows"]:
            m = r.get("metadata") or {}
            fam_splits.setdefault(m.get("familyId"), set()).add(m.get("split"))
            seen_ids[m.get("caseId")] += 1
            seen_inputs[json.dumps(r.get("input"), sort_keys=True, ensure_ascii=False)] += 1
    return {
        "families": len(fam_splits),
        "leaks": sum(1 for s in fam_splits.values() if len(s) > 1),
        "dupIds": sum(1 for n in seen_ids.values() if n > 1),
        "dupInputs": sum(1 for n in seen_inputs.values() if n > 1),
    }


def parse_review(path: Path) -> dict:
    text = read_text(path)
    m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", text.splitlines()[0] if text else "")
    if not m:
        m = re.search(r"v(\d+)_(\d+)", path.stem)
        ver = "%s.%s" % (m.group(1), m.group(2)) if m else path.stem
    else:
        ver = m.group(1)
    concl = find_section(text, 2, "결론")

    def cell(label: str) -> str:
        mm = re.search(r"^\|\s*%s\s*\|\s*(.+?)\s*\|\s*$" % label, concl, re.M)
        return mm.group(1) if mm else ""

    def first_int(s: str):
        mm = re.search(r"\d+", s)
        return int(mm.group(0)) if mm else None

    score_m = re.search(r"\*\*(\d+)\s*/\s*(\d+)\*\*", cell("점수"))
    score = int(score_m.group(1)) if score_m else None
    counts = {k: first_int(cell(k)) for k in ("critical", "high", "medium", "low")}
    if score is None or counts["critical"] is None or counts["high"] is None:
        verdict = None
    else:
        verdict = score >= PASS_SCORE and counts["critical"] == 0 and counts["high"] == 0

    findings: dict[str, list[tuple[str, str]]] = {}
    sev = None
    heads = {"Critical": "critical", "High": "high", "Medium": "medium", "Low": "low"}
    for line in find_section(text, 2, "발견 사항").splitlines():
        hm = re.match(r"^###\s+(Critical|High|Medium|Low)", line)
        if hm:
            sev = heads[hm.group(1)]
            findings[sev] = []
            continue
        im = re.match(r"^(?:- )?\*\*([CHML]\d+)\.?\s*(.*?)\*\*\s*(.*)$", line.strip())
        if im and sev:
            title = im.group(2) or im.group(3)
            findings[sev].append((im.group(1), title))
    unverified = [ln.strip("- ").strip() for ln in text.splitlines() if "미확인" in ln]
    return {
        "path": path,
        "ver": ver,
        "score": score,
        "max": int(score_m.group(2)) if score_m else None,
        "counts": counts,
        "verdict": verdict,
        "findings": findings,
        "text": text,
        "unverified": unverified,
        "rawVerdict": cell("통과 여부"),
    }


def collect_reviews() -> dict[str, dict]:
    out = {}
    for p in sorted(REVIEW_DIR.glob("claude_opus_v*.md")):
        try:
            r = parse_review(p)
        except (ValueError, IndexError, AttributeError):
            continue
        out[r["ver"]] = r
    return out


def collect_revisions() -> dict[str, dict]:
    out = {}
    for p in sorted(DATA_DIR.glob("revision_v*_report.md")):
        m = re.search(r"revision_v(\d+)_(\d+)_report", p.name)
        if not m:
            continue
        out["%s.%s" % (m.group(1), m.group(2))] = {"path": p, "text": read_text(p)}
    return out


def minor_key(ver: str) -> str:
    return ".".join(ver.split(".")[:2])


# --------------------------------------------------------------------------
# 섹션 렌더러
# --------------------------------------------------------------------------
def status_badge(text: str, kind: str = "") -> str:
    return '<span class="badge %s">%s</span>' % (kind, esc(text))


def sec_overview(manifest, datasets, total_rows, integ) -> str:
    ver = manifest.get("version") or FAIL_TEXT
    split_total = {sp: sum(d["splits"][sp] for d in datasets) for sp in SPLIT_ORDER}
    badges = []
    badges.append(status_badge("합성 데이터" if manifest.get("piiClass") == "synthetic" else "piiClass 확인 필요"))
    if manifest.get("modelApiCalled") is False and manifest.get("networkUsed") is False:
        badges.append(status_badge("생성 중 모델 API·네트워크 호출 없음 (API 비용 0)"))
    else:
        badges.append(status_badge("API 사용 여부 manifest 확인 필요", "warn"))
    hs = manifest.get("humanReviewStatus")
    badges.append(
        status_badge("사람 라벨 미확정", "warn")
        if hs and "pending" in str(hs)
        else status_badge("사람 검토 상태: %s" % (hs or FAIL_TEXT), "warn")
    )
    badges.append(
        status_badge("holdout 평문 (봉인 아님)", "warn")
        if manifest.get("holdoutAccessStatus") == "plaintext_not_sealed"
        else ""
    )
    stats = (
        '<div class="stats">'
        '<div class="stat"><b>%s</b><span>데이터셋 버전</span></div>'
        '<div class="stat"><b>%d</b><span>총 케이스</span></div>'
        '<div class="stat"><b>%d</b><span>Dataset 수</span></div>'
        '<div class="stat"><b>%d</b><span>family(분할 단위)</span></div>'
        "</div>" % (esc(str(ver)), total_rows, len(datasets), integ["families"])
    )
    split_rows = "".join(
        '<div class="stat small"><b>%d</b><span>%s</span></div>' % (split_total[sp], esc(SPLIT_KO[sp]))
        for sp in SPLIT_ORDER
    )
    rows = []
    for d in datasets:
        rows.append(
            [
                "<code>%s</code><br><small>%s</small>" % (esc(d["name"]), esc(d["label"])),
                esc(d["feature"]),
                str(d["count"]),
                str(d["splits"]["calibration"]),
                str(d["splits"]["regression"]),
                str(d["splits"]["blind_holdout"]),
                stack_bar(d["splits"]),
            ]
        )
    rows.append(
        [
            "<strong>합계</strong>",
            "",
            "<strong>%d</strong>" % total_rows,
            "<strong>%d</strong>" % split_total["calibration"],
            "<strong>%d</strong>" % split_total["regression"],
            "<strong>%d</strong>" % split_total["blind_holdout"],
            stack_bar(split_total),
        ]
    )
    table = scroll_table(
        ["Dataset", "평가 대상 기능", "건수", "calibration", "regression", "blind_holdout", "분할 비율"],
        rows,
        num_cols=(2, 3, 4, 5),
    )
    legend = (
        '<p class="legend"><span class="dot s-calibration"></span>calibration '
        '<span class="dot s-regression"></span>regression '
        '<span class="dot s-blind_holdout"></span>blind_holdout</p>'
    )
    cats = ""
    for d in datasets:
        items = sorted(d["categories"].items(), key=lambda kv: (-kv[1], str(kv[0])))
        cats += details(
            "%s 카테고리 분포 (%d종)" % (esc(d["label"]), len(items)),
            bar_rows([(str(k), v) for k, v in items], d["count"]),
        )
    diff = Counter()
    for d in datasets:
        diff.update(d["difficulties"])
    diff_items = sorted(diff.items(), key=lambda kv: (-kv[1], str(kv[0])))
    warn = ""
    mf_total = (manifest.get("integrity") or {}).get("total")
    if mf_total is not None and mf_total != total_rows:
        warn = (
            '<p class="callout warn">manifest의 total(%s)과 JSONL 합계(%d)가 다릅니다. 데이터셋이 재생성 중일 수 있습니다.</p>'
            % (
                esc(str(mf_total)),
                total_rows,
            )
        )
    return (
        "<p>소개팅 AI 서버는 AI가 하는 일이 세 가지입니다. <strong>① 온보딩</strong>(사용자에게 질문하고 답에서 성향을 뽑아 "
        "페르소나 만들기), <strong>② 연습대화</strong>(다른 사람 역할을 맡은 AI와 대화), <strong>③ 시뮬레이션</strong>"
        "(두 사람의 가상 대화와 궁합 리포트). 이 문서는 그 세 가지를 <strong>같은 문제로 반복해서 채점</strong>하려고 만든 "
        "<strong>시험 문제집(합성 테스트셋)</strong>을 설명합니다. 세 가지를 더 잘게 나눠 문제집을 %d종으로 만들었습니다(아래 표).</p>"
        '<p class="callout">이 문제집에는 <strong>문제와 채점 기준만</strong> 들어 있습니다. AI에게 문제를 풀게 하거나 점수를 '
        "매기는 기능은 아직 없습니다. 아래 수치는 이 문서를 만든 시점의 데이터 파일에서 직접 센 값입니다.</p>"
        % len(datasets)
        + '<div class="badges">%s</div>' % "".join(badges)
        + stats
        + '<div class="stats">%s</div>' % split_rows
        + warn
        + table
        + legend
        + "<h3>용어 풀이</h3>"
        + "<ul>"
        "<li><strong>케이스</strong>: 문제 1개. <strong>Dataset</strong>: 기능별로 묶은 문제 묶음.</li>"
        "<li><strong>합성 데이터</strong>: 실제 사용자 대화가 아니라 코드와 기획서를 바탕으로 만든 가상 문제입니다(개인정보 없음).</li>"
        "<li><strong>calibration(보정용)</strong>: 채점 기준이 납득되는지 사람이 맞춰 보는 문제. 프롬프트를 고칠 때 봐도 됩니다.</li>"
        "<li><strong>regression(회귀 검사용)</strong>: 프롬프트를 바꿀 때마다 돌려서 이전보다 나빠지지 않았는지 확인하는 문제.</li>"
        "<li><strong>blind_holdout(최종 확인용)</strong>: 프롬프트를 고칠 때 보지 않고 마지막에 확인하는 문제. "
        "다만 파일이 그대로 공개돼 있어 진짜 비밀은 아닙니다.</li>"
        "<li><strong>family(원형 묶음)</strong>: 비슷한 원형에서 나온 문제의 묶음. 한 묶음은 한 곳에만 넣어서, 미리 본 문제가 최종 확인에 섞이지 않게 합니다.</li>"
        "<li><strong>카테고리·난이도</strong>: 문제의 종류(예: 짧은 답변, 정체 질문)와 어려운 정도.</li>"
        "</ul>"
        + "<h3>난이도 분포 (전체)</h3>"
        + bar_rows([(str(k), v) for k, v in diff_items], total_rows)
        + "<h3>Dataset별 카테고리</h3>"
        + cats
    )


def parse_plan() -> dict:
    text = read_text(PLAN_PATH)
    if not text:
        return {}
    plan: dict = {"text": text}
    design = find_section(text, 2, "설계 원칙")
    plan["principles"] = [t for t in md_sections(design, 3).keys()]
    tbl = re.findall(r"^\|\s*`(quality/[^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*$", design, re.M)
    plan["datasets"] = tbl
    purpose = find_section(text, 2, "문서 목적")
    plan["bases"] = re.findall(r"^- \*\*(.+?)\*\*:\s*(.+)$", purpose, re.M)
    plan["ratios"] = re.findall(
        r"`(calibration|regression|blind_holdout)`\s*(\d+)%", find_section(text, 2, "구축 규모")
    )
    return plan


def sec_why(plan: dict) -> str:
    intro = (
        "<p>프롬프트나 모델을 바꿀 때마다 &ldquo;좋아졌나?&rdquo;를 감으로 판단하면 회귀를 놓칩니다. "
        "<strong>같은 입력 묶음</strong>으로 전후를 비교하려고 고정된 테스트셋이 필요했고, 기능마다 실패 경계가 달라서 Dataset을 나눴습니다. "
        "기획서 <code>docs/v1docs/langfuse-quality-dataset-plan.md</code>의 핵심은 다음과 같습니다.</p>"
    )
    if not plan:
        return intro + '<p class="callout warn">기획서를 읽지 못했습니다 (%s).</p>' % FAIL_TEXT
    parts = [intro]
    if plan.get("datasets"):
        rows = [["<code>%s</code>" % esc(a), "<code>%s</code>" % esc(b), esc(c)] for a, b, c in plan["datasets"]]
        parts.append(
            "<h3>Dataset 분리 원칙</h3><p>하나의 통합 Dataset에 넣지 않고 generation 이름과 실패 경계에 맞춰 분리합니다. "
            "발화는 좋아졌지만 태깅이 나빠진 경우처럼 원인을 나눠 볼 수 있기 때문입니다.</p>"
            + scroll_table(["Langfuse Dataset", "대상 generation", "평가 단위"], rows)
        )
    else:
        parts.append("<h3>Dataset 분리 원칙</h3><p>%s</p>" % FAIL_TEXT)
    if plan.get("principles"):
        parts.append(
            "<h3>기획서의 설계 원칙</h3><ul>%s</ul>" % "".join("<li>%s</li>" % md_inline(t) for t in plan["principles"])
        )
    if plan.get("bases"):
        parts.append(
            "<h3>제품 기준값과 초기 제안값</h3><p>기획서는 수치를 두 종류로 구분합니다. 뒤쪽은 데이터가 쌓인 뒤 보정할 값이며 "
            "처음부터 영구 합격선이 아닙니다.</p><ul>%s</ul>"
            % "".join("<li><strong>%s</strong>: %s</li>" % (esc(a), md_inline(b)) for a, b in plan["bases"])
        )
    if plan.get("ratios"):
        parts.append(
            "<p>기획서의 권장 분할은 %s 입니다. 같은 원형에서 파생된 항목은 모두 같은 split에 둡니다.</p>"
            % ", ".join("<code>%s</code> %s%%" % (esc(a), esc(b)) for a, b in plan["ratios"])
        )
    return "".join(parts)


def sec_case(datasets) -> str:
    intro = (
        "<p>각 줄(케이스)은 <code>input</code>, <code>expectedOutput</code>, <code>metadata</code> 세 덩어리입니다. "
        "<strong>expectedOutput은 정답 문장이 아니라 채점 기준</strong>입니다. 생성형 답변은 정답이 여러 개라서, "
        "반드시 지킬 계약(hardAssertions), 하면 안 되는 것(forbidden), 1/3/5점 루브릭, 기능별 세부 contract를 저장합니다.</p>"
        '<div class="grid3"><div class="card"><h4>input</h4><p>모델(또는 평가 어댑터)에 주는 상황. '
        "일부 필드(<code>candidateOutput</code>, <code>faultInjection</code> 등)는 모델에 넣는 힌트가 아니라 어댑터용 fixture입니다.</p></div>"
        '<div class="card"><h4>expectedOutput</h4><p>채점 기준. 점수도 단일 정답이 아니라 <code>acceptableRanges</code>(검토 대기 범위)로 둡니다.</p></div>'
        '<div class="card"><h4>metadata</h4><p>caseId, category, difficulty, familyId, split, sourceRefs(근거 코드 위치), reviewFlags 등.</p></div></div>'
        "<p>아래는 Dataset별 실제 케이스 한 건(regression 분할의 첫 케이스)을 줄여 보인 것입니다. 긴 값은 90자에서 자르고, 원본 전체는 review 문서에서 볼 수 있습니다.</p>"
    )
    out = [intro]
    for d in datasets:
        pick = next((r for r in d["rows"] if (r.get("metadata") or {}).get("split") == "regression"), None)
        if pick is None and d["rows"]:
            pick = d["rows"][0]
        if pick is None:
            continue
        exp = pick.get("expectedOutput") or {}
        meta = pick.get("metadata") or {}
        rub = exp.get("rubric") or {}
        anchors = rub.get("anchors") or {}
        extra = sorted(k for k in exp if k not in COMMON_EXPECTED)
        ha = "".join("<li>%s</li>" % esc(clip(x)) for x in (exp.get("hardAssertions") or [])[:4]) or "<li>없음</li>"
        fb = "".join("<li>%s</li>" % esc(clip(x, 100)) for x in (exp.get("forbidden") or [])[:4]) or "<li>없음</li>"
        anc = "".join(
            "<tr><th>%s점</th><td>%s</td></tr>" % (esc(k), esc(clip(anchors.get(k, ""), 220)))
            for k in ("1", "3", "5")
            if k in anchors
        )
        body = (
            '<p class="muted">%s / 카테고리 <code>%s</code> / 난이도 <code>%s</code> / family <code>%s</code></p>'
            % (
                esc(str(meta.get("caseId"))),
                esc(str(meta.get("category"))),
                esc(str(meta.get("difficulty"))),
                esc(str(meta.get("familyId"))),
            )
            + "<h4>input (축약)</h4>"
            + pre_json(shrink(pick.get("input")))
            + '<div class="cols"><div><h4>hardAssertions (앞 4개)</h4><ul>%s</ul></div>' % ha
            + "<div><h4>forbidden (앞 4개)</h4><ul>%s</ul></div></div>" % fb
            + ("<h4>rubric 앵커</h4>" + '<div class="scroll"><table class="kv">%s</table></div>' % anc if anc else "")
            + "<h4>기능별 세부 contract 키</h4><p>%s</p>"
            % (" ".join("<code>%s</code>" % esc(k) for k in extra) or "없음")
            + details(
                "이 케이스 전체 (축약 JSON)",
                pre_json(
                    shrink(
                        {
                            "input": pick.get("input"),
                            "expectedOutput": exp,
                            "metadata": {k: v for k, v in meta.items() if k != "sourceRefs"},
                            "sourceRefs": "%d개" % len(meta.get("sourceRefs") or []),
                        }
                    )
                ),
            )
        )
        out.append(details("<strong>%s</strong> <small>%s</small>" % (esc(d["label"]), esc(d["name"])), body))
    return "".join(out)


def sec_split(manifest, datasets, integ, readme: str) -> str:
    fam_rows = []
    for sp in SPLIT_ORDER:
        fams = set()
        n = 0
        for d in datasets:
            for r in d["rows"]:
                m = r.get("metadata") or {}
                if m.get("split") == sp:
                    fams.add(m.get("familyId"))
                    n += 1
        fam_rows.append([esc(SPLIT_KO[sp]), str(n), str(len(fams))])
    sm = re.search(
        r"SequenceMatcher 비율 ([\d.]+) 이상, 문자 3-gram Dice ([\d.]+) 이상, 공통 문자 (\d+)자 이상", readme
    )
    short_m = re.search(r"(\d+)자 미만 관용문", readme)
    if sm:
        approx = (
            "<li><strong>근사 일치</strong>: NFKC·문장부호·공백 정규화 뒤 SequenceMatcher 비율 <code>%s</code> 이상, 문자 3-gram Dice <code>%s</code> 이상, 공통 문자 <code>%s</code>자 이상을 <em>동시에</em> 만족하면 충돌로 봅니다.%s</li>"
            % (
                esc(sm.group(1)),
                esc(sm.group(2)),
                esc(sm.group(3)),
                (" %s자 미만 관용문은 근사 검사에서 제외합니다." % esc(short_m.group(1))) if short_m else "",
            )
        )
    else:
        approx = "<li><strong>근사 일치</strong>: %s (README에서 임계값을 찾지 못함)</li>" % FAIL_TEXT
    exact_ok = "정확 일치" in readme
    exact = (
        "<li><strong>정확 일치</strong>: 공백·종결부호를 정규화한 뒤 입력 발화·인용·후보 출력의 전체 문자열과 개별 문장을 비교합니다. 한 글자 답도 검사하고 코드로 고정된 첫 인사만 예외입니다.</li>"
        if exact_ok
        else "<li><strong>정확 일치</strong>: %s</li>" % FAIL_TEXT
    )
    audit = read_json(DATA_DIR / "audit_report.json")
    audit_txt = ""
    if audit:
        audit_txt = (
            "<p>저장된 <code>audit_report.json</code> 결과: ok=<code>%s</code>, holdout 교차 정확 일치 %s건, 근사 일치 쌍 %s건.</p>"
            % (
                esc(str(audit.get("ok"))),
                esc(str(audit.get("holdoutStimulusCrossSplitExactSentenceCount", FAIL_TEXT))),
                esc(str(audit.get("holdoutStimulusCrossSplitApproximatePairCount", FAIL_TEXT))),
            )
        )
    return (
        "<p>같은 원형에서 나온 케이스가 서로 다른 split에 있으면, calibration에서 맞춘 프롬프트가 holdout 점수를 부풀립니다. "
        "그래서 <strong>family 단위로 분할</strong>합니다. 같은 high/low 태깅과 build 파생, 같은 페르소나 쌍의 시뮬레이션과 preview는 한 family이고 <code>splitGroup=familyId</code>입니다.</p>"
        + scroll_table(["split", "케이스 수", "family 수"], fam_rows, num_cols=(1, 2))
        + '<div class="callout ok"><strong>이 문서를 만들며 JSONL에서 직접 확인한 값</strong>: family %d개, 여러 split에 걸친 family %d개, 중복 caseId %d건, 중복 input %d건.</div>'
        % (integ["families"], integ["leaks"], integ["dupIds"], integ["dupInputs"])
        + '<div class="callout warn"><strong>한계</strong>: <code>blind_holdout</code>은 데이터 분할 표식일 뿐입니다. 같은 저장소에 평문으로 있어서 (<code>holdoutAccessStatus=%s</code>) 접근 통제된 진짜 비공개 holdout이 아닙니다. '
        "프롬프트 작성자가 본 뒤에는 새 원형을 독립적으로 써서 봉인해야 합니다.</div>"
        % esc(str(manifest.get("holdoutAccessStatus", FAIL_TEXT)))
        + "<h3>audit 방법</h3><ul>"
        + exact
        + approx
        + "</ul>"
        + audit_txt
        + "<p>이 검사는 문장 구조 유사를 찾는 휴리스틱이고 의미 동등성을 증명하지 않습니다. 실제로 독립 검토(Opus)는 자동 audit 통과와 별개로 holdout을 직접 읽어 비교했고, 부분적으로만 분리된 사례를 찾았습니다(아래 검토 이력).</p>"
    )


def sec_build(manifest, readme: str) -> str:
    gen = manifest.get("generator", "scripts/generate_quality_datasets.py")
    block = ""
    m = re.search(r"```sh\n(.*?)```", find_section(readme, 2, "재생성"), re.S)
    if m:
        block = "<pre><code>%s</code></pre>" % esc(m.group(1).strip())
    else:
        block = "<p>%s</p>" % FAIL_TEXT
    srcs = manifest.get("sourceSha256") or {}
    matched = sum(1 for p, h in srcs.items() if sha256_of(ROOT / p) == h)
    files = manifest.get("files") or {}
    frows = [
        ["<code>%s</code>" % esc(n), "<code>%s</code>" % esc(str(v.get("sha256", ""))[:12]), str(v.get("bytes", ""))]
        for n, v in files.items()
    ]
    cur_gen = sha256_of(ROOT / gen)
    gen_state = (
        "일치"
        if cur_gen and cur_gen == manifest.get("generatorSha256")
        else "불일치 (생성기가 manifest 작성 뒤 바뀌었거나 재생성 중일 수 있음)"
    )
    return (
        "<p><code>%s</code> 한 파일이 앱 코드와 기획서를 근거로 케이스를 <strong>결정적으로 합성</strong>합니다. "
        "시각·환경 변수·난수·앱 모듈 import에 의존하지 않고, 실행 중 네트워크나 모델 API를 부르지 않습니다(그래서 API 비용 0). "
        "<code>generationMethod=%s</code>는 초안을 쓴 작업 식별자이며 실행 시 그 모델을 호출한다는 뜻이 아닙니다.</p>"
        % (esc(gen), esc(str(manifest.get("generationMethod", FAIL_TEXT))))
        + '<div class="grid3"><div class="card"><h4>--check 재현성</h4><p>파일을 쓰지 않고 소스에서 다시 조립한 바이트를 모든 산출물과 비교합니다. 다르면 실패합니다.</p></div>'
        '<div class="card"><h4>validator · audit</h4><p><code>validate_quality_datasets.py</code>가 스키마·경계·필수 필드를, <code>audit_quality_datasets.py</code>가 split 간 문장 충돌을 검사합니다.</p></div>'
        '<div class="card"><h4>해시(manifest)</h4><p>산출물, 생성기, 근거 소스 파일의 SHA-256을 기록해 &ldquo;어떤 코드 기준으로 만들었는지&rdquo;를 남깁니다.</p></div></div>'
        + "<h3>재생성과 검증 명령 (README 발췌)</h3>"
        + block
        + '<div class="callout"><strong>이 문서 생성 시점의 해시 대조</strong>: 근거 소스 %d개 중 %d개가 manifest 해시와 일치합니다. 생성기 파일은 manifest와 %s.'
        " 앱 코드가 데이터셋 생성 뒤에 바뀌면 불일치가 생기며, 이때 기대값이 낡았을 수 있어 재생성과 재검토가 필요합니다.</div>"
        % (len(srcs), matched, gen_state)
        + details("산출물 파일 해시 (앞 12자)", scroll_table(["파일", "sha256", "bytes"], frows, num_cols=(2,)))
    )


def finding_list(rev: dict) -> str:
    sev_ko = [("critical", "Critical"), ("high", "High"), ("medium", "Medium"), ("low", "Low")]
    out = []
    for key, label in sev_ko:
        items = rev["findings"].get(key)
        n = rev["counts"].get(key)
        head = "%s %s건" % (label, n if n is not None else FAIL_TEXT)
        if items:
            lis = "".join("<li><code>%s</code> %s</li>" % (esc(a), md_inline(clip(b, 130))) for a, b in items)
            out.append(details(head, "<ul>%s</ul>" % lis, open_=key == "high"))
        else:
            out.append(
                '<p class="sev-line"><strong>%s</strong> %s</p>'
                % (esc(head), "" if (n == 0) else '<span class="muted">(항목 상세를 읽지 못함)</span>')
            )
    return "".join(out)


def review_card(ver: str, rev: dict | None, rvs: dict | None) -> str:
    title = short_ver(ver)
    parts = []
    if rev:
        c = rev["counts"]
        score = "%s / %s" % (rev["score"], rev["max"]) if rev["score"] is not None else FAIL_TEXT
        cnt = " · ".join(
            "%s %s" % (k, c[k] if c[k] is not None else FAIL_TEXT) for k in ("critical", "high", "medium", "low")
        )
        if rev["verdict"] is None:
            badge = status_badge(FAIL_TEXT, "warn")
        elif rev["verdict"]:
            badge = status_badge("통과 기준 충족", "ok")
        else:
            badge = status_badge("통과 기준 미달", "bad")
        parts.append(
            '<p class="score">Opus 독립 검토 <strong>%s점</strong> %s<br><small>%s</small></p>'
            % (esc(score), badge, esc(cnt))
        )
        parts.append(finding_list(rev))
        parts.append('<p class="muted">원문: <code>%s</code></p>' % esc(str(rev["path"].relative_to(ROOT))))
    else:
        parts.append('<p class="muted">이 버전에 대한 독립 재검토는 아직 없습니다.</p>')
    if rvs:
        text = rvs["text"]
        intro = text.split("\n## ", 1)[0]
        intro = "\n".join(intro.splitlines()[1:])
        parts.append(details("수정 보고서 요약 (%s, 작성 시점 기준)" % esc(rvs["path"].name), md_blocks(intro)))
    return '<div class="tl-item"><div class="tl-dot"></div><div class="tl-body"><h3>%s</h3>%s</div></div>' % (
        esc(title),
        "".join(parts),
    )


def sec_history(manifest, reviews, revisions) -> str:
    vers = set(map(minor_key, reviews)) | set(map(minor_key, revisions))
    if manifest.get("version"):
        vers.add(minor_key(manifest["version"]))
    ordered = sorted(vers, key=ver_key)
    rev_by = {minor_key(k): v for k, v in reviews.items()}
    rvs_by = {minor_key(k): v for k, v in revisions.items()}
    cards = "".join(review_card(v, rev_by.get(v), rvs_by.get(v)) for v in ordered)
    verdicts = [r["verdict"] for r in reviews.values()]
    if reviews and all(v is False for v in verdicts):
        honest = (
            "지금까지의 독립 검토 %d회는 <strong>모두 통과 기준(%d점 이상, critical·high 0건)에 미달</strong>했습니다. "
            "점수는 검토마다 다른 관점과 기준 코드로 매긴 값이라 <strong>직접 비교하면 안 됩니다</strong>. "
            "예를 들어 점수가 내려간 검토는 데이터가 나빠졌다는 뜻이 아니라, 그 사이 바뀐 앱 코드와의 정합성을 새로 따진 결과일 수 있습니다."
            % (len(reviews), PASS_SCORE)
        )
        kind = "warn"
    elif reviews:
        honest = "검토 %d회의 결과는 아래와 같습니다. 점수는 서로 독립된 검토라 직접 비교하지 않습니다." % len(reviews)
        kind = ""
    else:
        honest = "검토 보고서를 읽지 못했습니다 (%s)." % FAIL_TEXT
        kind = "warn"
    latest = ordered[-1] if ordered else None
    tail = ""
    if latest and latest not in rev_by:
        tail = (
            '<p class="callout">가장 최근 버전(%s)은 아직 독립 재검토를 받지 않았습니다. 다음 버전 작업이 진행 중일 수 있습니다.</p>'
            % esc(short_ver(latest))
        )
    return (
        '<p class="callout %s">%s</p>' % (kind, honest)
        + "<p>흐름은 &ldquo;독립 검토(Opus) &rarr; 발견 사항 수정 &rarr; 앱 코드 변경 반영 &rarr; 재검토&rdquo;입니다. 심각도는 Critical/High가 통과를 막고, Medium/Low는 개선 항목입니다.</p>"
        + '<div class="timeline">%s</div>' % cards
        + tail
    )


def sec_drift(manifest, revisions) -> str:
    src = None
    for _, r in sorted(revisions.items(), key=lambda kv: ver_key(kv[0]), reverse=True):
        if "바뀐 앱 동작" in r["text"]:
            src = r
            break
    intro = (
        "<p>이 데이터셋의 기대값(특히 규칙 점수·총점·등급·토큰 상한)은 <strong>앱 코드에서 계산한 값</strong>입니다. "
        "그래서 앱 동작이 바뀌면 케이스 자체가 아니라 <strong>기대값이 낡습니다</strong>. "
        "manifest에 근거 소스의 해시를 기록하는 것도, 앱 순수 함수로 기대값을 재계산하는 드리프트 테스트(<code>tests/test_quality_datasets_drift.py</code>)를 두는 것도 이 때문입니다.</p>"
    )
    if not src:
        return intro + '<p class="callout warn">앱 코드 변경 반영 보고서를 찾지 못했습니다 (%s).</p>' % FAIL_TEXT
    text = src["text"]
    commit = re.search(r"커밋 `([0-9a-f]{7,40})`", text)
    changes = find_section(text, 2, "바뀐 앱 동작")
    scope = find_section(text, 2, "변경 범위")
    example = re.search(r"예:\s*([^\n]+?)(?:\.|\n)", scope)
    ex_html = ""
    if example:
        ex_html = '<div class="callout"><strong>총점이 바뀐 예</strong>: %s</div>' % md_inline(example.group(1))
    head = (
        "<p>기준이 된 앱 커밋은 <code>%s</code>이고, 그 뒤 바뀐 동작은 다음과 같이 반영되었습니다 (<code>%s</code> 발췌).</p>"
        % (
            esc(commit.group(1)) if commit else FAIL_TEXT,
            esc(src["path"].name),
        )
    )
    return (
        intro
        + head
        + md_blocks(changes)
        + "<h3>바뀐 케이스 수</h3>"
        + (md_blocks(scope) if scope else "<p>%s</p>" % FAIL_TEXT)
        + ex_html
        + "<p>케이스 수·분할·caseId는 그대로이고 기대값만 정정되었습니다. 드리프트 테스트는 수정 전 다수가 실패했고 수정 후 통과했다고 보고서에 적혀 있습니다.</p>"
    )


def sec_trust(manifest, reviews, readme: str) -> str:
    latest = reviews[max(reviews, key=ver_key)] if reviews else None
    status_rows = []
    labels = [
        ("humanReviewStatus", "사람 이중 검토"),
        ("judgeCalibrationStatus", "Judge calibration"),
        ("sutExecutionStatus", "실제 모델 실행(SUT)"),
        ("langfuseUploadStatus", "Langfuse 업로드"),
        ("holdoutAccessStatus", "holdout 접근"),
    ]
    for key, ko in labels:
        status_rows.append([esc(ko), "<code>%s</code>" % esc(str(manifest.get(key, FAIL_TEXT)))])
    oracle = ""
    if latest:
        sec = find_section(latest["text"], 2, "재계산")
        first_tbl = "\n".join(ln for ln in sec.splitlines() if ln.startswith("|"))
        # 첫 번째 표만 사용한다 (빈 줄 뒤의 두 번째 표는 제외)
        block = []
        for ln in sec.splitlines():
            if ln.startswith("|"):
                block.append(ln)
            elif block:
                break
        first_tbl = "\n".join(block)
        if first_tbl:
            oracle = "<h3>앱 함수로 다시 계산해 확인된 것 (%s 검토)</h3>%s" % (
                esc(short_ver(latest["ver"])),
                md_blocks(first_tbl),
            )
    lims = manifest.get("limitations") or []
    lim_html = "<ul>%s</ul>" % "".join("<li>%s</li>" % esc(str(x)) for x in lims) if lims else "<p>%s</p>" % FAIL_TEXT
    gap = find_section(readme, 2, "현재 코드와 품질 계약의 차이")
    gap_html = md_blocks(gap) if gap else "<p>%s</p>" % FAIL_TEXT
    stale = ""
    if (
        latest
        and manifest.get("version")
        and ver_key(minor_key(manifest["version"])) > ver_key(minor_key(latest["ver"]))
    ):
        stale = (
            '<p class="callout warn">manifest 버전(%s)이 마지막 독립 검토(%s)보다 새 판입니다. '
            "아래 재계산·검토 수치는 검토 시점의 데이터셋 기준이라 현재 건수와 다를 수 있습니다.</p>"
            % (esc(str(manifest["version"])), esc(short_ver(latest["ver"])))
        )
    unv = ""
    if latest and latest["unverified"]:
        unv = "<h3>검토 보고서가 확인하지 못했다고 적은 것</h3><ul>%s</ul>" % "".join(
            "<li>%s</li>" % md_inline(clip(x, 220)) for x in latest["unverified"][:8]
        )
    return (
        stale + '<div class="cols"><div class="card good"><h4>믿을 수 있는 것</h4><ul>'
        "<li>규칙 오라클: 점수 공식·총점·등급·위험 판정·토큰 상한처럼 앱 순수 함수로 <strong>재계산해 일치를 확인한</strong> 수치</li>"
        "<li>재현성: <code>--check</code>와 해시로 같은 소스에서 같은 파일이 나온다는 것</li>"
        "<li>split 누수 없음(family 단위, 자동 audit)</li></ul></div>"
        '<div class="card bad"><h4>아직 믿으면 안 되는 것</h4><ul>'
        "<li>의미 라벨과 허용 점수 범위: <strong>사람 검토 전 초안</strong>이고 정답으로 확정되지 않음</li>"
        "<li>루브릭 점수와 Judge: calibration 미실행</li>"
        "<li>holdout의 &ldquo;비공개&rdquo;: 평문이라 보장 안 됨</li>"
        "<li>실제 모델의 품질·지연·비용: 아직 한 번도 돌려보지 않음</li></ul></div></div>"
        + "<h3>진행 상태</h3>"
        + scroll_table(["항목", "manifest 값"], status_rows)
        + oracle
        + "<h3>알려진 한계 (manifest.limitations)</h3>"
        + lim_html
        + "<h3>현재 앱 코드와 품질 계약의 차이 (README)</h3><p>평가용 반례로 기록된 것이며 앱 코드는 고치지 않았습니다.</p>"
        + gap_html
        + unv
    )


def sec_howto() -> str:
    hm = re.search(r"const VERDICTS = \[\[(.*?)\]\];", read_text(REVIEW_HTML), re.S)
    verdicts = re.findall(r'"(\w+)","([^"]+)"', hm.group(1)) if hm else []
    exp_name = re.search(r'a\.download="([^"]+)"', read_text(REVIEW_HTML))
    vtxt = ", ".join("<strong>%s</strong>" % esc(v[1]) for v in verdicts) if verdicts else FAIL_TEXT
    fname = esc(exp_name.group(1)) if exp_name else FAIL_TEXT
    return (
        "<p>사람 라벨이 아직 없으므로, 팀원이 직접 읽고 판정하는 것이 다음 신뢰 확보 단계입니다. 검토용 문서는 "
        "<code>evals/review/quality_dataset_review.html</code> 하나이며 브라우저로 열면 됩니다(서버 불필요).</p>"
        "<ol><li>상단 탭에서 Dataset을 고르고, 분할 버튼(calibration/regression/blind_holdout)과 검색창으로 케이스를 좁힙니다.</li>"
        "<li>케이스를 펼쳐 입력, 반드시 지킬 것, 하면 안 되는 것, 채점 기준을 읽습니다.</li>"
        "<li>판정 버튼으로 %s 중 하나를 고르고, 이유·수정 제안을 메모합니다.</li>"
        "<li>판정은 이 브라우저의 localStorage에만 저장됩니다. 공유하려면 <strong>검토 결과 내보내기(JSON)</strong>로 <code>%s</code>를 받아 전달합니다.</li></ol>"
        "<h3>검토 순서 제안</h3><ol>"
        "<li><strong>calibration</strong>부터: 루브릭 문장이 채점 가능한지, 기준을 팀이 같이 맞춥니다.</li>"
        "<li>검토 보고서가 지적한 High 항목의 케이스를 우선 봅니다(위 검토 이력 참고).</li>"
        "<li>Dataset마다 무작위 몇 건을 골라 <em>입력에 정답 힌트가 섞이지 않았는지</em>, 허용 범위가 과하게 좁거나 넓지 않은지 봅니다.</li>"
        "<li>마지막에 <strong>blind_holdout</strong>을 봅니다. 프롬프트를 고치는 사람은 holdout을 보지 않는 것이 원칙입니다.</li>"
        "<li>같은 케이스를 두 사람이 독립적으로 판정하고 불일치만 토론합니다(이중 라벨의 예행연습).</li></ol>"
        % (vtxt, fname)
    )


def sec_next(revisions) -> str:
    left = ""
    for _, r in sorted(revisions.items(), key=lambda kv: ver_key(kv[0]), reverse=True):
        sec = find_section(r["text"], 2, "하지 않은 것")
        if sec:
            left = "<h3>최근 수정 보고서가 남긴 미해결 항목</h3>" + md_blocks(sec)
            break
    return (
        "<h3>신규 케이스 후보</h3><p>앱 프롬프트가 바뀌었지만 이를 검사하는 케이스가 아직 없는 영역입니다. 추가하면 케이스 수와 분할이 바뀌므로 별도 결정이 필요합니다.</p>"
        "<ul><li>온보딩·연습대화의 <strong>&ldquo;저번에/지난번에&rdquo; 금지</strong>와 기억은 대화에 실제로 있던 것만 쓰기</li>"
        "<li>시뮬레이션 <strong>화자 분리</strong>(두 사람의 특성·호칭이 뒤섞이지 않기)와 재시도 경로</li>"
        "<li><strong>범위 밖 요청</strong>(직업·나이 직접 질문, 조언 유도, 농담·욕설)</li>"
        "<li><strong>MBTI 말투 힌트</strong> 분기</li></ul>"
        "<h3>신뢰도를 올리는 순서</h3><ol>"
        "<li><strong>사람 이중 라벨</strong>: 두 사람이 독립 라벨링하고 조정해 의미 라벨과 허용 범위를 확정</li>"
        "<li><strong>Judge calibration</strong>: 사람 앵커와의 일치도를 확인한 뒤에만 LLM Judge 점수를 사용</li>"
        "<li><strong>실제 모델 baseline 실행</strong>: 평가 어댑터를 만들고 현재 프롬프트·모델의 품질, 지연, 비용 기준선을 측정</li>"
        "<li>새 원형으로 만든 <strong>봉인된 holdout</strong> 준비</li></ol>" + left
    )


# --------------------------------------------------------------------------
# 페이지 조립
# --------------------------------------------------------------------------
CSS = """
:root{--bg:#fbfbf9;--fg:#1c1f23;--muted:#5d646c;--line:#dcded9;--card:#ffffff;--soft:#f2f2ee;--accent:#2f5d8a;--code:#f0f0ec;
--ok:#2e7d4f;--okbg:#e7f3ec;--warn:#9a6700;--warnbg:#fbf1d9;--bad:#b3372f;--badbg:#fbe7e4;
--s-cal:#6a9bc3;--s-reg:#7fb08a;--s-blind:#d19a5c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15181b;--fg:#e4e6e8;--muted:#9aa1a8;--line:#2f3439;--card:#1b1f23;--soft:#20252a;--accent:#7fb0dc;--code:#232a30;
--ok:#7cc79a;--okbg:#17291f;--warn:#e0b458;--warnbg:#2b2413;--bad:#f08a80;--badbg:#301a18;
--s-cal:#5b86ab;--s-reg:#5f9470;--s-blind:#b98246}}
:root[data-theme="dark"]{--bg:#15181b;--fg:#e4e6e8;--muted:#9aa1a8;--line:#2f3439;--card:#1b1f23;--soft:#20252a;--accent:#7fb0dc;--code:#232a30;
--ok:#7cc79a;--okbg:#17291f;--warn:#e0b458;--warnbg:#2b2413;--bad:#f08a80;--badbg:#301a18;
--s-cal:#5b86ab;--s-reg:#5f9470;--s-blind:#b98246}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:16px}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",sans-serif;overflow-wrap:anywhere}
.layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:32px;max-width:1120px;margin:0 auto;padding:0 16px}
nav.toc{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;padding:24px 0;font-size:14px}
nav.toc b{display:block;margin-bottom:8px;color:var(--muted);font-weight:600}
nav.toc a{display:block;padding:4px 8px;border-left:2px solid var(--line);color:var(--muted);text-decoration:none}
nav.toc a:hover,nav.toc a.on{color:var(--accent);border-left-color:var(--accent)}
main{min-width:0;max-width:820px;padding:24px 0 80px}
header.top h1{font-size:1.75rem;line-height:1.3;margin:0 0 6px}
header.top p{color:var(--muted);margin:0}
section{margin-top:44px}
h2{font-size:1.35rem;margin:0 0 12px;padding-top:8px;border-top:1px solid var(--line)}
h2 .n{color:var(--muted);font-weight:500;margin-right:6px}
h3{font-size:1.05rem;margin:24px 0 8px}
h4{font-size:.95rem;margin:14px 0 6px}
p,li{margin:.5em 0}
ul,ol{padding-left:1.3em}
small,.muted{color:var(--muted)}
code{background:var(--code);padding:1px 5px;border-radius:4px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
pre{background:var(--code);padding:12px;border-radius:6px;overflow:auto;max-height:420px;margin:8px 0}
pre code{padding:0;background:none;white-space:pre}
.scroll{overflow-x:auto;margin:10px 0;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
table.kv th{width:64px}
details{border:1px solid var(--line);border-radius:6px;margin:8px 0;background:var(--card)}
summary{cursor:pointer;padding:8px 12px;font-weight:500}
details>*:not(summary){margin-left:12px;margin-right:12px}
details[open]{padding-bottom:8px}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;background:var(--soft);border:1px solid var(--line)}
.badge.ok{background:var(--okbg);color:var(--ok);border-color:transparent}
.badge.warn{background:var(--warnbg);color:var(--warn);border-color:transparent}
.badge.bad{background:var(--badbg);color:var(--bad);border-color:transparent}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.stat b{display:block;font-size:1.5rem;line-height:1.2}
.stat.small b{font-size:1.15rem}
.stat span{color:var(--muted);font-size:13px}
.callout{padding:10px 14px;border-radius:6px;background:var(--soft);border-left:4px solid var(--accent);margin:12px 0}
.callout.warn{background:var(--warnbg);border-left-color:var(--warn)}
.callout.ok{background:var(--okbg);border-left-color:var(--ok)}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:12px 0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 14px 8px}
.card.good{border-top:3px solid var(--ok)}
.card.bad{border-top:3px solid var(--bad)}
.stack{display:flex;height:16px;min-width:140px;border-radius:3px;overflow:hidden;background:var(--soft);font-size:11px;line-height:16px;color:#fff;text-align:center}
.stack .seg{display:block;overflow:hidden}
.s-calibration{background:var(--s-cal)}.s-regression{background:var(--s-reg)}.s-blind_holdout{background:var(--s-blind)}
.legend .dot{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 12px}
.bars{margin:8px 0}
.bar-row{display:grid;grid-template-columns:minmax(90px,200px) 1fr 84px;gap:8px;align-items:center;font-size:13px;margin:3px 0}
.bar-label{overflow-wrap:anywhere}
.bar-track{height:10px;background:var(--soft);border-radius:3px;overflow:hidden}
.bar-fill{display:block;height:100%;background:var(--accent);opacity:.75}
.bar-num{text-align:right;font-variant-numeric:tabular-nums}
.timeline{border-left:2px solid var(--line);margin:16px 0 0 6px;padding-left:20px}
.tl-item{position:relative;margin-bottom:18px}
.tl-dot{position:absolute;left:-27px;top:8px;width:12px;height:12px;border-radius:50%;background:var(--accent)}
.tl-body h3{margin-top:0}
.score{margin:.3em 0}
.sev-line{margin:.3em 0}
footer{margin-top:56px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:900px){
.layout{display:block}
nav.toc{position:static;max-height:none;padding:16px 0 0;display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center}
nav.toc b{width:100%;margin:0}
nav.toc a{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:13px}
.bar-row{grid-template-columns:90px 1fr 74px}
}
"""

JS = """
(function () {
  try {
    var links = Array.prototype.slice.call(document.querySelectorAll("nav.toc a"));
    var map = {};
    links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    if (!("IntersectionObserver" in window)) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && map[e.target.id]) {
          links.forEach(function (a) { a.classList.remove("on"); });
          map[e.target.id].classList.add("on");
        }
      });
    }, { rootMargin: "-10% 0px -80% 0px" });
    document.querySelectorAll("main section[id]").forEach(function (s) { io.observe(s); });
  } catch (err) { /* 목차 강조는 없어도 문서는 읽힌다 */ }
})();
"""


def build_html() -> str:
    manifest = read_json(DATA_DIR / "manifest.json")
    readme = read_text(DATA_DIR / "README.md")
    datasets = collect_datasets(manifest)
    total_rows = sum(d["count"] for d in datasets)
    integ = check_integrity(datasets)
    reviews = collect_reviews()
    revisions = collect_revisions()
    plan = parse_plan()

    sections = [
        ("overview", "한눈에 보기", sec_overview(manifest, datasets, total_rows, integ)),
        ("why", "왜 만들었나", sec_why(plan)),
        ("case", "케이스 한 건의 구조", sec_case(datasets)),
        ("split", "분할과 누수 방지", sec_split(manifest, datasets, integ, readme)),
        ("build", "어떻게 만들었나", sec_build(manifest, readme)),
        ("history", "검토 이력 타임라인", sec_history(manifest, reviews, revisions)),
        ("drift", "앱 코드가 바뀌면 테스트셋도 바뀌는 이유", sec_drift(manifest, revisions)),
        ("trust", "신뢰할 수 있는 것과 없는 것", sec_trust(manifest, reviews, readme)),
        ("howto", "직접 검토하는 방법", sec_howto()),
        ("next", "다음 단계", sec_next(revisions)),
    ]
    toc = "".join('<a href="#%s">%d. %s</a>' % (sid, i + 1, esc(t)) for i, (sid, t, _) in enumerate(sections))
    body = "".join(
        '<section id="%s"><h2><span class="n">%d.</span>%s</h2>%s</section>' % (sid, i + 1, esc(t), inner)
        for i, (sid, t, inner) in enumerate(sections)
    )
    ver = manifest.get("version") or FAIL_TEXT
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        "<title>품질 데이터셋 가이드</title><style>%s</style></head><body>"
        '<div class="layout"><nav class="toc" aria-label="목차"><b>목차</b>%s</nav>'
        '<main><header class="top"><h1>품질 데이터셋 가이드</h1>'
        "<p>소개팅 AI 서버의 LLM 기능 평가용 합성 테스트셋: 무엇을 왜 어떻게 만들었고 얼마나 믿을 수 있는가 (데이터셋 %s, 총 %d건)</p></header>"
        "%s<footer>이 문서는 <code>scripts/export_dataset_guide_html.py</code>가 <code>evals/quality_datasets</code>를 읽어 생성했습니다. "
        "데이터가 바뀌면 스크립트를 다시 실행하세요.</footer></main></div><script>%s</script></body></html>"
    ) % (CSS, toc, esc(str(ver)), total_rows, body, JS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    out = build_html()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out, encoding="utf-8")
    print("wrote %s (%d bytes)" % (args.output, len(out.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
