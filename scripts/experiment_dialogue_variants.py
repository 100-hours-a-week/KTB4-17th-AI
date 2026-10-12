"""new(LangGraph) 시뮬레이션 대사의 '덕담 되풀이·딱딱한 말투' 원인 분리 실험.

앱 코드는 수정하지 않는다. 변형은 실행 중에 prompts 모듈의 상수·함수를 덮어써서 만들고, DB에는 쓰지 않는다.
설계는 docs/plan-simulation-dialogue-stiffness.md 4장. 쌍 선택·혼동 판정·실행기는 measure_confusion.py 를 그대로 쓴다.

  E0  현재 new (기준선)
  E1  HINT_LATE 를 금지에서 지시로 (원인 2)
  E2  COMMON_STYLE 을 old 수준으로 복구 (원인 3)
  E3  reply_anchor 의 "이 문장에만 답하라" 완화 (원인 1·4)
  (E4 가드레일 enforce 는 shadow 고정이라 하지 않는다)
  E5  시스템 프롬프트에 "총 N줄 중 k번째, 앞으로 M줄 남음" 길이 인식 문장 추가 (이전 방식은 전체 길이를 알고 썼다)
      주의: 이 변형의 기준선은 현재 코드(E1+E2 반영)다. E0 도 현재 코드이므로 E0 vs E5 로 읽는다.

  uv run python scripts/experiment_dialogue_variants.py --out <폴더>            # E0~E3 실행
  uv run python scripts/experiment_dialogue_variants.py --out <폴더> --summary  # 지금까지 결과 요약
  uv run python scripts/experiment_dialogue_variants.py --out <폴더> --offline-check  # 호출 없이 변형 적용 확인

results.jsonl 의 path 칸에 변형 이름(E0~E3)을 넣어서 judge_naturalness.py 를 수정 없이 쓴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402

import measure_confusion as mc  # noqa: E402  (환경변수를 먼저 정하는 모듈이라 import 순서를 지킨다)

VARIANTS = ["E0", "E1", "E2", "E3", "E5"]

# 변형 문구. 문서의 해결안 A 를 그대로 옮긴 것이라 이 문구가 곧 반영안이다.
HINT_LATE_E1 = (
    "아직 마지막이 아니다. 들어가세요, 나중에 봐요, 연락할게요, 잘 자요 같은 작별 문장은 쓰지 마라. "
    "상대 말에 한 마디 반응한 뒤, 네 프로필에 있는 이야기 하나를 꺼내거나 상대에게 구체적인 질문 하나를 덧붙여라. "
    "기쁘다·즐겁다·다행이다 같은 감정 표현은 앞에서 이미 나왔으면 다시 쓰지 마라."
)
COMMON_STYLE_E2 = (
    "존댓말, 편안한 구어체로 1~3문장, 이모지 없음, 가벼운 ㅎㅎ 만 허용, AI·챗봇·페르소나 연기 금지, "
    "자기 닉네임에 님을 붙이지 않음. 위 프로필에 '대화 스타일'이 있으면 그 말투(자주 쓰는 말·어미 포함)를 따른다."
)


def reply_anchor_e3(text: str) -> str:
    return (
        f"상대가 방금 한 말: {text}\n"
        "이 말에 먼저 답하되, 거기서 멈추지 마라. 네 직전 말의 변명을 덧붙이지 마라. "
        "상대가 하지 않은 감정이나 거절은 전제로 쓰지 마라."
    )


def length_notice(index: int, turns: int) -> str:
    total = turns * 2
    left = total - index - 1
    return (
        f"이 대화는 총 {total}줄이고 지금은 {index + 1}번째 줄이다. 앞으로 {left}줄이 남았다. "
        "남은 분량 동안 새 화제를 천천히 풀어 가고, 끝나기 전에는 마무리하는 말을 하지 마라."
        if left > 2
        else f"이 대화는 총 {total}줄이고 지금은 {index + 1}번째 줄이다. 곧 끝난다."
    )


def with_length_notice(orig):
    def wrapped(**kw):
        msgs = orig(**kw)
        if kw.get("index") is not None and kw.get("turns") is not None:
            msgs[0] = {**msgs[0], "content": msgs[0]["content"] + "\n\n" + length_notice(kw["index"], kw["turns"])}
        return msgs

    return wrapped


def apply_variant(name: str) -> None:
    """prompts 모듈을 변형 상태로 만든다. 항상 원본으로 되돌린 뒤 적용한다."""
    from app.features.simulation_migration import prompts, tools

    for attr, value in ORIGINAL.items():
        setattr(prompts, attr, value)
    tools.speaker_messages = ORIGINAL["speaker_messages"]  # tools 는 이름으로 직접 import 하므로 따로 되돌린다
    if name == "E1":
        prompts.HINT_LATE = HINT_LATE_E1
    elif name == "E2":
        prompts.COMMON_STYLE = COMMON_STYLE_E2
    elif name == "E3":
        prompts.reply_anchor = reply_anchor_e3
    elif name == "E5":
        patched = with_length_notice(ORIGINAL["speaker_messages"])
        prompts.speaker_messages = patched
        tools.speaker_messages = patched
    elif name != "E0":
        raise ValueError(f"알 수 없는 변형: {name}")


def _snapshot() -> dict:
    from app.features.simulation_migration import prompts

    return {k: getattr(prompts, k) for k in ("HINT_LATE", "COMMON_STYLE", "reply_anchor", "speaker_messages")}


ORIGINAL: dict = {}

# ── 로컬 보조 지표 (심판 없이 셀 수 있는 것) ────────────────────────
_FORMAL = re.compile(r"(?:습니다|ㅂ니다|십시오|시지요|하시는지요|겠습니다)[.?!\s]*$")
_PRAISE = re.compile(r"기쁘|즐거|다행|좋네요|반가|감사|공감|기대|좋아요|행복")
_CLOSE_RE = re.compile(r"(?:^|\s)(?:네|넵|네네)[.!~ ]*$")


def local_metrics(transcript: list) -> dict:
    n = len(transcript)
    if not n:
        return {}
    texts = [t for _, t in transcript]
    late = texts[n // 2 :]
    return {
        "lines": n,
        "avg_chars": sum(len(t) for t in texts) / n,
        "formal_ratio": sum(bool(_FORMAL.search(t.strip())) for t in texts) / n,
        "praise_ratio_late": sum(bool(_PRAISE.search(t)) for t in late) / max(1, len(late)),
        "question_ratio_late": sum(("?" in t or "？" in t) for t in late) / max(1, len(late)),
        "very_short_ratio": sum(len(t.strip()) <= 6 for t in texts) / n,
    }


# ── 실행 ─────────────────────────────────────────────────────────
async def run_variant(variant: str, pairs, reps, build, nick, out_file: Path, done: set, parallel: int) -> None:
    apply_variant(variant)  # 변형 하나가 끝날 때까지 다른 변형은 돌리지 않는다 (모듈 전역을 덮어쓰므로)
    sem = asyncio.Semaphore(parallel)

    async def one(p: dict, rep: int) -> None:
        if (variant, p["pair"], rep) in done:
            return
        async with sem:
            if mc.BUDGET.tripped:
                return
            pa, pb = build(p["a"]), build(p["b"])
            na, nb = nick[p["a"]], nick[p["b"]]
            t0 = time.monotonic()
            try:
                transcript, err = await mc.run_new(pa, pb, na, nb)
            except Exception as e:  # noqa: BLE001 - 실험 스크립트라 실패도 기록한다
                transcript, err = [], f"{type(e).__name__}: {str(e)[:120]}"
            if mc.BUDGET.tripped:
                return  # 예산 초과로 끊긴 실행은 기록하지 않는다
            rec = {
                "stage": "variants",
                "path": variant,
                "kind": p["kind"],
                "pair": p["pair"],
                "rep": rep,
                "names": [na, nb],
                "wall_s": round(time.monotonic() - t0, 2),
                "error": err,
                "has_style": [bool(pa.conversation_style), bool(pb.conversation_style)],
                "transcript": transcript,
                "check": mc.check_transcript(transcript, pa, pb, na, nb) if transcript else None,
                "local": local_metrics(transcript),
            }
            with out_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(
                f"[{variant} {p['kind']:5} rep{rep}] {rec['wall_s']:5.1f}s 오류 {err or '-'} 누적 {mc.BUDGET.used}",
                flush=True,
            )

    await asyncio.gather(*(one(p, rep) for rep in reps for p in pairs))


def load(out_file: Path) -> list[dict]:
    return [json.loads(line) for line in out_file.open(encoding="utf-8")] if out_file.exists() else []


def summary(out: Path) -> None:
    res = load(out / "results.jsonl")
    judged = {}
    jf = out / "naturalness.jsonl"
    if jf.exists():
        for line in jf.open(encoding="utf-8"):
            r = json.loads(line)
            if r["verdict"]:
                judged[(r["path"], r["pair"], r["rep"])] = r["verdict"]
    print(
        f"{'변형':4} {'실행':>4} {'실패':>4} {'혼동실행':>8} {'합쇼체%':>8} {'후반덕담%':>9} {'후반질문%':>9} {'초단문%':>8} {'평균자':>6}",
        end="",
    )
    print(f" {'flow':>5} {'human':>6} {'반복/100줄':>10} {'딱딱/100줄':>10} {'읊기/100줄':>10} {'심판N':>5}")
    for v in VARIANTS:
        rs = [r for r in res if r["path"] == v]
        ok = [r for r in rs if r["transcript"] and not r["error"]]
        if not rs:
            continue
        n = len(ok) or 1

        def avg(key: str, ok: list = ok, n: int = n) -> float:
            return sum(r["local"][key] for r in ok) / n

        conf = sum(r["check"]["confused"] for r in ok if r["check"])
        js = [judged[(v, r["pair"], r["rep"])] for r in ok if (v, r["pair"], r["rep"]) in judged]
        types: Counter = Counter()
        lines = 0
        for r in ok:
            if (v, r["pair"], r["rep"]) in judged:
                lines += r["local"]["lines"]
                for pb in judged[(v, r["pair"], r["rep"])].get("problems", []):
                    types[pb.get("type")] += 1

        def per100(t: str, types: Counter = types, lines: int = lines) -> str:
            return f"{100 * types[t] / lines:.1f}" if lines else "-"

        flow = f"{sum(j['flow'] for j in js) / len(js):.2f}" if js else "-"
        human = f"{sum(j['human_like'] for j in js) / len(js):.2f}" if js else "-"
        print(
            f"{v:4} {len(ok):4} {len(rs) - len(ok):4} {conf:8} {100 * avg('formal_ratio'):8.1f} {100 * avg('praise_ratio_late'):9.1f} "
            f"{100 * avg('question_ratio_late'):9.1f} {100 * avg('very_short_ratio'):8.1f} {avg('avg_chars'):6.1f}"
            f" {flow:>5} {human:>6} {per100('repetition'):>10} {per100('stiff'):>10} {per100('profile_recital'):>10} {len(js):5}"
        )
    styled = [r for r in res if r["path"] == "E0" and r["transcript"]]
    if styled:
        s = sum(1 for r in styled for x in r["has_style"] if x)
        print(f"\nE0 실행 {len(styled)}건의 페르소나 {2 * len(styled)}명 중 conversation_style 있음: {s}명")
    print("누적 호출", json.loads((out / "state.json").read_text())["used"] if (out / "state.json").exists() else 0)


def offline_check() -> None:
    """호출 없이 변형이 실제 프롬프트에 반영되는지 본다."""
    from app.features.simulation_migration.prompts import speaker_messages

    sims, nick, build = mc.load_data(*_default_csv())
    p = mc.select_pairs(sims)[0]
    persona = build(p["a"])
    na, nb = nick[p["a"]], nick[p["b"]]
    tr = [("a", "안녕하세요 반가워요"), ("b", "네 안녕하세요"), ("a", "오늘 어떠세요"), ("b", "좋아요")] * 3
    for v in VARIANTS:
        apply_variant(v)
        # 뒤 구간(late) 줄: turns=10 이면 index 12 가 late
        m = speaker_messages(
            speaker="a", persona=persona, nickname_self=na, nickname_other=nb,
            transcript=tr, closing_hint="none", index=12, turns=10,
        )  # fmt: skip
        sysmsg, last = m[0]["content"], m[-1]["content"]
        print(
            f"[{v}] 말투 구어체={'편안한 구어체' in sysmsg}  late 지시={'질문 하나' in sysmsg}  anchor 완화={'거기서 멈추지' in last}"
        )
    apply_variant("E0")


def _default_csv():
    return (str(Path.home() / "personas_202610062227.csv"), str(Path.home() / "simulations_202610062215.csv"))


async def main() -> None:  # noqa: ASYNC240 - 스크립트라 블로킹 파일 접근을 허용한다
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=5)
    ap.add_argument("--cap", type=int, default=900, help="누적 OpenRouter 호출 상한")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--offline-check", action="store_true")
    ap.add_argument("--personas", default=_default_csv()[0])
    ap.add_argument("--sims", default=_default_csv()[1])
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - 스크립트라 블로킹 파일 접근 허용

    ORIGINAL.update(_snapshot())
    if args.offline_check:
        offline_check()
        return
    if args.summary:
        summary(out)
        return

    mc.HARD_CAP = args.cap
    mc.BUDGET = mc.Budget(out / "state.json")
    mc.install_counter()
    sims, nick, build = mc.load_data(args.personas, args.sims)
    pairs = mc.select_pairs(sims)
    (out / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    out_file = out / "results.jsonl"
    done = {(r["path"], r["pair"], r["rep"]) for r in load(out_file) if not r["error"]}
    for v in args.variants.split(","):
        await run_variant(v, pairs, range(1, args.reps + 1), build, nick, out_file, done, args.parallel)
        if mc.BUDGET.tripped:
            print("예산 상한에 닿아 중단합니다.")
            break
    summary(out)


if __name__ == "__main__":
    asyncio.run(main())
