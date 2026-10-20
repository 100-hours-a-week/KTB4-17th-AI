# 임베딩 테스트 (Embedding Benchmark Suite)

사용자 페르소나 메모리 RAG (v2) 개발을 위해 후보 임베딩 모델군(**KURE-v1**, **Google EmbeddingGemma 2**, **KURE-v2**, **NGA-KR/ko-embed-v0**, **Qwen/Qwen3-Embedding-4B**, **sionic-ai/comsat-embed-ko-8b-preview**)의 핵심 스펙을 대조하고, 실제 한국어 소개팅·연습대화 도메인 평가셋에서 검색 정확도, Reconcile 판별력, CPU 추론 지연시간을 전수 실측 비교한 테스트 산출물 모음입니다.

---

## 📁 폴더 구성 파일 목록

| 파일명 | 종류 | 설명 |
|---|---|---|
| **[`임베딩_모델_비교_분석_보고서.md`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/임베딩_모델_비교_분석_보고서.md)** | 종합 보고서 (MD) | 측정 방법론·기준, 6대 모델 스펙 대조표, 전 모델 실측 벤치마크 결과, 8B 실측 분석, 최종 권고안 |
| **[`임베딩_모델_비교_분석_보고서.html`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/임베딩_모델_비교_분석_보고서.html)** | 웹 보고서 (HTML) | 브라우저에서 바로 열람 가능한 인터랙티브 다크 테마 대시보드 보고서 |
| **[`eval_dataset.json`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/eval_dataset.json)** | 평가 데이터셋 | 60개 Fact 코퍼스 + 50개 한국어 구어체 평가 질문 + 30쌍 Reconcile 문장 쌍 |
| **[`benchmark_results.json`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/benchmark_results.json)** | 실측 결과 데이터 | 10개 모델/설정의 Recall, MRR, 지연시간, 문장쌍별 유사도 원본 JSON |
| **[`benchmark_embeddings.py`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/benchmark_embeddings.py)** | 벤치마크 스크립트 | KURE-v1 ONNX int8 및 EmbeddingGemma 2 ONNX int8 측정 스크립트 |
| **[`benchmark_kure_v2.py`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/benchmark_kure_v2.py)** | 벤치마크 스크립트 | KURE-v2 (MultiVector ColBERT MaxSim) 단독 측정 스크립트 |
| **[`benchmark_ko_embed.py`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/benchmark_ko_embed.py)** | 벤치마크 스크립트 | NGA-KR/ko-embed-v0 (149M Mean Pooling) 단독 측정 스크립트 |
| **[`benchmark_qwen3.py`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/benchmark_qwen3.py)** | 벤치마크 스크립트 | Qwen/Qwen3-Embedding-4B (4.0B bfloat16) 단독 측정 스크립트 |
| **[`benchmark_comsat_8b.py`](file:///Users/hrun/Documents/ktb4-17/KTB-17DEV/임베딩%20테스트/benchmark_comsat_8b.py)** | 벤치마크 스크립트 | sionic-ai/comsat-embed-ko-8b-preview (8.0B bfloat16) 단독 측정 스크립트 |

---

## 🎯 벤치마크 설계 요약: 무엇으로, 어떻게, 무슨 기준으로 측정했는가?

### 1. 어떤 걸로 측정했는가?
- **하드웨어/런타임**: Apple Silicon Mac (24GB 통합 RAM), Python 3.12, GPU 배제 CPU 실행 (`intra_op_num_threads=2`). 프로덕션 백엔드 CPU 컨테이너 환경을 정확히 모사.
- **도메인 데이터셋 (`eval_dataset.json`, 총 140건)**:
  - **60개 Fact 코퍼스**: 취미, 일상, 연애관, 가치관, 과거이력 5개 범주의 실제 사용자 페르소나 기억.
  - **50개 구어체 질문**: Fact 텍스트와 단순 키워드가 겹치지 않는 한국어 구어체·줄임말 질문.
  - **30쌍 Reconcile 문장**: 동일 의미(10쌍), 수치/상태 모순(10쌍), 완전히 무관한 문장(10쌍).

### 2. 어떻게 측정했는가?
- **문서 색인**: 60개 Fact를 인코딩하여 L2 정규화된 문서 행렬 (60, Dim) 생성.
- **질의 검색**: 50개 질문을 모델별 프롬프트 규격에 맞게 인코딩 후 코사인 유사도 행렬 (50, 60) 계산.
- **순위화**: 유사도 내림차순 정렬 후 사전에 정의된 정답 Fact ID(`expected_fact_id`)의 랭크를 전수 계산.
- **지연시간 실측**: 워밍업 1~2회 수행 후 단일 질의(실시간 채팅 모사) 3~5회 측정하여 P50/P95(ms) 산출.

### 3. 무슨 기준으로 판정했는가?
- **`Recall@5` (100% 필수)**: 가이드 v2 기준 프롬프트에 주입되는 상위 5개 기억 내에 정답이 포함되어야 함.
- **`MRR@5` (순위 역수 평균)**: 1.0에 가까울수록 가장 관련성 높은 기억이 1순위에 정확히 노출됨.
- **`Reconcile Gap` (0.40 이상 필수)**: 동일 의미 유사도와 무관 문장 유사도의 격차. 가이드의 `0.70` 임계값으로 명확히 분기되어 불필요한 LLM API 호출을 방지해야 함.
- **`단일 쿼리 P50` (30ms 이내 권장)**: 실시간 채팅 TTFT(First Token) 500ms 제약 조건 하에서 검색 지연을 최소화해야 함.

---

## 🚀 벤치마크 실행 방법

### 1. KURE-v1 & EmbeddingGemma 2 (ONNX int8)
```bash
uv run python "임베딩 테스트/benchmark_embeddings.py"
```

### 2. KURE-v2 (Late-Interaction Multi-Vector)
```bash
uv run --with "torch" --with "sentence-transformers" python "임베딩 테스트/benchmark_kure_v2.py"
```

### 3. NGA-KR/ko-embed-v0 (149M Mean Pooling)
```bash
uv run --with "torch" --with "sentence-transformers" python "임베딩 테스트/benchmark_ko_embed.py"
```

### 4. Qwen/Qwen3-Embedding-4B (4.0B bfloat16)
```bash
uv run --with "torch" --with "transformers>=4.51.0" --with "sentence-transformers>=2.7.0" python "임베딩 테스트/benchmark_qwen3.py"
```

### 5. sionic-ai/comsat-embed-ko-8b-preview (8.0B bfloat16)
```bash
uv run --with "torch" --with "transformers>=4.51.0" --with "sentence-transformers>=2.7.0" python "임베딩 테스트/benchmark_comsat_8b.py"
```

---

## 📊 6대 모델 핵심 벤치마크 전수 실측 요약

| 모델 | 차원 | 파라미터 | Recall@1 | Recall@5 | MRR@5 | 단일 쿼리 P50 (CPU) | 배치 P50 | Reconcile 분리력 (Gap) | pgvector 호환 |
|---|---|---|---|---|---|---|---|---|---|
| **Qwen3-Embedding-4B** | 2560d | 4.0B | **100.0%** | **100.0%** | **1.000** | 109.3 ms | 710.9 ms (B8) | **0.5292 (전체 1위)** | ⚠️ 2560d 고부하 |
| **comsat-embed-ko-8b** | 4096d | **8.0B** | **98.0%** | **100.0%** | **0.990** | **238.2 ms (매우 느림)** | **676.5 ms (B4)** | **0.4848 (매우 우수)** | ⚠️ 4096d 극심 부하 |
| **KURE-v1 (CLS)** | 1024d | 568M | 94.0% | **100.0%** | 0.970 | **16.6 ms (초고속)** | 138.8 ms (B8) | **0.4214 (최적)** | **완벽 호환 (단일)** |
| **ko-embed-v0** | 768d | **149M** | 88.0% | **100.0%** | 0.940 | **12.2 ms (최고속)** | **23.5 ms (B8)** | **0.4023 (우수)** | **완벽 호환 (단일)** |
| **KURE-v2** | 128d | 154M | 96.0% | **100.0%** | 0.980 | **20.8 ms (초고속)** | 30.2 ms (B8) | **29.19 pt (완전 분리)** | ⚠️ ColBERT 엔진 필요 |
| **EmbeddingGemma 2** | 768d | 270M | 96.0% | **100.0%** | 0.975 | 130.3 ms | 159.2 ms (B8) | 0.1755 (좁음) | 완벽 호환 (단일) |
