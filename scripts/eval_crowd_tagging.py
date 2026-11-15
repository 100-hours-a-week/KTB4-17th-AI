"""태깅 모델을 다수결 평가셋(scripts/export_crowd_tagging.py 의 eval.jsonl)으로 채점한다 (설계 5.7).

다수결은 (문장, 차원) 하나에 대한 판단이라 전체 태그 일치가 아니라 그 차원의 라벨만 맞는지 본다.
  accuracy_pct           : 정답이 있는 행(AGREE · 확인된 DISAGREE)에서 그 차원 라벨(direct · inferred · none)이 다수결과 같은 비율
  ambiguous_confident_pct: AMBIGUOUS 행에서 그 차원을 primary(직접 말함)로 단정한 비율. 낮을수록 좋다
  failure_pct            : 호출 실패 · 형식 위반 (strict 검증)

  uv run python scripts/eval_crowd_tagging.py data/crowd/export/eval.jsonl \\
      --base-url http://127.0.0.1:8011/v1 --model candidate --out evals/results/crowd-candidate.json

eval_tagging.py 와 같이 지정한 모델 하나만 잰다. SLM 라우팅 · 메인 LLM 폴백은 끈다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def predicted_label(output: dict | None, dimension: str) -> str | None:
    if output is None:
        return None
    if dimension in output["primary"]:
        return "direct"
    if dimension in output["secondary"]:
        return "inferred"
    return "none"


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def pct(part: int, whole: int) -> float:
        return round(100 * part / whole, 1) if whole else 0.0

    labeled = [r for r in results if r["label"] is not None]
    ambiguous = [r for r in results if r["label"] is None]
    return {
        "rows": len(results),
        "labeled": len(labeled),
        "accuracy_pct": pct(sum(r["predicted"] == r["label"] for r in labeled), len(labeled)),
        "ambiguous": len(ambiguous),
        "ambiguous_confident_pct": pct(sum(r["predicted"] == "direct" for r in ambiguous), len(ambiguous)),
        "failure_pct": pct(sum(r["predicted"] is None for r in results), len(results)),
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.features.persona.agents import TaggingAgent

    settings = get_settings()
    agent = TaggingAgent()
    gate = asyncio.Semaphore(args.concurrency)
    rows = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        async with gate:
            tags = await agent.tag(
                row["question"], row["answer"], timeout=settings.persona_retag_timeout_s, strict=True
            )
        output = tags.model_dump() if tags is not None else None
        return {
            "id": row["id"],
            "dimension": row["dimension"],
            "label": row["label"],
            "predicted": predicted_label(output, row["dimension"]),
        }

    results = await asyncio.gather(*(one(row) for row in rows))
    return {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "summary": summarize(results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    # Settings 는 처음 읽을 때 캐시되므로 import 전에 바꾼다. 지정한 모델만 잰다
    os.environ["LLM_BASE_URL"] = args.base_url
    os.environ["LLM_MODEL"] = args.model
    os.environ["SLM_BASE_URL"] = ""
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    if "app.core.config" in sys.modules:
        sys.modules["app.core.config"].get_settings.cache_clear()

    report = asyncio.run(_run(args))
    print(json.dumps({"model": report["model"], "summary": report["summary"]}, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
