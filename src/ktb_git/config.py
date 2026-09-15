from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ktb_git.errors import KtbError

USER_CONFIG_PATH = Path.home() / ".config" / "ktb" / "config.toml"


@dataclass
class UserConfig:
    nickname: str
    nickname_slug: str


@dataclass
class ProjectConfig:
    default_base: str = "dev"
    hotfix_base: str = "main"
    github_host: str = "github.com"


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise KtbError(f"설정을 읽을 수 없습니다: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise KtbError(f"설정 형식이 올바르지 않습니다: {path}")
    return data


def _required_string(data: dict, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KtbError(f"설정에 {key} 값이 필요합니다: {path}")
    return value


def load_user_config(path: Path | None = None) -> UserConfig | None:
    path = path or USER_CONFIG_PATH
    if not path.exists():
        return None
    data = _read_toml(path)
    return UserConfig(
        nickname=_required_string(data, "nickname", path),
        nickname_slug=_required_string(data, "nickname_slug", path),
    )


def save_user_config(cfg: UserConfig, path: Path | None = None) -> Path:
    path = path or USER_CONFIG_PATH
    if not cfg.nickname.strip() or not cfg.nickname_slug.strip():
        raise KtbError("닉네임과 브랜치용 닉네임을 입력하세요.")
    content = (
        f"nickname = {json.dumps(cfg.nickname, ensure_ascii=False)}\n"
        f"nickname_slug = {json.dumps(cfg.nickname_slug, ensure_ascii=False)}\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise KtbError(f"설정을 저장할 수 없습니다: {path}: {exc}") from exc
    return path


def load_project_config(repo_root: Path) -> ProjectConfig:
    path = repo_root / ".ktb.toml"
    if not path.exists():
        return ProjectConfig()
    data = _read_toml(path)
    defaults = ProjectConfig()
    values = {}
    for key in ("default_base", "hotfix_base", "github_host"):
        value = data.get(key, getattr(defaults, key))
        if not isinstance(value, str) or not value.strip():
            raise KtbError(f"설정의 {key} 값이 올바르지 않습니다: {path}")
        values[key] = value
    return ProjectConfig(**values)
