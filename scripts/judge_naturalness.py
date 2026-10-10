"""measure_confusion.py 결과의 대화 자연스러움을 Claude(CLI)로 평가한다. API 과금 없이 `claude -p` 를 쓴다.

화자 혼동과는 별개로, 맥락 없이 프로필을 읊거나 질문과 상관없는 말을 하는 줄을 센다.
심판에게는 old/new 구분을 숨기고 실행 순서를 섞어서 준다.

  uv run python scripts/judge_naturalness.py --out <측정 출력 폴더>
"""

from __future__ import annotations

import os

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PROMPT = """너는 소개팅 메신저 대화의 자연스러움을 평가하는 심판이다. 두 사람(a, b)의 프로필과 두 사람이 나눈 대화를 읽고 평가하라.
화자가 뒤바뀌었는지는 보지 않는다(다른 심판이 본다). **대화가 사람이 실제로 주고받는 메신저 대화처럼 자연스러운지**만 본다.

## 문제 줄 유형
- non_sequitur: 바로 앞 상대 말이나 질문과 이어지지 않고 갑자기 다른 화제로 넘어간다. 예: 주말 계획을 물었는데 난데없이 고민 대처 방식을 이야기한다.
- profile_recital: 대화 맥락과 상관없이 프로필에 적힌 성향이나 특징을 설명서처럼 읊는다. 예: "저는 갈등이 생기면 대화로 푸는 편이고, 연락은 수시로 하는 걸 선호해요."
- invented_fact: 프로필에 없는 구체적인 사실(직업, 나이, 사는 곳, 구체적 경험, 약속된 사실 등)을 새로 지어낸다. 사소한 맞장구나 일반적인 감상은 해당하지 않는다.
- repetition: 같은 화자가 앞에서 한 말을 거의 그대로 반복한다.
- stiff: 메신저 대화가 아니라 발표문이나 상담원처럼 딱딱하고 어색한 말투다.

## 점수 (1~5)
- flow: 앞말에 이어지는 정도. 5는 모든 줄이 앞말에 자연스럽게 이어짐, 1은 대부분 따로 논다.
- human_like: 실제 사람이 메신저로 하는 말 같은 정도. 5는 구분이 안 됨, 1은 기계적이다.

확신이 서지 않는 줄은 문제로 세지 마라. 한 줄이 여러 유형에 해당하면 가장 알맞은 하나만 골라라.

## 출력
아래 JSON 한 개만 출력하라. 다른 글은 쓰지 마라.
{{"flow": 1~5 정수, "human_like": 1~5 정수, "problems": [{{"index": 줄번호, "type": "non_sequitur|profile_recital|invented_fact|repetition|stiff", "reason": "한 문장"}}]}}

문제가 없으면 problems 를 빈 배열로 둬라.

## 프로필
{profiles}

## 대화 (줄번호는 0부터, a가 먼저 말함)
{dialogue}
"""


async def judge_one(sem: asyncio.Semaphore, prompt: str) -> dict | None:
    async with sem:
        for _ in range(2):
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", "--model", "opus", "--effort", "xhigh", "--no-session-persistence",
                "--output-format", "text",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            out, _err = await proc.communicate(prompt.encode())
            m = re.search(r"\{.*\}", out.decode(errors="ignore"), re.S)
            if m:
                try:
                    v = json.loads(m.group(0))
                    if isinstance(v.get("flow"), int) and isinstance(v.get("human_like"), int):
                        return v
                except json.JSONDecodeError:
                    pass
        return None


async def main() -> None:
    import measure_confusion as mc

    from app.features.persona.profile import describe

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--personas", default=str(Path.home() / "personas_202610062227.csv"))
    ap.add_argument("--sims", default=str(Path.home() / "simulations_202610062215.csv"))
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    out = Path(args.out)

    sims, nick, build = mc.load_data(args.personas, args.sims)
    pairs = {p["pair"]: p for p in json.loads((out / "pairs.json").read_text())}
    results = [json.loads(line) for line in (out / "results.jsonl").open(encoding="utf-8")]
    judged_file = out / "naturalness.jsonl"
    prev = [json.loads(line) for line in judged_file.open(encoding="utf-8")] if judged_file.exists() else []
    done = {(r["path"], r["pair"], r["rep"]) for r in prev if r["verdict"] is not None}  # 실패한 건은 다시 한다
    todo = [r for r in results if r["transcript"] and not r["error"] and (r["path"], r["pair"], r["rep"]) not in done]
    random.Random(23).shuffle(todo)

    sem = asyncio.Semaphore(args.parallel)

    async def run(r: dict) -> None:
        p = pairs[r["pair"]]
        na, nb = r["names"]
        profiles = describe(f"a · {na}", build(p["a"])) + "\n\n" + describe(f"b · {nb}", build(p["b"]))
        dialogue = "\n".join(
            f"[{i}] {spk} ({na if spk == 'a' else nb}): {text}" for i, (spk, text) in enumerate(r["transcript"])
        )
        verdict = await judge_one(sem, PROMPT.format(profiles=profiles, dialogue=dialogue))
        rec = {"path": r["path"], "kind": r["kind"], "pair": r["pair"], "rep": r["rep"], "verdict": verdict}
        with judged_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("완료" if verdict else "판정 실패", flush=True)

    await asyncio.gather(*(run(r) for r in todo))


if __name__ == "__main__":
    asyncio.run(main())
