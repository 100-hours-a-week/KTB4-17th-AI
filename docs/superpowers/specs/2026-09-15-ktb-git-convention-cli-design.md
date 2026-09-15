# ktb Git Convention CLI Design

Date: 2026-09-15
Status: Draft for user review
CLI name: `ktb`

## Problem

KTB4-17th-AI uses a simplified Git-Flow and Conventional Commits, but the rules live in a document. People have to remember branch names, commit types, issue titles, PR titles, and the PR template. The tool must walk a developer through `issue → branch → commit → push → PR to dev` without requiring them to memorize subcommands.

## Goals

- A developer can start work, commit, and open a PR that matches team convention without looking at the convention doc.
- Running `ktb` with no arguments shows current git/GitHub state and a menu of next actions.
- Direct subcommands still work for people who remember them.
- The tool never merges feat→feat, never pushes to `main`, and never creates a PR whose base is a feature branch.

## Non-goals (this version)

- Web UI, VS Code/Cursor extension, full-screen Textual TUI
- GitLab or other hosts
- Git hooks that hard-block non-conforming commits
- LLM-generated commit/PR text
- Release tagging, changelog generation, or `dev` → `main` deploy automation
- Multi-commit interactive rebase, amend, or stash management

## Users and environment

- Same-team developers on macOS/Linux
- Repo: GitHub `100-hours-a-week/KTB4-17th-AI`
- Required local tools: `git`, GitHub CLI `gh` (authenticated)
- Python 3.11+

## Product surface

| Invocation | Behavior |
|---|---|
| `ktb` | Hub: print status, then interactive menu |
| `ktb status` | Status only, no menu |
| `ktb init` | Save nickname and confirm `gh` auth |
| `ktb start` | Create or select issue, then create branch |
| `ktb commit` | Conventional commit wizard |
| `ktb ship` | Push current branch and open PR |
| `ktb start --dry-run` (same flag on `commit`, `ship`) | Print planned git/gh commands and rendered text, do not execute |

Hub menu items (disabled items stay visible with a reason):

1. 이슈 만들고 브랜치 시작 (`start`)
2. 커밋 (`commit`)
3. Push하고 PR 열기 (`ship`)
4. 설정 (`init`)
5. 종료

Enabled rules:
- start: always (if already on feat/fix/hotfix, warn then continue)
- commit: only when staged, unstaged, or untracked files exist
- ship: only when current branch is feat/fix/hotfix AND it has commits not on the intended base
- init: always

Hub always shows status above the menu so the user does not need to know which command is next.

## Convention rules (source of truth for the tool)

### Branches

| Kind | Pattern | Forks from | PR base |
|---|---|---|---|
| feature | `feat/{nickname}-{slug}` | `origin/dev` | `dev` |
| fix | `fix/{nickname}-{slug}` | `origin/dev` | `dev` |
| hotfix | `hotfix/{nickname}-{slug}` | `origin/main` | `main` |

- Nickname: config stores `nickname` (display, Korean allowed) and `nickname_slug` (branch-safe: `[a-z0-9]+`). Init asks both. If the display name is already ASCII, slug defaults to its lowercased form; if it contains non-ASCII, slug is required (no automatic romanization).
- Slug: lowercase, digits, hyphen only. Spaces and underscores become hyphens. Consecutive hyphens collapse.
- No direct push to `main`.
- No PR whose head and base are both `feat/*` or `fix/*`.

### Issues

Title: `[분류][담당자] 작업 내용`

- 분류: `feat` | `fix` | `chore` | `refactor` | `docs` | `test` | `hotfix`
- 담당자: display nickname, default from config
- 작업 내용: free text, required

### Commits (Conventional Commits v1.0.0)

```
<type>[!]: <subject>

<body>

<footer>
```

- Types: `feat` `fix` `docs` `style` `refactor` `test` `chore`
- Breaking: `!` after type. Body is required. Footer must include a `BREAKING CHANGE:` line.
- Subject: imperative, present tense, max 50 characters, no trailing period. Korean and English allowed.
- Footer may include `Resolves: #N`.

### Pull requests

- Title: `[#<issue>] 변경 사항 요약`
- Body template:

```markdown
## 🔗 관련 이슈
- closes #<issue>

## ✨ 변경 내용
- ...

## ✅ 체크리스트
- [ ] 로컬에서 빌드/테스트 통과
- [ ] 관련 문서(README, API 문서 등) 업데이트
- [ ] Breaking Change 여부 확인 (있다면 아래에 명시)

## ⚠️ Breaking Change (선택)
- ...

## 📌 기타 참고 사항 (선택)
- ...
```

- Merge target: `dev`, except `hotfix/*` → `main`.

## Configuration

User config path: `~/.config/ktb/config.toml`

```toml
nickname = "가넷"
nickname_slug = "garnet"
```

Project config path: `.ktb.toml` at repo root (optional, committed)

```toml
default_base = "dev"
hotfix_base = "main"
github_host = "github.com"
```

- `ktb init` writes user config. Project config is not overwritten by init.
- If user config is missing, any command that needs a nickname prompts and then writes it (same questions as `init`).
- Issue number for the current branch is stored in git: `git config branch.<name>.ktbIssue`. This stays in the local repo, not in the toml file.

## Architecture

Python package `ktb_git`, console script `ktb`.

```
src/ktb_git/
  __init__.py
  cli.py            # Typer app, hub dispatch
  hub.py            # no-arg menu
  status.py         # snapshot of repo/issue/pr
  config.py
  conventions.py    # pure functions: names, messages, validation
  gitops.py         # git subprocess wrapper
  github.py         # gh subprocess wrapper
  commands/
    init.py
    start.py
    commit.py
    ship.py
```

External processes:

- `git` for all local vcs
- `gh` for `auth status`, `issue create`, `issue list`, `pr create`, `pr view`

No GitHub REST client library. `gh` is the only GitHub integration.

### Module boundaries

- `conventions.py` has no I/O. Easy to unit test.
- `gitops.py` / `github.py` accept a runner callable so tests inject a fake.
- Commands orchestrate: read status → wizard questions → validate → preview → execute.
- Hub calls the same command functions as the subcommands. It does not reimplement flows.

## Command flows

### `ktb init`

1. Check `git` and `gh` exist; if `gh` missing, print install URL and exit 1.
2. Run `gh auth status`; if unauthenticated, print `gh auth login` and exit 1.
3. Ask display nickname. If it is ASCII, suggest `lower(name)` as slug; otherwise require a slug of `[a-z0-9]+`. Allow edit.
4. Write `~/.config/ktb/config.toml`.
5. Print saved values.

### `ktb start`

1. Preconditions: must be a git repo. Refuse detached HEAD. Dirty working tree is allowed (warn, do not block).
2. Ask: 새 이슈 생성 / 기존 이슈 선택.
3. New issue: 분류, 담당자 (default nickname), 작업 내용 → preview title → `gh issue create --title ... --body ...` → capture number.
4. Existing: `gh issue list --state open --limit 30`, numbered picker. Record number and infer issue 분류 from a leading `[feat]`-style prefix when present; if missing, ask 분류.
5. Branch kind is only `feat` | `fix` | `hotfix`. Map issue 분류 `fix` → `fix`, `hotfix` → `hotfix`, everything else → `feat`. Allow the user to override before creation.
6. Ask slug (기능명). Preview full branch name.
7. Fetch origin. Checkout latest `origin/dev` (or `origin/main` for hotfix). Create and checkout branch.
8. `git config branch.<name>.ktbIssue <N>`
9. Print next hint: commit when ready, then `ktb` or `ktb ship`.

New issues get body `이슈 분류: <분류>\n담당: <nickname>\n` so `gh issue create` is never called with an empty body.

Do not push in `start`. First push happens in `ship` with `-u`.

### `ktb commit`

1. If no changes (unstaged/staged/untracked), exit with "커밋할 변경이 없습니다".
2. Show changed files. Ask: 전부 스테이징 / 파일 선택 / 이미 스테이징된 것만.
3. Ask type, breaking yes/no, subject, optional body, optional footer.
4. If branch has `ktbIssue`, offer `Resolves: #N` in footer (default yes).
5. If breaking, require body and append `BREAKING CHANGE:` footer if user did not write one.
6. Validate subject. Re-ask on failure.
7. Preview full message. Confirm. Write the message to a temp file and run `git commit -F <file>` so newlines and body/footer survive.

Does not push.

### `ktb ship`

1. Refuse if current branch is `main` or `dev`.
2. Refuse if no commits vs the intended base.
3. Determine base: `hotfix/*` → `main`, else `dev`.
4. Push `-u origin HEAD`. If remote exists, push without force. Non-fast-forward → stop and explain.
5. If PR already exists for this branch, print URL and skip create.
6. Ask PR summary (default: last commit subject with a leading `type:` / `type!:` prefix stripped).
7. Ask bullet list for 변경 내용 (one or more).
8. Ask checklist flags (build/test, docs, breaking).
9. If any commit on the branch is breaking (`type!` or `BREAKING CHANGE`), pre-check the breaking box and require the breaking section text.
10. Preview title + body. Confirm. `gh pr create --base <base> --title ... --body ...`
11. Print PR URL.

### `ktb status` / hub header

Print:

- Current branch
- Linked issue (`#N` or "없음")
- Dirty files count
- Commits ahead of base
- Existing PR URL or "PR 없음"
- Next action sentence, e.g. "다음: 커밋하세요 (`ktb commit` 또는 메뉴 2번)"

## Error handling

| Situation | Behavior |
|---|---|
| Not a git repo | Exit 1, say to run inside the project |
| `gh` missing / not logged in | Exit 1 with exact next command |
| `main` commit or push | Refuse. Suggest `ktb start` or hotfix |
| PR base would be feat/fix | Refuse |
| Invalid subject / branch slug | Re-prompt, do not execute |
| `gh issue create` succeeds then git checkout fails | Leave issue; status shows "이슈 #N 있음, 브랜치 없음". Start can resume by selecting that issue |
| Push rejected | Do not open PR |
| `--dry-run` | Print commands and rendered issue/commit/PR text only |

No automatic deletion of issues or branches on failure.

## Testing

Framework: pytest.

1. `tests/test_conventions.py` — branch names, issue titles, commit messages, PR titles, slugify, subject rules, breaking footer.
2. `tests/test_start.py` — fake runner records `gh issue create` and `git checkout -b feat/garnet-login`.
3. `tests/test_commit.py` — message assembly including `!` and `Resolves`.
4. `tests/test_ship.py` — base selection feat→dev, hotfix→main; skip create if PR exists; refuse feat-to-feat.
5. `tests/test_status.py` — next-action mapping for: no branch, dirty, unpushed, no PR, PR open.

No live GitHub network in tests. `gh`/`git` are fakes.

## Packaging and docs

- `pyproject.toml` with project name `ktb-git`, script `ktb = ktb_git.cli:app`
- README: install (`pip install -e .`), `gh auth login`, `ktb init`, hub screenshot-as-text, convention mapping table
- Python 3.11+, dependencies: `typer`, `rich`, `questionary`, `tomli` only if 3.10 must be supported (not required)

## Success criteria

- From a clean clone with `gh` authenticated, a new teammate can: init → start (create issue + branch) → dummy commit → ship, and the GitHub issue title, branch name, commit message, and PR title/body all match the convention examples.
- `ktb` with no args is usable without reading `--help`.
- `pytest` passes without network.

## Open decisions (resolved)

- Form: terminal wizard, not web/extension
- Commands: split start/commit/ship plus hub menu for discoverability
- Issues: create new or pick existing
- Nickname: saved once in user config
- Implementation: Python + `gh`
- Validation: wizard enforces rules; no git hooks in v1
