# 파인튜닝 — 온보딩 태깅

OpenRouter(외부 API) 대신 오픈 웨이트 모델에 LoRA 어댑터를 학습해 vLLM 으로 직접 서빙하기 위한 파이프라인이다.
첫 대상은 온보딩 태깅(`TaggingAgent`)이다. 출력이 짧은 JSON이라 정답과 자동으로 비교할 수 있다.

```
학습 데이터(JSONL) ─ build_sft.py ─▶ train/val ─ train_lora.py (GPU) ─▶ 어댑터
                     검증·누수 검사                                       │
평가 데이터셋(#91) ◀── scripts/eval_tagging.py ◀── vLLM (OpenAI 호환) ◀────┘
     같은 기준으로 지금 모델과 비교
```

## 기준선 (2026-10-12, regression split 47건)

| 모델 | 완전 일치 | primary | secondary | off_topic | JSON 실패 | 지연 p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| google/gemini-2.5-flash-lite (OpenRouter) | 23.4% | 29.8% | 72.3% | 100% (4/4) | 0% | 0.81s / 2.39s |

주 오류는 정답에 없는 차원을 덧붙이는 과잉 태깅이다(예: 정답 `disclosure` → `disclosure, openness`).
평가 라벨은 아직 사람 이중 검수 전이다(`reviewFlags: human_anchor_pending`). 라벨 자체가 틀린 케이스가 있을 수 있다.

## 1. 학습 데이터

한 줄에 한 건, `training/tagging/data.py` 의 `TagExample` 형식이다. 형식 예시는 `tagging/examples.sample.jsonl` 이다(검수 전이라 학습용이 아님).

| 필드 | 설명 |
| --- | --- |
| `question`, `answer` | 온보딩 질문과 사용자 답변 |
| `primary`, `secondary`, `off_topic` | 정답 태그. 19개 등록 차원만 |
| `source` | `team_written` · `synthetic` · `production_consented` |
| `reviewed_by` | 정답을 확인한 사람. 없으면 학습에서 막는다 |
| `consent_basis` | 운영 데이터일 때 학습 활용 동의 근거 |

`build_sft.py` 가 막는 것: 등록되지 않은 차원, primary·secondary 중복, off_topic 인데 태그가 있는 경우, 검수 없는 라벨,
동의 근거 없는 운영 데이터, **평가 데이터셋과 같은 답변(평가 누수)**, 중복.

운영 대화를 쓰려면 학습 활용 동의가 있어야 한다. Gemini 출력을 정답으로 쓰는 증류는 제공사 약관(경쟁 모델 개발 제한)을
확인해야 한다.

### 합성 세션 + teacher 라벨 (2026-10-15)

기준선의 낮은 정답률은 모델보다 `TAG_PROMPT` 문제였다. 차원 이름만 있어 모델이 영어 키로 뜻을 짐작했다.
`TAG_PROMPT` 에 차원 정의와 유보 규칙을 넣은 뒤 골드셋(calibration+regression 60건) primary F1 이
flash-lite 3.1 기준 0.33 → 0.88 이 됐다. 그래서 teacher 라벨을 학습 데이터 후보로 쓴다. teacher 는 출력 학습 제약이 적은
오픈 웨이트 `qwen/qwen3.8-27b`(Apache 2.0, 공식 LICENSE 파일 확인 전제)다. 선정 근거·측정 결과는
`docs/성능평가/10_증류_teacher_모델_선정기준.md` 에 있다(docs 는 로컬 산출물).

답변을 만드는 사용자 시뮬레이터는 teacher 와 다른 계열인 `google/gemini-3.8-flash`(프롬프트 v2.1)가 기본이다.
시드 성향 충실도가 가장 높았다(구간 일치 0.87, GLM 계열 최선 0.73). `SIM_LLM_MODEL` 로 바꿀 수 있다.
비용은 1,000세션 기준 시뮬레이터 약 $12 + 질문 생성 약 $2 + teacher 라벨링 약 $28, 합계 약 $42 다.

```bash
uv run python scripts/generate_synthetic_onboarding.py --n 200 --out data/synthetic/sessions.jsonl   # 시드 성향 → 합성 온보딩
uv run python scripts/label_with_teacher.py --synthetic data/synthetic/sessions.jsonl --out-dir data/teacher/run1
uv run python scripts/check_synthetic_labels.py data/teacher/run1 --sessions data/synthetic/sessions.jsonl
uv run python training/tagging/from_teacher.py data/teacher/run1/tagging.jsonl --out data/tagging.jsonl
uv run python training/tagging/build_sft.py data/tagging.jsonl --allow-unreviewed   # 검수 전에는 시험용으로만
```

teacher 라벨은 `reviewed_by` 가 비어 있어 사람이 확인하기 전에는 학습에 들어가지 않는다. 검수는 5회 샘플 판정 일치율
(`aggregate.agreement`)이 낮은 것부터 본다. teacher 자체의 골드셋 점수는 `scripts/score_teacher_gold.py` 로 잰다.
teacher 를 RunPod 에 직접 띄워 라벨링하는 절차는 `serving/teacher/README.md` 에 있다.

```bash
uv run python training/tagging/build_sft.py data/tagging.jsonl --out training/tagging/out
```

prompt 는 서빙 코드의 `TAG_PROMPT` 와 `tagging_messages()` 를 그대로 쓴다. `TAG_PROMPT` 를 고치면
`manifest.json` 의 `prompt_sha256` 이 바뀌므로 어댑터를 다시 학습한다.

## 2. 학습 (외부 GPU)

7~8B 모델 LoRA 는 24GB GPU(L4·A10G·RTX 4090) 한 장이면 된다. 메모리가 모자라면 `--load-in-4bit`(QLoRA).
학습 데이터가 해외 GPU 클라우드로 나가므로, 운영 데이터를 쓰게 되면 국외 이전 고지·동의를 확인한다.

```bash
uv sync                                     # build_sft.py 가 app 코드를 import
pip install -r training/requirements.txt    # torch·trl 등은 서버 이미지에 넣지 않는다
python training/tagging/train_lora.py --base-model <HF 모델 id> \
    --data-dir training/tagging/out --output-dir training/tagging/adapters/v1
```

- 손실은 completion(정답 JSON)에만 걸린다. 프롬프트는 외우지 않는다.
- `adapter/` 에 `data_manifest.json`(데이터·프롬프트 해시)과 `training_args.json` 이 함께 저장된다. 어댑터는 HF Hub(private)나 S3 에 버전으로 올린다.
- 기본 모델은 한국어 성능과 **상업 이용 가능한 라이선스**를 함께 본다. 상업 이용이 제한된 라이선스도 있다.
- `trl` 은 1.14.0 으로 고정했다. 1.15 는 Triton 커널을 무조건 써서 CUDA 가 없으면 돌지 않는다. `--max-steps 4` 로 노트북에서도 파이프라인을 점검할 수 있다.

### 추출 어댑터 (`training/extraction/`)

teacher 의 `extraction.jsonl` 을 운영과 같은 프롬프트(`RUBRIC` + 대화 + 답한 항목)의 학습 파일로 바꾼다. 어떤 행을 왜 빼는지는 `build_sft.py` 머리말과 `manifest.json` 의 `skipped` 에 있다.

```bash
uv run python training/extraction/build_sft.py data/teacher/qwen-fp8-sub1000/extraction.jsonl --allow-unreviewed
python training/tagging/train_lora.py --base-model kakaocorp/kanana-2-3b-instruct \
    --data-dir training/extraction/out --output-dir training/extraction/adapters/extraction-v1 \
    --max-length 4096 --batch-size 4 --grad-accum 4
```

- 추출 예시는 입력과 정답을 합쳐 최대 약 3,350 토큰이다. `--max-length` 기본값(2048)이면 정답이 잘려 학습이 망가진다.
- 입력이 길어 메모리를 많이 쓰므로 배치를 줄이고 `--grad-accum` 으로 같은 업데이트 크기를 유지한다.
- `--base-model` 에는 로컬 경로가 아니라 HF ID 를 넘긴다. `serving/vllm/start.sh` 가 어댑터의 기본 모델 이름을 검사한다.

## 3. 서빙과 평가

기본 모델은 `kakaocorp/kanana-2-3b-instruct` 다. 학습(`--base-model`)과 서빙(`serving/vllm/config.yaml`)이 같아야 한다.
서빙·RunPod·콜드 스타트는 `serving/README.md`.

```bash
serving/vllm/start.sh   # RunPod Pod 안. 기본 모델·LoRA 설정은 serving/vllm/config.yaml
uv run python scripts/eval_tagging.py --slm --model tagging-v1 \
    --out evals/results/tagging-v1.json
```

평가 스크립트는 운영 `TaggingAgent` 를 그대로 호출하므로 프롬프트·타임아웃(15초)·JSON 파싱·차원 필터가 운영과 같다.
기본은 regression split 이다. `blind_holdout` 은 최종 판정 때만 `--allow-holdout` 으로 본다.

점수를 Langfuse 에서 실행끼리 비교하려면 결과 파일을 올린다. `.env` 에 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY` 가 있어야 한다.

```bash
uv run python scripts/upload_eval_scores.py evals/results/tagging-v1.json --dry-run
uv run python scripts/upload_eval_scores.py evals/results/tagging-v1.json
```

세션 `eval-<실행 이름>`, 환경 `eval-results` 로 올라간다. 케이스 점수는 trace 에, 요약 점수는 세션에 붙는다. 다시 올리면 덮어쓴다.
`evals/_harness/stage5_run.py` 결과도 같은 명령으로 올린다. 입력·출력·Judge 근거는 올리지 않는다.

### 전환 기준 (제안)

- regression 완전 일치가 기준선(23.4%)보다 높고, off_topic 양성을 모두 잡고, JSON 실패 1% 이하
- 지연 p95 가 운영 타임아웃(4초)보다 충분히 낮음
- 위를 통과하면 `blind_holdout` 으로 한 번 확인한 뒤 `LLM_BASE_URL`·`LLM_MODEL` 을 바꿔 일부 트래픽부터 전환

## 4. 문장 판정 카드 다수결로 자동 학습 (태깅)

사용자들이 축 질문 카드("이 말은 '연락 빈도'에 대한 이야기인가요?")에 답한 다수결로 태깅 어댑터를 고친다.
카드는 차원 하나만 묻기 때문에, 적재 때 저장한 champion 태거 전체 출력에서 **판정한 차원만** 다수결로 바꿔 예시를 만든다.
한 차원 라벨만으로 전체를 정답 삼으면 묻지 않은 차원을 지우는 법을 배우기 때문이다.

```
BE 답변 묶음 → 다수결 판정 → (AGREE 250 · 확인된 DISAGREE 100 증가) → Discord 학습 후보 알림
  ↓ 사람
scripts/review_disagreements.py     DISAGREE 검수 (confirm · reject)
scripts/export_crowd_tagging.py     train.jsonl · eval.jsonl → HF private dataset (crowd-<날짜>-t<후보 번호> 태그)
  ↓ 사람이 학습 Pod 를 켠다
training/pipeline/run_tagging.py    받기 → build_sft → train_lora → vLLM(champion·candidate) 평가 → gate → candidate-* 태그 · Discord
  ↓ 사람 승인
serving/vllm 의 TAGGING_ADAPTER_REVISION 을 candidate 태그로 교체
```

- 학습 정답: AGREE 는 다수결이 확인으로 인정(`reviewed_by = "crowd"`), DISAGREE 는 사람이 confirm 한 것만. AMBIGUOUS 차원은 champion 출력 그대로
- 학습 · 평가 분리는 `family_id` 해시(20% 평가). 평가 묶음 문장은 학습에 들어가지 않는다
- 게이트(`training/pipeline/gate.py`): 사람 골드셋(완전 일치 · primary · 실패율)과 다수결 평가셋(차원 라벨 정확도 · AMBIGUOUS 단정 비율 · 실패율) 모두 champion 보다 나빠지지 않아야 통과
- 다수결 데이터는 운영 답변에서 나온 것이라 공개 학습 레포에 올리지 않는다. HF private dataset 으로만 옮긴다 (`data/crowd/` 는 gitignore)
