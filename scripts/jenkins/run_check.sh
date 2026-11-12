#!/usr/bin/env bash
# Host wrapper. Does not need the Jenkins Docker Pipeline plugin.
set -euo pipefail
: "${CI_IMAGE:?Build Dockerfile.ci first and set CI_IMAGE}"

docker run --rm --platform linux/amd64 \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace \
  --env HOME=/tmp --env UV_CACHE_DIR=/tmp/uv-cache \
  --env AWS_EC2_METADATA_DISABLED=true \
  "$CI_IMAGE" bash scripts/jenkins/check.sh "${1:-}"
