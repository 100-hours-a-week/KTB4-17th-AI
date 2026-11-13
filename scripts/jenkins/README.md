# AI Jenkins staging / prod CI/CD

기존 `ci/ai` Multibranch, `linux && docker` label, executor 1개를 재사용한다. 브랜치별 CI 이후 GitOps 게시와 실제 rollout 완료 확인까지 실행한다.

```text
dev  → CI → 검증한 SHA 이미지 ECR 게시 → CLOUD/main의 staging tag + digest 5개 → Argo CD → staging EKS
main → CI → 검증한 SHA 이미지 ECR 게시 → CLOUD/main의 prod tag + digest 5개    → Argo CD → prod EKS
```

CI는 Python 3.12 / uv 0.12.18 컨테이너의 lint/format/security/unit/API/compile, linux/amd64 앱 빌드와 네트워크 없는 `/health` smoke다. Docker Pipeline 플러그인을 요구하지 않는다. AWS CLI/ECR 인증은 EC2 host의 instance profile을 사용한다.

## 한 번의 환경 활성화

Jenkins **Manage Jenkins → System → Global properties → Environment variables**에 아래 값을 설정한다. 서버 설정이며 build parameter가 아니다. 설정하지 않은 값은 `false`다.

| 서버 환경 변수 | 초기값 | 한 번 true로 활성화한 뒤 |
| --- | --- | --- |
| `AI_STAGING_AUTO_DEPLOY_ENABLED` | `false` | dev CI 성공마다 ECR 게시 + staging tag/digest GitOps commit |
| `AI_PROD_AUTO_DEPLOY_ENABLED` | `false` | main CI 성공마다 ECR 게시 + prod tag/digest GitOps commit |

사전 연결을 확인한 환경만 한 번 활성화한다. 이후 매번 체크박스를 켤 필요가 없다. `dev`는 prod flag를 사용하지 않고 `main`은 staging flag를 사용하지 않는다. feature/fork/PR job과 원본 AI 저장소가 아닌 checkout은 거절한다. 초기 false 상태는 CI 검증·인프라 연결을 위한 상태이며 실배포 완료가 아니다.

기존 `PUSH_IMAGE=false`, `CREATE_GITOPS_PR=false` parameter는 수동 보조 모드다. 자동 활성화 시 `PUSH_IMAGE=false`여도 게시·자동 commit이 이어진다. 수동 PR은 **자동 prod가 꺼진 main + PUSH_IMAGE=true**에서만 허용하며 staging 자동배포 경로에 사용하지 않는다. 자동 모드와 PR 모드를 함께 요청하면 실패한다.

## credential과 CLOUD 쓰기

- `github-app-star23`: AI 원본 Contents read/Metadata read 및 **Actions read**. Actions read는 같은 SHA의 release workflow 조회에 필요하다. checkout과 현재 dev/main SHA 확인에 사용한다. Multibranch repository access는 기존 AI 저장소 설정을 따른다.
- `github-app-star23-gitops`: CLOUD에만 설치한 별도 App. 직접 commit에는 Contents read/write, Metadata read가 필요하다. 보조 PR 모드도 쓰려면 Pull requests read/write를 추가한다.
- `github-app-star23-cloud-read`: CLOUD Contents/Metadata read만 허용하는 별도 App credential. rollout 관찰에 사용한다.
- CLOUD App의 Advanced → Repository access strategy → Specify accessible repositories에 owner `100-hours-a-week`, repository `KTB4-17th-CLOUD`를 지정한다. Default permissions strategy는 설치 App의 필요한 권한을 포함하도록 설정한다. [공식 App credential 안내](https://github.com/jenkinsci/github-branch-source-plugin/blob/master/docs/github-app.adoc).
- CLOUD/main 보호 규칙은 이 App의 **non-force** 자동 commit을 허용해야 한다. 거절되면 HTTP 오류로 실패한다. 필요한 운영 연결은 해당 App에 대한 적절한 branch/ruleset 정책이며, 코드가 보호를 해제하거나 PR 방식으로 우회하지 않는다.
- 소스 읽기와 CLOUD 쓰기 token을 별도 바인딩한다. private key·비밀번호·AWS access key를 코드나 artifact에 넣지 않는다. Argo 관찰에는 별도의 읽기 권한을 사용한다.

credential ID 기재와 등록 완료는 별개다. 실제 등록·branch scan 확인 결과는 검증 보고서에 기록한다.

## 고정된 환경 경계

ECR은 `975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai`다. 태그는 checkout의 전체 `GIT_COMMIT`이고, 배포는 검증한 digest로 고정한다. 기존 SHA 태그가 있으면 pull한 image ID가 이번 검증 이미지와 같을 때만 재사용한다. 다른 이미지로 덮어쓰지 않는다. 현재 MUTABLE 저장소의 조회/push 사이 경쟁은 원자적이지 않으므로 cutover 때 다른 publisher를 정리하거나 별도로 tag immutability를 구성해야 한다.

CLOUD `main`, chart `manifest/helm/star23-service`만 사용한다.

| 소스 | 환경 / 클러스터 | values 경로 | Argo Application |
| --- | --- | --- | --- |
| dev | staging / star-EKS-v3-staging | `values/staging/<role>.yaml` | staging 클러스터의 기존 `<role>` |
| main | prod / star-EKS-v3 | `values/<role>.yaml` | `<role>` |

role은 아래 다섯 개로 고정한다.

```text
onboarding-persona-ai       (namespace user)
onboarding-face-certify-ai  (namespace user)
chat-ai-practice           (namespace chat)
chat-ai-simulation         (namespace chat)
chat-ai-cleanup            (namespace chat)
```

각 파일의 `image.tag`와 `image.digest`만 바꾸고 한 commit으로 묶는다. image-worker·BE·다른 환경 파일은 수정하지 않는다. 중복/escaped YAML image 키와 외부 image repository는 거절한다. staging 파일이 없으면 실패하며 prod 파일을 대신 사용하지 않는다.

GitOps 준비 전과 ref 갱신 직전에 원본 소스 branch SHA를 확인한다. 소스가 진행됐으면 오래된 빌드는 배포를 중단한다. CLOUD ref는 `force:false`로만 갱신한다. 충돌 시 최신 HEAD를 읽고, 자기 대상 tag와 digest가 모두 그대로인 경우에만 다른 변경을 보존하여 최대 3번 시도한다. 다른 릴리스가 대상 tag 또는 digest를 바꾸면 중단한다. 같은 tag/digest는 `unchanged`로 멱등 처리한다. GitHub에는 두 저장소를 묶는 transaction이 없어 마지막 소스 조회와 CLOUD ref 갱신 사이의 짧은 경쟁 구간은 남는다.

`cloud-updated` / `unchanged`는 GitOps 상태다. `pr-open`은 보조 PR 준비 상태다. 실제 배포 완료는 다섯 Application의 동일 CLOUD revision `Synced/Healthy`까지 별도로 확인한다.

## 버전 태그와 digest

- staging: ECR에 전체 source SHA와 `dev-<전체 SHA>`를 같은 이미지로 유지하고, CLOUD에는 `image.tag=dev-<전체 SHA>`와 검증한 `image.digest`를 함께 기록한다.
- prod: 기존 `.github/workflows/release.yml` / `release.config.cjs`의 semantic-release만 버전을 발급한다. Jenkins는 같은 main SHA의 push workflow가 성공할 때까지 최대 600초(5초 간격, HTTP 요청별 timeout 30초) 확인한다. 최신 동일 SHA run의 실패·취소·조회 오류·timeout은 중단 사유다.
- 성공한 workflow와 같은 commit으로 해석되는 `vMAJOR.MINOR.PATCH` 태그를 사용한다. annotated/lightweight를 모두 commit까지 해석하며, 여러 정식 태그가 같은 source에 있으면 모호성으로 중단한다. 성공했지만 그 SHA에 정식 태그가 없으면 `sha-<전체 SHA>`를 사용한다. 과거 버전이나 브랜치 이름·merge 메시지로 버전을 추측하지 않는다.
- CI는 이미 검증한 이미지를 다시 빌드하지 않는다. ECR의 해당 digest manifest를 BatchGetImage로 읽어 PutImage로 추가 태그를 붙인다. 이미 같은 tag/digest면 재사용하고, 다른 digest 또는 조회 실패면 중단한다. 두 필드가 한 CLOUD commit에 들어가며 digest가 같아도 tag 변경은 반영한다. 자동 모드와 수동 PR 모드 모두 같은 검증을 적용한다.
- `release-version.json`은 source SHA, release tag/상태, workflow URL을 기록한다. ECR 확인이 끝난 뒤에만 `image-tag.txt`, `image-digest.txt`, `image-reference.txt`를 남긴다. GitOps 전에도 소스 release 결과를 다시 확인해 임의 버전 주입을 거절한다.
- CLOUD chart는 계속 `repository@digest`로 실행하며 tag는 Pod의 `app.kubernetes.io/version`에 표시한다. chart를 직접 수정하거나 digest를 지우지 않는다.

**운영 연결:** 2026-11-12 조회에서 `star23/ai`는 `MUTABLE`이다. ECR PutImage는 create-if-absent 옵션이 없어 조회 뒤 생성 사이의 덮어쓰기 경쟁을 원자적으로 막으려면 새 버전 태그가 immutable 범위여야 한다. 새 tag는 이 조건을 검사하고 충족하지 않으면 실패한다. 이미 같은 digest인 tag 재사용은 write 없이 가능하다. [AWS tag immutability 문서](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-tag-mutability.html).

CLOUD/AWS 담당이 기존 publisher 영향을 검토해 `dev-*`, `v*`, `sha-*`와 SHA 추적 태그의 보호 정책을 정해야 한다. 이 작업은 정책을 변경하지 않는다. host role에는 기존 push 권한 외에 `ecr:DescribeRepositories`, `ecr:BatchGetImage`, `ecr:PutImage`가 필요하다. source App의 Actions read 추가와 설치 권한 승인을 확인한다. 실제 host 조회에서는 `arn:aws:sts::975779326240:assumed-role/star23-ci-controller/i-068114cc973f72fdc`의 `ecr:DescribeRepositories`가 `arn:aws:ecr:ap-northeast-2:975779326240:repository/star23/ai`에서 AccessDenied로 확인됐다. Terraform/AWS 설정 변경은 이번 범위에 포함하지 않는다. legacy ECS가 켜진 상태에서는 release workflow가 그 CD 완료까지 기다릴 수 있으므로 prod 자동 활성화 전에 앞 절의 cutover를 완료한다.

## CLOUD 준비와 migration

두 환경은 각자의 Argo CD를 사용한다. staging의 기존 12개 앱은 CLOUD의 `argocd/staging`으로 관리하고 `main`과 `values/staging/<role>.yaml`을 추적한다. prod는 기존 `argocd` 루트와 `values/<role>.yaml`을 유지한다. 두 환경 모두 로컬 목적지 `https://kubernetes.default.svc`와 기존 Application/Helm release 이름을 사용한다. 원격 클러스터 등록이나 앱 이름 변경은 하지 않는다.

staging 전용 values는 현재 staging 설정·시크릿 참조·Ingress·이미지를 보존한다. `star23/staging/...` 및 `/star23/staging/...` 참조를 prod 값으로 대체하지 않는다. 앱 namespace·ESO/IRSA·DB 연결은 실제 환경의 기존 구성을 사용한다.

자동 migration hook은 없다. 자동 활성화 전에 해당 환경의 schema 준비를 확인하고, 이후 schema 변경 release는 소스 병합 전에 호환되는 migration 절차를 준비한다. 검증한 **동일 digest** 이미지와 staging 전용 보안 env 파일을 사용해 운영자가 다음을 순서대로 수행한다. 이 구현/검증 작업은 DB migration을 실행하지 않는다.

```sh
# IMAGE_REFERENCE는 .ci-reports/image-reference.txt의 실제 검증한 URI@sha256 값
# /secure/staging-ai.env는 별도로 준비한 staging 전용 DATABASE_URL 등이며 저장소에 넣지 않는다.
docker run --rm --env-file /secure/staging-ai.env --entrypoint /code/.venv/bin/alembic "$IMAGE_REFERENCE" current
docker run --rm --env-file /secure/staging-ai.env --entrypoint /code/.venv/bin/alembic "$IMAGE_REFERENCE" upgrade head
docker run --rm --env-file /secure/staging-ai.env --entrypoint /code/.venv/bin/alembic "$IMAGE_REFERENCE" current
```

현재 revision과 이미지의 Alembic heads 일치, 실행 결과, staging 연결 대상 확인을 배포 기록에 남긴다. prod migration은 별도 운영 절차다.

## 기존 CD 전환과 활성화 순서

1. 이 코드를 AI dev/main에 반영한다.
2. source/CLOUD App credential과 CLOUD main 쓰기 정책을 준비하고 기존 `ci/ai`에서 Scan Multibranch Pipeline Now로 main/dev job을 생성한다.
3. 두 자동 flag를 false로 두고 실제 CI·이미지 빌드를 확인한 뒤, 허용된 수동 ECR 검증을 수행한다.
4. staging values/Argo/namespace/secret/schema를 준비하고 배포 승인 범위에서 staging 연결 검증을 수행한다. `AI_STAGING_AUTO_DEPLOY_ENABLED=true`를 한 번 설정하면 이후 dev CI 성공은 자동 배포로 이어진다.
5. DEV CD는 기존 workflow disabled + build/deploy `if:false` 상태를 유지한다. 진행 중인 구 CD가 없는지 확인한다.
6. prod EKS 전환 전 GitHub repository variable `AI_LEGACY_ECS_CD_ENABLED=false`를 한 번 설정한다. release.yml의 ECS 호출, prod-cd.yml의 promote/deploy 모두 이 값을 검사한다. 미설정은 기존 동작을 보존한다. 실행 중인 ECS CD도 종료됐는지 확인한 뒤 `AI_PROD_AUTO_DEPLOY_ENABLED=true`를 설정한다. prod CI와 semantic release는 유지한다.
7. 배포 증거는 `source SHA → Jenkins URL → Git release/SHA tag → ECR tag와 digest → CLOUD commit → 다섯 Argo Application의 같은 revision Synced/Healthy`로 연결한다. staging 검증 전후 prod values/Application/실행 digest가 같음도 확인한다.

활성화 여부는 Jenkins 서버 설정과 실제 배포 산출물에서 확인한다. rollback 시 먼저 해당 자동 flag를 끄고, 검토한 이전 digest를 새 CLOUD commit으로 복원한다. DB rollback은 별도 검토한다.

## 읽기 전용 배포 관찰

observer는 dev에서 **star-EKS-v3-staging**, main에서 **star-EKS-v3**를 조회한다. host Python 3.9 이상과 AWS CLI가 instance profile로 EKS를 조회하며, 각 클러스터 `argocd`의 다섯 Application에 대한 GET만 필요하다. Secret 읽기·앱 수정·sync 권한은 없다.

Jenkins의 `Wait for environment rollout` 단계는 게시 receipt와 별도 읽기 credential을 사용한다.

```sh
python3 -m scripts.jenkins.observe_rollout --source-branch dev \
  --deployment-file .ci-reports/gitops.json --output-file .ci-reports/argocd-status.json \
  --timeout-seconds 900
```

다섯 Application이 동일한 CLOUD revision에서 Synced/Healthy이고 기대 digest만 실행 중일 때 성공한다. 게시 commit 이후 다른 환경/BE 변경이 들어온 경우에는 후손 revision의 AI tag/digest가 그대로인지 확인한다. 현재 CLOUD/main에서도 기대 tag/digest를 다시 확인해 이미 대체된 릴리스를 성공으로 표시하지 않는다. 권한·환경 계약 오류는 실패하며 일시적인 EKS 조회 오류와 진행 중 rollout은 제한 시간 내 재조회한다. GitOps 게시만 성공한 상태와 실제 배포 완료 상태를 구분해 산출물에 기록한다.

## 로컬 검증

```sh
docker build --platform linux/amd64 -f Dockerfile.ci -t star23-ai-ci:local .
# lint format security unit api compile 각각 실행
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh unit
uv run pytest tests/test_jenkins_gitops.py tests/test_jenkins_autodeploy.py \
  tests/test_jenkins_deployment_policy.py tests/test_jenkins_publish.py tests/test_jenkins_rollout.py \
  tests/test_jenkins_version.py tests/test_jenkins_ecr_policy.py
python3 scripts/jenkins/gitops_release.py --source-branch dev \
  --cloud-dir ../KTB4-17th-CLOUD --source-sha "$SOURCE_SHA" --digest "$IMAGE_DIGEST" \
  --image-tag "dev-$SOURCE_SHA" --output-dir .ci-reports
```

offline mode는 CLOUD를 읽고 패치만 출력한다. staging 파일이 없으면 먼저 CLOUD 담당의 준비를 기다린다. mock fixture는 계약 경로로 검증하며 실제 staging 준비/배포와 구분한다. 결과 artifacts는 .ci-reports의 unit/API XML, 보안 JSON, image ID/tag/digest/reference, release-version.json과 gitops.json이다. 로컬·컨테이너·Jenkins linter·실제 job/ECR·실배포 결과를 각각 기록한다.

## 잠금 의존성 보안 패치

보안 감사에서 검출된 기존 잠금 의존성은 2026-11-12에 다음 버전으로 갱신했다. 앱 검사나 보안 gate를 우회하지 않는다.

| 잠금 패키지 | 변경 | 수정 근거 |
| --- | --- | --- |
| certifi | 2026.7.22 → 2026.10.18 | [공식 보안 공지](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2) |
| langgraph-sdk | 0.4.4 → 0.4.6 | [공식 보안 공지](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-3fx2-cqw9-xr3c) |
| opentelemetry-api / sdk | 1.45.0 → 1.45.1 | [API 보안 공지](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h), SDK와 버전을 맞춤 |
| opentelemetry-semantic-conventions | 0.66b0 → 0.66b1 | SDK 1.45.1의 고정 의존성 |
| urllib3 | 2.7.0 → 2.8.0 | [공식 보안 공지](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw) |
