"""LLM 심사 오탐률 측정 — 기획안 9절 "심사 오탐률 ≤ 20%".

사람이 줄마다 판정한 대본(라벨 파일)에 심사(verifier.judge, 전체 심사)를 돌려
  - 오탐률 = 문제없는 줄 중 심사가 judge_mixup 으로 지적한 줄의 비율
  - 대본 단위 오탐률 = 혼동이 없는 대본 중 심사가 하나라도 judge_mixup 을 지적한 비율
  - 재현율 = 사람이 혼동으로 판정한 줄 중 심사가 지적한 비율
을 잰다. 응답성·말투 지적은 라벨이 없어 세지 않는다.

라벨 파일 형식: [{"transcript": [{"speaker": "a"|"b", "text": "..."}], "mixup_lines": [줄 번호, ...]}, ...]
사용법 (실행 중인 DB 에 민수·지은 페르소나·fact 가 있어야 한다):
    PYTHONPATH=. uv run python scripts/eval_judge_fp.py --labels labels.json --me u-minsu --partner u-jieun [--model google/gemini-2.5-flash]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


async def _run(args: argparse.Namespace) -> dict:
    from app.core.db import SessionLocal, engine
    from app.features.persona.lookup import load_persona
    from app.features.persona.schemas import PersonaRef
    from app.v2.simulation.llm import call_tools
    from app.v2.simulation.schemas import ScriptLineV2, SimContext
    from app.v2.simulation.topics import fetch_facts, match_topics
    from app.v2.simulation.verifier import judge

    async with SessionLocal() as db:
        a = await load_persona(db, PersonaRef(user_id=args.me))
        b = await load_persona(db, PersonaRef(user_id=args.partner))
        plan = match_topics(await fetch_facts(db, args.me), await fetch_facts(db, args.partner), turns=10)
    await engine.dispose()
    ctx = SimContext(
        me_user_id=args.me,
        partner_user_id=args.partner,
        persona_a_id=a.record.id,
        persona_b_id=b.record.id,
        persona_a=a.response,
        persona_b=b.response,
        name_a=a.nickname,
        name_b=b.nickname,
    )
    labels = args.labels_data
    clean_lines = flagged_clean = mixed_lines = caught = 0
    clean_scripts = flagged_scripts = skipped = 0
    rows = []
    for item in labels:
        script = [ScriptLineV2(i=k, speaker=t["speaker"], text=t["text"]) for k, t in enumerate(item["transcript"])]
        truth = set(item["mixup_lines"])
        res = await judge(call_tools, script, ctx, plan, focus=None, timeout=40, model=args.model)
        if res.skipped:
            skipped += 1
            rows.append({"skipped": True})
            continue
        flagged = {x.i for x in res.issues if x.type == "judge_mixup"}
        clean_lines += len(script) - len(truth)
        flagged_clean += len(flagged - truth)
        mixed_lines += len(truth)
        caught += len(flagged & truth)
        if not truth:
            clean_scripts += 1
            flagged_scripts += bool(flagged)
        rows.append({"truth": sorted(truth), "flagged": sorted(flagged)})
    return {
        "model": args.model or "(생성 모델과 같음)",
        "scripts": len(labels),
        "judge_skipped": skipped,
        "line_fp_rate": round(flagged_clean / clean_lines, 4) if clean_lines else None,
        "script_fp_rate": round(flagged_scripts / clean_scripts, 4) if clean_scripts else None,
        "recall": round(caught / mixed_lines, 4) if mixed_lines else None,
        "counts": {
            "clean_lines": clean_lines,
            "flagged_clean": flagged_clean,
            "mixed_lines": mixed_lines,
            "caught": caught,
            "clean_scripts": clean_scripts,
            "flagged_clean_scripts": flagged_scripts,
        },
        "rows": rows,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", required=True)
    p.add_argument("--me", required=True)
    p.add_argument("--partner", required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    args.labels_data = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    result = asyncio.run(_run(args))
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
