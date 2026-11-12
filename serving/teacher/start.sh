#!/usr/bin/env bash
# RunPod Pod 안에서 teacher(Qwen3.8-27B) vLLM 을 띄운다. 라벨링이 끝나면 Pod 를 Stop 한다.
#
# 필수 환경변수 (RunPod Secrets 로 넣는다)
#   VLLM_API_KEY   Mac 의 TEACHER_LLM_API_KEY 와 같은 값. 없으면 공개 프록시 주소로 누구나 GPU 를 쓴다
#
# 첫 기동은 가중치 55.6GB 다운로드 때문에 오래 걸린다. 모델(HF_HOME)과 컴파일 캐시(VLLM_CACHE_ROOT)를
# 볼륨(/workspace)에 두면 두 번째 기동부터 건너뛴다. 볼륨은 70GB 이상 잡는다.
set -euo pipefail

: "${VLLM_API_KEY:?VLLM_API_KEY 가 없습니다. RunPod Secrets 에 vllm_api_key 를 만들고 환경변수로 넣으세요}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HF_HOME="${HF_HOME:-/workspace/hf}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-/workspace/vllm-cache}"

exec vllm serve --config "${VLLM_CONFIG:-$HERE/config.yaml}" "$@"
