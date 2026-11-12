// Private EC2 runner: Docker CLI on host; Python/uv/app checks in containers.
// Existing GitHub Actions remain active until this pipeline is validated.
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
            description: 'main + PUSH_IMAGE only: create a CLOUD branch/commit/PR. Merging it starts prod Argo CD rollout.')
    }
    environment {
        AWS_REGION = 'ap-northeast-2'
        AWS_ACCOUNT_ID = '975779326240'
        ECR_REGISTRY = '975779326240.dkr.ecr.ap-northeast-2.amazonaws.com'
        ECR_REPOSITORY = 'star23/ai'
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
                    if (!(env.BRANCH_NAME in ['main', 'dev']) || env.CHANGE_ID) {
                        error('This credentialed EC2 runner accepts only main/dev branch builds')
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
        stage('Publish ECR image') {
            when { expression { params.PUSH_IMAGE } }
            steps { sh 'bash scripts/jenkins/push_image.sh' }
        }
        stage('Create production GitOps PR') {
            when {
                allOf {
                    branch 'main'
                    expression { params.PUSH_IMAGE && params.CREATE_GITOPS_PR && !env.CHANGE_ID }
                }
            }
            steps {
                // Separate App: statically scoped to CLOUD, Contents/PR write.
                // The source-checkout credential github-app-star23 stays read-only.
                withCredentials([usernamePassword(credentialsId: 'github-app-star23-gitops',
                    usernameVariable: 'GITOPS_APP_ID', passwordVariable: 'GITHUB_TOKEN')]) {
                    sh '''#!/usr/bin/env bash
set +x
set -euo pipefail
digest=$(cat .ci-reports/image-digest.txt)
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --workdir /workspace --env HOME=/tmp \
  --env GITHUB_TOKEN --env BRANCH_NAME --env CHANGE_ID \
  "$CI_IMAGE" python scripts/jenkins/gitops_release.py \
  --source-sha "$GIT_COMMIT" --digest "$digest" --create-pr --output-dir .ci-reports
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
