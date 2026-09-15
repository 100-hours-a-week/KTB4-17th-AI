from __future__ import annotations

from pathlib import Path

from ktb_git import config, conventions, github, gitops
from ktb_git.config import UserConfig
from ktb_git.runner import Runner
from ktb_git.wizard import Wizard


def _ask_config(wizard: Wizard, existing: UserConfig | None = None) -> UserConfig:
    display = wizard.text(
        "nickname",
        "표시할 닉네임을 입력하세요:",
        default=existing.nickname if existing else "",
    ).strip()
    while not display:
        print("닉네임은 비울 수 없습니다.")
        display = wizard.text("nickname", "표시할 닉네임을 입력하세요:").strip()
    suggested = conventions.suggest_nickname_slug(display)
    default_slug = existing.nickname_slug if existing and existing.nickname == display else suggested or ""
    while True:
        slug = wizard.text(
            "nickname_slug",
            "브랜치용 영문 닉네임([a-z0-9]+)을 입력하세요:",
            default=default_slug,
        ).strip()
        try:
            conventions.validate_nickname_slug(slug)
            return UserConfig(display, slug)
        except conventions.ConventionError as exc:
            print(exc)


def ensure_user_config(wizard: Wizard, *, config_path: Path | None = None, dry_run: bool = False) -> UserConfig:
    existing = config.load_user_config(config_path)
    if existing:
        return existing
    print("사용자 설정이 없어 닉네임을 먼저 입력합니다.")
    cfg = _ask_config(wizard)
    if dry_run:
        print(f"설정 저장 예정: {cfg.nickname} / {cfg.nickname_slug}")
    else:
        saved = config.save_user_config(cfg, config_path)
        print(f"사용자 설정 저장: {saved}")
    return cfg


def run(runner: Runner, wizard: Wizard, *, config_path: Path | None = None) -> UserConfig:
    gitops.repo_root(runner)
    github.ensure_gh(runner)
    existing = config.load_user_config(config_path)
    cfg = _ask_config(wizard, existing)
    saved = config.save_user_config(cfg, config_path)
    print(f"설정 저장: {saved} ({cfg.nickname} / {cfg.nickname_slug})")
    return cfg
