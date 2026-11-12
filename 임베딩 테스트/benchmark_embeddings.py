"""KURE-v1 ONNX int8 vs Google EmbeddingGemma 2 ONNX int8 벤치마크 스크립트.

실행 방법:
    uv run python "임베딩 테스트/benchmark_embeddings.py"

동작:
1. 같은 폴더의 `eval_dataset.json`에서 60개 Fact 코퍼스, 50개 한국어 구어체 질문, 30쌍 Reconcile 문장 쌍을 로드합니다.
2. ONNX 모델 파일이 로컬 캐시(`models_cache/`)에 없으면 Hugging Face에서 자동으로 내려받습니다.
3. 6가지 모델 설정을 동일한 CPU 환경(onnxruntime CPUExecutionProvider, intra_op_num_threads=2)에서 측정합니다:
   - KURE-v1 ONNX int8 (1024d, CLS 풀링 - v2 구현 가이드 기본)
   - KURE-v1 ONNX int8 (1024d, Mean 풀링)
   - EmbeddingGemma 2 ONNX int8 (768d, Task Prefix 적용)
   - EmbeddingGemma 2 ONNX int8 (512d MRL 축소, Task Prefix 적용)
   - EmbeddingGemma 2 ONNX int8 (256d MRL 축소, Task Prefix 적용)
   - EmbeddingGemma 2 ONNX int8 (768d, Prefix 미적용)
4. 측정 결과를 `benchmark_results.json`에 저장하고 콘솔에 요약표를 출력합니다.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "eval_dataset.json"
RESULTS_PATH = BASE_DIR / "benchmark_results.json"
MODELS_DIR = BASE_DIR / "models_cache"

DOWNLOAD_TARGETS = [
    (
        "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/onnx/model_quantized.onnx",
        MODELS_DIR / "gemma2_int8" / "model_quantized.onnx",
    ),
    (
        "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/onnx/model_quantized.onnx_data",
        MODELS_DIR / "gemma2_int8" / "model_quantized.onnx_data",
    ),
    (
        "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/tokenizer.json",
        MODELS_DIR / "gemma2_int8" / "tokenizer.json",
    ),
    (
        "https://huggingface.co/LaraAI-Labs/KURE-v1-onnx-int8/resolve/main/model.onnx",
        MODELS_DIR / "kure_v1_int8" / "model_quantized.onnx",
    ),
    (
        "https://huggingface.co/LaraAI-Labs/KURE-v1-onnx-int8/resolve/main/tokenizer.json",
        MODELS_DIR / "kure_v1_int8" / "tokenizer.json",
    ),
]


def ensure_models_downloaded() -> None:
    """필요한 ONNX 모델과 토크나이저가 로컬에 없으면 다운로드한다."""
    (MODELS_DIR / "gemma2_int8").mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "kure_v1_int8").mkdir(parents=True, exist_ok=True)

    def _dl(item: tuple[str, Path]) -> None:
        url, dest = item
        if dest.exists() and dest.stat().st_size > 1000:
            return
        print(f"[Download] {dest.name} 다운로드 중...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
            while chunk := resp.read(4 * 1024 * 1024):
                out.write(chunk)
        print(f"[Download] 완료: {dest.name} ({dest.stat().st_size / (1024 * 1024):.1f} MB)")

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_dl, DOWNLOAD_TARGETS))


class OnnxRunner:
    def __init__(self, model_path: Path, tokenizer_path: Path, max_length: int = 512) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=max_length)
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            pad_id = self.tokenizer.token_to_id("[PAD]") or 0
        self.tokenizer.enable_padding(pad_id=pad_id)
        self.input_names = [inp.name for inp in self.session.get_inputs()]
        self.output_names = [out.name for out in self.session.get_outputs()]
        self._empty_mm = np.zeros((0, 512), dtype=np.float32)

    def run_raw(self, texts: list[str]) -> tuple[list[np.ndarray], np.ndarray]:
        enc = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in enc], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds: dict[str, np.ndarray] = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        if "position_ids" in self.input_names:
            seq_len = input_ids.shape[1]
            pos = np.arange(seq_len, dtype=np.int64)[None, :]
            feeds["position_ids"] = np.repeat(pos, input_ids.shape[0], axis=0)
        for mm_key in ("image_features", "video_features", "audio_features"):
            if mm_key in self.input_names:
                feeds[mm_key] = self._empty_mm
        target_outputs = ["sentence_embedding"] if "sentence_embedding" in self.output_names else None
        return self.session.run(target_outputs, feeds), attention_mask


def l2_normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return (vecs / np.clip(norms, a_min=1e-12, a_max=None)).astype(np.float32)


def make_kure_encoder(
    runner: OnnxRunner, pooling: str = "cls"
) -> tuple[Callable[[list[str]], np.ndarray], Callable[[list[str]], np.ndarray]]:
    def _encode(texts: list[str]) -> np.ndarray:
        outputs, mask = runner.run_raw(texts)
        token_emb = outputs[0]  # (B, L, 1024)
        if pooling == "cls":
            pooled = token_emb[:, 0, :].astype(np.float32)
        else:
            m = np.expand_dims(mask, axis=-1).astype(np.float32)
            pooled = (token_emb * m).sum(axis=1) / np.clip(m.sum(axis=1), a_min=1e-9, a_max=None)
        return l2_normalize(pooled)

    return _encode, _encode


def make_gemma2_encoder(
    runner: OnnxRunner,
    *,
    use_prefix: bool = True,
    truncate_dim: int = 768,
    sim_mode: bool = False,
) -> tuple[Callable[[list[str]], np.ndarray], Callable[[list[str]], np.ndarray]]:
    def _extract_vec(outputs: list[np.ndarray], mask: np.ndarray) -> np.ndarray:
        for out in outputs:
            if out.ndim == 2 and out.shape[-1] == 768:
                vec = out.astype(np.float32)
                if truncate_dim < 768:
                    vec = vec[:, :truncate_dim]
                return l2_normalize(vec)
        token_emb = outputs[0].astype(np.float32)
        m = np.expand_dims(mask, axis=-1).astype(np.float32)
        pooled = (token_emb * m).sum(axis=1) / np.clip(m.sum(axis=1), a_min=1e-9, a_max=None)
        if truncate_dim < pooled.shape[-1]:
            pooled = pooled[:, :truncate_dim]
        return l2_normalize(pooled)

    def encode_query(texts: list[str]) -> np.ndarray:
        if use_prefix:
            p = "task: sentence similarity | query: " if sim_mode else "task: search result | query: "
            texts = [f"{p}{t}" for t in texts]
        outputs, mask = runner.run_raw(texts)
        return _extract_vec(outputs, mask)

    def encode_doc(texts: list[str]) -> np.ndarray:
        if use_prefix:
            p = "task: sentence similarity | query: " if sim_mode else "title: none | text: "
            texts = [f"{p}{t}" for t in texts]
        outputs, mask = runner.run_raw(texts)
        return _extract_vec(outputs, mask)

    return encode_query, encode_doc


def evaluate_model(
    name: str,
    dataset: dict,
    encode_query: Callable[[list[str]], np.ndarray],
    encode_doc: Callable[[list[str]], np.ndarray],
    encode_sim: Callable[[list[str]], np.ndarray],
) -> dict:
    facts_corpus = dataset["facts_corpus"]
    queries_eval = dataset["queries_eval"]
    reconcile_pairs = dataset["reconcile_pairs"]

    fact_ids = [f["id"] for f in facts_corpus]
    fact_texts = [f["fact"] for f in facts_corpus]

    doc_vecs = encode_doc(fact_texts)
    queries = [q["query"] for q in queries_eval]
    expected_ids = [q["expected_fact_id"] for q in queries_eval]
    q_vecs = encode_query(queries)

    sim_matrix = np.matmul(q_vecs, doc_vecs.T)
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

    reconcile_stats = {}
    for group_name, pairs in reconcile_pairs.items():
        left_vecs = encode_sim([p["sentence_a"] for p in pairs])
        right_vecs = encode_sim([p["sentence_b"] for p in pairs])
        pair_sims = np.sum(left_vecs * right_vecs, axis=1)
        reconcile_stats[group_name] = {
            "mean_sim": round(float(np.mean(pair_sims)), 4),
            "min_sim": round(float(np.min(pair_sims)), 4),
            "max_sim": round(float(np.max(pair_sims)), 4),
        }

    for _ in range(3):
        encode_query(["주말에 산 타러 가는 거 좋아하세요?"])

    single_latencies = []
    for q_text in queries:
        t0 = time.perf_counter()
        encode_query([q_text])
        single_latencies.append((time.perf_counter() - t0) * 1000.0)

    batch8_latencies = []
    batch_sample = fact_texts[:8]
    for _ in range(15):
        t0 = time.perf_counter()
        encode_doc(batch_sample)
        batch8_latencies.append((time.perf_counter() - t0) * 1000.0)

    return {
        "name": name,
        "dim": int(doc_vecs.shape[1]),
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


def main() -> None:
    ensure_models_downloaded()
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    kure_dir = MODELS_DIR / "kure_v1_int8"
    gemma_dir = MODELS_DIR / "gemma2_int8"

    print("=== Loading KURE-v1 ONNX int8 ===")
    kure_runner = OnnxRunner(kure_dir / "model_quantized.onnx", kure_dir / "tokenizer.json")
    print("=== Loading EmbeddingGemma-2 ONNX int8 ===")
    gemma_runner = OnnxRunner(gemma_dir / "model_quantized.onnx", gemma_dir / "tokenizer.json")

    results = []
    eq, ed = make_kure_encoder(kure_runner, pooling="cls")
    results.append(evaluate_model("KURE-v1 ONNX int8 (1024d, CLS)", dataset, eq, ed, eq))

    eq_m, ed_m = make_kure_encoder(kure_runner, pooling="mean")
    results.append(evaluate_model("KURE-v1 ONNX int8 (1024d, Mean)", dataset, eq_m, ed_m, eq_m))

    gq_768, gd_768 = make_gemma2_encoder(gemma_runner, use_prefix=True, truncate_dim=768)
    gsim_768, _ = make_gemma2_encoder(gemma_runner, use_prefix=True, truncate_dim=768, sim_mode=True)
    results.append(evaluate_model("EmbeddingGemma-2 ONNX int8 (768d, Prefix)", dataset, gq_768, gd_768, gsim_768))

    gq_512, gd_512 = make_gemma2_encoder(gemma_runner, use_prefix=True, truncate_dim=512)
    gsim_512, _ = make_gemma2_encoder(gemma_runner, use_prefix=True, truncate_dim=512, sim_mode=True)
    results.append(evaluate_model("EmbeddingGemma-2 ONNX int8 (512d MRL, Prefix)", dataset, gq_512, gd_512, gsim_512))

    gq_256, gd_256 = make_gemma2_encoder(gemma_runner, use_prefix=True, truncate_dim=256)
    gsim_256, _ = make_gemma2_encoder(gemma_runner, use_prefix=True, truncate_dim=256, sim_mode=True)
    results.append(evaluate_model("EmbeddingGemma-2 ONNX int8 (256d MRL, Prefix)", dataset, gq_256, gd_256, gsim_256))

    gq_nop, gd_nop = make_gemma2_encoder(gemma_runner, use_prefix=False, truncate_dim=768)
    results.append(evaluate_model("EmbeddingGemma-2 ONNX int8 (768d, No Prefix)", dataset, gq_nop, gd_nop, gq_nop))

    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== BENCHMARK SUMMARY ===")
    for r in results:
        print(
            f"{r['name']:45s} | R@1: {r['recall_at_1']:.2f} | R@5: {r['recall_at_5']:.2f} | "
            f"MRR: {r['mrr_at_5']:.3f} | Margin: {r['dist_margin']:.3f} | "
            f"Single P50: {r['latency_single_p50_ms']:.1f}ms | Batch8 P50: {r['latency_batch8_p50_ms']:.1f}ms"
        )


if __name__ == "__main__":
    main()
