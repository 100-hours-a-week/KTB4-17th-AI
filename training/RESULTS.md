# 태깅·추출 파인튜닝 결과

> 📌 **기준:** Langfuse 품질 데이터셋의 **regression split** (학습에 안 쓴 문항). 태깅 47건(`applies=true`), 추출 18건(`label_accuracy`).
> 요청당 1회, **greedy(temperature 0)** — vLLM 을 `--override-generation-config '{"temperature": 0}'` 로 띄워서 잰다 (아래 "보는 법").
> 채점: 태깅 `scripts/eval_tagging.py`, 추출 `scripts/eval_extraction.py`. 지표 정의는 `scripts/quality_metrics.py` 하나.
> **채점 기준이나 프롬프트가 바뀌면 모든 버전을 다시 채점해 이 표를 통째로 갱신한다.** 마지막 채점: —

## 한눈에 보기

### 지금까지 확인된 것 (10/19, 골드셋 60건 · 추출 24건 — calibration + regression)

`label_with_teacher.py --gold` 출력을 `score_teacher_gold.py` 로 채점한 값이다. teacher 라벨링 조건(5회 샘플 다수결, temperature 0.7)이라
아래 regression 표(greedy 1회)와 직접 비교하지 않는다.

| 모델 | 태깅 primary F1 | 태깅 완전 일치 | off_topic 양성 | off_topic 오탐 | 추출 차원 판정 | 추출 범위 안 | 서술 자동 점수 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Gemini 2.5 flash-lite (운영)** | **90.2** | **86.7** | 5/6 | 0/54 | 98.6 | 21/24 | 92.7 |
| 교사 Qwen3.8-27B (추론 끔) | 83.1 | 71.7 | 4/6 | 1/54 | **100** | **24/24** | 92.7 |

- 태깅 프롬프트 `67757f18`(차원 정의 버전), 추출 RUBRIC `0481859d`(서술 규칙 변경 전) 기준.
- ⚠️ **태깅은 교사가 운영 모델보다 낮다.** 교사의 주 오류는 정답에 없는 차원을 덧붙이는 과잉 태깅이다 (`positivity` → `disclosure, positivity`,
  간접 단서만 있는 답에 `disclosure`·`assurances`). 학생은 보통 교사 수준까지 따라오므로, 이 교사로 증류하면 운영보다 낮을 가능성이 크다.
  → 학습 전에 정할 것: 교사 개선(추론 켜기·프롬프트), 다른 교사, 또는 "운영보다 싸고 빠르면서 F2 게이트(85%)만 넘기면 된다"로 목표를 둘지.
- 추출 점수는 교사가 운영보다 낫다(차원 판정 100%, 범위 24/24).

### 모델별 비교 — regression (greedy 1회)

| 모델 | 태깅 완전 일치 | primary F1 | secondary F1 | off_topic 양성 | JSON 실패 | 추출 차원 판정 | 미답변 생략 | 범위 안 | 지연 p50 / p95 | 건당 비용 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Gemini 2.5 flash-lite (운영) | | | | | | | | | | |
| 학습 전 Kanana-2 3B | | | | | | | | | | |
| Kanana + LoRA | | | | | | | | | | |
| Kanana + QLoRA | | | | | | | | | | |

- 이전 기준선(10/12, 옛 TAG_PROMPT `23e8e610`): Gemini 2.5 flash-lite 완전 일치 23.4%, primary 29.8%. 프롬프트가 바뀌어 다시 잰다.

### 배포 게이트 (docs/성능평가 02_F2 · 03_F3 5절)

| 기능 | 게이트 | 운영 | LoRA | QLoRA |
| --- | --- | --- | --- | --- |
| 태깅 | primary·off_topic 동시 일치 **85% 이상** (`gate.primary_and_off_topic_pct`) | | | |
| 태깅 | None 실패 **5% 이하** (`failure_pct`) | | | |
| 추출 | 미답변 차원 점수 생략 **100%** (E3-CRIT-01, `unknown_omitted_pct`) | | | |
| 추출 | 점수 허용 범위 통과 **80% 이상** (`score_in_range_pct`) | | | |
| 추출 | 실패(폴백) **5% 이하** (`failure_pct`) | | | |
| 추출 | 서술 정성 평균 **3점 이상** (블라인드 심판) | | | |

- 태깅 E2-CRIT-01(없는 차원)·E2-CRIT-02(점수 키)는 `TaggingAgent` 가 걸러 낸 뒤라 eval 출력으로는 안 보인다. 모델 원출력은 Langfuse 트레이스에서 본다.

## LoRA vs QLoRA — 학습 비용 (`run_metrics.json`)

| 작업 | 모드 | GPU | 원본 정밀도 | 최대 메모리 (GiB) | 학습 시간 | 학습 비용 | eval loss | 어댑터 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 태깅 | LoRA | | bf16 | | | | | |
| 태깅 | QLoRA | | nf4 | | | | | |
| 추출 | LoRA | | bf16 | | | | | |
| 추출 | QLoRA | | nf4 | | | | | |

**판정:** 품질 차이가 아래 오차 범위 안이면 같은 수준으로 보고 더 싼 쪽, 밖이면 품질이 좋은 쪽을 고르되 그 차이가 비용만큼 가치 있는지 적는다.
서빙 비용은 학습 방식이 아니라 원본을 어떤 정밀도로 올리느냐로 정해지므로, 두 어댑터를 같은 서버(같은 원본)에 올려 잰다.

## 버전별 결과

| 버전 | 바꾼 것 | 학습 데이터 (train / val) | 모드 | 학습 시간 | eval loss | 태깅 프롬프트 | 완전 일치 | primary F1 | 추출 차원 판정 | 범위 안 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 학습 전 | — | — | — | — | — | `67757f18` | | | | |
| v1 | 첫 학습 (pilot200 teacher 라벨, 검수 전) | 태깅 1,696 / 198 · 추출 178 / 20 | | | | `67757f18` | | | | |

## 보는 법

- **오차가 크다.** 태깅 regression 47건이라 1건 ≈ 2.1%p, 비율이 70% 근처면 95% 신뢰구간이 약 ±13%p 다. 추출은 18건(1건 ≈ 5.6%p).
  몇 건 차이는 같은 수준으로 본다. 문항 단위 완전 일치보다 라벨 단위 micro F1 이 덜 흔들린다.
- **temperature:** 앱은 temperature 를 보내지 않고, `serving/vllm/config.yaml` 은 `generation-config: vllm` 이라 지금은 **temperature 1.0 샘플링**이다.
  평가 서버는 `serving/vllm/start.sh --override-generation-config '{"temperature": 0}'` 로 띄워 greedy 로 맞춘다.
  OpenRouter(Gemini)는 서버 쪽에서 바꿀 수 없어 기본값으로 잰다 — 운영 조건 그대로라는 뜻이고, 같은 모델을 다시 재면 몇 건 흔들릴 수 있다.
- **기본 모델 커밋:** 서빙(`config.yaml`)은 `revision 6a5d7889` 로 고정돼 있다. 학습도 `train_lora.py --revision` 으로 같은 커밋을 쓴다
  (Colab 노트북 기본값). `run_metrics.json` 의 `revision` 칸으로 확인한다.
- **eval loss:** 버전마다 검증 데이터가 달라 버전끼리 비교하지 않는다. 같은 버전 안에서 내려갔는지(과적합 아님)만 본다.
  태깅 `build_sft.py` 는 문항 id 로 나눠 같은 세션의 답이 train·val 에 섞이므로 실제보다 낙관적이다(LoRA·QLoRA 에 똑같이 적용).
- **학습 비용:** `train_seconds × GPU 시간당 단가`. Colab 은 컴퓨팅 단위를 달러로 환산한 값을 `--gpu-hourly-usd` 로 준다.
- **pilot200 추출 라벨은 서술 규칙 변경(7e10dc0) 전 RUBRIC** 이다. 점수 규칙은 같아서 위 점수 지표에는 영향이 없지만,
  서술·특징은 옛 규칙(답변 인용)을 배운다. 서술 품질을 볼 때는 다시 라벨링한 버전으로 잰다 (`training/README.md`).
- `blind_holdout` 은 최종 판정 때만 `--allow-holdout` 으로 본다.

## 갱신하는 법

```bash
# 1) 학습 (Colab: training/notebooks/lora_qlora_compare_colab.ipynb) → 어댑터 폴더마다 run_metrics.json
# 2) RunPod 에서 어댑터들을 한 서버에 greedy 로 띄운다 (serving/README.md)
#    --lora-modules 를 다시 주면 start.sh 의 tagging-v1 대신 이 목록이 쓰인다. config.yaml 은 max-loras 1 이라 함께 올린다.
#    뜬 뒤 GET /v1/models 에 네 이름이 다 보이는지 확인한다.
TAGGING_ADAPTER_DIR=<LoRA 태깅 adapter> serving/vllm/start.sh \
    --override-generation-config '{"temperature": 0}' --max-loras 4 \
    --lora-modules tagging-lora=<LoRA 태깅> tagging-qlora=<QLoRA 태깅> extraction-lora=<LoRA 추출> extraction-qlora=<QLoRA 추출>
# 3) 채점 (맥)
uv run python scripts/eval_tagging.py --out evals/results/tagging-gemini.json
uv run python scripts/eval_tagging.py --slm --model kanana --out evals/results/tagging-kanana-base.json
uv run python scripts/eval_tagging.py --slm --model tagging-lora --out evals/results/tagging-v1-lora.json
uv run python scripts/eval_tagging.py --slm --model tagging-qlora --out evals/results/tagging-v1-qlora.json
uv run python scripts/eval_extraction.py --slm --model extraction-lora --out evals/results/extraction-v1-lora.json
# 4) Langfuse 실험으로도 남기려면
LLM_MODEL=tagging-lora uv run python scripts/run_langfuse_calibration.py \
    --dataset quality/persona/onboarding-tagging --split regression --run-name kanana-v1-lora-regression
```

표 칸 ↔ 결과 JSON `summary`: 완전 일치 `exact_pct` · primary F1 `primary_f1_pct` · secondary F1 `secondary_f1_pct` ·
off_topic 양성 `off_topic_positive` · JSON 실패 `failure_pct` · 지연 `latency_p50_s`/`latency_p95_s` ·
추출 차원 판정 `dimension_judgment_pct` · 미답변 생략 `unknown_omitted_pct` · 범위 안 `score_in_range_pct`.
