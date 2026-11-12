#!/usr/bin/env bash
# Run on the EC2 host: ordinary bridge containers cannot reach its IAM profile.
set +x
set -euo pipefail

reports=${CI_REPORT_DIR:-.ci-reports}
mkdir -p "$reports"
rm -f "$reports/image-digest.txt" "$reports/image-reference.txt"
: "${AWS_REGION:?}" "${AWS_ACCOUNT_ID:?}" "${ECR_REPOSITORY:?}" "${LOCAL_IMAGE:?}" "${GIT_COMMIT:?}"
[[ "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]]
export AWS_PAGER=''
account=$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)
if [[ "$account" != "$AWS_ACCOUNT_ID" ]]; then
  echo 'AWS account does not match the configured ECR account.' >&2
  exit 1
fi

registry="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
repository="$registry/$ECR_REPOSITORY"
image="$repository:$GIT_COMMIT"
DOCKER_CONFIG=$(mktemp -d)
export DOCKER_CONFIG
trap 'rm -rf "$DOCKER_CONFIG"' EXIT

# ECR is currently MUTABLE. Never replace an existing SHA tag. A retry may
# reuse it only if Docker proves identical image config + root filesystem.
existing=''
if existing=$(aws ecr describe-images --region "$AWS_REGION" \
  --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$GIT_COMMIT" \
  --query 'imageDetails[0].imageDigest' --output text \
  2> "$DOCKER_CONFIG/lookup-error"); then
  [[ "$existing" =~ ^sha256:[0-9a-f]{64}$ ]]
elif ! grep -Fq '(ImageNotFoundException)' "$DOCKER_CONFIG/lookup-error"; then
  cat "$DOCKER_CONFIG/lookup-error" >&2
  exit 1
fi

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$registry"
if [[ -n "$existing" ]]; then
  docker pull "$repository@$existing"
  local_id=$(docker image inspect "$LOCAL_IMAGE" --format '{{.Id}}')
  remote_id=$(docker image inspect "$repository@$existing" --format '{{.Id}}')
  if [[ "$local_id" != "$remote_id" ]]; then
    echo 'The SHA tag exists with a different image; refusing to overwrite or deploy it.' >&2
    exit 1
  fi
  digest="$existing"
  published_image="$repository@$digest"
  echo 'Reusing the identical previously published image.'
else
  docker tag "$LOCAL_IMAGE" "$image"
  docker push "$image"
  digest=$(aws ecr describe-images --region "$AWS_REGION" \
    --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$GIT_COMMIT" \
    --query 'imageDetails[0].imageDigest' --output text)
  published_image="$image"
fi
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
repo_digests=$(docker image inspect "$published_image" --format '{{range .RepoDigests}}{{println .}}{{end}}')
if ! grep -Fxq "$repository@$digest" <<< "$repo_digests"; then
  echo 'ECR digest does not match the image just pushed.' >&2
  exit 1
fi
printf '%s\n' "$digest" > "$reports/image-digest.txt"
printf '%s\n' "$repository@$digest" > "$reports/image-reference.txt"
printf 'Verified published image: %s@%s\n' "$repository" "$digest"
