"""온보딩 태깅을 평가 데이터셋으로 채점한다. 지금 모델과 파인튜닝 모델을 같은 기준으로 비교하는 용도.

    # 지금 .env 의 모델 (OpenRouter)
    uv run python scripts/eval_tagging.py --out evals/results/tagging-baseline.json
    # .env 의 SLM_* 로 설정한 RunPod vLLM (serving/README.md). 모델 기본값은 SLM_TAGGING_MODEL
    uv run python scripts/eval_tagging.py --slm --out evals/results/tagging-v1.json
    uv run python scripts/eval_tagging.py --slm --model kanana --out evals/results/kanana-base.json
    # 다른 OpenAI 호환 서버
    uv run python scripts/eval_tagging.py --base-url http://gpu-host:8000/v1 --model tagging-lora \\
        --out evals/results/tagging-lora.json

평가는 지정한 모델 하나만 잰다. 앱의 SLM 라우팅·메인 LLM 폴백은 끈다 (폴백이 섞이면 점수가 두 모델의 혼합이 된다).

서빙 코드의 TaggingAgent 를 그대로 호출하므로 프롬프트·타임아웃·JSON 파싱·차원 필터까지 운영과 같다.
blind_holdout 은 최종 판정 때만 본다. 개발 중에 보면 그 split 에 맞춰 튜닝하게 돼 의미가 없어진다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DATASET = PROJECT_ROOT / "evals/quality_datasets/persona_onboarding_tagging.jsonl"
SPLITS = ("calibration", "regression", "blind_holdout")
# docs/성능평가/02_F2 5절 배포 게이트: primary·off_topic 동시 일치 85% 이상, None 실패 5% 이하
GATE_MATCH_PCT = 85.0
GATE_FAILURE_PCT = 5.0


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=SPLITS, action="append", help="여러 번 줄 수 있음. 기본 regression")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET,
        help="평가 JSONL. 사람 골드셋은 evals/quality_datasets/human/persona_onboarding_tagging.jsonl",
    )
    parser.add_argument("--allow-holdout", action="store_true", help="blind_holdout 채점 허용 (최종 판정용)")
    parser.add_argument("--slm", action="store_true", help=".env 의 SLM_BASE_URL·SLM_API_KEY 서버를 평가")
    parser.add_argument("--base-url", help="LLM_BASE_URL 덮어쓰기")
    parser.add_argument("--model", help="LLM_MODEL 덮어쓰기")
    parser.add_argument("--timeout", type=float, help="기본은 운영 값 ONBOARDING_TAG_TIMEOUT_S")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, help="케이스별 결과와 요약 JSON")
    args = parser.parse_args(argv)
    args.split = args.split or ["regression"]
    if "blind_holdout" in args.split and not args.allow_holdout:
        parser.error("blind_holdout 은 --allow-holdout 과 함께만 채점합니다")
    return args


def load_cases(splits: list[str], path: Path = DATASET) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    # applies=false 는 장애 주입 케이스라 라벨을 채점하지 않는다 (데이터셋 계약)
    return [
        row for row in rows if row["metadata"]["split"] in splits and row["expectedOutput"]["autoMetrics"]["applies"]
    ]


def score(output: dict[str, Any] | None, expected: dict[str, Any]) -> dict[str, bool]:
    """autoMetrics 계약대로 채점한다. 정답 또는 허용 대안 중 하나와 집합이 같으면 맞음. 실패(None)는 전부 오답."""
    metrics = expected["autoMetrics"]
    if output is None:
        return {"primary": False, "secondary": False, "off_topic": False, "exact": False, "failed": True}

    def set_match(field: str) -> bool:
        return any(set(output[field]) == set(accepted) for accepted in metrics[field]["accepted"])

    result = {
        "primary": set_match("primary"),
        "secondary": set_match("secondary"),
        "off_topic": output["off_topic"] in metrics["off_topic"]["accepted"],
        "failed": False,
    }
    result["exact"] = result["primary"] and result["secondary"] and result["off_topic"]
    return result


def label_counts(output: dict[str, Any] | None, expected: dict[str, Any]) -> dict[str, list[int]]:
    """primary·secondary 의 (tp, fp, fn). 허용 대안 중 예측과 가장 가까운 것 기준, 실패는 정답을 모두 놓친 것으로 센다."""
    from scripts.quality_metrics import tagging_item

    item = tagging_item({"status": "ok", "mode": "model", "result": output}, expected)
    return {field: list(item[field]) for field in ("primary", "secondary")}


def micro_f1(results: list[dict[str, Any]], field: str) -> float | None:
    rows = [r["counts"][field] for r in results if "counts" in r]
    if not rows:
        return None
    tp, fp, fn = (sum(row[i] for row in rows) for i in range(3))
    return round(100 * 2 * tp / (2 * tp + fp + fn), 1) if tp + fp + fn else 100.0


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(rows: list[dict[str, Any]], key: str) -> float:
        return round(100 * sum(r["score"][key] for r in rows) / len(rows), 1) if rows else 0.0

    gate_match = (
        round(100 * sum(r["score"]["primary"] and r["score"]["off_topic"] for r in results) / len(results), 1)
        if results
        else 0.0
    )

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
    off_topic_cases = [r for r in results if r["expected_off_topic"]]
    latencies = sorted(r["latency_s"] for r in results)
    return {
        "cases": len(results),
        "exact_pct": rate(results, "exact"),
        "primary_pct": rate(results, "primary"),
        "secondary_pct": rate(results, "secondary"),
        "off_topic_pct": rate(results, "off_topic"),
        # off_topic 양성은 적어서 비율만 보면 착시가 생긴다 (데이터셋 autoMetrics.aggregate)
        "off_topic_positive": {
            "cases": len(off_topic_cases),
            "caught": sum(r["score"]["off_topic"] for r in off_topic_cases),
        },
        "failure_pct": rate(results, "failed"),
        # 문항 단위 완전 일치보다 촘촘하다 — 라벨 하나만 틀려도 0점이 되지 않는다
        "primary_f1_pct": micro_f1(results, "primary"),
        "secondary_f1_pct": micro_f1(results, "secondary"),
        "gate": {
            "primary_and_off_topic_pct": gate_match,
            "passed": gate_match >= GATE_MATCH_PCT and rate(results, "failed") <= GATE_FAILURE_PCT,
        },
        "latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p95_s": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else None,
        "by_category": {
            name: {"cases": len(rows), "exact_pct": rate(rows, "exact")} for name, rows in sorted(by_category.items())
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.features.persona.agents import TaggingAgent

    settings = get_settings()
    timeout = args.timeout or settings.onboarding_tag_timeout_s
    agent = TaggingAgent()
    gate = asyncio.Semaphore(args.concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        inp = case["input"]
        async with gate:
            started = time.perf_counter()
            tags = await agent.tag(inp["question"], inp["answer"], timeout=timeout)
            latency = time.perf_counter() - started
        output = tags.model_dump() if tags is not None else None
        return {
            "case_id": case["metadata"]["caseId"],
            "category": case["metadata"]["category"],
            "expected_off_topic": case["expectedOutput"]["referenceLabels"]["off_topic"],
            "output": output,
            "score": score(output, case["expectedOutput"]),
            "counts": label_counts(output, case["expectedOutput"]),
            "latency_s": round(latency, 3),
        }

    cases = load_cases(args.split, args.dataset)
    results = await asyncio.gather(*(one(case) for case in cases))
    return {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "splits": args.split,
        "timeout_s": timeout,
        "summary": summarize(results),
        "results": results,
    }


def _point_main_llm_at_slm(args: argparse.Namespace) -> None:
    from app.core.config import Settings

    configured = Settings()  # .env 를 읽는다. 아래에서 환경변수를 바꾼 뒤 get_settings 가 다시 읽는다
    if not configured.slm_base_url:
        raise SystemExit("--slm 은 .env 에 SLM_BASE_URL 이 있어야 합니다")
    os.environ["LLM_BASE_URL"] = configured.slm_base_url
    os.environ["LLM_API_KEY"] = configured.slm_api_key
    args.model = args.model or configured.slm_tagging_model or None


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    # Settings 는 import 시점에 캐시되므로 덮어쓸 값은 import 전에 넣는다
    if args.slm:
        _point_main_llm_at_slm(args)
    # 지정한 모델만 잰다. SLM 라우팅이 켜져 있으면 태깅이 다른 서버로 가거나 폴백이 섞인다
    os.environ["SLM_BASE_URL"] = ""
    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    if "app.core.config" in sys.modules:  # --slm 이 .env 를 읽느라 먼저 import 했으면 바뀐 환경변수로 다시 읽게
        sys.modules["app.core.config"].get_settings.cache_clear()

    report = asyncio.run(_run(args))
    print(json.dumps({k: report[k] for k in ("model", "splits", "summary")}, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
