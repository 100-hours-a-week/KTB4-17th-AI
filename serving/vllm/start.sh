#!/usr/bin/env bash
# RunPod Pod 안에서 태깅·추출용 vLLM 을 띄운다. 이미지(Dockerfile)의 ENTRYPOINT 이자, 연습용 Pod 에서 직접 실행하는 스크립트.
#
# 필수 환경변수 (RunPod Secrets 로 넣는다)
#   VLLM_API_KEY            앱의 SLM_API_KEY 와 같은 값. 없으면 공개 프록시 주소로 누구나 GPU 를 쓴다
# 선택 환경변수
#   TAGGING_ADAPTER_DIR     어댑터 위치 (기본 /workspace/adapters/tagging-v1)
#   TAGGING_ADAPTER_REPO    어댑터가 위 경로에 없으면 받아 올 HF private 리포 (예: <org>/ktb-tagging-lora)
#   TAGGING_ADAPTER_REVISION  그 리포의 커밋·태그 (기본 main). 운영은 태그로 고정한다
#   HF_TOKEN                private 리포를 받을 때
#
# 콜드 스타트를 줄이려고 모델(HF_HOME)과 torch.compile 산출물(VLLM_CACHE_ROOT)을 볼륨(/workspace)에 둔다.
# 두 번째 기동부터는 다운로드·컴파일을 건너뛴다. 앱은 /health 가 200 이 될 때까지 메인 LLM 으로 폴백한다.
set -euo pipefail

: "${VLLM_API_KEY:?VLLM_API_KEY 가 없습니다. RunPod Secrets 에 vllm_api_key 를 만들고 환경변수로 넣으세요}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${VLLM_CONFIG:-$HERE/config.yaml}"
ADAPTER_DIR="${TAGGING_ADAPTER_DIR:-/workspace/adapters/tagging-v1}"

export HF_HOME="${HF_HOME:-/workspace/hf}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-/workspace/vllm-cache}"

if [[ ! -f "$ADAPTER_DIR/adapter_config.json" ]]; then
  : "${TAGGING_ADAPTER_REPO:?$ADAPTER_DIR 에 어댑터가 없습니다. 파일을 올리거나 TAGGING_ADAPTER_REPO 를 지정하세요}"
  echo "downloading adapter $TAGGING_ADAPTER_REPO@${TAGGING_ADAPTER_REVISION:-main} -> $ADAPTER_DIR"
  python3 - "$TAGGING_ADAPTER_REPO" "${TAGGING_ADAPTER_REVISION:-main}" "$ADAPTER_DIR" <<'PY'
import sys

from huggingface_hub import snapshot_download

repo, revision, target = sys.argv[1:]
snapshot_download(repo_id=repo, revision=revision, local_dir=target)
PY
fi

# 어댑터를 다른 기본 모델로 학습했으면 서버는 뜨지만 출력이 망가진다. 뜨기 전에 막는다
python3 - "$ADAPTER_DIR/adapter_config.json" "$CONFIG" <<'PY'
import json
import re
import sys

adapter_base = json.load(open(sys.argv[1]))["base_model_name_or_path"]
served = re.search(r"^model:\s*(\S+)", open(sys.argv[2]).read(), re.M).group(1)
if adapter_base.rstrip("/").split("/")[-2:] != served.split("/")[-2:]:
    sys.exit(f"adapter base model {adapter_base!r} != served model {served!r}")
PY

exec vllm serve --config "$CONFIG" --lora-modules "tagging-v1=$ADAPTER_DIR" "$@"
