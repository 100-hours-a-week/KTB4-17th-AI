import pytest

from ktb_git.conventions import (
    ConventionError,
    assemble_commit_message,
    branch_kind_from_issue_type,
    branch_name,
    fork_ref,
    issue_body,
    issue_title,
    messages_include_breaking,
    parse_issue_type_from_title,
    pr_base_for_branch,
    pr_body,
    pr_title,
    slugify_feature,
    strip_commit_subject_prefix,
    suggest_nickname_slug,
    validate_nickname_slug,
    validate_subject,
)


def test_branch_name_feat_garnet_login() -> None:
    assert branch_name("feat", "garnet", "login") == "feat/garnet-login"


def test_issue_title_format() -> None:
    assert issue_title("feat", "가넷", "회원가입 API 유효성 검사 추가") == "[feat][가넷] 회원가입 API 유효성 검사 추가"


def test_issue_body_not_empty() -> None:
    assert issue_body("feat", "가넷") == "이슈 분류: feat\n담당: 가넷\n"


def test_commit_breaking_and_resolves() -> None:
    message = assemble_commit_message(
        commit_type="feat",
        breaking=True,
        subject="로그인 테이블 스키마 변경",
        body="기존 name 스키마를 nickname으로 변경",
        resolves=45,
    )
    assert message.startswith("feat!: 로그인 테이블 스키마 변경\n")
    assert "BREAKING CHANGE: 로그인 테이블 스키마 변경" in message
    assert "Resolves: #45" in message
    assert message.endswith("\n")


def test_subject_rejects_period_and_over_50() -> None:
    with pytest.raises(ConventionError):
        validate_subject("마침표가 있습니다.")
    with pytest.raises(ConventionError):
        validate_subject("가" * 51)
    assert validate_subject("  로그인 기능 구현  ") == "로그인 기능 구현"


def test_breaking_requires_body() -> None:
    with pytest.raises(ConventionError):
        assemble_commit_message(commit_type="feat", breaking=True, subject="스키마 변경")


def test_slugify_and_nickname() -> None:
    assert slugify_feature("DB Connection") == "db-connection"
    assert slugify_feature("login__flow") == "login-flow"
    assert suggest_nickname_slug("Garnet") == "garnet"
    assert suggest_nickname_slug("가넷") is None
    assert validate_nickname_slug("garnet") == "garnet"
    with pytest.raises(ConventionError):
        validate_nickname_slug("Garnet")


def test_pr_title_and_body_template() -> None:
    assert pr_title(3, "로그인 기능 구현") == "[#3] 로그인 기능 구현"
    body = pr_body(
        issue_number=3,
        changes=["로그인 API 응답 포맷 변경", "- 회원가입 유효성 검사 추가"],
        checklist_build=True,
        checklist_docs=False,
        checklist_breaking=True,
        breaking_notes="- 로그인 응답 스키마 변경",
        notes="- 테스트: uv run pytest",
    )
    assert "- closes #3" in body
    assert "- 로그인 API 응답 포맷 변경" in body
    assert "- 회원가입 유효성 검사 추가" in body
    assert "- [x] 로컬에서 빌드/테스트 통과" in body
    assert "- [ ] 관련 문서(README, API 문서 등) 업데이트" in body
    assert "- [x] Breaking Change 여부 확인 (있다면 아래에 명시)" in body
    assert "- 로그인 응답 스키마 변경" in body


def test_hotfix_and_feat_pr_bases() -> None:
    assert pr_base_for_branch("hotfix/garnet-login-crash") == "main"
    assert pr_base_for_branch("feat/garnet-login") == "dev"
    assert fork_ref("hotfix") == "origin/main"
    assert fork_ref("feat") == "origin/dev"
    assert branch_kind_from_issue_type("chore") == "feat"
    assert branch_kind_from_issue_type("fix") == "fix"
    assert branch_kind_from_issue_type("hotfix") == "hotfix"


def test_parse_issue_type_and_strip_prefix() -> None:
    assert parse_issue_type_from_title("[feat][가넷] 로그인") == "feat"
    assert parse_issue_type_from_title("제목만") is None
    assert strip_commit_subject_prefix("feat: 로그인 기능") == "로그인 기능"
    assert strip_commit_subject_prefix("feat!: 스키마 변경") == "스키마 변경"


def test_messages_include_breaking() -> None:
    assert messages_include_breaking(["feat!: 스키마 변경\n\n본문\n"])
    assert messages_include_breaking(["feat: 로그인\n\nBREAKING CHANGE: 응답 변경\n"])
    assert not messages_include_breaking(["feat: 로그인 기능\n"])
