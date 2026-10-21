"""평가셋('질문 -> 찾아야 할 사실' 50~200쌍) 기반 Recall@5, MRR, 코사인 거리 분포 측정."""

from __future__ import annotations

import numpy as np


def evaluate_retrieval_metrics(
    results: list[tuple[str, list[str]]],
    *,
    distances: list[float] | None = None,
    k: int = 5,
) -> dict[str, float]:
    """results: [(expected_fact, [retrieved_fact_1, ...]), ...], distances: 매칭된 사실들의 코사인 거리 목록."""
    if not results:
        return {"recall_at_k": 0.0, "mrr": 0.0}

    hits = 0
    rr_sum = 0.0
    for expected, retrieved in results:
        top_k_list = retrieved[:k]
        if expected in top_k_list:
            hits += 1
            rank = top_k_list.index(expected) + 1
            rr_sum += 1.0 / rank

    total = len(results)
    metrics: dict[str, float] = {
        "recall_at_k": round(hits / total, 4),
        "mrr": round(rr_sum / total, 4),
    }
    if distances:
        arr = np.asarray(distances, dtype=np.float32)
        metrics["dist_mean"] = round(float(np.mean(arr)), 4)
        metrics["dist_p50"] = round(float(np.percentile(arr, 50)), 4)
        metrics["dist_p90"] = round(float(np.percentile(arr, 90)), 4)
        metrics["dist_max"] = round(float(np.max(arr)), 4)
    return metrics
