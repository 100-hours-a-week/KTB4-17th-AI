"""환경 변수 기반 애플리케이션 설정.

.env 파일 또는 시스템 환경 변수를 읽어 Pydantic Settings 객체로 관리합니다.
OpenRouter 및 로컬 서빙 LLM(vLLM, Ollama 등)을 모두 지원하도록 설계되었습니다.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """런타임 설정과 프롬프트별 기본 모델 및 타임아웃 제한."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # 대소문자 구분 없이 환경 변수 매핑 허용
        extra="ignore",  # 정의되지 않은 추가 환경 변수는 무시
    )

    # ── 내부 보안 & 데이터베이스 ──────────────────────────────────
    # 내부 서비스 간 인증용 API Key
    internal_api_key: str = Field(
        default="",
        validation_alias="INTERNAL_API_KEY",
        description="내부 서비스 간 호출 인증에 사용하는 API 키",
    )
    # PostgreSQL 비동기 접속 URL (로컬 docker 기본값: postgresql+asyncpg://ktb:ktb@localhost:5432/ktb)
    database_url: str = Field(
        default="postgresql+asyncpg://ktb:ktb@localhost:5432/ktb",
        validation_alias="DATABASE_URL",
        description="데이터베이스 연결 URL (PostgreSQL + asyncpg)",
    )
    # SQLAlchemy SQL 실행 로그 출력 여부 (True 이면 콘솔에 모든 쿼리 출력)
    db_echo: bool = Field(
        default=False,
        validation_alias="DB_ECHO",
        description="SQLAlchemy 엔진 쿼리 로깅 활성화 여부",
    )

    # ── LLM (OpenRouter / 로컬 서빙 vLLM, Ollama 등 OpenAI 호환 규격) ────
    # OpenRouter: "https://openrouter.ai/api/v1"
    # 로컬 vLLM: "http://localhost:8001/v1"
    # 로컬 Ollama: "http://localhost:11434/v1"
    llm_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias="LLM_BASE_URL",
        description="OpenAI 호환 API 서버 Base URL (OpenRouter 또는 로컬 vLLM/Ollama)",
    )
    # API 키 (.env 에서 LLM_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY 중 하나를 자동으로 인식)
    # 로컬 서빙 모델 사용 시에는 비워두거나 아무 문자열(예: 'dummy')을 넣어도 무방합니다.
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"),
        description="LLM API 키 (OpenRouter 키 또는 로컬 서빙용 더미 키)",
    )
    # 사용할 모델 식별자
    # OpenRouter 예시: "anthropic/claude-3.5-sonnet", "meta-llama/llama-3.3-70b-instruct"
    # 로컬 서빙 예시: "local-model" 또는 서빙 중인 모델 이름
    llm_model: str = Field(
        default="anthropic/claude-3.5-sonnet",
        validation_alias="LLM_MODEL",
        description="호출할 기본 LLM 모델명",
    )

    # 시뮬레이션 동시 실행 최대 수 (과도한 부하 방지용 세마포어 한도)
    simulation_max_inflight: int = Field(
        default=20,
        ge=1,
        validation_alias="SIMULATION_MAX_INFLIGHT",
        description="동시에 진행할 수 있는 시뮬레이션 최대 요청 수",
    )

    # ── 기능별 LLM 요청 제한 시간(초) ──────────────────────────
    # 온보딩 태그 추출 타임아웃
    onboarding_tag_timeout_s: float = 1.5
    # 온보딩 대화 추천 문구 생성 타임아웃
    onboarding_phrase_timeout_s: float = 2.5
    # 페르소나 프로필 종합 추출 타임아웃
    persona_extract_timeout_s: float = 15.0
    # 연습 대화(practice) 실시간 스트리밍 답변 전체 타임아웃
    practice_timeout_s: float = 12.0
    # 시뮬레이션 단일 발화 생성 타임아웃
    simulation_utterance_timeout_s: float = 5.0
    # 시뮬레이션 최종 종합 리포트 생성 타임아웃
    simulation_report_timeout_s: float = 15.0


@lru_cache
def get_settings() -> Settings:
    """프로세스에서 재사용할 불변 설정 스냅샷을 반환한다.

    @lru_cache 를 통해 애플리케이션 실행 중 최초 1회만 .env 를 읽어 싱글톤으로 캐싱합니다.
    테스트 코드 등에서 환경변수를 변경해 다시 로드해야 할 경우:
        get_settings.cache_clear()
    를 호출하면 캐시를 초기화할 수 있습니다.
    """
    return Settings()


# 기존 코드 호환용 전역 싱글톤 객체 (e.g. from app.core.config import settings)
settings = get_settings()
