"""환경 변수 기반 애플리케이션 설정.

.env 파일 또는 시스템 환경 변수를 읽어 Pydantic Settings 객체로 관리합니다.
OpenRouter 및 로컬 서빙 LLM(vLLM, Ollama 등)을 모두 지원하도록 설계되었습니다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
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

    # response_format={"type": "json_object"} 를 붙일지. 지원 안 하는 로컬 모델이면 0 으로 끈다
    llm_json_mode: bool = Field(
        default=True,
        validation_alias="LLM_JSON_MODE",
        description="시뮬레이션 LLM 호출에 JSON 모드(response_format)를 켤지 여부",
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
    # 1.5초였을 때 운영 태깅 호출이 전부 끊겼다 (#80). 답마다 다음 질문 전에 도는 호출이라 너무 늘리지는 않는다
    onboarding_tag_timeout_s: float = 4.0
    # /build 때 온보딩 중 실패한 답을 다시 태깅하는 타임아웃. 결과를 기다리는 단계라 더 넉넉히
    persona_retag_timeout_s: float = 8.0
    # 2턴 이후 질문 생성(220토큰). 2.5초였을 때 운영에서 정확히 2.50초에 끊겨 기본 질문으로 떨어졌다 (#87)
    onboarding_phrase_timeout_s: float = 5.0
    # 페르소나 프로필 종합 추출 타임아웃
    persona_extract_timeout_s: float = 15.0
    # 온보딩 첫 턴(5개 항목 JSON, 450토큰). 일반 턴과 같은 2.5초로는 운영에서 매번 템플릿으로 떨어졌다 (#82).
    # 온보딩을 시작할 때 한 번만 기다리는 호출이라 넉넉히
    onboarding_first_turn_timeout_s: float = 8.0
    # 연습 대화(practice) 실시간 스트리밍 답변 전체 타임아웃
    practice_timeout_s: float = 12.0
    # 시뮬레이션 대본+리포트 1회 호출 타임아웃 (SimulationAgent.run)
    simulation_script_timeout_s: float = 120.0

    # ── 프로필 신뢰(대표사진 심사·얼굴 인증) ──────────────────────────
    # /test 브라우저 테스트 페이지. 운영에서는 꺼 둔다
    enable_test_ui: bool = Field(default=False, validation_alias="ENABLE_TEST_UI")

    yunet_model_path: Path = Field(
        default=Path("models/face_detection_yunet_2023mar.onnx"),
        validation_alias="YUNET_MODEL_PATH",
    )
    lbf_model_path: Path = Field(default=Path("models/lbfmodel.yaml"), validation_alias="LBF_MODEL_PATH")
    sface_model_path: Path = Field(
        default=Path("models/face_recognition_sface_2021dec.onnx"),
        validation_alias="SFACE_MODEL_PATH",
    )
    synthetic_model_path: Path = Field(
        default=Path("models/community_forensics_vit_int8.onnx"),
        validation_alias="SYNTHETIC_MODEL_PATH",
    )
    c2pa_trust_anchors_path: Path | None = Field(default=None, validation_alias="C2PA_TRUST_ANCHORS_PATH")

    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, validation_alias="MAX_IMAGE_BYTES")
    max_video_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, validation_alias="MAX_VIDEO_BYTES")
    min_image_edge_px: int = Field(default=160, ge=64, validation_alias="MIN_IMAGE_EDGE_PX")
    min_blur_variance: float = Field(default=8.0, ge=0, validation_alias="MIN_BLUR_VARIANCE")
    masked_face_max_blur_variance: float = Field(
        default=20.0,
        ge=0,
        validation_alias="MASKED_FACE_MAX_BLUR_VARIANCE",
    )
    masked_face_min_overlay_ratio: float = Field(
        default=0.35,
        ge=0,
        le=1,
        validation_alias="MASKED_FACE_MIN_OVERLAY_RATIO",
    )
    background_face_max_relative_area: float = Field(
        default=0.15,
        ge=0,
        le=1,
        validation_alias="BACKGROUND_FACE_MAX_RELATIVE_AREA",
    )
    min_brightness: float = Field(default=45.0, ge=0, le=255, validation_alias="MIN_BRIGHTNESS")
    max_brightness: float = Field(default=215.0, ge=0, le=255, validation_alias="MAX_BRIGHTNESS")
    max_abs_yaw_deg: float = Field(default=20.0, gt=0, validation_alias="MAX_ABS_YAW_DEG")
    max_abs_pitch_deg: float = Field(default=18.0, gt=0, validation_alias="MAX_ABS_PITCH_DEG")
    max_abs_roll_deg: float = Field(default=22.0, gt=0, validation_alias="MAX_ABS_ROLL_DEG")

    synthetic_risk_threshold: float = Field(default=0.70, gt=0, lt=1, validation_alias="SYNTHETIC_RISK_THRESHOLD")
    pixel_art_min_axis_ratio: float = Field(
        default=0.45,
        ge=0,
        le=1,
        validation_alias="PIXEL_ART_MIN_AXIS_RATIO",
    )
    pixel_art_min_edge_density: float = Field(
        default=0.07,
        ge=0,
        le=1,
        validation_alias="PIXEL_ART_MIN_EDGE_DENSITY",
    )
    face_match_threshold: float = Field(default=0.42, gt=-1, lt=1, validation_alias="FACE_MATCH_THRESHOLD")
    liveness_max_abs_yaw_deg: float = Field(default=25.0, gt=0, validation_alias="LIVENESS_MAX_ABS_YAW_DEG")
    liveness_max_abs_pitch_deg: float = Field(default=25.0, gt=0, validation_alias="LIVENESS_MAX_ABS_PITCH_DEG")
    liveness_max_abs_roll_deg: float = Field(default=40.0, gt=0, validation_alias="LIVENESS_MAX_ABS_ROLL_DEG")

    liveness_token_secret: str = Field(
        default="replace-with-at-least-32-random-characters",
        validation_alias="LIVENESS_TOKEN_SECRET",
    )
    liveness_token_ttl_seconds: int = Field(default=300, ge=60, le=1800, validation_alias="LIVENESS_TOKEN_TTL_SECONDS")

    @field_validator("c2pa_trust_anchors_path", mode="before")
    @classmethod
    def empty_path_is_none(cls, value: object) -> object:
        return None if value == "" else value


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
