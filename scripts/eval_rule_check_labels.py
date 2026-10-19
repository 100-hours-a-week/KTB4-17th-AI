"""규칙 검사(verifier.rule_check) 오탐률·재현율 — 사람 판정 라벨셋 기준.

사용법: PYTHONPATH=. uv run python scripts/eval_rule_check_labels.py docs/실험_및_분석_결과/v2_로컬_LLM_테스트_원본/혼동_라벨셋_사람판정.json
전제: DB 에 민수(u-minsu)·지은(u-jieun) 페르소나와 v2 fact 가 있어야 한다.
"""

import asyncio
import json
import sys

from app.core.db import SessionLocal, engine
from app.features.persona.lookup import load_persona
from app.features.persona.schemas import PersonaRef
from app.v2.simulation.schemas import ScriptLineV2, SimContext
from app.v2.simulation.topics import fetch_facts, match_topics
from app.v2.simulation.verifier import rule_check

MIX = {"self_address", "perspective", "ownership"}


async def main():
    async with SessionLocal() as db:
        a = await load_persona(db, PersonaRef(user_id="u-minsu"))
        b = await load_persona(db, PersonaRef(user_id="u-jieun"))
        plan = match_topics(await fetch_facts(db, "u-minsu"), await fetch_facts(db, "u-jieun"), turns=10)
    await engine.dispose()
    ctx = SimContext(
        me_user_id="u-minsu",
        partner_user_id="u-jieun",
        persona_a_id="a",
        persona_b_id="b",
        persona_a=a.response,
        persona_b=b.response,
        name_a=a.nickname,
        name_b=b.nickname,
    )
    labels = LABELS
    cl = fc = ml = ca = cs = fs = 0
    fps = []
    misses = []
    for it in labels:
        s = [ScriptLineV2(i=k, speaker=t["speaker"], text=t["text"]) for k, t in enumerate(it["transcript"])]
        iss, _ = rule_check(s, ctx, plan, max(len(s) // 2, 1))
        flagged = {x.i for x in iss if x.type in MIX and x.i is not None}
        truth = set(it["mixup_lines"])
        cl += len(s) - len(truth)
        fc += len(flagged - truth)
        ml += len(truth)
        ca += len(flagged & truth)
        if not truth:
            cs += 1
            fs += bool(flagged)
        for i in flagged - truth:
            fps.append((it["source"], it["run"], i, s[i].text[:80], [x.evidence[:30] for x in iss if x.i == i][:1]))
        for i in truth - flagged:
            misses.append((it["source"], it["run"], i, s[i].text[:80]))
    print(
        json.dumps(
            {
                "line_fp": round(fc / cl, 4),
                "script_fp": round(fs / cs, 4),
                "recall": round(ca / ml, 4),
                "counts": {
                    "clean_lines": cl,
                    "fp": fc,
                    "mixed": ml,
                    "caught": ca,
                    "clean_scripts": cs,
                    "fp_scripts": fs,
                },
            },
            ensure_ascii=False,
        )
    )
    print("FP:")
    [print(" ", x) for x in fps]
    print("MISS:")
    [print(" ", x) for x in misses]


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        LABELS = json.load(f)
    asyncio.run(main())
