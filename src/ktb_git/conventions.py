from __future__ import annotations

import re

ISSUE_TYPES: tuple[str, ...] = ("feat", "fix", "chore", "refactor", "docs", "test", "hotfix")
COMMIT_TYPES: tuple[str, ...] = ("feat", "fix", "docs", "style", "refactor", "test", "chore")
BRANCH_KINDS: tuple[str, ...] = ("feat", "fix", "hotfix")

_NICKNAME_SLUG_RE = re.compile(r"^[a-z0-9]+$")
_FEATURE_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ISSUE_TYPE_PREFIX_RE = re.compile(r"^\[([^\]]+)\]")
_COMMIT_PREFIX_RE = re.compile(r"^(feat|fix|docs|style|refactor|test|chore)!?:\s+")
_BREAKING_FIRST_LINE_RE = re.compile(r"^(feat|fix|docs|style|refactor|test|chore)!:\s+")


class ConventionError(ValueError):
    """Raised when a git-convention value is invalid."""


def is_ascii_name(name: str) -> bool:
    return name.isascii()


def suggest_nickname_slug(display: str) -> str | None:
    if not is_ascii_name(display):
        return None
    slug = re.sub(r"[^a-z0-9]", "", display.lower())
    return slug or None


def validate_nickname_slug(slug: str) -> str:
    if not _NICKNAME_SLUG_RE.fullmatch(slug):
        raise ConventionError("닉네임 slug는 [a-z0-9]+ 만 허용합니다.")
    return slug


def slugify_feature(text: str) -> str:
    lowered = text.strip().lower().replace("_", " ")
    hyphenated = re.sub(r"[\s]+", "-", lowered)
    cleaned = re.sub(r"[^a-z0-9-]", "", hyphenated)
    collapsed = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return collapsed


def validate_feature_slug(slug: str) -> str:
    if not _FEATURE_SLUG_RE.fullmatch(slug):
        raise ConventionError("기능명 slug는 소문자, 숫자, 하이픈만 허용합니다.")
    return slug


def branch_name(kind: str, nickname_slug: str, feature_slug: str) -> str:
    if kind not in BRANCH_KINDS:
        raise ConventionError(f"브랜치 종류는 {', '.join(BRANCH_KINDS)} 만 허용합니다.")
    validate_nickname_slug(nickname_slug)
    validate_feature_slug(feature_slug)
    return f"{kind}/{nickname_slug}-{feature_slug}"


def branch_kind_from_issue_type(issue_type: str) -> str:
    if issue_type == "fix":
        return "fix"
    if issue_type == "hotfix":
        return "hotfix"
    return "feat"


def fork_ref(kind: str, default_base: str = "dev", hotfix_base: str = "main") -> str:
    base = hotfix_base if kind == "hotfix" else default_base
    return f"origin/{base}"


def pr_base_for_branch(branch: str, default_base: str = "dev", hotfix_base: str = "main") -> str:
    if branch.startswith("hotfix/"):
        return hotfix_base
    return default_base


def is_protected_branch(branch: str) -> bool:
    return branch in {"main", "dev"}


def is_workflow_branch(branch: str) -> bool:
    return branch.startswith(("feat/", "fix/", "hotfix/"))


def is_feat_or_fix_branch(branch: str) -> bool:
    return branch.startswith(("feat/", "fix/"))


def parse_issue_type_from_title(title: str) -> str | None:
    match = _ISSUE_TYPE_PREFIX_RE.match(title.strip())
    if not match:
        return None
    issue_type = match.group(1)
    if issue_type in ISSUE_TYPES:
        return issue_type
    return None


def issue_title(issue_type: str, assignee: str, summary: str) -> str:
    if issue_type not in ISSUE_TYPES:
        raise ConventionError(f"이슈 분류는 {', '.join(ISSUE_TYPES)} 만 허용합니다.")
    summary = summary.strip()
    if not summary:
        raise ConventionError("이슈 작업 내용은 필수입니다.")
    return f"[{issue_type}][{assignee}] {summary}"


def issue_body(issue_type: str, nickname: str) -> str:
    return f"이슈 분류: {issue_type}\n담당: {nickname}\n"


def validate_subject(subject: str) -> str:
    cleaned = subject.strip()
    if not cleaned:
        raise ConventionError("커밋 제목은 필수입니다.")
    if cleaned.endswith("."):
        raise ConventionError("커밋 제목 끝에는 마침표를 쓰지 않습니다.")
    if len(cleaned) > 50:
        raise ConventionError("커밋 제목은 50자 이내여야 합니다.")
    return cleaned


def _append_footer_line(footer: str, line: str) -> str:
    existing = footer.strip()
    if line in existing.splitlines():
        return existing
    if not existing:
        return line
    return f"{existing}\n{line}"


def assemble_commit_message(
    *,
    commit_type: str,
    breaking: bool,
    subject: str,
    body: str = "",
    footer: str = "",
    resolves: int | None = None,
) -> str:
    if commit_type not in COMMIT_TYPES:
        raise ConventionError(f"커밋 타입은 {', '.join(COMMIT_TYPES)} 만 허용합니다.")
    subject = validate_subject(subject)
    body_text = body.strip()
    footer_text = footer.strip()
    if breaking and not body_text:
        raise ConventionError("Breaking Change는 본문이 필수입니다.")
    bang = "!" if breaking else ""
    header = f"{commit_type}{bang}: {subject}"
    if breaking and "BREAKING CHANGE:" not in footer_text:
        footer_text = _append_footer_line(footer_text, f"BREAKING CHANGE: {subject}")
    if resolves is not None:
        footer_text = _append_footer_line(footer_text, f"Resolves: #{resolves}")
    parts = [header]
    if body_text:
        parts.extend(["", body_text])
    if footer_text:
        parts.extend(["", footer_text])
    return "\n".join(parts) + "\n"


def strip_commit_subject_prefix(subject: str) -> str:
    return _COMMIT_PREFIX_RE.sub("", subject.strip())


def pr_title(issue_number: int, summary: str) -> str:
    cleaned = summary.strip()
    if not cleaned:
        raise ConventionError("PR 요약은 필수입니다.")
    return f"[#{issue_number}] {cleaned}"


def _checkbox(checked: bool) -> str:
    return "[x]" if checked else "[ ]"


def pr_body(
    *,
    issue_number: int,
    changes: list[str],
    checklist_build: bool,
    checklist_docs: bool,
    checklist_breaking: bool,
    breaking_notes: str = "",
    notes: str = "",
) -> str:
    bullets: list[str] = []
    for item in changes:
        text = item.strip()
        if text.startswith("- "):
            text = text[2:].strip()
        if text:
            bullets.append(f"- {text}")
    if not bullets:
        raise ConventionError("변경 내용은 한 줄 이상 필요합니다.")
    breaking_section = breaking_notes.strip() or "- ..."
    notes_section = notes.strip() or "- ..."
    return (
        "## 🔗 관련 이슈\n"
        f"- closes #{issue_number}\n"
        "\n"
        "## ✨ 변경 내용\n" + "\n".join(bullets) + "\n"
        "\n"
        "## ✅ 체크리스트\n"
        f"- {_checkbox(checklist_build)} 로컬에서 빌드/테스트 통과\n"
        f"- {_checkbox(checklist_docs)} 관련 문서(README, API 문서 등) 업데이트\n"
        f"- {_checkbox(checklist_breaking)} Breaking Change 여부 확인 (있다면 아래에 명시)\n"
        "\n"
        "## ⚠️ Breaking Change (선택)\n"
        f"{breaking_section}\n"
        "\n"
        "## 📌 기타 참고 사항 (선택)\n"
        f"{notes_section}\n"
    )


def messages_include_breaking(messages: list[str]) -> bool:
    for message in messages:
        first_line = message.strip().splitlines()[0] if message.strip() else ""
        if _BREAKING_FIRST_LINE_RE.match(first_line):
            return True
        if re.search(r"^BREAKING CHANGE:", message, flags=re.MULTILINE):
            return True
    return False
