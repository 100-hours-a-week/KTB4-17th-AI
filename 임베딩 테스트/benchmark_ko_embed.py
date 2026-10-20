"""NGA-KR/ko-embed-v0 (149M, 768d 단일 밀집 벡터) 벤치마크 스크립트.

실행 방법:
    uv run --with "torch" --with "sentence-transformers" python "임베딩 테스트/benchmark_ko_embed.py"

동작:
1. `eval_dataset.json` 로드 (60개 Fact, 50개 구어체 질문, 30쌍 Reconcile)
2. `sentence_transformers.SentenceTransformer('NGA-KR/ko-embed-v0')` 로드
3. 쿼리(`prompt_name='query'`) vs Fact(`prompt_name='passage'`) 코사인 유사도 기반 Recall@1, Recall@3, Recall@5, MRR 측정
4. Reconcile 문장 쌍 유사도 측정
5. CPU 추론 지연시간 (단일 쿼리 P50/P95 ms, 배치 8건 P50 ms) 측정
6. benchmark_results.json 에 결과 반영
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "eval_dataset.json"
RESULTS_PATH = BASE_DIR / "benchmark_results.json"


def main() -> None:
    print("=== Loading NGA-KR/ko-embed-v0 ===")
    t0 = time.perf_counter()
    model = SentenceTransformer("NGA-KR/ko-embed-v0")
    load_s = time.perf_counter() - t0
    print(f"ko-embed-v0 loaded in {load_s:.2f}s")

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    facts_corpus = dataset["facts_corpus"]
    queries_eval = dataset["queries_eval"]
    reconcile_pairs = dataset["reconcile_pairs"]

    fact_ids = [f["id"] for f in facts_corpus]
    fact_texts = [f["fact"] for f in facts_corpus]
    queries = [q["query"] for q in queries_eval]
    expected_ids = [q["expected_fact_id"] for q in queries_eval]

    # 1. 문서 인코딩 (60개 Fact, prompt='passage')
    print("Encoding 60 facts...")
    doc_embs = model.encode(fact_texts, prompt_name="passage", normalize_embeddings=True)

    # 2. 쿼리 인코딩 (50개 질문, prompt='query')
    print("Encoding 50 queries...")
    query_embs = model.encode(queries, prompt_name="query", normalize_embeddings=True)

    # 3. 코사인 유사도 행렬: (50, 60)
    print("Calculating similarity matrix...")
    sim_matrix = np.matmul(query_embs, doc_embs.T)
    dist_matrix = 1.0 - sim_matrix

    r1, r3, r5, rr_sum = 0, 0, 0, 0.0
    pos_dists = []
    neg_min_dists = []
    misses = []

    for i, exp_id in enumerate(expected_ids):
        target_idx = fact_ids.index(exp_id)
        sims = sim_matrix[i]
        dists = dist_matrix[i]
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

        pos_dists.append(float(dists[target_idx]))
        other_dists = np.delete(dists, target_idx)
        neg_min_dists.append(float(np.min(other_dists)))

    total_q = len(queries_eval)

    # 4. Reconcile 문장 쌍 유사도 측정
    print("Evaluating Reconcile pairs...")
    reconcile_stats = {}
    for group_name, pairs in reconcile_pairs.items():
        left_texts = [p["sentence_a"] for p in pairs]
        right_texts = [p["sentence_b"] for p in pairs]
        l_embs = model.encode(left_texts, normalize_embeddings=True)
        r_embs = model.encode(right_texts, normalize_embeddings=True)
        pair_sims = np.sum(l_embs * r_embs, axis=1)

        reconcile_stats[group_name] = {
            "mean_sim": round(float(np.mean(pair_sims)), 4),
            "min_sim": round(float(np.min(pair_sims)), 4),
            "max_sim": round(float(np.max(pair_sims)), 4),
        }

    # 5. Latency 측정
    print("Benchmarking latency...")
    for _ in range(3):
        model.encode(["주말에 산 타러 가는 거 좋아하세요?"], prompt_name="query")

    single_latencies = []
    for q_text in queries:
        t0 = time.perf_counter()
        model.encode([q_text], prompt_name="query")
        single_latencies.append((time.perf_counter() - t0) * 1000.0)

    batch8_latencies = []
    batch_sample = fact_texts[:8]
    for _ in range(15):
        t0 = time.perf_counter()
        model.encode(batch_sample, prompt_name="passage")
        batch8_latencies.append((time.perf_counter() - t0) * 1000.0)

    result = {
        "name": "ko-embed-v0 (149M, 768d, skt/A.X-Encoder)",
        "dim": 768,
        "architecture": "Single-vector (Mean Pooling), 768d",
        "params": "149M",
        "recall_at_1": round(r1 / total_q, 4),
        "recall_at_3": round(r3 / total_q, 4),
        "recall_at_5": round(r5 / total_q, 4),
        "mrr_at_5": round(rr_sum / total_q, 4),
        "pos_dist_mean": round(float(np.mean(pos_dists)), 4),
        "neg_min_dist_mean": round(float(np.mean(neg_min_dists)), 4),
        "dist_margin": round(float(np.mean(neg_min_dists) - np.mean(pos_dists)), 4),
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
        existing = [item for item in existing if "ko-embed-v0" not in item.get("name", "")]

    existing.append(result)
    RESULTS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== ko-embed-v0 BENCHMARK RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
