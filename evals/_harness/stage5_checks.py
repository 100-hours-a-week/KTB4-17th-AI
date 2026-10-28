"""5단계 코드 채점 — 판정 방법이 '코드'인 실패 유형과, 정답 기준과 코드로 비교할 수 있는 유형.

입력: 골드셋 케이스(case) + 실행 결과(sample: id, input, output_raw, output_parsed, latency_ms, error …).
출력: {F-xx: {"pass": True | False | None, "reason": str}} — None 은 이 케이스에 해당 없음.

2단계 자동 검사(auto_checks.py)를 요구사항 단위로 돌린 뒤 유형으로 묶는다. 3단계·4단계 결정을 반영한 차이:
- 카톡 v2 '발화에 없는 표현'(F-02): 어미 축약을 맞춘 뒤 비교한다 (3단계 결정).
- 온보딩 페르소나 추출 F-05: 답하지 않은 차원의 50점은 허용, 50이 아닌 점수만 실패 (결정 ⑤).
- 시뮬레이션 v1 한 줄 생성: 줄 실패 사유(invalid_json / guardrail / closing_failed)로 유형을 나눈다.
- 대본 생성·재생성: 라벨 a/b 혼동·자기 이름 호칭·상대 fact ID 를 따로 센다 (요구사항이 겹쳐 있어서).
- 종료 메시지 F-01(검사기 오탐): 실패(FAILED)인데 원문에 질문·재만남 여지가 없으면 오탐.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import auto_checks as A  # noqa: E402
import stage3_types as S  # noqa: E402

RES = {"pass": None, "reason": "해당 없음"}


def R(ok, reason: str) -> dict:
    return {"pass": ok, "reason": reason}


def code_types(slug: str) -> list[dict]:
    return [t for t in S.D[slug]["types"] if "코드" in t["method"]]


def by_req(checks: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in checks:
        out.setdefault(c["req"], []).append(c)
    return out


def from_reqs(checks: list[dict], reqs: list[str], prefer: str = "raw") -> dict:
    """요구사항 목록 중 하나라도 실패하면 Fail. 모델 원문(raw) 검사를 우선 쓰고, 없으면 최종(final)."""
    rows = [c for c in checks if c["req"] in reqs]
    if not rows:
        return RES
    pref = [c for c in rows if c["on"] == prefer] or rows
    bad = [c for c in pref if c["pass"] is False]
    if bad:
        return R(False, "; ".join(f"{c['req']} {c['reason']}" for c in bad)[:300])
    if all(c["pass"] is None for c in pref):
        return RES
    return R(True, "검사 통과")


# ── 기능별 보정 ────────────────────────────────────────────────


def jamo(t: str) -> str:
    """한글 음절을 초·중·종성으로 풀어 쓴다 ('볼게' → 'ㅂㅗㄹㄱㅔ'). 어미 '~ㄹ게' 비교용."""
    cho = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
    jung = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
    jong = ["", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"]
    out = []
    for ch in t:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out.append(cho[c // 588] + jung[(c % 588) // 28] + jong[c % 28])
        else:
            out.append(ch)
    return "".join(out)


def observed(core: str, text: str, ending: bool) -> bool:
    flat = re.sub(r"[\s.,!?~ㅋㅎ]+", "", text)
    if not ending:
        return core in text or re.sub(r"[\s.,!?~ㅋㅎ]+", "", core) in flat
    # 어미는 낱말 끝과 자모 단위로 맞춘다 ('~ㄹ게' ↔ '맞춰볼게'). 낱말 중간에 든 글자('다음엔'의 '음')는 세지 않는다.
    c = re.sub(r"\s+", "", core)
    forms = {jamo(c)}
    if len(c) >= 2 and "가" <= c[0] <= "힣":  # '~할래요'·'~할 것 같아': 첫 음절은 대표 동사로 보고 받침만 남긴다
        forms.add(jamo(c)[2:] if len(jamo(c[0])) == 3 else jamo(c[1:]))
    if re.fullmatch(r"[ㄱ-ㅎㅏ-ㅣ\s~]+", core):  # 'ㅋㅋ'·'ㅎㅎ' 같은 자모 표현은 글자 그대로 찾는다
        return c in re.sub(r"\s+", "", text)
    words = [re.sub(r"[ㅋㅎㅠㅜ]+$", "", w) for w in re.findall(r"[가-힣ㄱ-ㅎㅏ-ㅣ]+", text)]  # '좋아ㅋㅋ' → '좋아'
    # 존댓말·반말 분류는 평가 제외(사용자 결정) — 끝의 '요/용'을 뗀 형태도 같은 어미로 본다 ('좋아요' ↔ '~아')
    words += [w[:-1] for w in words if len(w) > 1 and w[-1] in "요용"]
    jflat = jamo(re.sub(r"[ㅋㅎㅠㅜ]+", "", flat))
    if c in (
        "함",
        "음",
        "임",
        "슴",
    ):  # 명사형 '~함/~음'은 받침 ㅁ 으로 끝나는 발화('아님', '했음')를 줄여 적은 것으로 본다 (3단계 기준)
        return any(jamo(w).endswith("ㅁ") for w in words if w and "가" <= w[-1] <= "힣")
    if " " in core.strip():  # 여러 낱말 어미는 붙여 쓴 발화에서 찾는다
        return any(f in jflat for f in forms)
    return any(jamo(w).endswith(f) for w in words for f in forms if f)


PRONOUNS = {"나", "너", "저", "제", "내", "우리", "저희"}
# 말투 표현이 아닌 낱말 (3단계 기준: '나' 같은 낱말은 Fail). 부사 '좀', 날짜·시간 명사 하나뿐인 항목.
NOT_STYLE = {
    "interjections": {"좀", "그", "이", "저기"},
    "frequent_phrases": {"나", "너", "저", "내일", "오늘", "어제", "주말", "지금", "요즘"},
}
# '같아'·'좋아'처럼 형용사 어간+아 는 호칭이 아니다 ('같이'가 있어도 이름으로 보지 않는다)
ADJ_STEMS = {
    "같",
    "좋",
    "많",
    "싫",
    "괜찮",
    "작",
    "높",
    "낮",
    "넓",
    "밝",
    "맑",
    "적",
    "짧",
    "길",
    "깊",
    "얕",
    "가",
    "봐",
    "해",
}
VOCATIVE = re.compile(r"(?<![가-힣])([가-힣]{1,2}?)(?:아|야)(?![가-힣])")


def name_calls(style: dict, sample: dict) -> list[str]:
    """말투 항목 안의 사람 이름 호칭('준아'). 이름 = 대화 참여자 + 발화에 '○○이/가/는/씨/님'으로 나온 낱말."""
    text = sample["input"].get("text", "")
    names = set(re.findall(r", ([가-힣]{1,4}) : ", text)) | set(re.findall(r"(?:^|\n)([가-힣]{1,4}) : ", text))
    body = " ".join(re.findall(r" : (.*)", text))
    for m in VOCATIVE.finditer(body):
        x = m.group(1)
        if x not in ADJ_STEMS and re.search(rf"(?<![가-힣]){x}(?:이|가|는|씨|님)(?:\s|$)", body):
            names.add(x)
    out = []
    for k in ("frequent_phrases", "endings", "interjections", "slang"):
        for p in style.get(k) or []:
            if any(m.group(1) in names for m in VOCATIVE.finditer(p)):
                out.append(p)
    return out


def kakao_style(sample: dict) -> tuple[list[str], list[str]]:
    """(본인 발화에 없는 표현, 그중 상대만 쓴 표현)."""
    d = A.as_json(sample.get("output_raw"))
    if not isinstance(d, dict):
        return [], []
    style = d.get("style") or {}
    mine, other = A.split_kakao(sample)
    missing, only_other = [], []
    for k in ("frequent_phrases", "endings", "interjections", "slang"):
        for p in style.get(k) or []:
            core = re.sub(r"\(.*?\)", "", p).strip().lstrip("~").strip()
            if k == "endings":  # '~요 ㅎㅎ' → '요' (웃음은 따로 본다)
                core = re.sub(r"[\sㅋㅎ]+$", "", core).strip() or core
            if not core or core in NOT_STYLE.get(k, ()) or (k != "frequent_phrases" and core in PRONOUNS):
                missing.append(p)  # '~' 한 글자, 대명사, 일반 낱말은 말투 표현이 아니다
                continue
            if observed(core, mine, k == "endings"):
                continue
            missing.append(p)
            if observed(core, other, k == "endings"):
                only_other.append(p)
    return missing, only_other


def f05_unobserved(sample: dict) -> dict:
    """어미 축약을 맞춘 뒤 비교 — '~함', '~아요'처럼 줄여 적은 어미는 본인 발화 어딘가의 끝말이면 통과."""
    if not isinstance(A.as_json(sample.get("output_raw")), dict):
        return RES
    missing, _ = kakao_style(sample)
    return R(not missing, f"본인 발화에 없는 표현 {missing[:4]}" if missing else "모든 표현이 본인 발화에 있음")


def kakao_partner_leak(sample: dict) -> dict:
    if not isinstance(A.as_json(sample.get("output_raw")), dict):
        return RES
    _, only_other = kakao_style(sample)
    calls = name_calls(A.as_json(sample.get("output_raw")).get("style") or {}, sample)
    bad = only_other + [c for c in calls if c not in only_other]
    return R(not bad, f"상대만 쓴 표현·이름 호칭 {bad[:4]}" if bad else "상대 표현 없음")


def f03_unanswered(checks: list[dict]) -> dict:
    def dims(req):
        return {
            d for c in checks if c["req"] == req and c["pass"] is False for d in re.findall(r"'(\w+)'", c["reason"])
        }

    non50 = dims("R-03") - dims("R-02")
    if not any(c["req"] == "R-03" for c in checks):
        return RES
    return R(not non50, f"답하지 않은 차원에 50이 아닌 점수 {sorted(non50)}" if non50 else "없음(50점은 허용)")


def f10_line(sample: dict) -> dict[str, dict]:
    err = sample.get("error") or ""
    out = {
        "F-01": R("invalid_json" not in err, err or "정상 JSON"),
        "F-02": R("guardrail" not in err, err or "가드레일 통과")
        if sample["input"].get("nickname_a") == sample["input"].get("nickname_b")
        else RES,
        "F-03": R("closing_failed" not in err, err or "마무리 생성")
        if sample["input"].get("index") == sample["input"].get("turns", 0) * 2 - 1
        else RES,
    }
    if not err:
        inp = dict(sample["input"])
        inp.setdefault("speaker", "a" if inp.get("index", 0) % 2 == 0 else "b")  # 짝수 줄은 a
        inp.setdefault("previous", [])
        checks = A.f10({**sample, "input": inp})
        out["F-05"] = from_reqs(checks, ["R-11", "R-12"], prefer="final")
    else:
        out["F-05"] = RES
    return out


def script_flags(sample: dict) -> dict[str, list]:
    """대본 생성·재생성 원문에서 줄 번호별 문제를 직접 센다."""
    lines, _ = A.script_lines(sample.get("output_raw") or "")
    a, b = sample["input"]["nickname_a"], sample["input"]["nickname_b"]
    same = sample["input"].get("same_nickname")
    n = sample["input"]["turns"] * 2
    label, selfcall, wrongref, early, order, ownclaim = [], [], [], [], [], []
    for k, (num, _sp, text) in enumerate(lines):
        body = re.sub(r"\s*\[[^\]]*\]\s*$", "", text)
        refs = re.findall(r"\b([AB])-f\d+", text[len(body) :])
        me = _sp if (not same and _sp in (a, b)) else (a if k % 2 == 0 else b)
        if same and re.search(r"(?<![A-Za-z])[ab](?![A-Za-z])", body):  # '안녕 b', '저는 a예요'
            label.append(num)
        # '○○야'는 '(나) 준호야' 같은 자기소개일 수 있어 세지 않는다. '○○님/씨' 호칭과 문장 첫머리 3인칭('수아는 …')만 센다.
        # 같은 닉네임 쌍은 상대를 부른 것과 구분할 수 없어 세지 않는다.
        if not same and (
            re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(me)}\s?(님|씨)", body)
            or re.search(rf"(?<![가-힣]){re.escape(me)}(?:는|가)\s", body)  # '수아는 주말에…' 자기 3인칭
            or re.search(
                rf"(?:안녕|반가워)\S*\s*{re.escape(me)}(?:야|아)(?![가-힣])", body
            )  # '안녕 민수야'(자기 이름으로 인사)
        ):
            selfcall.append(num)
        # 화자는 줄 앞 이름으로 정한다 (같은 사람이 두 줄 연속 말하면 순서로는 틀린다). 같은 닉네임 쌍은 a/b 라벨.
        la_, lb_ = ("a", "b") if same else (a, b)
        own = "A" if _sp == la_ else "B" if _sp == lb_ else ("A" if k % 2 == 0 else "B")
        # '베이킹하시던 거 …하시는 거예요'처럼 상대 얘기를 높여 묻거나 받아 주는 줄은 자기 얘기가 아니다
        about_partner = re.search(
            r"(?:하|으|이)?시(?:던|는|나|죠|네|어|잖|고|면|ㄴ|군)|(?:님|씨)(?:은|는|이|도|께)", body
        )
        if [r for r in refs if r != own]:
            wrongref.append(num)  # 대본 생성: 상대 ID 를 붙인 것 자체가 실패
            if not about_partner:
                ownclaim.append(num)  # 대본 재생성: 상대 fact 를 자기 얘기로 말한 줄만
        if k < n - 2 and A.contains_farewell(body):
            early.append(num)
    la, lb = ("a", "b") if same else (a, b)
    order = [sp for _, sp, _ in lines] == [la if k % 2 == 0 else lb for k in range(len(lines))]
    return {
        "label": label,
        "selfcall": selfcall,
        "wrongref": wrongref,
        "ownclaim": ownclaim,
        "early": early,
        "order_ok": order,
        "n": len(lines),
    }


def f12_types(sample: dict, slug: str) -> dict[str, dict]:
    if not sample.get("output_raw"):
        return {}
    f = script_flags(sample)
    n_want = sample["input"]["turns"] * 2
    out = {
        "F-01": R(not f["label"], f"라벨을 이름처럼 쓴 줄 {f['label']}"),
        "F-02": R(not f["selfcall"], f"자기 닉네임 호칭 줄 {f['selfcall']}"),
    }
    if slug == "f12":
        out |= {
            "F-03": R(not f["wrongref"], f"상대 fact ID 를 붙인 줄 {f['wrongref']}"),
            "F-08": R(
                f["order_ok"] and f["n"] == n_want, f"줄 {f['n']}/{n_want}, 순서 {'정상' if f['order_ok'] else '깨짐'}"
            ),
            "F-09": R(not f["early"], f"마지막 왕복 전 작별 줄 {f['early']}"),
        }
    else:  # f13: F-03 = 상대 fact 를 자기 것으로 (fact ID 기준)
        out["F-03"] = R(not f["ownclaim"], f"상대 fact 를 자기 얘기로 말한 줄 {f['ownclaim']}")
    return out


def f18_false_positive(sample: dict) -> dict:
    fin = sample.get("output_parsed") or {}
    if fin.get("status") != "FAILED":
        return RES
    d = A.as_json(sample.get("output_raw"))
    raw = (d or {}).get("ai_response") if isinstance(d, dict) else None
    if not raw:
        return RES
    # '친구로 지내자는 말씀은 감사하지만'처럼 상대 제안을 인용한 거절은 정상 문장이다
    real = "?" in raw or re.search(r"다음에|또 (연락|봬|만나)|나중에|시간 되면|친구로 지내(?:요|자)(?!는)", raw)
    return R(bool(real), "원문에 실제 질문·재만남 여지가 있음" if real else f"원문은 정상인데 FAILED 처리: {raw[:80]}")


# ── 정답 기준과 코드로 비교 ─────────────────────────────────────────


def f02_compare(case: dict, sample: dict) -> dict[str, dict]:
    exp = case["oracle"]["expect"]
    d = A.as_json(sample.get("output_raw")) or sample.get("output_parsed") or {}
    if not isinstance(d, dict):
        return {"F-01": R(False, "출력 없음"), "F-02": R(False, "출력 없음")}
    prim, off = list(d.get("primary") or []), bool(d.get("off_topic"))
    allt = prim + list(d.get("secondary") or [])
    out = {}
    if exp.get("off_topic"):  # 관련 없음이 정답이면 태그 문제는 F-02 하나로만 센다
        out["F-02"] = R(off and not allt, f"off_topic 기대 True / 출력 {off}, 태그 {allt}")
        out["F-01"] = RES
        return out
    if "off_topic" in exp:
        out["F-02"] = R(not (exp["off_topic"] and not off), f"off_topic 기대 {exp['off_topic']} / 출력 {off}")
    none_of = exp.get("primary_none_of") or []
    any_of = exp.get("primary_any_of") or []
    bad = allt if "*" in none_of else [p for p in allt if p in none_of]  # 보조 태그의 오태깅도 실패 (2단계 메모 기준)
    miss = bool(any_of) and not set(any_of) & set(prim)
    out["F-01"] = R(
        not bad and not miss,
        f"primary {prim} — 금지 {bad}"
        if bad
        else (f"primary {prim} — 필요 {any_of} 없음" if miss else f"primary {prim}"),
    )
    return out


def f07_compare(case: dict, sample: dict) -> dict[str, dict]:
    exp = case["oracle"]["expect"].get("allow")
    d = A.as_json(sample.get("output_raw"))
    got = d.get("allow") if isinstance(d, dict) else None
    if exp is None:
        return {}
    return {"F-01": R(not (exp is False and got is True), f"allow 기대 {exp} / 출력 {got}")}


def f09_compare(case: dict, sample: dict) -> dict[str, dict]:
    if "F-04" not in case["target_failures"]:
        return {}
    d = A.as_json(sample.get("output_raw")) or {}
    deletes = [x for x in d.get("decisions") or [] if x.get("action") == "DELETE"]
    return {
        "F-04": R(
            not deletes, f"삭제 요청에 DELETE {[x.get('target_id') for x in deletes]}" if deletes else "DELETE 없음"
        )
    }


def f14_compare(case: dict, sample: dict) -> dict[str, dict]:
    exp = case["oracle"]["expect"]
    if "expected_issues" not in exp and "must_not_flag_lines" not in exp:
        return {}
    args = A.calls_args(sample.get("output_raw"), "submit_review")
    if not isinstance(args, dict):
        return {k: R(False, "심사 출력 형식 오류") for k in ("F-01", "F-02", "F-03", "F-06")}
    issues = [x for x in args.get("issues") or [] if x.get("type") != "judge_tone"]  # judge_tone 은 채점 안 함
    mix = {x.get("i") for x in issues if x.get("type") == "judge_mixup"}
    unr = {x.get("i") for x in issues if x.get("type") == "judge_unresponsive"}
    must_not = set(exp.get("must_not_flag_lines") or [])
    want_mix = {x["i"] for x in exp.get("expected_issues") or [] if x["type"] == "judge_mixup"}
    missed = sorted(want_mix - mix)
    tg = set(case["target_failures"])
    out = {
        "F-01": R(not (mix & must_not), f"혼동 오탐 줄 {sorted(mix & must_not)}"),
        "F-03": R(not (unr & must_not), f"무반응 오탐 줄 {sorted(unr & must_not)}"),
    }
    if "F-04" in tg:  # 모순을 화자 혼동으로 분류한 경우는 F-04 로만 센다
        out["F-04"] = R(not (mix & must_not), f"모순을 화자 혼동으로 분류한 줄 {sorted(mix & must_not)}")
        out["F-01"] = RES
    if want_mix:
        key = "F-06" if "F-06" in tg and "F-02" not in tg else "F-02"
        out[key] = R(not missed, f"놓친 화자 혼동 줄 {missed}" if missed else "모두 잡음")
    return out


def f16_quotes(sample: dict) -> dict:
    args = A.calls_args(sample.get("output_raw"), "submit_report")
    if not isinstance(args, dict):
        return RES
    lines = {
        int(m.group(1)): m.group(3)
        for m in (A.SCRIPT_LINE.match(x) for x in (sample["input"].get("transcript") or "").splitlines())
        if m
    }
    norm = lambda x: re.sub(r"\s+", "", x or "")  # noqa: E731
    unlabel = lambda q: re.sub(r"^\s*[^:：\s]{1,12}\s*[:：]\s*", "", q or "")  # noqa: E731
    bad = []
    for h in args.get("highlights") or []:
        i = h.get("turn_index")
        parts = [p for p in re.split(r"\s+/\s+", h.get("quote") or "") if p.strip()]
        ok = all(norm(unlabel(p)) in norm(lines.get(i + k, "")) for k, p in enumerate(parts))
        if not ok:
            bad.append((i, (h.get("quote") or "")[:30]))
    return R(not bad, f"대화록 원문과 다른 인용 {bad[:3]}" if bad else "인용 일치 (화자 이름·이어 붙인 줄 허용)")


COMPARE = {"f02": f02_compare, "f07": f07_compare, "f09": f09_compare, "f14": f14_compare}


# ── 진입점 ───────────────────────────────────────────────────


def run_checks(slug: str, case: dict, sample: dict) -> dict[str, dict]:
    fn = A.CHECKS[next(k for k in A.CHECKS if k.startswith(slug + "-"))]
    try:
        checks = fn(sample) if not (slug == "f10") else []
    except Exception as e:  # noqa: BLE001
        checks = [{"req": "CHECK", "on": "final", "pass": None, "reason": f"검사 오류 {type(e).__name__}: {e}"}]
    out: dict[str, dict] = {}
    special: dict[str, dict] = {}
    if slug == "f03":
        special["F-05"] = f03_unanswered(checks)
    if slug in ("f04", "f05"):  # 어미는 자모 단위로 맞춘다 (3단계 결정: 어미 축약을 실패로 잡지 않음)
        special["F-02"] = f05_unobserved(sample) if sample.get("output_raw") else RES
    if slug == "f05":
        special["F-01"] = kakao_partner_leak(sample) if sample.get("output_raw") else RES
    if slug == "f10":
        special |= f10_line(sample)
    if slug in ("f12", "f13"):
        special |= f12_types(sample, slug)
    if slug == "f01":  # 요구사항 R-08 은 '물음표 정확히 1개'라 0개도 실패로 센다 — 유형 F-06 은 2개 이상만 실패
        q8 = [c for c in checks if c["req"] == "R-08" and c["on"] == "raw"] or [c for c in checks if c["req"] == "R-08"]
        if q8:
            n = int(re.search(r"(\d+)", q8[0]["reason"]).group(1))
            special["F-06"] = R(n <= 1, f"물음표 {n}개")
    if slug == "f18":
        special["F-01"] = f18_false_positive(sample)
    if slug == "f16":
        special["F-03"] = f16_quotes(sample)
    for t in code_types(slug):
        reqs = re.findall(r"R-\d+", t["reqs"])
        out[t["id"]] = special.get(t["id"]) or from_reqs(checks, reqs)
    if slug in COMPARE:
        out |= COMPARE[slug](case, sample)
    if sample.get("error") and slug != "f10":
        out["_error"] = R(False, sample["error"][:300])
    return out
