#!/usr/bin/env bash
# Run on the EC2 host: ordinary bridge containers cannot reach its IAM profile.
set +x
set -euo pipefail

reports=${CI_REPORT_DIR:-.ci-reports}
mkdir -p "$reports"
rm -f "$reports/image-digest.txt" "$reports/image-reference.txt" "$reports/image-tag.txt"
: "${AWS_REGION:?}" "${AWS_ACCOUNT_ID:?}" "${ECR_REPOSITORY:?}" "${LOCAL_IMAGE:?}" "${GIT_COMMIT:?}" "${VERSION_IMAGE_TAG:?}"
if [[ "$AWS_REGION" != 'ap-northeast-2' || "$AWS_ACCOUNT_ID" != '975779326240' || "$ECR_REPOSITORY" != 'star23/ai' ]]; then
  echo 'Only the contracted Seoul star23/ai ECR repository is allowed.' >&2
  exit 1
fi
[[ "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]]
if [[ -n "${CHANGE_ID:-}" || -n "${CHANGE_FORK:-}" ]]; then
  echo 'PR and fork jobs cannot publish deployment tags.' >&2
  exit 1
fi
case "${BRANCH_NAME:-}" in
  dev) [[ "$VERSION_IMAGE_TAG" == "dev-$GIT_COMMIT" ]] ;;
  main) [[ "$VERSION_IMAGE_TAG" == "sha-$GIT_COMMIT" || "$VERSION_IMAGE_TAG" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] ;;
  *) echo 'Only original dev/main builds may publish deployment tags.' >&2; exit 1 ;;
esac
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

# Attach the exact stored manifest, never rebuild for a human-readable version.
version_digest=''
if version_digest=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
  --image-ids "imageTag=$VERSION_IMAGE_TAG" --query 'imageDetails[0].imageDigest' --output text \
  2> "$DOCKER_CONFIG/version-lookup-error"); then
  if [[ "$version_digest" != "$digest" ]]; then
    echo 'Version tag already points at a different digest; refusing to overwrite it.' >&2
    exit 1
  fi
elif grep -Fq '(ImageNotFoundException)' "$DOCKER_CONFIG/version-lookup-error"; then
  # ECR PutImage has no create-if-absent option. Fail closed unless the tag is
  # immutable, so a racing publisher cannot be overwritten between lookup/put.
  aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPOSITORY" \
    --output json > "$DOCKER_CONFIG/repository.json"
  python3 scripts/jenkins/ecr_tag_policy.py "$DOCKER_CONFIG/repository.json" "$VERSION_IMAGE_TAG"
  aws ecr batch-get-image --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --image-ids "imageDigest=$digest" --output json > "$DOCKER_CONFIG/manifest-response.json"
  python3 scripts/jenkins/ecr_tag_policy.py manifest "$DOCKER_CONFIG/manifest-response.json" "$digest" "$DOCKER_CONFIG/manifest.json"
  if ! aws ecr put-image --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --image-tag "$VERSION_IMAGE_TAG" --image-digest "$digest" --image-manifest "file://$DOCKER_CONFIG/manifest.json" \
    --query 'image.imageId.imageDigest' --output text > "$DOCKER_CONFIG/tagged-digest" 2> "$DOCKER_CONFIG/version-put-error"; then
    if ! grep -Eq '\((ImageTagAlreadyExistsException|ImageAlreadyExistsException)\)' "$DOCKER_CONFIG/version-put-error"; then
      cat "$DOCKER_CONFIG/version-put-error" >&2
      exit 1
    fi
  fi
  version_digest=$(aws ecr describe-images --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --image-ids "imageTag=$VERSION_IMAGE_TAG" --query 'imageDetails[0].imageDigest' --output text)
  [[ "$version_digest" == "$digest" ]]
else
  cat "$DOCKER_CONFIG/version-lookup-error" >&2
  exit 1
fi
printf '%s\n' "$digest" > "$reports/image-digest.txt"
printf '%s\n' "$repository@$digest" > "$reports/image-reference.txt"
printf '%s\n' "$VERSION_IMAGE_TAG" > "$reports/image-tag.txt"
printf 'Verified published image: %s:%s @ %s\n' "$repository" "$VERSION_IMAGE_TAG" "$digest"
