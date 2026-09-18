"""환경 변수. .env 는 app.main 이 load_dotenv 로 먼저 올리고, 여기서는 읽기만 한다."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 기본값은 docs/postgres.md 의 로컬 docker 컨테이너. 배포 환경은 .env 로 덮어쓴다.
    database_url: str = "postgresql+asyncpg://ktb:ktb@localhost:5432/ktb"
    db_echo: bool = False


settings = Settings()
