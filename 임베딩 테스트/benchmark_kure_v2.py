"""KURE-v2 (154M Late-Interaction Multi-Vector) 벤치마크 스크립트.

실행 방법:
    uv run --with "torch" --with "sentence-transformers" python "임베딩 테스트/benchmark_kure_v2.py"

동작:
1. `eval_dataset.json`에서 60개 Fact 코퍼스, 50개 구어체 질문, 30쌍 Reconcile 로드
2. `sentence_transformers.MultiVectorEncoder('nlpai-lab/KURE-v2')` 로드
3. 50개 쿼리 vs 60개 Fact 코퍼스 MaxSim 기반 Recall@1, Recall@3, Recall@5, MRR 측정
4. 30쌍 Reconcile 문장 쌍 MaxSim 유사도 측정
5. CPU 추론 지연시간 (단일 쿼리 P50/P95 ms, 배치 8건 P50 ms) 측정
6. `benchmark_results.json`에 결과를 반영하여 업데이트
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from sentence_transformers import MultiVectorEncoder

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "eval_dataset.json"
RESULTS_PATH = BASE_DIR / "benchmark_results.json"


def main() -> None:
    print("=== Loading KURE-v2 (MultiVectorEncoder) ===")
    t0 = time.perf_counter()
    model = MultiVectorEncoder("nlpai-lab/KURE-v2")
    load_s = time.perf_counter() - t0
    print(f"KURE-v2 loaded in {load_s:.2f}s")

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    facts_corpus = dataset["facts_corpus"]
    queries_eval = dataset["queries_eval"]
    reconcile_pairs = dataset["reconcile_pairs"]

    fact_ids = [f["id"] for f in facts_corpus]
    fact_texts = [f["fact"] for f in facts_corpus]
    queries = [q["query"] for q in queries_eval]
    expected_ids = [q["expected_fact_id"] for q in queries_eval]

    # 1. 문서 인코딩 (60개 Fact)
    print("Encoding 60 facts...")
    doc_embs = model.encode_document(fact_texts)

    # 2. 쿼리 인코딩 (50개 질문)
    print("Encoding 50 queries...")
    query_embs = model.encode_query(queries)

    # 3. MaxSim 유사도 행렬 계산: (50, 60)
    print("Calculating similarity matrix...")
    sim_tensor = model.similarity(query_embs, doc_embs)
    sim_matrix = sim_tensor.cpu().numpy()

    r1, r3, r5, rr_sum = 0, 0, 0, 0.0
    pos_sims = []
    neg_max_sims = []
    misses = []

    for i, exp_id in enumerate(expected_ids):
        target_idx = fact_ids.index(exp_id)
        sims = sim_matrix[i]
        ranked_indices = np.argsort(-sims)
        rank = int(np.where(ranked_indices == target_idx)[0][0]) + 1

        if rank == 1:
            r1 += 1
        if rank <= 3:
            r3 += 1
        if rank <= 5:
            r5 += 1
            rr_sum += 1.0 / rank
        else:
            top1_idx = int(ranked_indices[0])
            misses.append(
                {
                    "query": queries[i],
                    "expected": fact_texts[target_idx],
                    "rank": rank,
                    "top1_pred": fact_texts[top1_idx],
                }
            )

        pos_sims.append(float(sims[target_idx]))
        other_sims = np.delete(sims, target_idx)
        neg_max_sims.append(float(np.max(other_sims)))

    total_q = len(queries_eval)

    # 4. Reconcile 문장 쌍 유사도 측정
    print("Evaluating Reconcile pairs...")
    reconcile_stats = {}
    for group_name, pairs in reconcile_pairs.items():
        left_texts = [p["sentence_a"] for p in pairs]
        right_texts = [p["sentence_b"] for p in pairs]
        l_embs = model.encode_query(left_texts)
        r_embs = model.encode_document(right_texts)
        sims_diag = []
        for j in range(len(pairs)):
            s = model.similarity(l_embs[j : j + 1], r_embs[j : j + 1]).item()
            sims_diag.append(s)

        reconcile_stats[group_name] = {
            "mean_sim": round(float(np.mean(sims_diag)), 4),
            "min_sim": round(float(np.min(sims_diag)), 4),
            "max_sim": round(float(np.max(sims_diag)), 4),
        }

    # 5. Latency 측정
    print("Benchmarking latency...")
    for _ in range(3):
        model.encode_query(["주말에 산 타러 가는 거 좋아하세요?"])

    single_latencies = []
    for q_text in queries:
        t0 = time.perf_counter()
        model.encode_query([q_text])
        single_latencies.append((time.perf_counter() - t0) * 1000.0)

    batch8_latencies = []
    batch_sample = fact_texts[:8]
    for _ in range(15):
        t0 = time.perf_counter()
        model.encode_document(batch_sample)
        batch8_latencies.append((time.perf_counter() - t0) * 1000.0)

    kure_v2_result = {
        "name": "KURE-v2 (154M, Late-Interaction Multi-Vector 128d)",
        "dim": 128,
        "architecture": "Late-Interaction (ColBERT / MaxSim), token-level 128d",
        "params": "154M",
        "recall_at_1": round(r1 / total_q, 4),
        "recall_at_3": round(r3 / total_q, 4),
        "recall_at_5": round(r5 / total_q, 4),
        "mrr_at_5": round(rr_sum / total_q, 4),
        "pos_dist_mean": round(float(np.mean(pos_sims)), 4),
        "neg_min_dist_mean": round(float(np.mean(neg_max_sims)), 4),
        "dist_margin": round(float(np.mean(pos_sims) - np.mean(neg_max_sims)), 4),
        "reconcile": reconcile_stats,
        "reconcile_gap_para_vs_unrel": round(
            reconcile_stats["paraphrase"]["mean_sim"] - reconcile_stats["unrelated"]["mean_sim"], 4
        ),
        "latency_single_p50_ms": round(float(np.percentile(single_latencies, 50)), 2),
        "latency_single_p95_ms": round(float(np.percentile(single_latencies, 95)), 2),
        "latency_single_mean_ms": round(float(np.mean(single_latencies)), 2),
        "latency_batch8_p50_ms": round(float(np.percentile(batch8_latencies, 50)), 2),
        "misses_outside_top5": misses[:3],
    }

    existing = []
    if RESULTS_PATH.exists():
        existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        existing = [item for item in existing if "KURE-v2" not in item.get("name", "")]

    existing.append(kure_v2_result)
    RESULTS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== KURE-v2 BENCHMARK RESULT ===")
    print(json.dumps(kure_v2_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
