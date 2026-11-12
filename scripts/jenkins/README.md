# AI Jenkins prod CI/CD

루트 [Jenkinsfile](../../Jenkinsfile)은 현재 private EC2의 `ci/ai` Multibranch Pipeline용이다. `main`, `dev`에서 기존 AI 검사를 실행하고 `linux/amd64` 이미지를 빌드한다. Docker Pipeline 플러그인 없이 host의 Docker CLI를 사용한다. Controller Java 21은 Python 앱 빌드와 무관하다.

흐름: `checkout scm` → Python 3.12/uv 0.12.18 검사 환경 → lint/format → 잠금 의존성 보안 검사 → unit/API → compile → 앱 Docker build → `/health` smoke → 선택 ECR push → 선택 CLOUD 배포 PR.

## 실행 옵션

| 옵션 | 기본값 | 동작 |
| --- | --- | --- |
| `PUSH_IMAGE` | `false` | 검사·이미지 smoke 성공 후 ECR에 `GIT_COMMIT` 태그로 push |
| `CREATE_GITOPS_PR` | `false` | `main`과 `PUSH_IMAGE=true`에서만 CLOUD 배포 PR 생성 |

기본 실행과 주기 스캔은 이미지 빌드까지만 한다. `dev`에서는 선택 ECR push까지 가능하며 prod PR 요청은 빌드 초기에 실패한다. 두 옵션을 모두 켜면 **CLOUD에 배포용 branch·commit·PR이 생성**된다. AI 소스 저장소에 commit/tag를 만들지 않으며 CLOUD `main` 직접 push와 PR 자동 병합은 하지 않는다.

ECR은 `975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai`다. AWS CLI는 **agent EC2 host**에서 instance profile로 실행한다. push 후 ECR의 digest와 Docker가 기록한 RepoDigest가 같아야 배포용 digest를 남긴다. Docker 로그인 파일은 별도 임시 디렉터리에 만들고 성공·실패 모두 삭제한다. SHA 태그가 이미 존재하면 해당 digest로 pull한 이미지의 Docker image ID를 이번에 검사·빌드한 이미지와 비교한다. config와 root filesystem이 동일할 때만 기존 digest를 재사용하므로, publish-only 검증 후 PR 활성화나 GitHub 오류 이후 재실행이 가능하다. 다르면 기존 태그를 덮어쓰거나 배포하지 않고 실패한다. 캐시 소실·base image 변경 등으로 다른 이미지가 빌드되면 임의의 기존 이미지를 승격하지 말고 새 소스 revision을 검토한다. IAM/네트워크 조회 실패를 이미지 미존재로 취급하지 않는다.

2026-11-12 조회한 ECR repository는 `MUTABLE`이다. 사전 존재 검사와 push 사이에 다른 writer가 같은 태그를 만드는 경쟁은 차단할 수 없으므로, prod 전환 전 동일 repository를 쓰는 기존 Actions와 동시 publish를 막거나 ECR에 SHA 태그 immutability를 별도로 구성해야 한다. 이번 작업은 repository 정책을 변경하지 않았다. 배포에는 태그 대신 push 후 검증한 digest를 고정한다.

앱 검사 컨테이너에는 AWS/GitHub credential이나 Docker socket을 넘기지 않는다. `/opt/ci-venv`에 Linux 의존성을 설치해 workspace의 `.venv`와 분리하고 host UID/GID로 실행한다. 테스트 DB 주소는 `postgresql+asyncpg://ci:ci@127.0.0.1:5432/ktb_eval`이며 DB 호출은 기존 테스트의 SQLite/가짜 의존성으로 대체한다. 실제 DB·LLM·Langfuse secret은 필요 없다. 이미지 smoke는 네트워크를 차단한 상태에서 앱을 import하고 ASGI `/health` 응답을 검사한다. Uvicorn 기동·lifespan이나 운영 DB 연결을 검증하는 단계는 아니다.

기존 Actions의 `pip-audit .` 대신 `uv.lock`의 모든 dependency group을 export해 해당 고정 버전을 검사한다. 취약점이나 검사 API 오류가 발생하면 publish/PR로 진행하지 않는다. 결과는 Jenkins artifacts의 `.ci-reports/`에 unit/API JUnit XML, 보안 검사 JSON, 빌드 image ID, 검증한 digest/reference와 GitOps PR 정보로 남긴다. 현재 runner 플러그인에 맞춰 `archiveArtifacts`만 사용하며 JUnit 플러그인은 추가로 요구하지 않는다. 종료 시 이번 빌드의 이미지 태그와 workspace를 정리한다. Docker base/cache layer의 주기적인 용량 관리는 agent 운영 작업이다.

## 잠금 의존성 보안 패치

보안 감사에서 검출된 기존 잠금 의존성은 2026-11-12에 다음 버전으로 갱신했다. 앱 검사나 보안 gate를 우회하지 않는다.

| 잠금 패키지 | 변경 | 수정 근거 |
| --- | --- | --- |
| certifi | 2026.7.22 → 2026.10.18 | [공식 보안 공지](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2) |
| langgraph-sdk | 0.4.4 → 0.4.6 | [공식 보안 공지](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-3fx2-cqw9-xr3c) |
| opentelemetry-api / sdk | 1.45.0 → 1.45.1 | [API 보안 공지](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h), SDK와 버전을 맞춤 |
| opentelemetry-semantic-conventions | 0.66b0 → 0.66b1 | SDK 1.45.1의 고정 의존성 |
| urllib3 | 2.7.0 → 2.8.0 | [공식 보안 공지](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw) |

## 연결할 credential

1. 기존 인계대로 source GitHub App을 AI/BE에 설치하고 `github-app-star23`을 등록한다. source Contents는 read-only이며 Multibranch의 repository access는 `Infer accessible repository`를 유지한다.
2. 배포 PR을 사용하려면 **별도의 CLOUD 전용 GitHub App**을 CLOUD에만 설치하고 Jenkins GitHub App credential `github-app-star23-gitops`을 등록한다. repository permissions는 Contents read/write, Pull requests read/write, Metadata read-only다. `Advanced → Repository access strategy → Specify accessible repositories`에서 owner `100-hours-a-week`, repository `KTB4-17th-CLOUD`를 명시한다. `Default permissions strategy`는 `All permissions available to the app installation`로 설정해야 PR 권한도 포함된다. `Infer accessible repository`로 두면 AI job의 token이 CLOUD에 접근하지 못한다. App private key는 Jenkins credential store에만 넣는다. [공식 App credential 안내](https://github.com/jenkinsci/github-branch-source-plugin/blob/master/docs/github-app.adoc).

`github-app-star23-gitops`은 `CREATE_GITOPS_PR=false` 실행에는 필요 없다. private key·비밀번호·agent secret·AWS access key는 이 저장소에 넣지 않는다. checkout credential은 `checkout scm`이 job 설정에서 가져오므로 Jenkinsfile에서 다시 바인딩하지 않는다.

## prod 배포 경계

PR 대상은 CLOUD `main`이며 아래 실제 values의 `image.digest`만 한 번의 commit으로 교체한다.

```text
manifest/helm/star23-service/values/onboarding-persona-ai.yaml
manifest/helm/star23-service/values/onboarding-face-certify-ai.yaml
manifest/helm/star23-service/values/chat-ai-practice.yaml
manifest/helm/star23-service/values/chat-ai-simulation.yaml
manifest/helm/star23-service/values/chat-ai-cleanup.yaml
```

현재 차트의 AI migration hook은 없다. **스키마가 바뀐 릴리스는 같은 digest의 이미지로 Alembic migration을 성공시킨 뒤 PR을 병합**해야 한다. DB migration 자동화나 kubectl/클러스터 쓰기 권한을 이 pipeline이 추가하지는 않는다. 서로 다른 릴리스 PR의 병합 순서도 검토해야 한다. 동일 source/digest의 열린 PR은 내용이 일치할 때 재사용하고, 다른 내용의 기존 branch는 덮어쓰지 않는다.

PR 병합 후 기존 Argo CD가 다섯 Application을 자동 동기화한다. Jenkins의 `pr-open` 성공은 **배포 PR 준비 완료**이며 EKS rollout 성공을 뜻하지 않는다. 병합 후 Argo CD의 `Synced`/`Healthy`와 각 서비스 `/health`를 확인한다. 이미지 rollback은 CLOUD의 digest 변경을 되돌리는 PR로 진행하며 DB 변경은 따로 검토한다.

DEV CD는 중단했다. `.github/workflows/dev-cd.yml`의 build/push와 deploy job 모두 `if: ${{ false }}`로 비활성화하며, GitHub의 해당 workflow도 disabled 상태로 둔다. 재개하려면 workflow 활성화와 두 job 조건 복구를 함께 검토한다.

기존 `.github/workflows/prod-ci.yml`, `release.yml`, `prod-cd.yml`은 유지했다. 특히 기존 Actions release/CD는 ECS를 대상으로 하므로 Jenkins cutover 시 중복·다른 플랫폼 배포가 일어나지 않도록 별도로 전환한다. DEV CD 중단만으로 기존 ECS prod 배포가 중지되지는 않는다.

## 활성화 순서

1. 변경을 검토하고 운영자가 AI `main`/`dev`에 Jenkinsfile과 보조 파일을 반영한다. 이 작업에서는 commit/push하지 않는다.
2. source App credential 등록 후 `ci/ai → Scan Multibranch Pipeline Now`로 branch job을 만든다.
3. 두 옵션을 false로 실제 앱 검사·이미지 빌드·artifact를 확인한다. 전체 agent executor는 인계대로 1개여야 BE와 직렬 실행된다.
4. `PUSH_IMAGE=true`만으로 host IAM/ECR digest 검증을 확인한다.
5. CLOUD 전용 App을 등록하고 `main`에서 두 옵션을 true로 실행한다. 생성된 PR·migration 여부를 검토한 후 병합하고 Argo CD rollout을 확인한다.

## 로컬 검증

```sh
docker build --platform linux/amd64 -f Dockerfile.ci -t star23-ai-ci:local .
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh lint
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh format
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh security
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh unit
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh api
CI_IMAGE=star23-ai-ci:local bash scripts/jenkins/run_check.sh compile
docker build --platform linux/amd64 -t star23-ai-build:local .
uv run pytest tests/test_jenkins_gitops.py tests/test_jenkins_publish.py
```

CLOUD를 변경하거나 GitHub에 쓰지 않고 배포 diff를 검토할 수 있다. `SOURCE_SHA`는 checkout의 전체 40자리 SHA, `IMAGE_DIGEST`는 검증한 실제 ECR digest다.

```sh
python3 scripts/jenkins/gitops_release.py \
  --cloud-dir ../KTB4-17th-CLOUD \
  --source-sha "$SOURCE_SHA" --digest "$IMAGE_DIGEST" \
  --output-dir .ci-reports
git -C ../KTB4-17th-CLOUD apply --check "$PWD/.ci-reports/gitops.patch"
```

이 offline 모드는 CLOUD 파일을 읽어 패치 산출물만 생성하며 token도 사용하지 않는다. Jenkins Declarative 문법은 실제 controller의 [공식 linter](https://www.jenkins.io/doc/book/pipeline/development/#linter)로 별도 확인한다. 문법 검사·mock 경계 테스트·로컬 이미지 빌드가 실제 Jenkins 앱 job, ECR push 또는 EKS 배포 성공을 대신하지 않는다.
