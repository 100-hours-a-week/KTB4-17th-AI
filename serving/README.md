# 서빙 — 태깅·추출 전용 SLM (vLLM on RunPod)

앱(FastAPI)과 따로 배포하는 GPU 서빙 설정이다. 앱 이미지(루트 `Dockerfile`)에는 들어가지 않는다.

```
앱 (FastAPI, EC2)                                       RunPod Pod (GPU 1장)
 ├ 대화·질문 생성·연습·시뮬 ── LLM_* ──▶ OpenRouter Gemini
 └ 태깅·추출 ───────────────── SLM_* ──▶ vLLM 0.30.0  ── kakaocorp/kanana-2-3b-instruct
        │ 실패·콜드 스타트면                              └ LoRA tagging-v1
        └──────────── 메인 LLM(Gemini)으로 폴백
```

| 앱 호출 (`name=`) | 보내는 모델 | 설정 |
|---|---|---|
| `persona-tagging` | `tagging-v1` (Kanana + LoRA) | `SLM_TAGGING_MODEL` |
| `persona-extraction`, `persona-extraction-style` | `kanana` (기본 모델) | `SLM_EXTRACTION_MODEL` |
| 그 밖의 모든 호출 | Gemini | `LLM_MODEL` |

추출용 LoRA 를 따로 학습하면 `config.yaml` 의 `max-loras` 를 올리고 `start.sh` 에 `--lora-modules` 를 하나 더 붙인 뒤
`SLM_EXTRACTION_MODEL` 을 그 이름으로 바꾼다. 앱 코드는 바꾸지 않는다.

## 파일

| 파일 | 역할 |
|---|---|
| `vllm/config.yaml` | `vllm serve --config` 설정. 모델·리비전·메모리·LoRA |
| `vllm/start.sh` | Pod 안 진입점. API 키 확인 → 어댑터 준비(HF 다운로드) → 기본 모델 일치 검사 → `vllm serve` |
| `vllm/Dockerfile` | RunPod 템플릿 이미지. `vllm/vllm-openai:v0.30.0` + 위 두 파일 |

## 모델

- `kakaocorp/kanana-2-3b-instruct`, 커밋 `6a5d788` 고정. 구조는 `Qwen3ForCausalLM` (vLLM·LoRA 지원), 3.5B, BF16 약 7GB.
- 채팅 템플릿 기본값이 `no_think` 라 추론 블록 없이 바로 답한다. 태깅 JSON 에 맞다.
- GPU: **16GB 이상** 한 장이면 된다 (RTX A4000·4080·L4·4090 등). RunPod 에서 **CUDA 13.0 이상** 필터로 고른다
  (vLLM 0.30.0 기본 빌드는 드라이버 580+ 필요. 없으면 `Dockerfile` 베이스를 `-cu129` 로).

### 라이선스 — Kanana Open License

- 우리 서비스 안에서 쓰는 건 별도 상업 라이선스 없이 된다 (4.2). API·SI·온디바이스로 **제3자에게 제공·재판매**할 때만 별도 계약 (4.1).
- **의무**: 서비스 화면·소개 페이지·문서 중 사용자가 알아볼 수 있는 곳에 **"Powered by Kanana"** 표기 (3.1(v)).
  어댑터·파생 모델을 외부에 배포하면 라이선스 사본 동봉, 이름 앞에 `Kanana` 접두사.
- 출시 전 기획·법무 확인 필요. 원문: 모델 리포의 `LICENSE`.

## 콜드 스타트

Pod 를 켜면 이미지 기동 → 모델 로드 → (첫 기동이면 다운로드·컴파일) 동안 vLLM 이 응답하지 않는다.

| 쪽 | 대응 |
|---|---|
| 앱 (`app/core/slm.py`) | 시작할 때 `/health` 확인. 실패하면 SLM 을 '내려감'으로 두고 태깅·추출을 **곧장 Gemini 로** 보낸다. 백그라운드에서 `SLM_PROBE_INTERVAL_S`(기본 15초)마다 `/health` 를 다시 보고 200 이면 되돌린다. 요청 중 연결 실패·5xx·404·타임아웃이 나도 같은 상태로 바뀐다 |
| 앱 | SLM 은 재시도하지 않고 한 번 실패하면 메인 LLM 으로 넘긴다. 메인 LLM 은 기존 재시도 규칙(`LLM_RETRY_ATTEMPTS`) 그대로. SLM 이 JSON 을 깨뜨려도 메인 LLM 으로 한 번 더 |
| 서빙 (`start.sh`) | 모델 캐시 `HF_HOME=/workspace/hf`, 컴파일 캐시 `VLLM_CACHE_ROOT=/workspace/vllm-cache` 를 볼륨에 둔다. 두 번째 기동부터 다운로드·컴파일을 건너뛴다 |
| 운영 | Pod 를 Stop 하지 않고 켜 두는 시간대를 정한다. 꺼 둔 동안에도 앱은 Gemini 로 정상 동작한다 |

폴백은 Langfuse 에 `slm-fallback` 이벤트로 남는다. 상태가 바뀌면 앱 로그에
`SLM marked down` / `SLM is healthy again` 이 찍힌다.

## RunPod 에 올리기

### A. 연습용 Pod (처음 한 번, 로그를 보며 확인)

1. RunPod 콘솔 **Secrets** 에 `hf_token`, `vllm_api_key`(`openssl rand -hex 24`) 생성.
2. **Pods → Deploy** → CUDA 13.0 필터 → 16GB 이상 GPU → 템플릿 `Runpod Pytorch` →
   Volume `/workspace` 50GB, **Expose HTTP Ports** `8888,8000`, 환경변수
   `HF_TOKEN={{ RUNPOD_SECRET_hf_token }}`, `VLLM_API_KEY={{ RUNPOD_SECRET_vllm_api_key }}`.
3. **Connect → Jupyter Lab → Terminal**:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
   cd /workspace && uv venv vllm-env --python 3.12 && source vllm-env/bin/activate
   uv pip install "vllm==0.30.0" --torch-backend=auto
   git clone https://github.com/100-hours-a-week/KTB4-17th-AI.git && cd KTB4-17th-AI
   ```

4. 어댑터를 `/workspace/adapters/tagging-v1` 에 둔다 (Jupyter 업로드 / `runpodctl send` / `TAGGING_ADAPTER_REPO`).
5. `serving/vllm/start.sh` 실행 → `Uvicorn running on http://0.0.0.0:8000` 이 보이면 성공.

### B. 운영용 템플릿 (이미지로 고정)

1. 이미지 빌드·푸시 (Apple Silicon 이면 `--platform linux/amd64` 필수):

   ```bash
   docker buildx build --platform linux/amd64 -f serving/vllm/Dockerfile -t <registry>/ktb-slm-vllm:v1 --push serving/vllm
   ```

2. **Templates → New Template**: Container Image `<registry>/ktb-slm-vllm:v1`, Container Disk 30GB,
   Volume `/workspace` 50GB(또는 Network Volume), Expose HTTP Ports `8000`, 환경변수는 A-2 와 같고
   `TAGGING_ADAPTER_REPO`, `TAGGING_ADAPTER_REVISION` 추가. Container Start Command 는 비운다.
3. 그 템플릿으로 Pod Deploy → **Logs** 에서 기동 확인.

### 확인 (Mac)

```bash
export POD_URL="https://<POD_ID>-8000.proxy.runpod.net"
curl -s "$POD_URL/health" -o /dev/null -w "%{http_code}\n"                       # 200 이면 준비 완료
curl -s "$POD_URL/v1/models" -H "Authorization: Bearer $VLLM_API_KEY"            # kanana, tagging-v1
uv run python scripts/eval_tagging.py --base-url "$POD_URL/v1" --model tagging-v1 --out evals/results/tagging-v1.json
```

## 앱 설정 (`.env`)

```dotenv
SLM_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net/v1
SLM_API_KEY=<vllm_api_key 와 같은 값>
SLM_TAGGING_MODEL=tagging-v1
SLM_EXTRACTION_MODEL=kanana
# SLM_PROBE_INTERVAL_S=15
```

`SLM_BASE_URL` 을 비우면 모든 호출이 예전처럼 메인 LLM 으로 간다 (즉시 롤백).
작업별 모델 이름을 비우면 그 작업만 메인 LLM 으로 간다 — 예: 추출 품질이 기준에 못 미치면 `SLM_EXTRACTION_MODEL=` 로 태깅만 SLM.

## 보안

- `VLLM_API_KEY` 없이 뜨지 않게 `start.sh` 가 막는다. 프록시 주소는 공개 인터넷이다.
- `--api-key` 는 `/v1` 경로만 막는다. `/health`, `/metrics` 는 열려 있다 — 운영에서는 앞단 프록시로 허용 경로만 연다.
- 운영 사용자 답변이 RunPod(해외)로 나간다. 국외 이전 고지·동의를 확인한다.
