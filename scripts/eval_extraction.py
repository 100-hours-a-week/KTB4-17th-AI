"""페르소나 추출을 평가 데이터셋으로 채점한다. 지금 모델과 파인튜닝 모델을 같은 기준으로 비교하는 용도.

    # 지금 .env 의 모델 (OpenRouter)
    uv run python scripts/eval_extraction.py --out evals/results/extraction-baseline.json
    # .env 의 SLM_* 로 설정한 RunPod vLLM. 모델 기본값은 SLM_EXTRACTION_MODEL
    uv run python scripts/eval_extraction.py --slm --out evals/results/extraction-v1.json
    # 다른 OpenAI 호환 서버
    uv run python scripts/eval_extraction.py --base-url http://gpu-host:8000/v1 --model extraction-lora \\
        --out evals/results/extraction-lora.json

scripts/eval_tagging.py 와 같은 방식이다. 서빙 코드의 ExtractionAgent 를 그대로 호출하므로 프롬프트·타임아웃·JSON 파싱·
스키마 검증이 운영과 같고, 지정한 모델 하나만 잰다(SLM 라우팅·폴백 끔).

채점 대상은 label_accuracy 사례뿐이다. code_behavior 는 모델이 아니라 서비스 후처리 동작을 보는 fixture 다
(docs/성능평가/03_F3 4.1절 — regression 유효 모수 18건).

  지표 (scripts/quality_metrics.py 와 같은 정의)
    dimension_judgment_pct  차원마다 "점수를 내야 하나 / 생략해야 하나"가 맞은 비율 (대표 지표)
    unknown_omitted_pct     답하지 않은 차원의 점수를 원출력에서 생략한 비율 — F3 게이트 E3-CRIT-01, 100% 필수
    score_in_range_pct      점수를 내야 하는 차원 중 허용 범위 안 — F3 게이트 80% 이상
    failure_pct             호출·검증 실패(운영에서는 폴백 초안) — F3 게이트 5% 이하
  유형명 사용(E3-CRIT-02)·프롬프트 복창(E3-CRIT-03)·서술 품질은 글을 읽어야 해서 여기서 보지 않는다 (블라인드 심판).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DATASET = PROJECT_ROOT / "evals/quality_datasets/persona_build.jsonl"
SPLITS = ("calibration", "regression", "blind_holdout")
# docs/성능평가/03_F3 5절 배포 게이트
GATE_UNKNOWN_OMITTED_PCT = 100.0
GATE_IN_RANGE_PCT = 80.0
GATE_FAILURE_PCT = 5.0


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=SPLITS, action="append", help="여러 번 줄 수 있음. 기본 regression")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--allow-holdout", action="store_true", help="blind_holdout 채점 허용 (최종 판정용)")
    parser.add_argument("--slm", action="store_true", help=".env 의 SLM_BASE_URL·SLM_API_KEY 서버를 평가")
    parser.add_argument("--base-url", help="LLM_BASE_URL 덮어쓰기")
    parser.add_argument("--model", help="LLM_MODEL 덮어쓰기")
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
    return [
        row
        for row in rows
        if row["metadata"]["split"] in splits
        and row["metadata"]["evaluationKind"] == "label_accuracy"
        and row["expectedOutput"]["autoMetrics"]["applies"]
    ]


def score(raw: dict[str, Any] | None, expected: dict[str, Any]) -> dict[str, Any]:
    """원출력(raw) 하나를 autoMetrics 계약대로 채점한다. 실패(None)는 모든 차원을 오답으로 센다."""
    from scripts.quality_metrics import build_item

    metrics = expected["autoMetrics"]
    output = {"status": "ok", "mode": "model", "result": {"raw": raw}} if raw is not None else {"status": "error"}
    item = build_item(output, expected)
    omitted = metrics.get("mustOmitFromRawModel", [])
    return {
        "failed": raw is None,
        "judgments": [sum(item["judgments"]), len(item["judgments"])],
        "in_range": [sum(item["inRange"]), len(item["inRange"])],
        "unknown_omitted": [sum(raw is not None and raw.get(key) is None for key in omitted), len(omitted)],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def pooled(key: str) -> float | None:
        hit = sum(r["score"][key][0] for r in results)
        total = sum(r["score"][key][1] for r in results)
        return round(100 * hit / total, 1) if total else None

    failure = round(100 * sum(r["score"]["failed"] for r in results) / len(results), 1) if results else 0.0
    unknown_omitted = pooled("unknown_omitted")
    in_range = pooled("in_range")
    latencies = sorted(r["latency_s"] for r in results)
    return {
        "cases": len(results),
        "dimension_judgment_pct": pooled("judgments"),
        "unknown_omitted_pct": unknown_omitted,
        "score_in_range_pct": in_range,
        "failure_pct": failure,
        "latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p95_s": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else None,
        "gate": {
            "passed": (unknown_omitted is None or unknown_omitted >= GATE_UNKNOWN_OMITTED_PCT)
            and (in_range is None or in_range >= GATE_IN_RANGE_PCT)
            and failure <= GATE_FAILURE_PCT,
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.features.persona.agents import BuildFailed, ExtractionAgent

    settings = get_settings()
    agent = ExtractionAgent()
    gate = asyncio.Semaphore(args.concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        inp = case["input"]
        async with gate:
            started = time.perf_counter()
            try:
                raw = (await agent.extract(inp["history"], answered=set(inp["answeredDimensions"]))).model_dump(
                    mode="json", exclude_unset=True
                )
                error = None
            except BuildFailed as e:
                raw, error = None, str(e)[:300]
            latency = time.perf_counter() - started
        return {
            "case_id": case["metadata"]["caseId"],
            "category": case["metadata"]["category"],
            "output": raw,
            "error": error,
            "score": score(raw, case["expectedOutput"]),
            "latency_s": round(latency, 3),
        }

    cases = load_cases(args.split, args.dataset)
    results = await asyncio.gather(*(one(case) for case in cases))
    return {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "splits": args.split,
        # 03_F3 2절: enforce 면 가드레일 위반 서술을 지우므로 결과표에 모드를 적는다
        "guardrail_mode": os.environ.get("GUARDRAIL_MODE", "shadow"),
        "summary": summarize(results),
        "results": results,
    }


def _point_main_llm_at_slm(args: argparse.Namespace) -> None:
    from app.core.config import Settings

    configured = Settings()
    if not configured.slm_base_url:
        raise SystemExit("--slm 은 .env 에 SLM_BASE_URL 이 있어야 합니다")
    os.environ["LLM_BASE_URL"] = configured.slm_base_url
    os.environ["LLM_API_KEY"] = configured.slm_api_key
    args.model = args.model or configured.slm_extraction_model or None


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    # eval_tagging.py 와 같은 순서: Settings 캐시 전에 환경변수를 바꾼다
    if args.slm:
        _point_main_llm_at_slm(args)
    os.environ["SLM_BASE_URL"] = ""
    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    if "app.core.config" in sys.modules:
        sys.modules["app.core.config"].get_settings.cache_clear()

    report = asyncio.run(_run(args))
    print(
        json.dumps(
            {k: report[k] for k in ("model", "splits", "guardrail_mode", "summary")}, ensure_ascii=False, indent=2
        )
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
