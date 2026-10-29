# teacher 라벨링 — Qwen3.8-27B on RunPod

태깅·추출 student(Kanana) 학습 데이터의 정답을 teacher 로 단다. teacher 선정 근거는 `docs/성능평가/10_증류_teacher_모델_선정기준.md` 4.4절.
라벨링 동안만 Pod 를 켜고, 끝나면 Stop 한다. 운영 서빙(`serving/vllm`, Kanana)과는 별개다.

```
Mac (scripts/label_with_teacher.py) ── TEACHER_LLM_* ──▶ RunPod H100 · vLLM 0.30.0 · Qwen/Qwen3.8-27B
        ▲                                                       (serving/teacher/start.sh)
        └ data/synthetic/sub-1000-rewritten (997세션) · evals/quality_datasets/claude_gold (63·24)
```

| 항목 | 값 |
| --- | --- |
| 모델 | `Qwen/Qwen3.8-27B` 커밋 `1d4bf0f` (apache-2.0, BF16 55.6GB, `Qwen3_5ForConditionalGeneration`) |
| GPU | H100 80GB 1장. 가중치가 55.6GB 라 48GB 이하 GPU 에는 BF16 로 안 들어간다 |
| 볼륨 | 100GB, `/workspace` (가중치 + 컴파일 캐시) |
| 호출 수 | 골드 63×5 + 24 = 339회, 합성 10,279×5 + 997 = 52,392회 |
| 추론(thinking) | 끈다. Qwen3.8 템플릿 기본값이 켜짐이라 스크립트가 요청마다 `chat_template_kwargs.enable_thinking=false` 를 보낸다 |

## 1. Pod 띄우기 (RunPod 화면)

1. Secrets 에 `vllm_api_key` 를 만든다. 값은 `openssl rand -hex 24` 로 만들고 Mac 에도 적어 둔다.
2. Network Volume 100GB 를 만든다 (H100 이 있는 리전).
3. Pod: H100 80GB(SXM 또는 PCIe) × 1, 이미지 `vllm/vllm-openai:v0.30.0`, CUDA 13.0 이상 필터,
   HTTP 포트 8000 노출, 환경변수 `VLLM_API_KEY={{ RUNPOD_SECRET_vllm_api_key }}`, 볼륨을 `/workspace` 에 연결.
   이미지 기본 진입점이 vllm 을 바로 띄우므로 Start Command 는 `sleep infinity` 로 둔다.
4. Pod 터미널에서:

```bash
cd /workspace && git clone https://github.com/100-hours-a-week/KTB4-17th-AI.git ai   # 이미 있으면 git -C ai pull
bash ai/serving/teacher/start.sh
```

첫 기동은 다운로드 때문에 수십 분 걸린다. 로그에 `Application startup complete` 가 나오면 된다.
`KV cache` / `Maximum concurrency` 줄을 보고 동시 요청 수(아래 `--concurrency`)를 그 값 이하로 잡는다.

## 2. Mac 에서 연결 확인

```bash
export TEACHER_LLM_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net/v1
export TEACHER_LLM_MODEL=qwen3.8-27b
export TEACHER_LLM_API_KEY=<vllm_api_key 값>

curl -s "$TEACHER_LLM_BASE_URL/chat/completions" -H "Authorization: Bearer $TEACHER_LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.8-27b","messages":[{"role":"user","content":"한 단어로 답해: 하늘 색"}],"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}'
```

답에 `<think>` 가 없어야 한다.

## 3. teacher 정확도 확인 (평가셋, 약 339회)

합성 데이터를 라벨링하기 전에 먼저 본다. 여기서 기준에 못 미치면 대량 라벨링을 하지 않는다.

```bash
uv run python scripts/label_with_teacher.py --gold --gold-dir evals/quality_datasets/claude_gold \
    --out-dir data/teacher/qwen-gold-claude --concurrency 16
uv run python scripts/score_teacher_gold.py data/teacher/qwen-gold-claude \
    /Users/hyerin.cho/KTB-pj/V2/KTB4-17th-AI/data/teacher/gold-gemini-2.5-flash-lite \
    --gold-dir evals/quality_datasets/claude_gold --out data/teacher/qwen-gold-claude-score.json
```

| 지표 | 기준 (제안) | 근거 |
| --- | --- | --- |
| 태깅 primary F1 | 0.85 이상, Gemini 2.5 flash-lite(0.878)와 표본 오차 안 | 10 문서 7.1·7.4 (Qwen + 정의 0.87) |
| 태깅 형식 위반 | 0 | 위반이 학습 데이터로 들어간다 |
| 추출 범위 적중 / 환각률 | 23/24 이상 / 0.6% 이하 | Gemini 기준선 23/24 · 0.006 |
| 추출 호출·파싱 실패 | 0 | 추론이 켜지면 여기서 먼저 깨진다 |

골드셋은 Claude 라벨(사람 검수 아님)이다. 결과를 적을 때 "Claude 라벨 기준"이라고 쓴다.

## 4. 합성 1,000세션 라벨링 (약 52,392회)

```bash
# 시험: 3세션 (태깅 30 + 추출 3)
uv run python scripts/label_with_teacher.py --synthetic data/synthetic/sub-1000-rewritten/sessions.jsonl \
    --limit 3 --out-dir data/teacher/qwen-sub1000-try
# 전체. 끊기면 같은 명령에 --resume 을 붙여 이어서
uv run python scripts/label_with_teacher.py --synthetic data/synthetic/sub-1000-rewritten/sessions.jsonl \
    --out-dir data/teacher/qwen-sub1000 --concurrency 32 --resume
uv run python scripts/check_synthetic_labels.py data/teacher/qwen-sub1000 \
    --sessions data/synthetic/sub-1000-rewritten/sessions.jsonl
```

`data/synthetic/`·`data/teacher/` 는 Git 에서 제외된다. 원본 세션은 V2 작업 폴더에서 복사했다.

## 5. 끝나면

- RunPod 에서 Pod 를 **Stop** 한다 (볼륨은 남겨 두면 다음 기동이 빠르다. 다시 안 쓰면 볼륨도 지운다).
- 태깅: `training/tagging/from_teacher.py` → `build_sft.py` (`training/README.md` 1절).
- 추출: 학습 데이터 변환 코드(`training/extraction/`)가 아직 없다.
