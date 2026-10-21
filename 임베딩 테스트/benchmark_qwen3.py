"""Qwen/Qwen3-Embedding-4B 실측 벤치마크 스크립트.

실행 방법:
    uv run --with "torch" --with "transformers>=4.51.0" --with "sentence-transformers>=2.7.0" python "임베딩 테스트/benchmark_qwen3.py"

동작:
1. `eval_dataset.json` 로드 (60개 Fact, 50개 구어체 질문, 30쌍 Reconcile)
2. `sentence_transformers.SentenceTransformer('Qwen/Qwen3-Embedding-4B')` 로드 (torch_dtype=bfloat16)
3. 쿼리(`prompt_name='query'`) vs Fact(프롬프트 없음) 코사인 유사도 기반 Recall@1, Recall@3, Recall@5, MRR 측정
4. Reconcile 문장 쌍 유사도 측정 (프롬프트 없이 Fact간 비교)
5. CPU 추론 지연시간 (단일 쿼리 P50/P95 ms, 배치 8건 P50 ms) 측정
6. benchmark_results.json 에 결과 반영
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "eval_dataset.json"
RESULTS_PATH = BASE_DIR / "benchmark_results.json"


def main() -> None:
    print("=== Loading Qwen/Qwen3-Embedding-4B ===")
    t0 = time.perf_counter()
    # CPU 환경에서 메모리 절약과 연산 최적화를 위해 bfloat16 사용
    model = SentenceTransformer(
        "Qwen/Qwen3-Embedding-4B",
        model_kwargs={"torch_dtype": torch.bfloat16},
    )
    load_s = time.perf_counter() - t0
    print(f"Qwen3-Embedding-4B loaded in {load_s:.2f}s")
    print(f"Model prompts available: {getattr(model, 'prompts', {})}")

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    facts_corpus = dataset["facts_corpus"]
    queries_eval = dataset["queries_eval"]
    reconcile_pairs = dataset["reconcile_pairs"]

    fact_ids = [f["id"] for f in facts_corpus]
    fact_texts = [f["fact"] for f in facts_corpus]
    queries = [q["query"] for q in queries_eval]
    expected_ids = [q["expected_fact_id"] for q in queries_eval]

    # 1. 문서 인코딩 (60개 Fact, 프롬프트 없음)
    print("Encoding 60 facts (documents)...")
    t_docs = time.perf_counter()
    doc_embs = model.encode(
        fact_texts,
        batch_size=8,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    print(f"Facts encoded in {time.perf_counter() - t_docs:.2f}s, shape: {doc_embs.shape}")

    # 2. 쿼리 인코딩 (50개 질문, prompt_name='query' 사용 가능 시 적용)
    prompt_kw = {}
    if hasattr(model, "prompts") and "query" in model.prompts:
        prompt_kw["prompt_name"] = "query"
        print("Using prompt_name='query' for queries.")
    else:
        print("No predefined 'query' prompt found; encoding queries directly.")

    print("Encoding 50 queries...")
    t_q = time.perf_counter()
    query_embs = model.encode(
        queries,
        batch_size=8,
        show_progress_bar=True,
        normalize_embeddings=True,
        **prompt_kw,
    )
    print(f"Queries encoded in {time.perf_counter() - t_q:.2f}s, shape: {query_embs.shape}")

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

    # 4. Reconcile 문장 쌍 유사도 측정 (프롬프트 없음)
    print("Evaluating Reconcile pairs...")
    reconcile_stats = {}
    for group_name, pairs in reconcile_pairs.items():
        left_texts = [p["sentence_a"] for p in pairs]
        right_texts = [p["sentence_b"] for p in pairs]
        l_embs = model.encode(left_texts, normalize_embeddings=True)
        r_embs = model.encode(right_texts, normalize_embeddings=True)
        sims = np.sum(l_embs * r_embs, axis=1)
        reconcile_stats[group_name] = {
            "mean_sim": float(np.round(np.mean(sims), 4)),
            "min_sim": float(np.round(np.min(sims), 4)),
            "max_sim": float(np.round(np.max(sims), 4)),
        }

    gap = float(
        np.round(
            reconcile_stats["paraphrase"]["mean_sim"] - reconcile_stats["unrelated"]["mean_sim"],
            4,
        )
    )

    # 5. CPU 추론 지연시간 측정 (워밍업 2회, 5회 단일 쿼리 측정)
    print("Measuring CPU Latency...")
    sample_q = queries[0]
    for _ in range(2):
        _ = model.encode([sample_q], normalize_embeddings=True, **prompt_kw)

    latencies_single = []
    for _ in range(5):
        t_start = time.perf_counter()
        _ = model.encode([sample_q], normalize_embeddings=True, **prompt_kw)
        latencies_single.append((time.perf_counter() - t_start) * 1000.0)

    # 배치 8건 지연시간 (3회 측정)
    sample_batch = queries[:8]
    latencies_batch8 = []
    for _ in range(3):
        t_start = time.perf_counter()
        _ = model.encode(sample_batch, normalize_embeddings=True, **prompt_kw)
        latencies_batch8.append((time.perf_counter() - t_start) * 1000.0)

    result_entry = {
        "name": "Qwen3-Embedding-4B (4.0B, 2560d)",
        "dim": int(query_embs.shape[1]),
        "architecture": "LLM Decoder (Last Token Pooling, bfloat16)",
        "params": "4.0B",
        "recall_at_1": round(r1 / total_q, 4),
        "recall_at_3": round(r3 / total_q, 4),
        "recall_at_5": round(r5 / total_q, 4),
        "mrr_at_5": round(rr_sum / total_q, 4),
        "pos_dist_mean": float(np.round(np.mean(pos_dists), 4)),
        "neg_min_dist_mean": float(np.round(np.mean(neg_min_dists), 4)),
        "dist_margin": float(np.round(np.mean(neg_min_dists) - np.mean(pos_dists), 4)),
        "reconcile": reconcile_stats,
        "reconcile_gap_para_vs_unrel": gap,
        "latency_single_p50_ms": float(np.round(np.median(latencies_single), 2)),
        "latency_single_p95_ms": float(np.round(np.percentile(latencies_single, 95), 2)),
        "latency_single_mean_ms": float(np.round(np.mean(latencies_single), 2)),
        "latency_batch8_p50_ms": float(np.round(np.median(latencies_batch8), 2)),
        "misses_outside_top5": misses,
    }

    print("\n=== Result Summary for Qwen3-Embedding-4B ===")
    print(json.dumps(result_entry, indent=2, ensure_ascii=False))

    # benchmark_results.json 업데이트
    results_list = []
    if RESULTS_PATH.exists():
        results_list = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

    results_list = [r for r in results_list if not r.get("name", "").startswith("Qwen3-Embedding-4B")]
    results_list.append(result_entry)
    RESULTS_PATH.write_text(json.dumps(results_list, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nUpdated {RESULTS_PATH} successfully!")


if __name__ == "__main__":
    main()
