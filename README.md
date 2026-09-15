# ktb-git

KTB 팀의 이슈 → 브랜치 → 커밋 → Push → PR 규칙을 터미널에서 안내하는 도구입니다. macOS/Linux, Python 3.11+, `git`, `gh`, `uv`가 필요합니다.

```sh
uv sync
gh auth login
uv run ktb init
uv run ktb
```

`ktb`만 실행하면 현재 브랜치, 연결된 이슈, 변경 파일 수, 기준 브랜치보다 앞선 커밋 수, 열린 PR을 보여 준 뒤 다음 작업을 고르는 메뉴가 열립니다.

```text
브랜치: feat/garnet-login
연결 이슈: #42
변경 파일: 2개
기준 브랜치보다 앞선 커밋: 0
PR: 없음
다음: ktb commit으로 변경 사항을 커밋하세요.

이슈 만들고 브랜치 시작
커밋
Push하고 PR 열기 (앞선 커밋이 필요하면 선택 불가)
설정
종료
```

명령을 직접 실행할 수도 있습니다. `ktb start`는 새 이슈를 만들거나 기존 이슈를 고른 뒤 브랜치를 만듭니다. `ktb commit`은 변경 파일을 스테이징하고 Conventional Commit 메시지를 묻습니다. `ktb ship`은 검사를 통과한 브랜치를 Push하고 PR을 엽니다. `ktb status`는 상태만 표시합니다. `uv run ktb --help`에서 모든 옵션을 볼 수 있습니다.

| 작업 | 규칙 |
|---|---|
| 이슈 제목 | `[feat][가넷] 로그인 구현`처럼 `[분류][담당자] 작업 내용` |
| 기능 브랜치 | `feat/garnet-login` → `origin/dev`에서 시작, PR 대상 `dev` |
| 수정 브랜치 | `fix/garnet-login` → `origin/dev`에서 시작, PR 대상 `dev` |
| 긴급 수정 | `hotfix/garnet-login` → `origin/main`에서 시작, PR 대상 `main` |
| 커밋 | `feat: add login` 또는 `feat!: change API`와 본문·Footer |
| PR 제목 | `[#42] 로그인 구현` |

커밋 전에는 아래 두 명령으로 파일을 고치고 포맷합니다.

```sh
uv run ruff check --fix .
uv run ruff format .
```

Push 전에는 아래 두 명령으로 수정 없이 검사합니다.

```sh
uv run ruff format --check .
uv run ruff check .
```

`start`, `commit`, `ship`의 `--dry-run`은 예정된 명령과 내용을 보여 줍니다. `commit`과 `ship`에서 `--skip-ruff`를 쓰면 Ruff를 건너뛰었다는 경고가 표시됩니다.
