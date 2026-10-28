"""2단계 자동 검사 — 1단계에서 '코드' 판정으로 정한 요구사항만 기계적으로 검사한다. 품질 판단은 하지 않는다.

각 샘플에 auto_checks = [{"req", "on": "raw"|"final", "pass": bool|None, "reason"}] 를 붙인다.
pass=None 은 '이 샘플에는 해당 없음'. on=raw 는 모델 원문(모델 점수), on=final 은 사용자에게 나간 결과(제품 점수).
"""

from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher

from common import EVALS, read_jsonl, write_jsonl

sys.path.insert(0, str(EVALS.parent))
from app.core.guardrail.rules import (  # noqa: E402
    EMOJI_REGEX,
    IDENTITY_REGEX,
    PAST_MEETING_REGEX,
    UNSAID_TRIGGER_REGEX,
    contains_profanity,
)
from app.features.chat_end.agents import FALLBACK_SCRIPTS, is_closing_message, is_daily_greeting  # noqa: E402
from app.features.persona.schemas import ALL_DIMENSIONS, AREAS, SCORED  # noqa: E402
from app.features.simulation_migration.order import contains_farewell, repeats_previous  # noqa: E402

LF = json.loads((EVALS / "_harness/runs/lf_eval_cache.json").read_text())
LEAK = ["## 절대 하지 않는 것", "[CORRECTION NOTICE]", "GUARDRAIL", "Chain of Verification"]
PII = re.compile(r"01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}|[\w.+-]+@[\w-]+\.\w+|https?://|www\.")
SPECULATION = re.compile(r"겠네요|수 있겠|수도 있겠|수도 있어요|것 같아요|것 같네요|듯해요|듯합니다")
INFORMAL_END = re.compile(r"(?:어|아|야|지|해|냐|니|자|게|래|거든|잖아|네)[.!?~ㅋㅎ\s]*$")
FORMAL_END = re.compile(r"(?:요|니다|죠|까)[.!?~ㅋㅎ\s]*$")


def C(req, on, ok, reason=""):
    return {"req": req, "on": on, "pass": ok, "reason": reason}


def sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+|\n+", (t or "").strip()) if s.strip()]


def chat_sentences(t: str) -> list[str]:
    """메신저 문장 수. ㅋㅋ·ㅎㅎ 뒤에 말이 이어지면 거기서 끊고, 웃음·부호만 있는 조각은 문장으로 세지 않는다."""
    parts = re.split(r"(?<=[.?!])\s+|\n+|(?<=[ㅋㅎㅠㅜ]{2})\s+(?=[가-힣A-Za-z0-9])", (t or "").strip())
    return [x for x in parts if re.search(r"[가-힣A-Za-z0-9]", x or "")]


def qcount(t: str) -> int:
    return (t or "").count("?") + (t or "").count("？")


def as_json(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw)
    a, b = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[a : b + 1] if a != -1 and b > a else s)
    except Exception:  # noqa: BLE001
        return None


def lf_input(name: str, raw: str):
    for g in LF.get(name, []):
        out = g["output"]
        txt = out.get("content") if isinstance(out, dict) else out
        if txt == raw or json.dumps(out, ensure_ascii=False) == raw:
            msgs = g["input"] if isinstance(g["input"], list) else []
            us = [m.get("content", "") for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
            return us[-1] if us else ""
    return None


def calls_args(raw, tool: str):
    d = as_json(raw)
    if not isinstance(d, dict):
        return None
    for c in d.get("calls") or []:
        if c.get("name") == tool:
            return c.get("arguments") or {}
    return d if tool in ("submit_review", "submit_patches", "submit_report") and "calls" not in d else None


def latency(req, s, limit_ms, key="latency_ms"):
    v = s.get(key)
    return C(req, "final", None if v is None else v <= limit_ms, f"{v}ms ≤ {limit_ms}ms")


# ── 기능별 ────────────────────────────────────────────────
def f01(s):
    out = []
    for on, t in (("raw", s.get("output_raw")), ("final", s.get("output_parsed"))):
        if t is None:
            continue
        out += [
            C("R-01", on, not PAST_MEETING_REGEX.search(t), "과거 만남 표현"),
            C("R-02", on, not UNSAID_TRIGGER_REGEX.search(t), "'하셨잖아요'류 (근거 대조는 Judge)"),
            C("R-04", on, not any(k in t for k in LEAK), "시스템 프롬프트 키워드"),
            C("R-08", on, qcount(t) == 1, f"물음표 {qcount(t)}개"),
            C("R-15", on, not EMOJI_REGEX.search(t), "이모지"),
            C("R-17", on, len(sentences(t)) <= 3, f"{len(sentences(t))}문장"),
        ]
    prev = (s["input"]["history"][-1]["q"] if s["input"]["history"] else "") or ""
    cur = s.get("output_parsed") or ""
    out.append(
        C(
            "R-18",
            "final",
            None if not prev else not ("편이에요?" in prev and "편이에요?" in cur),
            "직전 하루 질문과 연속",
        )
    )
    out.append(C("R-22", "final", not s.get("fallback_used"), "시드 폴백"))
    out.append(latency("R-24", s, 15000))
    return out


def f02(s):
    d = as_json(s.get("output_raw"))
    out = [C("R-01", "raw", isinstance(d, dict) and {"primary", "secondary", "off_topic"} <= set(d), "JSON 키")]
    if isinstance(d, dict):
        dims = [x for x in (d.get("primary") or []) + (d.get("secondary") or [])]
        bad = [x for x in dims if x not in ALL_DIMENSIONS]
        out.append(C("R-02", "raw", not bad, f"정의 밖 차원 {bad}"))
        if d.get("off_topic"):
            out.append(C("R-06", "raw", not d.get("primary") and not d.get("secondary"), "off_topic 인데 차원 있음"))
        out.append(C("R-10", "raw", not re.search(r"\d", json.dumps(d.get("primary"))), "점수 숫자"))
    out.append(C("R-12", "final", s.get("output_parsed") is not None, "태깅 실패(None)"))
    out.append(latency("R-13", s, 15000, "raw_latency_ms"))
    return out


def f03(s):
    d = as_json(s.get("output_raw"))
    fin = s.get("output_parsed") or {}
    out = [C("R-01", "raw", isinstance(d, dict), "JSON 파싱")]
    if isinstance(d, dict):
        bad_rng = [k for k in SCORED if k in d and not (isinstance(d[k], int) and 0 <= d[k] <= 100)]
        out.append(C("R-01", "raw", not bad_rng, f"점수 범위·정수 {bad_rng}"))
        known = set(ALL_DIMENSIONS) | {"narrative", "summaries"}
        ghost = [k for k in d if k not in known]
        out.append(C("R-04", "raw", not ghost, f"정의 밖 키 {ghost}"))
        fifty = [k for k in SCORED if d.get(k) == 50]
        out.append(C("R-02", "raw", not fifty, f"50점 기재 {fifty}"))
        dropped = [k for k in SCORED if d.get(k) is not None and (fin.get("scores") or {}).get(k) is None]
        out.append(C("R-03", "raw", not dropped, f"답하지 않은 차원 점수(서비스가 지움) {dropped}"))
        n = d.get("narrative") or {}
        if n:
            hl, traits = n.get("headline", ""), n.get("traits") or []
            out.append(
                C(
                    "R-09",
                    "raw",
                    len(hl) <= 40 and 3 <= len(traits) <= 5 and all(len(t) <= 30 for t in traits),
                    f"headline {len(hl)}자, traits {len(traits)}개 최대 {max([len(t) for t in traits] or [0])}자",
                )
            )
            txt = " ".join([hl, n.get("body", ""), *traits])
            out.append(C("R-11", "raw", not SPECULATION.search(txt), "짐작 어미"))
            out.append(C("R-13", "raw", not re.search(r"\d|당신|시군요|하셨네요", txt), "숫자·당신·대화체"))
            out.append(C("R-14", "raw", not re.search(r"회피형|불안형|안정형", txt), "유형명"))
            out.append(C("R-15", "raw", not contains_profanity(txt), "욕설"))
        sm = d.get("summaries") or []
        if sm:
            out.append(
                C(
                    "R-09",
                    "raw",
                    all(len(x.get("title", "")) <= 20 and len(x.get("content", "")) <= 40 for x in sm),
                    "summary title 20자·content 40자",
                )
            )
            cats = [x.get("category") for x in sm]
            out.append(
                C("R-16", "raw", all(c in AREAS for c in cats) and len(cats) == len(set(cats)), f"category {cats}")
            )
    out.append(C("R-18", "final", fin.get("source") != "fallback" if fin else None, "규칙 초안 폴백"))
    out.append(latency("R-20", s, 15000, "raw_latency_ms"))
    return out


def phrases_check(style: dict, my_text: str, other_text: str, req_real, req_pii, req_len, req_paren, req_other):
    out = []
    items = [p for k in ("frequent_phrases", "endings", "interjections", "slang") for p in (style.get(k) or [])]
    if not items:
        return out
    core = [re.sub(r"\(.*?\)", "", p).strip().lstrip("~").strip() for p in items]
    missing = [p for p, c in zip(items, core, strict=True) if c and c not in my_text]
    out.append(C(req_real, "raw", not missing, f"내 발화에 없는 표현 {missing[:4]}"))
    out.append(C(req_pii, "raw", not [p for p in items if PII.search(p) or re.search(r"\d", p)], "숫자·연락처·링크"))
    out.append(C(req_len, "raw", all(len(p) <= 30 for p in items), "30자"))
    out.append(C(req_paren, "raw", not [p for p in items if re.search(r"\(.*\d.*\)", p)], "괄호 속 숫자"))
    only_other = [p for p, c in zip(items, core, strict=True) if c and c not in my_text and c in other_text]
    out.append(C(req_other, "raw", not only_other, f"상대만 쓴 표현 {only_other[:3]}"))
    return out


def split_kakao(s):
    from app.v2.persona_extraction.parsers import parse_kakao

    lines = parse_kakao(s["input"]["text"])
    me = s["input"]["speaker_name"]
    return " ".join(x.text for x in lines if x.speaker == me), " ".join(x.text for x in lines if x.speaker != me)


def f04(s):
    d = as_json(s.get("output_raw"))
    out = [C("R-01", "raw", isinstance(d, dict), "JSON")]
    if not isinstance(d, dict):
        return out + [latency("R-16", s, 30000)]
    style = d.get("style") or {}
    mine, other = split_kakao(s)
    out += phrases_check(style, mine, other, "R-02", "R-04", "R-05", "R-07", "R-13")
    vals = json.dumps(style, ensure_ascii=False)
    out.append(C("R-03", "raw", not re.search(r'"\.\.\."|"~\.\.\."|메시지에서 확인한|한 문장"', vals), "자리표시 복사"))
    desc = style.get("punctuation") or ""
    for k, mark in (("물음표", "?"), ("느낌표", "!"), ("물결", "~")):
        if k in desc and not re.search(rf"{k}[^.,]*?(않|없|안 |드물|거의)", desc):  # "느낌표는 거의 안 씀"은 제외
            out.append(C("R-08", "raw", mark in mine, f"{k} 사용으로 서술했는데 발화에 없음"))
    extra = [k for k in d if k not in ("style", "disclosure", "positivity", "openness", "assurances")]
    in_style = [k for k in ("disclosure", "positivity", "openness", "assurances") if k in style]
    out.append(C("R-10", "raw", not extra and not in_style, f"점수 키 위치·정의 밖 {extra + in_style}"))
    out.append(
        C(
            "R-12",
            "raw",
            all(len(style.get(k) or []) <= 10 for k in ("frequent_phrases", "endings", "interjections", "slang")),
            "10개 이하",
        )
    )
    out.append(C("R-15", "final", s["output_parsed"]["job_status"] == "succeeded", "작업 상태"))
    out.append(latency("R-16", s, 30000))
    return out


def f05(s):
    d = as_json(s.get("output_raw"))
    out = [C("R-01", "raw", isinstance(d, dict) and "style" in d, "JSON·style 키")]
    if not isinstance(d, dict):
        return out + [latency("R-17", s, 30000)]
    n = s["output_parsed"].get("analyzed_count") or 0
    scores = d.get("scores") or {}
    bad_ev = {
        k: [e for e in (v.get("evidence") or []) if not (isinstance(e, int) and 1 <= e <= n)]
        for k, v in scores.items()
        if isinstance(v, dict)
    }
    bad_ev = {k: v for k, v in bad_ev.items() if v}
    out.append(C("R-02", "raw", not bad_ev, f"범위(1~{n}) 밖 근거 {bad_ev}"))
    no_ev = [k for k, v in scores.items() if isinstance(v, dict) and not v.get("evidence")]
    out.append(C("R-05", "raw", not no_ev, f"근거 없는 점수 {no_ev}"))
    ghost = [k for k in scores if k not in SCORED]
    out.append(C("R-07", "raw", not ghost, f"정의 밖 차원 {ghost}"))
    copy = [
        k
        for k, v in scores.items()
        if isinstance(v, dict) and (v.get("evidence") == [2, 19, 57] or (k == "disclosure" and v.get("value") == 72))
    ]
    out.append(C("R-13", "raw", not copy, f"예시값 복사 {copy}"))
    mine, other = split_kakao(s)
    out += phrases_check(d.get("style") or {}, mine, other, "R-11", "R-12", "R-11", "R-12", "R-08")
    out.append(C("R-16", "final", s["output_parsed"]["job_status"] == "succeeded", "작업 상태"))
    out.append(latency("R-17", s, 30000))
    return out


def f06(s):
    out = []
    for on, t in (("raw", s.get("output_raw")), ("final", s.get("output_parsed"))):
        if not t:
            continue
        out += [
            C(
                "R-01",
                on,
                not IDENTITY_REGEX.search(t) and not re.search(r"\bAI\b|인공지능|챗봇|언어 ?모델", t),
                "정체 자백 단어",
            ),
            C("R-03", on, not PAST_MEETING_REGEX.search(t), "과거 만남"),
            C("R-07", on, not any(k in t for k in LEAK), "프롬프트 키워드"),
            C("R-09", on, 1 <= len(chat_sentences(t)) <= 3, f"{len(chat_sentences(t))}문장"),
            C("R-10", on, qcount(t) <= 1, f"물음표 {qcount(t)}개"),
            C("R-17", on, not EMOJI_REGEX.search(t), "이모지"),
        ]
    mode = s["input"].get("speech_mode") or ""
    fin = s.get("output_parsed") or ""
    if s.get("version") == "v2" and fin:
        informal = [x for x in sentences(fin) if INFORMAL_END.search(x) and not FORMAL_END.search(x)]
        if mode.endswith("casual"):
            out.append(
                C("R-15", "final", not [x for x in sentences(fin) if FORMAL_END.search(x)], "허락 후 존댓말 남음")
            )
        else:
            out.append(C("R-14", "final", not informal, f"허락 전 반말 문장 {informal[:2]}"))
    v = (s.get("validation") or {}).get("violations") or []
    out.append(
        C(
            "R-21",
            "final",
            not ("RULE-IDENTITY-SELF" in [x.get("rule_id") for x in v] and IDENTITY_REGEX.search(fin)),
            "자백 교체",
        )
    )
    out.append(C("R-20", "final", not s.get("fallback_used"), "폴백 문장"))
    if s.get("streamed_text") is not None:
        out.append(
            C(
                "R-22",
                "final",
                s.get("ttft_ms") is not None and s["ttft_ms"] <= 2000,
                f"TTFT {s.get('ttft_ms')}ms ≤ 2000",
            )
        )
        status = (s.get("validation") or {}).get("status")
        out.append(
            C(
                "R-22",
                "final",
                None if status in (None, "PASS", "WARN") else False,
                f"가드레일 {status} 인데 스트림으로 원문 노출",
            )
        )
    out.append(latency("R-22", s, 15000))
    return out


def f07(s):
    d = as_json(s.get("output_raw"))
    return [
        C(
            "R-06",
            "raw",
            isinstance(d, dict) and isinstance(d.get("allow"), bool) and len(d) == 1,
            f"출력 {s.get('output_raw')}",
        ),
        latency("R-08", s, 10000, "raw_latency_ms"),
    ]


def f08(s):
    d = as_json(s.get("output_raw"))
    out = [C("R-13", "raw", isinstance(d, dict), "JSON")]
    if not isinstance(d, dict):
        return out
    core = d.get("core_updates") or {}
    out.append(
        C(
            "R-09",
            "raw",
            set(core) <= {"job", "region", "love_style", "personality", "marriage_plan"},
            f"코어 키 {list(core)}",
        )
    )
    cats = [f.get("category") for f in d.get("facts") or []]
    out.append(
        C(
            "R-10",
            "raw",
            all(c in ("취미", "가치관", "일상", "연애관", "과거이력") for c in cats),
            f"category {set(cats)}",
        )
    )
    facts = " ".join(str(f.get("fact")) for f in d.get("facts") or []) + " " + " ".join(str(v) for v in core.values())
    out.append(C("R-14", "raw", not PII.search(facts), "PII"))
    inp = lf_input("v2-memory-extract", s.get("output_raw")) or ""
    overlap_ids = set(re.findall(r"\[msg_id=(\d+)\]", inp))
    srcs = [str(f.get("source_message_id")) for f in d.get("facts") or [] if f.get("source_message_id") is not None]
    out.append(
        C(
            "R-11",
            "raw",
            all(x in overlap_ids for x in srcs) if inp else None,
            f"입력에 없는 msg_id {[x for x in srcs if x not in overlap_ids]}",
        )
    )
    out.append(latency("R-16", s, 30000))
    return out


def f09(s):
    d = as_json(s.get("output_raw"))
    out = [C("R-11", "raw", isinstance(d, dict) and isinstance(d.get("decisions"), list), "JSON decisions")]
    inp = lf_input("v2-memory-reconcile", s.get("output_raw"))
    try:
        payload = json.loads(inp) if inp else None
    except Exception:  # noqa: BLE001
        payload = None
    if not isinstance(d, dict) or not payload:
        return out
    decs = d.get("decisions") or []
    cand = {p["new_fact_index"]: {c["id"] for c in p["existing_candidates"]} for p in payload}
    bad = [
        x
        for x in decs
        if x.get("action") in ("UPDATE", "DELETE", "PROMOTE")
        and x.get("target_id") not in cand.get(x.get("new_fact_index"), set())
    ]
    out.append(
        C("R-01", "raw", not bad, f"후보 밖 target {[(x.get('new_fact_index'), x.get('target_id')) for x in bad]}")
    )
    out.append(
        C(
            "R-02",
            "raw",
            all(x.get("target_id") is not None for x in decs if x.get("action") in ("UPDATE", "DELETE", "PROMOTE")),
            "대상 누락",
        )
    )
    out.append(
        C(
            "R-08",
            "raw",
            {x.get("new_fact_index") for x in decs} >= set(cand),
            f"판정 누락 {set(cand) - {x.get('new_fact_index') for x in decs}}",
        )
    )
    tg = [
        x.get("target_id") for x in decs if x.get("action") in ("UPDATE", "DELETE") and x.get("target_id") is not None
    ]
    out.append(C("R-10", "raw", len(tg) == len(set(tg)), f"같은 대상 중복 수정·삭제 {tg}"))
    out.append(latency("R-13", s, 20000, "raw_latency_ms"))
    return out


def f10(s):
    if s.get("error"):
        return [C("R-14", "final", False, s["error"])]
    t = s.get("output_parsed") or ""
    spk = s["input"]["speaker"]
    me, other = (
        (s["input"]["nickname_a"], s["input"]["nickname_b"])
        if spk == "a"
        else (s["input"]["nickname_b"], s["input"]["nickname_a"])
    )
    n_lines = s["input"]["turns"] * 2
    idx = s["input"]["index"]
    own_prev = next((p.split(": ", 1)[1] for p in reversed(s["input"]["previous"]) if p.startswith(f"{spk}: ")), None)
    return [
        C("R-01", "final", not IDENTITY_REGEX.search(t), "정체 자백"),
        C(
            "R-02",
            "final",
            None if me == other else not re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(me)}\s?님", t),
            "자기 닉네임+님",
        ),
        C("R-06", "final", not PAST_MEETING_REGEX.search(t), "과거 만남"),
        C("R-09", "final", not (idx < n_lines - 2 and contains_farewell(t)), "마지막 왕복 전 작별"),
        C("R-10", "final", None if idx != n_lines - 1 else qcount(t) == 0, "마지막 b 줄 질문"),
        C("R-11", "final", None if not own_prev else not repeats_previous(t, own_prev), "직전 자기 말 반복"),
        C(
            "R-12",
            "final",
            1 <= len(sentences(t)) <= 3 and not EMOJI_REGEX.search(t),
            f"{len(sentences(t))}문장·이모지",
        ),
        C("R-14", "final", 1 <= len(t) <= 300, f"{len(t)}자"),
    ]


def highlights_check(rep: dict, transcript: list[dict], req_quote, req_count):
    hs = rep.get("highlights") or []
    by_i = {t.get("index", t.get("i")): t.get("text", "") for t in transcript or []}
    norm = lambda x: re.sub(r"\s+", "", x or "")  # noqa: E731
    unlabel = lambda q: re.sub(r"^\s*[^:：\s]{1,12}\s*[:：]\s*", "", q or "")  # noqa: E731  화자 이름표는 허용
    bad = [
        (h.get("turn_index"), (h.get("quote") or "")[:20])
        for h in hs
        if norm(unlabel(h.get("quote"))) not in norm(by_i.get(h.get("turn_index"), ""))
    ]
    kinds = {h.get("kind") for h in hs}
    return [
        C(req_quote, "raw", not bad, f"대화록 원문과 다른 인용·줄 번호 {bad[:3]}"),
        C(
            req_count,
            "raw",
            3 <= len(hs) <= 5 and kinds >= {"click", "friction"},
            f"{len(hs)}개 {sorted(k for k in kinds if k)}",
        ),
    ]


def ideal_check(rep, req):
    fit = rep.get("ideal_fit") or {}
    ok = set(fit) <= {"ideal_warmth", "ideal_vitality", "ideal_status"} and all(
        v is None or (isinstance(v, int) and 0 <= v <= 100) for v in fit.values()
    )
    return C(req, "raw", ok, f"ideal_fit {fit}")


def f11(s):
    d = as_json(s.get("output_raw"))
    out = [C("R-11", "raw", isinstance(d, dict), "JSON")]
    if isinstance(d, dict):
        out += highlights_check(d, s["input"].get("transcript"), "R-03", "R-04")
        out.append(ideal_check(d, "R-05"))
        out.append(
            C(
                "R-09",
                "raw",
                len(d.get("headline", "")) <= 30 and 3 <= len(sentences(d.get("summary", ""))) <= 5,
                f"headline {len(d.get('headline', ''))}자, summary {len(sentences(d.get('summary', '')))}문장",
            )
        )
    out.append(C("R-12", "final", not s.get("fallback_used"), "템플릿 서술"))
    return out


SCRIPT_LINE = re.compile(r"^\s*(\d+)\.\s*([^:：]{1,20})\s*[:：]\s*(.*)$")


def script_lines(raw: str):
    lines, extra = [], []
    for ln in (raw or "").splitlines():
        m = SCRIPT_LINE.match(ln)
        if m:
            lines.append((int(m.group(1)), m.group(2).strip(), m.group(3)))
        elif ln.strip():
            extra.append(ln.strip())
    return lines, extra


def f12(s):
    raw = s.get("output_raw") or ""
    lines, extra = script_lines(raw)
    a, b = s["input"]["nickname_a"], s["input"]["nickname_b"]
    same = s["input"]["same_nickname"]
    la, lb = ("a", "b") if same else (a, b)
    n = s["input"]["turns"] * 2
    order_ok = [sp for _, sp, _ in lines] == [la if k % 2 == 0 else lb for k in range(len(lines))]
    out = [
        C(
            "R-01",
            "raw",
            len(lines) == n and order_ok and [x[0] for x in lines] == list(range(1, n + 1)),
            f"줄 {len(lines)}/{n}, 순서 {'정상' if order_ok else '깨짐'}",
        ),
        C("R-02", "raw", not extra, f"대본 밖 텍스트 {extra[:2]}"),
    ]
    selfcall, inline, early, emoji, ident, past, wrongref, labelname = [], [], [], [], [], [], [], []
    for k, (num, sp, text) in enumerate(lines):
        me = a if sp in (a, "a") and k % 2 == 0 else b
        body = re.sub(r"\s*\[[^\]]*\]\s*$", "", text)
        refs = re.findall(r"\b([AB])-f\d+", text[len(body) :])
        if not same and re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(me)}\s?(님|씨)", body):
            selfcall.append(num)
        if re.search(r"\b[AB]-f\d+", body):
            inline.append(num)
        if k < n - 2 and contains_farewell(body):
            early.append(num)
        if EMOJI_REGEX.search(body):
            emoji.append(num)
        if IDENTITY_REGEX.search(body):
            ident.append(num)
        if PAST_MEETING_REGEX.search(body):
            past.append(num)
        own = "A" if k % 2 == 0 else "B"
        if [r for r in refs if r != own]:
            wrongref.append(num)
        if same and re.search(r"(?<![A-Za-z])[ab](?:라고|이에요|예요|입니다|님)", body):
            labelname.append(num)
    out += [
        C("R-04", "raw", not selfcall, f"자기 닉네임 호칭 줄 {selfcall}"),
        C("R-07", "raw", not past, f"과거 만남 줄 {past}"),
        C("R-08", "raw", not ident and not labelname, f"정체 자백 {ident}, a/b 라벨을 이름처럼 씀 {labelname}"),
        C("R-10", "raw", not wrongref, f"상대 fact ID 를 붙인 줄 {wrongref} (먼저 꺼냈는지는 rule_check)"),
        C("R-11", "raw", not inline, f"본문 속 fact ID 줄 {inline}"),
        C("R-12", "raw", not early, f"마지막 왕복 전 작별 줄 {early}"),
        C("R-14", "raw", not emoji, f"이모지 줄 {emoji}"),
    ]
    out.append(C("R-21", "final", (s.get("run_latency_ms") or 0) <= 90000, f"Job {s.get('run_latency_ms')}ms ≤ 90000"))
    out.append(
        C(
            "R-20",
            "final",
            None if s["id"].endswith("generate1") is False else s.get("run_outcome") == "succeeded",
            f"실행 결과 {s.get('run_outcome')}",
        )
    )
    return out


def f13(s):
    return [c for c in f12(s) if c["req"] != "R-20"] + [C("R-05", "final", True, "재생성 1회 이하(하네스 예산)")]


def f14(s):
    args = calls_args(s.get("output_raw"), "submit_review")
    out = [
        C(
            "R-07",
            "raw",
            isinstance(args, dict) and isinstance(args.get("issues"), list) and "pass" in args,
            "submit_review 형식",
        )
    ]
    if isinstance(args, dict):
        issues = args.get("issues") or []
        out.append(
            C("R-07", "raw", bool(args.get("pass")) == (not issues), f"pass={args.get('pass')} 이슈 {len(issues)}개")
        )
        visible = {int(x) for x in re.findall(r"^(\d+)\.", s["input"].get("script_under_review") or "", re.M)}
        bad = [x.get("i") for x in issues if visible and x.get("i") not in visible]
        out.append(C("R-05", "raw", not bad, f"범위 밖 줄 번호 {bad}"))
        out.append(
            C(
                "R-06",
                "raw",
                all(x.get("type") in ("judge_mixup", "judge_unresponsive", "judge_tone") for x in issues),
                "유형",
            )
        )
    out.append(latency("R-10", s, 20000))
    return out


def f15(s):
    args = calls_args(s.get("output_raw"), "submit_patches")
    out = [C("R-13", "raw", isinstance(args, dict) and isinstance(args.get("patches"), list), "submit_patches 형식")]
    blocks = re.findall(
        r"## (\d+)번 줄 — 화자 ([ab]) \((.*?)\), 상대 닉네임 (.*?), (마지막 왕복[^\n]*)",
        s["input"].get("repair_blocks") or "",
    )
    want = {int(i): (spk, me, other, last) for i, spk, me, other, last in blocks}
    if isinstance(args, dict):
        ps = [p for p in args.get("patches") or [] if isinstance(p, dict)]
        got = {p.get("i") for p in ps}
        out.append(C("R-02", "raw", got <= set(want), f"지적 안 된 줄 수정 {sorted(got - set(want))}"))
        out.append(C("R-03", "raw", set(want) <= got, f"패치 누락 {sorted(set(want) - got)}"))
        selfcall, early, inline, ident, wrongref = [], [], [], [], []
        for p in ps:
            i, t = p.get("i"), str(p.get("text") or "")
            if i not in want:
                continue
            spk, me, other, last = want[i]
            if me != other and re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(me)}\s?(님|씨)", t):
                selfcall.append(i)
            if "아님" in last and contains_farewell(t):
                early.append(i)
            if re.search(r"\b[AB]-f\d+", t):
                inline.append(i)
            if IDENTITY_REGEX.search(t) or PAST_MEETING_REGEX.search(t):
                ident.append(i)
            own = "A" if spk == "a" else "B"
            if [r for r in p.get("fact_refs") or [] if not str(r).startswith(own + "-")]:
                wrongref.append(i)
        out += [
            C("R-06", "raw", not selfcall, f"자기 닉네임 호칭 {selfcall}"),
            C("R-07", "raw", not early, f"마무리 금지 줄에 작별 {early}"),
            C("R-09", "raw", not wrongref, f"상대 fact_refs {wrongref}"),
            C("R-10", "raw", not inline, f"본문 속 fact ID {inline}"),
            C("R-12", "raw", not ident, f"정체 자백·과거 만남 {ident}"),
        ]
    out.append(latency("R-14", s, 20000))
    return out


def f16(s):
    args = calls_args(s.get("output_raw"), "submit_report")
    out = [C("R-11", "raw", isinstance(args, dict), "submit_report 형식")]
    if isinstance(args, dict):
        tr = [
            {"index": int(m.group(1)), "text": m.group(3)}
            for m in (SCRIPT_LINE.match(x) for x in (s["input"].get("transcript") or "").splitlines())
            if m
        ]
        tr = [{"index": t["index"], "text": t["text"]} for t in tr]
        out += highlights_check(args, tr, "R-02", "R-09")
        out.append(ideal_check(args, "R-04"))
        txt = " ".join(
            [
                args.get("headline", ""),
                args.get("summary", ""),
                *(args.get("area_comments") or {}).values(),
                *(args.get("strengths") or []),
                *(args.get("cautions") or []),
                args.get("date_comment", ""),
            ]
        )
        out.append(
            C(
                "R-05",
                "raw",
                not re.search(r"(?<![A-Za-z가-힣])[abAB](?:님|는|은|가|이|의)(?![A-Za-z])", txt),
                "a·b 기호로 사람 지칭",
            )
        )
        out.append(
            C(
                "R-10",
                "raw",
                len(args.get("headline", "")) <= 30 and 3 <= len(sentences(args.get("summary", ""))) <= 5,
                f"headline {len(args.get('headline', ''))}자, summary {len(sentences(args.get('summary', '')))}문장",
            )
        )
    rep = s.get("output_parsed") or {}
    if rep:
        out.append(
            C("R-12", "final", (rep.get("narrative_source") or rep.get("source")) != "template", "템플릿 서술 폴백")
        )
    out.append(latency("R-13", s, 30000))
    return out


def chat_common(t: str, on: str, ids: dict) -> list[dict]:
    return [
        C(ids["q"], on, "?" not in t and "？" not in t and not is_daily_greeting(t), "질문·재만남 여지"),
        C(ids["len"], on, len(" ".join(t.split())) <= 200 and not EMOJI_REGEX.search(t), f"{len(t)}자·이모지"),
        C(ids["third"], on, not re.search(r"두 분|여러분", t), "제3자 시점"),
        C(ids["ai"], on, not re.search(r"대신 (써|작성)|작성해 ?드|\bAI\b|인공지능|챗봇", t), "대필·AI 언급"),
    ]


def f17(s):
    d = as_json(s.get("output_raw"))
    raw_turns = (d or {}).get("turns") if isinstance(d, dict) else None
    out = [
        C(
            "R-17",
            "raw",
            isinstance(raw_turns, list) and len(raw_turns) == 3 and all(isinstance(x, str) for x in raw_turns),
            "turns 3개",
        )
    ]
    ids = {"q": "R-04", "len": "R-07", "third": "R-10", "ai": "R-11"}
    for on, turns in (("raw", raw_turns if isinstance(raw_turns, list) else None), ("final", s.get("output_parsed"))):
        if not turns or len(turns) < 3:
            continue
        for k, t in enumerate(turns[:3], start=1):
            t = str(t)
            out += chat_common(t, on, ids)
            if k < 3:
                out.append(C("R-03", on, not re.search(r"종료|더\s*이상\s*답장", t), f"{k}번째에 종료 선언"))
            else:
                out.append(C("R-02", on, is_closing_message(t), "3번째 종료 표현"))
            out.append(C("R-05", on, not re.search(r"좋은 (하루|밤|저녁|주말)|편안한 밤|굿나잇", t), "일상 인사"))
        norm = [" ".join(str(x).split()) for x in turns[:3]]
        out.append(C("R-13", on, len(set(norm)) == 3, "같은 문장 반복"))
        out.append(C("R-14", on, sum("만나시길" in x or "인연 만나" in x for x in norm) <= 1, "덕담 2곳 이상"))
        examples = [
            "좋은 분이라 고민했지만",
            "저와는 맞지 않는 것 같아서 여기서 끝내려고",
            "여기까지만 할게요. 얘기 나눠 줘서",
        ]
        out.append(C("R-16", on, not any(e in x for e in examples for x in norm), "예시 문장 복사"))
    fb = FALLBACK_SCRIPTS.get(s["input"]["end_type"], [])
    out.append(C("R-18", "final", not any(t in fb for t in s.get("output_parsed") or []), "폴백 문장 섞임"))
    out.append(latency("R-19", s, 10000))
    return out


def f18(s):
    fin = s["output_parsed"]
    out = [C("R-18", "final", fin.get("status") == "SUCCESS", f"status {fin.get('status')}")]
    d = as_json(s.get("output_raw"))
    raw_t = d.get("ai_response") if isinstance(d, dict) else None
    if isinstance(d, dict) and s["input"]["ai_turn"] == 3 and d.get("closing"):
        raw_t = f"{raw_t} {d.get('closing')}"
    out.append(C("R-17", "raw", isinstance(raw_t, str) and raw_t.strip() != "", "ai_response"))
    ids = {"q": "R-07", "len": "R-09", "third": "R-12", "ai": "R-12"}
    direction = " ".join((s["input"].get("direction") or "").split())
    for on, t in (("raw", raw_t), ("final", fin.get("ai_response"))):
        if not t:
            continue
        out += chat_common(t, on, ids)
        out.append(C("R-08", on, not re.search(r"좋은 (하루|밤|저녁|주말)|편안한 밤|굿나잇", t), "일상 인사"))
        if s["input"]["ai_turn"] == 3:
            out.append(C("R-02", on, is_closing_message(t), "3턴 종료 표현"))
        sim = SequenceMatcher(None, " ".join(t.split()), direction).ratio()
        out.append(C("R-05", on, sim < 0.9, f"초안 유사도 {sim:.2f}"))
    out.append(latency("R-19", s, 10000))
    return out


CHECKS = {
    "f01-persona-conversation": f01,
    "f02-persona-tagging": f02,
    "f03-persona-extraction": f03,
    "f04-persona-extraction-style": f04,
    "f05-v2-persona-extraction": f05,
    "f06-practice-reply": f06,
    "f07-v2-practice-speech-permission": f07,
    "f08-v2-memory-extract": f08,
    "f09-v2-memory-reconcile": f09,
    "f10-simulation-v1-speak": f10,
    "f11-simulation-migration-report": f11,
    "f12-simulation-v2-generate": f12,
    "f13-simulation-v2-regenerate": f13,
    "f14-simulation-v2-judge": f14,
    "f15-simulation-v2-repair": f15,
    "f16-simulation-v2-report": f16,
    "f17-chat-end-drafts": f17,
    "f18-chat-end-message": f18,
}


def main() -> None:
    for slug, fn in CHECKS.items():
        p = EVALS / slug / "samples.jsonl"
        rows = read_jsonl(p)
        fails = 0
        for s in rows:
            try:
                s["auto_checks"] = fn(s)
            except Exception as e:  # noqa: BLE001
                s["auto_checks"] = [C("CHECK", "final", None, f"검사 오류 {type(e).__name__}: {e}")]
            fails += any(c["pass"] is False for c in s["auto_checks"])
        write_jsonl(p, rows)
        errs = sum(1 for s in rows for c in s["auto_checks"] if c["req"] == "CHECK")
        print(f"{slug}: samples {len(rows)}, 자동검사 실패 샘플 {fails}, 검사오류 {errs}")


if __name__ == "__main__":
    main()
