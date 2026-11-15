// Private EC2 runner: Docker CLI on host; Python/uv/app checks in containers.
// Server flags enable automatic dev -> staging and main -> prod after cutover.
pipeline {
    agent { label 'linux && docker' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 90, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '10'))
    }
    parameters {
        booleanParam(name: 'PUSH_IMAGE', defaultValue: false,
            description: 'Publish the validated GIT_COMMIT image to star23/ai in ECR.')
        booleanParam(name: 'CREATE_GITOPS_PR', defaultValue: false,
            description: 'Auxiliary manual prod PR mode; main + PUSH_IMAGE and automatic prod deployment disabled.')
    }
    environment {
        AWS_REGION = 'ap-northeast-2'
        AWS_ACCOUNT_ID = '975779326240'
        ECR_REGISTRY = '975779326240.dkr.ecr.ap-northeast-2.amazonaws.com'
        ECR_REPOSITORY = 'star23/ai'
        SOURCE_REPOSITORY = '100-hours-a-week/KTB4-17th-AI'
    }
    stages {
        stage('Checkout') {
            steps {
                script {
                    def revision = checkout scm
                    env.GIT_COMMIT = revision.GIT_COMMIT
                    if (!(env.GIT_COMMIT ==~ /[0-9a-f]{40}/)) {
                        error('checkout scm did not return a full commit SHA')
                    }
                    if (!(env.BRANCH_NAME in ['main', 'dev']) || env.CHANGE_ID || env.CHANGE_FORK) {
                        error('This credentialed EC2 runner accepts only main/dev branch builds')
                    }
                    if (!(revision.GIT_URL in [
                        'https://github.com/100-hours-a-week/KTB4-17th-AI.git',
                        'https://github.com/100-hours-a-week/KTB4-17th-AI',
                        'git@github.com:100-hours-a-week/KTB4-17th-AI.git'
                    ])) {
                        error('checkout scm must use the original AI repository')
                    }
                    if (params.CREATE_GITOPS_PR && (env.BRANCH_NAME != 'main' || !params.PUSH_IMAGE)) {
                        error('CREATE_GITOPS_PR requires main and PUSH_IMAGE=true')
                    }
                    env.CI_IMAGE = "star23-ai-ci:${env.GIT_COMMIT}-${env.BUILD_NUMBER}"
                    env.LOCAL_IMAGE = "star23-ai-build:${env.GIT_COMMIT}-${env.BUILD_NUMBER}"
                }
                sh 'mkdir -p .ci-reports'
            }
        }
        stage('Build CI environment') {
            steps {
                sh 'docker build --platform linux/amd64 --file Dockerfile.ci --tag "$CI_IMAGE" .'
            }
        }
        stage('Deployment policy') {
            steps {
                script {
                    def manual = (params.PUSH_IMAGE ? ' --manual-push' : '') +
                        (params.CREATE_GITOPS_PR ? ' --manual-pr' : '')
                    def policy = sh(returnStdout: true, script: '''#!/usr/bin/env bash
set -euo pipefail
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace --env HOME=/tmp \
  --env AWS_EC2_METADATA_DISABLED=true --env BRANCH_NAME --env CHANGE_ID --env CHANGE_FORK \
  --env SOURCE_REPOSITORY --env AI_STAGING_AUTO_DEPLOY_ENABLED --env AI_PROD_AUTO_DEPLOY_ENABLED \
  "$CI_IMAGE" python -m scripts.jenkins.deployment_policy''' + manual).trim().split(' ')
                    if (policy.size() != 4) { error('Invalid deployment policy output') }
                    env.DEPLOY_ENVIRONMENT = policy[0]
                    env.AUTO_DEPLOY = policy[1]
                    env.PUBLISH_IMAGE = policy[2]
                    env.MANUAL_GITOPS_PR = policy[3]
                    echo "Deployment environment: ${env.DEPLOY_ENVIRONMENT}; automatic: ${env.AUTO_DEPLOY}"
                }
            }
        }
        stage('Lint') {
            steps { sh 'bash scripts/jenkins/run_check.sh lint' }
        }
        stage('Format') {
            steps { sh 'bash scripts/jenkins/run_check.sh format' }
        }
        stage('Dependency security') {
            steps { sh 'bash scripts/jenkins/run_check.sh security' }
        }
        stage('Unit tests') {
            steps { sh 'bash scripts/jenkins/run_check.sh unit' }
        }
        stage('API tests') {
            steps { sh 'bash scripts/jenkins/run_check.sh api' }
        }
        stage('Compile') {
            steps { sh 'bash scripts/jenkins/run_check.sh compile' }
        }
        stage('Build application image') {
            steps {
                sh '''#!/usr/bin/env bash
set -euo pipefail
docker build --platform linux/amd64 --file Dockerfile --tag "$LOCAL_IMAGE" \
  --label "org.opencontainers.image.revision=$GIT_COMMIT" --iidfile .ci-reports/image-id.txt .
test "$(docker image inspect "$LOCAL_IMAGE" --format '{{.Os}}/{{.Architecture}}')" = 'linux/amd64'
'''
            }
        }
        stage('Application image smoke') {
            steps {
                // Test packaging/imports and /health with networking disabled.
                sh '''#!/usr/bin/env bash
set -euo pipefail
docker run --rm --platform linux/amd64 --network none \
  --env LANGFUSE_TRACING_ENABLED=false \
  --env DATABASE_URL=postgresql+asyncpg://ci:ci@127.0.0.1:5432/ktb_eval \
  --entrypoint /code/.venv/bin/python "$LOCAL_IMAGE" -c '
import asyncio
import httpx
from app.main import app
async def check():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ci") as client:
        response = await client.get("/health")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok"}, response.text
asyncio.run(check())
'
'''
            }
        }
        stage('Resolve existing release version') {
            when { expression { env.PUBLISH_IMAGE == 'true' } }
            steps {
                withCredentials([usernamePassword(credentialsId: 'github-app-star23',
                    usernameVariable: 'SOURCE_APP_ID', passwordVariable: 'SOURCE_GITHUB_TOKEN')]) {
                    script {
                        env.VERSION_IMAGE_TAG = sh(returnStdout: true, script: '''#!/usr/bin/env bash
set +x
set -euo pipefail
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace --env HOME=/tmp \
  --env AWS_EC2_METADATA_DISABLED=true --env SOURCE_GITHUB_TOKEN \
  --env BRANCH_NAME --env CHANGE_ID --env CHANGE_FORK \
  "$CI_IMAGE" python -m scripts.jenkins.release_version --source-sha "$GIT_COMMIT" --output-dir .ci-reports
''').trim()
                    }
                }
            }
        }
        stage('Publish ECR image') {
            when { expression { env.PUBLISH_IMAGE == 'true' } }
            steps { sh 'bash scripts/jenkins/push_image.sh' }
        }
        stage('Publish environment GitOps commit') {
            when { expression { env.AUTO_DEPLOY == 'true' } }
            steps {
                withCredentials([
                    usernamePassword(credentialsId: 'github-app-star23',
                        usernameVariable: 'SOURCE_APP_ID', passwordVariable: 'SOURCE_GITHUB_TOKEN'),
                    usernamePassword(credentialsId: 'github-app-star23-gitops',
                        usernameVariable: 'GITOPS_APP_ID', passwordVariable: 'GITHUB_TOKEN')
                ]) {
                    sh '''#!/usr/bin/env bash
set +x
set -euo pipefail
digest=$(cat .ci-reports/image-digest.txt)
tag=$(cat .ci-reports/image-tag.txt)
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace --env HOME=/tmp \
  --env AWS_EC2_METADATA_DISABLED=true --env BRANCH_NAME --env CHANGE_ID --env CHANGE_FORK \
  --env GITHUB_TOKEN --env SOURCE_GITHUB_TOKEN \
  "$CI_IMAGE" python -m scripts.jenkins.gitops_release \
  --source-sha "$GIT_COMMIT" --digest "$digest" --image-tag "$tag" --apply --output-dir .ci-reports
'''
                }
            }
        }
        stage('Wait for environment rollout') {
            when { expression { env.AUTO_DEPLOY == 'true' } }
            options { timeout(time: 18, unit: 'MINUTES') }
            steps {
                withCredentials([usernamePassword(credentialsId: 'github-app-star23-cloud-read',
                    usernameVariable: 'CLOUD_READ_APP_ID', passwordVariable: 'CLOUD_READ_TOKEN')]) {
                    // Host AWS CLI uses the instance profile; observation needs no Kubernetes writes.
                    sh '''#!/usr/bin/env bash
set +x
set -euo pipefail
python3 -m scripts.jenkins.observe_rollout --source-branch "$BRANCH_NAME" \
  --deployment-file .ci-reports/gitops.json --output-file .ci-reports/argocd-status.json \
  --timeout-seconds 900
'''
                }
            }
        }
        stage('Prepare manual production GitOps PR') {
            when {
                allOf {
                    branch 'main'
                    expression { env.MANUAL_GITOPS_PR == 'true' && !env.CHANGE_ID }
                }
            }
            steps {
                // Separate App: statically scoped to CLOUD, Contents/PR write.
                // The source-checkout credential github-app-star23 stays read-only.
                withCredentials([
                    usernamePassword(credentialsId: 'github-app-star23-gitops',
                        usernameVariable: 'GITOPS_APP_ID', passwordVariable: 'GITHUB_TOKEN'),
                    usernamePassword(credentialsId: 'github-app-star23',
                        usernameVariable: 'SOURCE_APP_ID', passwordVariable: 'SOURCE_GITHUB_TOKEN')
                ]) {
                    sh '''#!/usr/bin/env bash
set +x
set -euo pipefail
digest=$(cat .ci-reports/image-digest.txt)
tag=$(cat .ci-reports/image-tag.txt)
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace --env HOME=/tmp \
  --env AWS_EC2_METADATA_DISABLED=true --env GITHUB_TOKEN --env SOURCE_GITHUB_TOKEN \
  --env BRANCH_NAME --env CHANGE_ID --env CHANGE_FORK \
  "$CI_IMAGE" python -m scripts.jenkins.gitops_release \
  --source-sha "$GIT_COMMIT" --digest "$digest" --image-tag "$tag" --create-pr --output-dir .ci-reports
'''
                }
            }
        }
    }
    post {
        always {
            archiveArtifacts(artifacts: '.ci-reports/**', allowEmptyArchive: true)
        }
        cleanup {
            sh '''#!/usr/bin/env bash
set +e
if [ -n "${CI_IMAGE:-}" ]; then docker image rm "$CI_IMAGE"; fi
if [ -n "${LOCAL_IMAGE:-}" ]; then docker image rm "$LOCAL_IMAGE"; fi
if [ -n "${GIT_COMMIT:-}" ]; then docker image rm "$ECR_REGISTRY/$ECR_REPOSITORY:$GIT_COMMIT"; fi
exit 0
'''
            deleteDir()
        }
    }
}
