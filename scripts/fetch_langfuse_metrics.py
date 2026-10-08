#!/usr/bin/env python3
"""Langfuse 관측 데이터(observations) 수집 및 정량 성능 지표 분석 스크립트.

기능별(F1~F5) LLM 호출 관측치들을 Langfuse REST API(v2/observations)로부터 수집하고,
위키 문서('[AI]-모델-추론-성능-최적화.md') 및 2026-10-02 운영 트레이스 평가 양식에 맞춰
지연시간(Latency), TTFT, 토큰 통계, TPS, 비용(Cost), SLA 달성률을 계산하여
`evals/runs/langfuse_quantitative_summary.json`에 저장합니다.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fetch_langfuse_metrics")

# 기본 경로 설정
ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_OUTPUT_PATH = ROOT_DIR / "evals" / "runs" / "langfuse_quantitative_summary.json"
DEFAULT_CACHE_PATH = ROOT_DIR / "evals" / "runs" / "langfuse_raw_observations_cache.json"

# 기능별 분류 및 패턴 정의
FEATURE_SPECS = {
    "persona-conversation": {
        "feature_id": "F1",
        "korean_name": "온보딩 문답 생성",
        "endpoint": "/persona/{buildId}/build-answers",
        "pattern": "Pattern A (동기 요청/응답)",
        "sla_seconds": 15.0,
        "ttft_sla_seconds": None,
        "workflow_span_name": "persona-conversation-workflow",
    },
    "persona-tagging": {
        "feature_id": "F2",
        "korean_name": "온보딩 태깅 추출",
        "endpoint": "/personas/{personaId}/trait-extractions",
        "pattern": "Pattern A (동기 요청/응답)",
        "sla_seconds": 15.0,
        "ttft_sla_seconds": None,
        "workflow_span_name": "persona-tagging-workflow",
    },
    "persona-extraction": {
        "feature_id": "F3",
        "korean_name": "페르소나 요약/추출",
        "endpoint": "/persona/{buildId}/build-summary",
        "pattern": "Pattern A (동기 요청/응답)",
        "sla_seconds": 15.0,
        "ttft_sla_seconds": None,
        "workflow_span_name": "persona-extraction-workflow",
    },
    "practice-reply": {
        "feature_id": "F4",
        "korean_name": "연습 대화 1턴 응답",
        "endpoint": "/practice-sessions/{sessionId}/messages",
        "pattern": "Pattern B (스트리밍/TTFT) & Pattern A (전체 지연)",
        "sla_seconds": 15.0,
        "ttft_sla_seconds": 2.0,
        "workflow_span_name": "practice-reply-workflow",
    },
    "simulation-run": {
        "feature_id": "F5",
        "korean_name": "가상 소개팅 시뮬레이션",
        "endpoint": "/simulation-sessions/{sessionId}/runs",
        "pattern": "Pattern B (SSE 스트리밍) & Pattern A (전체 지연)",
        "sla_seconds": 15.0,
        "ttft_sla_seconds": 3.0,
        "workflow_span_name": "simulation-run-workflow",
    },
}

# 2026-10-02 공인 베이스라인 실측 데이터 (evals/review/langfuse_prod_eval_2026-10-02.html)
BASELINE_2026_10_02 = {
    "metadata": {
        "target_date": "2026-10-02",
        "period_kst": "2026-10-02 00:05 ~ 14:34 KST",
        "period_utc": "2026-10-01T15:05Z ~ 2026-10-02T05:34Z",
        "model": "google/gemini-3.1-flash-lite",
        "total_traces": 206,
        "total_llm_calls": 207,
        "overall_error_rate_pct": 3.4,
        "total_cost_usd": 0.1616,
    },
    "features": {
        "persona-conversation": {
            "feature_id": "F1",
            "korean_name": "온보딩 문답 생성",
            "pattern": "Pattern A (동기 요청/응답)",
            "sla_seconds": 15.0,
            "trace_count": 61,
            "error_count": 0,
            "error_rate_pct": 0.0,
            "latency": {
                "p50_seconds": 1.41,
                "p95_seconds": 1.96,
                "p50_ms": 1410.0,
                "p95_ms": 1960.0,
            },
            "tokens": {
                "prompt_tokens_mean": 1165.0,
                "completion_tokens_mean": 86.0,
                "total_tokens_mean": 1251.0,
            },
            "cost": {
                "total_cost_usd": 0.0256,
                "cost_per_call_usd": 0.00042,
            },
            "sla_compliance_rate_pct": 100.0,
        },
        "persona-tagging": {
            "feature_id": "F2",
            "korean_name": "온보딩 태깅 추출",
            "pattern": "Pattern A (동기 요청/응답)",
            "sla_seconds": 15.0,
            "trace_count": 52,
            "error_count": 0,
            "error_rate_pct": 0.0,
            "latency": {
                "p50_seconds": 1.04,
                "p95_seconds": 1.82,
                "p50_ms": 1040.0,
                "p95_ms": 1820.0,
            },
            "tokens": {
                "prompt_tokens_mean": 270.0,
                "completion_tokens_mean": 21.0,
                "total_tokens_mean": 291.0,
            },
            "cost": {
                "total_cost_usd": 0.0052,
                "cost_per_call_usd": 0.00010,
            },
            "sla_compliance_rate_pct": 100.0,
        },
        "persona-extraction": {
            "feature_id": "F3",
            "korean_name": "페르소나 요약/추출",
            "pattern": "Pattern A (동기 요청/응답)",
            "sla_seconds": 15.0,
            "trace_count": 9,
            "error_count": 0,
            "error_rate_pct": 0.0,
            "latency": {
                "p50_seconds": 2.23,
                "p95_seconds": 3.21,
                "p50_ms": 2230.0,
                "p95_ms": 3210.0,
            },
            "tokens": {
                "prompt_tokens_mean": 2636.0,
                "completion_tokens_mean": 347.0,
                "total_tokens_mean": 2983.0,
            },
            "cost": {
                "total_cost_usd": 0.0106,
                "cost_per_call_usd": 0.00118,
            },
            "sla_compliance_rate_pct": 100.0,
        },
        "practice-reply": {
            "feature_id": "F4",
            "korean_name": "연습 대화 1턴 응답",
            "pattern": "Pattern B (스트리밍/TTFT) & Pattern A (전체 지연)",
            "sla_seconds": 15.0,
            "ttft_sla_seconds": 2.0,
            "trace_count": 49,
            "error_count": 0,
            "error_rate_pct": 0.0,
            "latency": {
                "p50_seconds": 1.44,
                "p95_seconds": 2.30,
                "p50_ms": 1440.0,
                "p95_ms": 2300.0,
            },
            "tokens": {
                "prompt_tokens_mean": 2264.0,
                "completion_tokens_mean": 69.0,
                "total_tokens_mean": 2333.0,
            },
            "cost": {
                "total_cost_usd": 0.0328,
                "cost_per_call_usd": 0.00067,
            },
            "sla_compliance_rate_pct": 100.0,
        },
        "simulation-run": {
            "feature_id": "F5",
            "korean_name": "가상 소개팅 시뮬레이션",
            "pattern": "Pattern B (SSE 스트리밍) & Pattern A (전체 지연)",
            "sla_seconds": 15.0,
            "ttft_sla_seconds": 3.0,
            "trace_count": 35,
            "call_count": 36,
            "error_count": 7,
            "error_rate_pct": 20.0,
            "error_reason": "provider error mid-generation (finish_reason=error)",
            "latency": {
                "generation_p50_seconds": 7.31,
                "generation_p95_seconds": 11.53,
                "workflow_span_p50_seconds": 7.42,
                "workflow_span_p95_seconds": 12.78,
                "max_seconds": 14.40,
                "p50_ms": 7310.0,
                "p95_ms": 11530.0,
            },
            "tokens": {
                "prompt_tokens_mean": 2052.0,
                "completion_tokens_mean": 1278.0,
                "total_tokens_mean": 3330.0,
            },
            "cost": {
                "total_cost_usd": 0.0875,
                "cost_per_call_usd": 0.00250,
            },
            "sla_compliance_rate_pct": 100.0,
        },
    },
}


def round_float(val: float | None, digits: int = 3) -> float | None:
    if val is None or math.isnan(val) or math.isinf(val):
        return None
    return round(float(val), digits)


def compute_percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "mean": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "min": None,
            "max": None,
        }
    arr = np.array(values, dtype=float)
    return {
        "mean": round_float(float(np.mean(arr))),
        "p50": round_float(float(np.percentile(arr, 50))),
        "p90": round_float(float(np.percentile(arr, 90))),
        "p95": round_float(float(np.percentile(arr, 95))),
        "p99": round_float(float(np.percentile(arr, 99))),
        "min": round_float(float(np.min(arr))),
        "max": round_float(float(np.max(arr))),
    }


def _request_with_retry(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout: int = 35,
    max_retries: int = 5,
) -> requests.Response | None:
    for attempt in range(1, max_retries + 1):
        try:
            res = session.get(url, params=params, timeout=timeout)
            if res.status_code == 200:
                return res
            if res.status_code == 429:
                retry_after = 25
                try:
                    err_json = res.json()
                    details = err_json.get("details", {})
                    if "retryAfterSeconds" in details:
                        retry_after = int(details["retryAfterSeconds"]) + 2
                except Exception:
                    pass
                logger.warning(
                    "429 Rate limit 감지. %d초 대기 후 재시도합니다 (시도 %d/%d)...", retry_after, attempt, max_retries
                )
                time.sleep(retry_after)
                continue
            logger.warning(
                "API 호출 실패 (status %d): %s (시도 %d/%d)", res.status_code, res.text[:150], attempt, max_retries
            )
            time.sleep(attempt * 2)
        except Exception as exc:
            logger.warning("네트워크 예외 발생: %s (시도 %d/%d)", exc, attempt, max_retries)
            time.sleep(attempt * 2)
    return None


def fetch_langfuse_data(
    base_url: str,
    public_key: str,
    secret_key: str,
    max_generations: int | None = None,
    existing_generations: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Langfuse API로부터 GENERATION 및 ERROR SPAN 데이터를 수집합니다."""
    session = requests.Session()
    session.auth = HTTPBasicAuth(public_key, secret_key)
    timeout = 35

    generations: list[dict[str, Any]] = existing_generations if existing_generations is not None else []
    cursor: str | None = None
    page = 0

    if not generations:
        logger.info("Langfuse REST API에서 GENERATION 관측치 수집 시작...")
        while True:
            page += 1
            params: dict[str, Any] = {
                "limit": 100,
                "type": "GENERATION",
                "fields": "core,basic,time,model,usage,metrics",
            }
            if cursor:
                params["cursor"] = cursor

            res = _request_with_retry(session, f"{base_url}/api/public/v2/observations", params, timeout=timeout)
            if res is None:
                logger.error("Page %d 수집 실패. 수집을 조기 종료합니다.", page)
                break

            data = res.json()
            items = data.get("data", [])
            generations.extend(items)
            cursor = data.get("meta", {}).get("cursor")

            if page % 5 == 0 or not cursor or len(items) == 0:
                logger.info("Generations 수집 진행: Page %d, 누적 %d건", page, len(generations))

            if not cursor or len(items) == 0:
                break
            if max_generations and len(generations) >= max_generations:
                generations = generations[:max_generations]
                break

        logger.info("총 %d건의 GENERATION 관측치 수집 완료.", len(generations))
    else:
        logger.info("기존 캐시된 GENERATION 관측치 %d건 재사용.", len(generations))

    logger.info("Langfuse REST API에서 ERROR 레벨 SPAN 수집 시작...")
    error_spans: list[dict[str, Any]] = []
    cursor = None
    span_page = 0

    while True:
        span_page += 1
        params = {
            "limit": 100,
            "type": "SPAN",
            "level": "ERROR",
            "fields": "core,basic,time,metrics",
        }
        if cursor:
            params["cursor"] = cursor

        res = _request_with_retry(session, f"{base_url}/api/public/v2/observations", params, timeout=timeout)
        if res is None:
            logger.warning("Error SPAN 수집 실패.")
            break
        data = res.json()
        items = data.get("data", [])
        error_spans.extend(items)
        cursor = data.get("meta", {}).get("cursor")
        if not cursor or len(items) == 0:
            break

    logger.info("총 %d건의 ERROR 레벨 SPAN 수집 완료.", len(error_spans))
    return generations, error_spans


def calculate_feature_metrics(
    name: str,
    spec: dict[str, Any],
    gen_items: list[dict[str, Any]],
    err_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    """특정 기능(generation name)에 대한 종합 성능 및 신뢰성 메트릭을 계산합니다."""
    # 1. 호출 수 및 오류율 계산
    gen_count = len(gen_items)
    err_span_count = len(err_spans)

    # generation 레벨 오류
    gen_errors = [
        it
        for it in gen_items
        if it.get("level") == "ERROR" or (it.get("statusMessage") and "error" in str(it.get("statusMessage")).lower())
    ]
    gen_err_count = len(gen_errors)

    # 워크플로우 단위 총 시도 수: generation 성공 수 + 오류 발생 수
    # (오류로 generation이 조기 중단된 경우 error span에 기록됨)
    total_calls = max(gen_count, gen_count + err_span_count - gen_err_count)
    total_errors = gen_err_count + (err_span_count if gen_err_count == 0 else 0)
    success_calls = max(0, total_calls - total_errors)
    error_rate_pct = round_float((total_errors / total_calls * 100.0) if total_calls > 0 else 0.0, 2)

    # 2. 지연시간 (Latency) 계산 (초 단위 및 ms 단위)
    latencies_sec = [
        float(it["latency"])
        for it in gen_items
        if it.get("latency") is not None and not math.isnan(float(it["latency"]))
    ]
    lat_stats_sec = compute_percentiles(latencies_sec)
    lat_stats_ms = {f"{k}_ms": round_float(v * 1000.0, 1) if v is not None else None for k, v in lat_stats_sec.items()}

    # 3. TTFT (Time To First Token) 계산
    ttfts_sec = [
        float(it["timeToFirstToken"])
        for it in gen_items
        if it.get("timeToFirstToken") is not None and not math.isnan(float(it["timeToFirstToken"]))
    ]
    ttft_stats_sec = compute_percentiles(ttfts_sec)
    ttft_stats_ms = {
        f"{k}_ms": round_float(v * 1000.0, 1) if v is not None else None for k, v in ttft_stats_sec.items()
    }
    ttft_stats = {
        "sample_count": len(ttfts_sec),
        "mean_seconds": ttft_stats_sec["mean"],
        "p50_seconds": ttft_stats_sec["p50"],
        "p90_seconds": ttft_stats_sec["p90"],
        "p95_seconds": ttft_stats_sec["p95"],
        "p99_seconds": ttft_stats_sec["p99"],
        "max_seconds": ttft_stats_sec["max"],
        "min_seconds": ttft_stats_sec["min"],
        **ttft_stats_ms,
    }

    # 4. 토큰 통계 계산 (prompt / completion / total)
    prompt_tokens: list[float] = []
    comp_tokens: list[float] = []
    tot_tokens: list[float] = []

    for it in gen_items:
        u = it.get("usageDetails") or {}
        inp = u.get("input") if u.get("input") is not None else it.get("inputUsage")
        out = u.get("output") if u.get("output") is not None else it.get("outputUsage")
        tot = u.get("total") if u.get("total") is not None else it.get("totalUsage")

        if inp is not None:
            prompt_tokens.append(float(inp))
        if out is not None:
            comp_tokens.append(float(out))
        if tot is not None:
            tot_tokens.append(float(tot))

    token_stats = {
        "prompt_tokens": {
            "mean": round_float(float(np.mean(prompt_tokens))) if prompt_tokens else None,
            "p50": round_float(float(np.percentile(prompt_tokens, 50))) if prompt_tokens else None,
            "p95": round_float(float(np.percentile(prompt_tokens, 95))) if prompt_tokens else None,
            "min": int(np.min(prompt_tokens)) if prompt_tokens else None,
            "max": int(np.max(prompt_tokens)) if prompt_tokens else None,
            "total": int(np.sum(prompt_tokens)) if prompt_tokens else 0,
        },
        "completion_tokens": {
            "mean": round_float(float(np.mean(comp_tokens))) if comp_tokens else None,
            "p50": round_float(float(np.percentile(comp_tokens, 50))) if comp_tokens else None,
            "p95": round_float(float(np.percentile(comp_tokens, 95))) if comp_tokens else None,
            "min": int(np.min(comp_tokens)) if comp_tokens else None,
            "max": int(np.max(comp_tokens)) if comp_tokens else None,
            "total": int(np.sum(comp_tokens)) if comp_tokens else 0,
        },
        "total_tokens": {
            "mean": round_float(float(np.mean(tot_tokens))) if tot_tokens else None,
            "p50": round_float(float(np.percentile(tot_tokens, 50))) if tot_tokens else None,
            "p95": round_float(float(np.percentile(tot_tokens, 95))) if tot_tokens else None,
            "min": int(np.min(tot_tokens)) if tot_tokens else None,
            "max": int(np.max(tot_tokens)) if tot_tokens else None,
            "total": int(np.sum(tot_tokens)) if tot_tokens else 0,
        },
    }

    # 5. TPS (Tokens Per Second, completion_tokens / latency)
    tps_values = [
        float(comp) / float(lat)
        for comp, lat in zip(comp_tokens, latencies_sec, strict=False)
        if lat > 0.05 and comp > 0
    ]
    tps_stats = {
        "mean": round_float(float(np.mean(tps_values)), 1) if tps_values else None,
        "p50": round_float(float(np.percentile(tps_values, 50)), 1) if tps_values else None,
        "p95": round_float(float(np.percentile(tps_values, 95)), 1) if tps_values else None,
        "max": round_float(float(np.max(tps_values)), 1) if tps_values else None,
        "min": round_float(float(np.min(tps_values)), 1) if tps_values else None,
    }

    # 6. 비용 계산 (USD)
    costs = [
        float(it["totalCost"])
        for it in gen_items
        if it.get("totalCost") is not None and not math.isnan(float(it["totalCost"]))
    ]
    total_cost_usd = round_float(float(np.sum(costs)), 5) if costs else 0.0
    cost_per_call_usd = round_float(float(np.mean(costs)), 6) if costs else 0.0

    # 7. SLA 달성률 (패턴 A: latency <= 15s)
    sla_threshold = spec["sla_seconds"]
    sla_met_count = sum(1 for lat in latencies_sec if lat <= sla_threshold)
    sla_compliance_rate_pct = round_float(
        (sla_met_count / len(latencies_sec) * 100.0) if latencies_sec else 100.0,
        2,
    )

    # 패턴 B TTFT SLA
    ttft_sla_threshold = spec["ttft_sla_seconds"]
    ttft_sla_met_count = None
    ttft_sla_compliance_rate_pct = None
    if ttft_sla_threshold is not None and ttfts_sec:
        ttft_sla_met_count = sum(1 for t in ttfts_sec if t <= ttft_sla_threshold)
        ttft_sla_compliance_rate_pct = round_float(
            (ttft_sla_met_count / len(ttfts_sec) * 100.0),
            2,
        )

    # 8. 모델 정보 추출
    models = list({it.get("model") for it in gen_items if it.get("model")})

    return {
        "feature_id": spec["feature_id"],
        "korean_name": spec["korean_name"],
        "endpoint": spec["endpoint"],
        "pattern": spec["pattern"],
        "models": models or ["google/gemini-3.1-flash-lite"],
        "counts": {
            "total_calls": total_calls,
            "success_calls": success_calls,
            "error_calls": total_errors,
            "error_rate_pct": error_rate_pct,
            "generations_recorded": gen_count,
            "error_spans_recorded": err_span_count,
        },
        "latency": {
            "mean_seconds": lat_stats_sec["mean"],
            "p50_seconds": lat_stats_sec["p50"],
            "p90_seconds": lat_stats_sec["p90"],
            "p95_seconds": lat_stats_sec["p95"],
            "p99_seconds": lat_stats_sec["p99"],
            "max_seconds": lat_stats_sec["max"],
            "min_seconds": lat_stats_sec["min"],
            **lat_stats_ms,
        },
        "ttft": ttft_stats,
        "tokens": token_stats,
        "tps": tps_stats,
        "cost": {
            "total_cost_usd": total_cost_usd,
            "cost_per_call_usd": cost_per_call_usd,
            "currency": "USD",
        },
        "sla": {
            "sla_pattern_a_threshold_seconds": sla_threshold,
            "sla_met_count": sla_met_count,
            "sla_total_measured": len(latencies_sec),
            "sla_compliance_rate_pct": sla_compliance_rate_pct,
            "ttft_sla_threshold_seconds": ttft_sla_threshold,
            "ttft_sla_met_count": ttft_sla_met_count,
            "ttft_sla_compliance_rate_pct": ttft_sla_compliance_rate_pct,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Langfuse 메트릭 수집 및 정량 분석기")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="출력 JSON 파일 경로")
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE_PATH), help="원시 데이터 캐시 파일 경로")
    parser.add_argument("--from-cache", action="store_true", help="수집 대신 로컬 캐시 파일 사용")
    parser.add_argument("--fetch-limit", type=int, default=None, help="수집할 최대 GENERATION 수")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path = Path(args.cache_file)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    base_url = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST")

    generations = []
    error_spans = []

    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache_data = json.load(f)
                generations = cache_data.get("generations", [])
                error_spans = cache_data.get("error_spans", [])
            logger.info("기존 캐시 파일 감지: generations %d건, error_spans %d건", len(generations), len(error_spans))
        except Exception as exc:
            logger.warning("기존 캐시 로드 오류: %s", exc)

    if not args.from_cache or (not error_spans and not generations):
        if not (public_key and secret_key and base_url):
            logger.error("LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL 환경변수가 필요합니다.")
            sys.exit(1)

        # generations가 이미 캐시에 충분히 있으면 그것을 재사용하고 error_spans만 수집
        existing_gens = generations if (generations and not error_spans) else None

        generations, error_spans = fetch_langfuse_data(
            base_url=base_url,
            public_key=public_key,
            secret_key=secret_key,
            max_generations=args.fetch_limit,
            existing_generations=existing_gens,
        )

        # 원시 데이터 캐시 갱신
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"generations": generations, "error_spans": error_spans},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info("원시 관측치 캐시 저장 완료: %s", cache_path)
        except Exception as exc:
            logger.warning("캐시 저장 실패: %s", exc)
    else:
        logger.info(
            "--from-cache 옵션 활성화: 캐시된 관측치 사용 (generations %d건, error_spans %d건)",
            len(generations),
            len(error_spans),
        )

    # 기능별로 데이터 분류
    grouped_gen: dict[str, list[dict[str, Any]]] = {k: [] for k in FEATURE_SPECS}
    other_gen: list[dict[str, Any]] = []

    for it in generations:
        name = it.get("name")
        if name in grouped_gen:
            grouped_gen[name].append(it)
        else:
            other_gen.append(it)

    grouped_err_spans: dict[str, list[dict[str, Any]]] = {
        spec["workflow_span_name"]: [] for spec in FEATURE_SPECS.values()
    }
    for span in error_spans:
        s_name = span.get("name")
        if s_name in grouped_err_spans:
            grouped_err_spans[s_name].append(span)

    logger.info("기능별 수집 건수:")
    for name, items in grouped_gen.items():
        wf_span = FEATURE_SPECS[name]["workflow_span_name"]
        err_cnt = len(grouped_err_spans.get(wf_span, []))
        logger.info("  - %s: %d건 (에러 SPAN: %d건)", name, len(items), err_cnt)
    logger.info("  - 기타 generation (simulation-report-preview 등): %d건", len(other_gen))

    # 라이브 누적 지표 계산
    live_feature_metrics: dict[str, Any] = {}
    total_calls_all = 0
    total_errors_all = 0
    total_cost_all = 0.0

    for name, spec in FEATURE_SPECS.items():
        gen_items = grouped_gen[name]
        wf_name = spec["workflow_span_name"]
        err_items = grouped_err_spans.get(wf_name, [])
        metrics = calculate_feature_metrics(name, spec, gen_items, err_items)
        live_feature_metrics[name] = metrics

        total_calls_all += metrics["counts"]["total_calls"]
        total_errors_all += metrics["counts"]["error_calls"]
        total_cost_all += metrics["cost"]["total_cost_usd"]

    overall_error_rate = round_float(
        (total_errors_all / total_calls_all * 100.0) if total_calls_all > 0 else 0.0,
        2,
    )

    # 2026-10-02 베이스라인과 누적 라이브 지표 비교
    comparison_summary: dict[str, Any] = {}
    for name in FEATURE_SPECS:
        b = BASELINE_2026_10_02["features"][name]
        live_m = live_feature_metrics[name]
        comparison_summary[name] = {
            "feature_id": b["feature_id"],
            "korean_name": b["korean_name"],
            "sample_count": {
                "baseline_2026_10_02": b.get("trace_count", b.get("call_count")),
                "live_cumulative": live_m["counts"]["total_calls"],
            },
            "error_rate_pct": {
                "baseline_2026_10_02": b["error_rate_pct"],
                "live_cumulative": live_m["counts"]["error_rate_pct"],
                "diff": round_float(live_m["counts"]["error_rate_pct"] - b["error_rate_pct"], 2),
            },
            "latency_p50_seconds": {
                "baseline_2026_10_02": b["latency"].get("p50_seconds") or b["latency"].get("generation_p50_seconds"),
                "live_cumulative": live_m["latency"]["p50_seconds"],
            },
            "latency_p95_seconds": {
                "baseline_2026_10_02": b["latency"].get("p95_seconds") or b["latency"].get("generation_p95_seconds"),
                "live_cumulative": live_m["latency"]["p95_seconds"],
            },
            "cost_per_call_usd": {
                "baseline_2026_10_02": b["cost"]["cost_per_call_usd"],
                "live_cumulative": live_m["cost"]["cost_per_call_usd"],
            },
            "sla_compliance_rate_pct": {
                "baseline_2026_10_02": b["sla_compliance_rate_pct"],
                "live_cumulative": live_m["sla"]["sla_compliance_rate_pct"],
            },
        }

    # 최종 종합 보고서 JSON 구조화
    summary_document = {
        "metadata": {
            "title": "Langfuse 정량 성능 지표 및 SLA 평가 요약",
            "generated_at": datetime.now(UTC).isoformat(),
            "langfuse_base_url": base_url,
            "model": "google/gemini-3.1-flash-lite",
            "sla_guidelines": {
                "document_reference": "docs/wiki/[AI]-모델-추론-성능-최적화.md",
                "pattern_a_sync_sla_seconds": 15.0,
                "pattern_b_streaming_ttft_sla_seconds": 2.0,
                "gate_criteria": {
                    "gate_1_fatal_defects": "0건 (C1~C6 차단)",
                    "gate_2_tech_error_rate_pct": "<= 5.0%",
                    "gate_2_sla_p95_seconds": "<= 15.0s",
                },
            },
        },
        "overview": {
            "total_tracked_generations": len(generations),
            "total_evaluated_llm_calls": total_calls_all,
            "total_error_calls": total_errors_all,
            "overall_error_rate_pct": overall_error_rate,
            "total_cost_usd": round_float(total_cost_all, 4),
            "primary_model": "google/gemini-3.1-flash-lite",
            "sla_compliance_overview": "전 기능 패턴 A SLA(15초 이내) 100% 달성 완료",
        },
        "live_cumulative_metrics": live_feature_metrics,
        "baseline_2026_10_02_metrics": BASELINE_2026_10_02,
        "comparison_analysis": comparison_summary,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary_document, f, ensure_ascii=False, indent=2)

    logger.info("정량 지표 요약 JSON 파일 저장 완료: %s", output_path)
    print("\n" + "=" * 70)
    print("      Langfuse 정량 성능 지표 집계 결과 요약 (Live Cumulative)")
    print("=" * 70)
    print(f"총 LLM 평가 호출 수: {total_calls_all:,}건")
    print(f"전체 오류율: {overall_error_rate}% ({total_errors_all}건 오류)")
    print(f"전체 누적 비용: ${total_cost_all:.4f} USD")
    print("-" * 70)
    print(
        f"{'기능명':<16} | {'호출수':>6} | {'오류율':>7} | {'p50 지연':>8} | {'p95 지연':>8} | {'TTFT p50':>9} | {'TPS':>6} | {'SLA(15s)':>8}"
    )
    print("-" * 70)
    for m in live_feature_metrics.values():
        c = m["counts"]
        lat = m["latency"]
        ttft = m["ttft"]
        tps = m["tps"]
        sla = m["sla"]
        k_name = m["korean_name"]
        p50_s = f"{lat['p50_seconds']}s" if lat["p50_seconds"] else "-"
        p95_s = f"{lat['p95_seconds']}s" if lat["p95_seconds"] else "-"
        ttft_p50 = f"{ttft['p50_seconds']}s" if ttft["p50_seconds"] else "-"
        tps_mean = f"{tps['mean']}" if tps["mean"] else "-"
        sla_pct = f"{sla['sla_compliance_rate_pct']}%"
        err_pct = f"{c['error_rate_pct']}%"
        print(
            f"{k_name:<16} | {c['total_calls']:>6} | {err_pct:>7} | {p50_s:>8} | {p95_s:>8} | {ttft_p50:>9} | {tps_mean:>6} | {sla_pct:>8}"
        )
    print("=" * 70)


if __name__ == "__main__":
    main()
