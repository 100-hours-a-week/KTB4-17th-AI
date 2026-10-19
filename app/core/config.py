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

    # ── 태깅·추출 전용 소형 모델 (RunPod vLLM, serving/vllm) ─────────────
    # 비워 두면 태깅·추출도 위 메인 LLM 을 쓴다. 실패하면 메인 LLM 으로 넘긴다 (app/core/slm.py)
    slm_base_url: str = Field(default="", validation_alias="SLM_BASE_URL")
    slm_api_key: str = Field(default="", validation_alias="SLM_API_KEY")
    # vLLM 의 --lora-modules / --served-model-name 이름. 비워 둔 작업은 메인 LLM 으로 간다
    slm_tagging_model: str = Field(default="", validation_alias="SLM_TAGGING_MODEL")
    slm_extraction_model: str = Field(default="", validation_alias="SLM_EXTRACTION_MODEL")
    # SLM 이 내려간 동안(콜드 스타트 등) /health 를 다시 확인하는 간격
    slm_probe_interval_s: float = Field(default=15.0, gt=0, validation_alias="SLM_PROBE_INTERVAL_S")

    # 시뮬레이션 동시 실행 최대 수 (과도한 부하 방지용 세마포어 한도)
    simulation_max_inflight: int = Field(
        default=20,
        ge=1,
        validation_alias="SIMULATION_MAX_INFLIGHT",
        description="동시에 진행할 수 있는 시뮬레이션 최대 요청 수",
    )

    # ── 기능별 LLM 요청 제한 시간(초) ──────────────────────────
    # 타임아웃·요청 한도·서버 오류로 실패한 LLM 호출을 다시 보내는 횟수 (app/core/llm_retry.py).
    # 아래 타임아웃은 시도마다 새로 적용된다 — 최악의 경우 대기 시간이 (1 + 이 값) 배가 된다
    llm_retry_attempts: int = Field(default=1, ge=0, validation_alias="LLM_RETRY_ATTEMPTS")
    # 온보딩 태그 추출 타임아웃
    # 1.5초였을 때 운영 태깅 호출이 전부 끊겼다 (#80). 4초로도 LLM 이 느린 순간엔 끊겨 15초로 늘렸다.
    # 이 시간이 지나면 사용자는 다음 질문으로 넘어가고, 태깅은 뒤에서 계속 돌아 /build 재태깅이 이어받는다
    onboarding_tag_timeout_s: float = 15.0
    # /build 때 온보딩 중 실패한 답을 다시 태깅하는 타임아웃. 결과를 기다리는 단계라 더 넉넉히
    persona_retag_timeout_s: float = 20.0
    # 2턴 이후 질문 생성(220토큰). 2.5초였을 때 운영에서 정확히 2.50초에 끊겨 기본 질문으로 떨어졌다 (#87).
    # 5초로도 LLM 이 느린 순간엔 끊겨 15초로 늘렸다
    onboarding_phrase_timeout_s: float = 15.0
    # 페르소나 프로필 종합 추출 타임아웃
    persona_extract_timeout_s: float = 15.0
    # 온보딩 첫 턴(5개 항목 JSON, 450토큰). 일반 턴과 같은 2.5초로는 운영에서 매번 템플릿으로 떨어졌다 (#82).
    # 온보딩을 시작할 때 한 번만 기다리는 호출이라 넉넉히
    onboarding_first_turn_timeout_s: float = 20.0
    # 연습 대화(practice) 실시간 스트리밍 답변 전체 타임아웃
    practice_timeout_s: float = 15.0
    # 채팅 종료 초안 3개(JSON)·최종 종료 메시지 1회 호출 타임아웃
    chat_end_timeout_s: float = 10.0
    # 시뮬레이션 대본+리포트 1회 호출 타임아웃 (SimulationAgent.run)
    simulation_script_timeout_s: float = 120.0
    # 마이그레이션 리포트 서술만 생성하는 호출 타임아웃
    simulation_narrative_timeout_s: float = 60.0
    simulation_migration_attempt_timeout_s: float = 20.0
    simulation_migration_slot_timeout_s: float = 30.0
    simulation_migration_stale_s: float = 120.0
    simulation_migration_max_runs: int = 2
    simulation_migration_max_event_streams: int = 8

    # ── 페르소나 추출 (연습대화·카카오톡 → 대화 스타일) ──────────────
    # 한 작업에서 분석하는 최대 본인 발화 수. 최근 것부터 — 최근 말투가 지금 성격에 가깝다고 본다
    extraction_max_utterances: int = 200
    # 업로드 대화에서 새 본인 발화가 이보다 적으면 거절한다 (말투를 판단하기엔 부족)
    extraction_min_utterances: int = 5
    # 업로드 파일·텍스트 최대 크기 (바이트)
    extraction_max_upload_bytes: int = 5 * 1024 * 1024
    # 점수 보정 가중치 — 기존 점수 비중. 나머지(0.3)가 관찰값
    extraction_base_weight: float = 0.7
    # 대화 스타일 추출 LLM 타임아웃. 백그라운드 작업이라 넉넉히
    extraction_timeout_s: float = 30.0
    # 이 시간(분) 넘게 running 인 작업은 서버 재시작 등으로 멈춘 것으로 보고 failed 처리
    extraction_job_stale_minutes: int = 10

    # ── 프로필 신뢰(대표사진 심사·얼굴 인증) ──────────────────────────
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

    # ── v2 사용자 페르소나 메모리 RAG 설정 ──────────────────────────
    embedding_model_name: str = Field(
        default="nlpai-lab/KURE-v1-onnx-int8",
        validation_alias="EMBEDDING_MODEL_NAME",
        description="DB embedding_model 컬럼에 기록할 임베딩 모델 버전 식별자",
    )
    embedding_onnx_path: Path = Field(
        default=Path("models/kure_v1_int8/model_quantized.onnx"),
        validation_alias="EMBEDDING_ONNX_PATH",
    )
    embedding_tokenizer_path: Path = Field(
        default=Path("models/kure_v1_int8/tokenizer.json"),
        validation_alias="EMBEDDING_TOKENIZER_PATH",
    )
    embedding_pooling_mode: str = Field(
        default="cls",
        validation_alias="EMBEDDING_POOLING_MODE",
        description="KURE-v1(bge-m3 계열) 및 arctic-embed-l-v2.0-ko의 기본 풀링 방식 ('cls' 또는 'mean')",
    )
    embedding_query_prefix: str = Field(
        default="",
        validation_alias="EMBEDDING_QUERY_PREFIX",
        description="쿼리 임베딩 시 앞에 붙일 접두사 (KURE-v1은 빈 문자열, arctic-embed는 'query: ')",
    )
    embedding_dim: int = Field(default=1024, validation_alias="EMBEDDING_DIM")
    embedding_max_length: int = Field(default=512, validation_alias="EMBEDDING_MAX_LENGTH")

    # 실시간 검색 (retrieve) 파라미터 — pgvector <=> 는 코사인 거리(1 - similarity)
    memory_top_k: int = Field(default=5, ge=1, le=20, validation_alias="MEMORY_TOP_K")
    memory_max_cosine_distance: float = Field(
        default=0.65,
        gt=0.0,
        lt=2.0,
        validation_alias="MEMORY_MAX_COSINE_DISTANCE",
        description="pgvector 코사인 거리(<=>) 상한 (0.65 = 코사인 유사도 0.35 이상만 허용)",
    )
    practice_v2_history_window: int = Field(
        default=20,
        ge=2,
        le=40,
        validation_alias="PRACTICE_V2_HISTORY_WINDOW",
        description="v2 연습대화에서 LLM에 전달할 최근 메시지 수 (User 10턴 + AI 10턴 = 20)",
    )

    # 백그라운드 메모리 추출 & Reconcile 파라미터 (유사도 = 1 - 코사인 거리)
    memory_trigger_turn_count: int = Field(default=8, ge=1, validation_alias="MEMORY_TRIGGER_TURN_COUNT")
    memory_trigger_idle_minutes: int = Field(default=20, ge=1, validation_alias="MEMORY_TRIGGER_IDLE_MINUTES")
    memory_context_overlap_pairs: int = Field(default=2, ge=0, le=5, validation_alias="MEMORY_CONTEXT_OVERLAP_PAIRS")
    memory_reconcile_noop_sim: float = Field(default=0.99, validation_alias="MEMORY_RECONCILE_NOOP_SIM")
    memory_reconcile_judge_sim: float = Field(default=0.70, validation_alias="MEMORY_RECONCILE_JUDGE_SIM")
    memory_confidence_confirmed: float = Field(default=0.8, validation_alias="MEMORY_CONFIDENCE_CONFIRMED")
    memory_confidence_tentative: float = Field(default=0.5, validation_alias="MEMORY_CONFIDENCE_TENTATIVE")
    memory_tentative_ttl_days: int = Field(default=30, ge=1, validation_alias="MEMORY_TENTATIVE_TTL_DAYS")
    memory_job_stale_minutes: int = Field(default=10, ge=1, validation_alias="MEMORY_JOB_STALE_MINUTES")
    memory_extract_timeout_s: float = Field(default=25.0, validation_alias="MEMORY_EXTRACT_TIMEOUT_S")
    memory_reconcile_timeout_s: float = Field(default=20.0, validation_alias="MEMORY_RECONCILE_TIMEOUT_S")

    # ── v2 시뮬레이션 하네스 (docs/기획_및_설계/시뮬레이션_v2_하네스_기획안_최종.md) ──
    # 심사 모델. 비우면 생성 모델(llm_model)과 같다
    simulation_v2_judge_model: str | None = Field(default=None, validation_alias="SIMULATION_V2_JUDGE_MODEL")
    # tools(function calling) 를 쓸지. 기본은 json_object 경로 — 2026-10-19 로컬 실측(gemini-2.5-flash-lite)에서
    # tools 는 긴 대본 인자가 깨지는 형식 오류가 잦아 성공 6/14, json_object 는 11/14 였다
    simulation_v2_use_tools: bool = Field(default=False, validation_alias="SIMULATION_V2_USE_TOOLS")
    simulation_v2_job_timeout_s: float = Field(default=90.0, validation_alias="SIMULATION_V2_JOB_TIMEOUT_S")
    simulation_v2_job_stale_minutes: int = Field(default=10, ge=1, validation_alias="SIMULATION_V2_JOB_STALE_MINUTES")
    # 호출별 타임아웃 상한. 실제로는 min(이 값, job 남은 시간)
    simulation_v2_generate_timeout_s: float = Field(default=45.0, validation_alias="SIMULATION_V2_GENERATE_TIMEOUT_S")
    simulation_v2_judge_timeout_s: float = Field(default=20.0, validation_alias="SIMULATION_V2_JUDGE_TIMEOUT_S")
    simulation_v2_repair_timeout_s: float = Field(default=20.0, validation_alias="SIMULATION_V2_REPAIR_TIMEOUT_S")
    simulation_v2_report_timeout_s: float = Field(default=30.0, validation_alias="SIMULATION_V2_REPORT_TIMEOUT_S")
    # 소재 매칭
    # 2026-10-19 합성 라벨셋(관련 46·무관 156쌍, scripts/calibrate_topic_tau.py) 보정: 0.60 은 정밀도 0.81,
    # 0.62 는 정밀도 0.91(재현율 0.46). 거짓 공통점이 혼동을 부추기므로 정밀도를 우선했다
    simulation_v2_tau_shared: float = Field(default=0.62, validation_alias="SIMULATION_V2_TAU_SHARED")
    simulation_v2_topic_dedup_sim: float = Field(default=0.80, validation_alias="SIMULATION_V2_TOPIC_DEDUP_SIM")
    simulation_v2_max_shared_topics: int = Field(default=3, ge=1, validation_alias="SIMULATION_V2_MAX_SHARED_TOPICS")
    # 프롬프트에 노출할 사용자당 fact 수 (소재 fact 포함)
    simulation_v2_max_facts_per_user: int = Field(default=8, ge=1, validation_alias="SIMULATION_V2_MAX_FACTS_PER_USER")
    # 규칙 검사 임계값
    simulation_v2_echo_jaccard: float = Field(default=0.5, validation_alias="SIMULATION_V2_ECHO_JACCARD")
    simulation_v2_low_effort_ratio: float = Field(default=0.6, validation_alias="SIMULATION_V2_LOW_EFFORT_RATIO")
    simulation_v2_regenerate_local_ratio: float = Field(
        default=0.3, validation_alias="SIMULATION_V2_REGENERATE_LOCAL_RATIO"
    )
    # 호출 예산 (하드 상한)
    simulation_v2_script_budget: int = Field(default=5, ge=2, validation_alias="SIMULATION_V2_SCRIPT_BUDGET")
    simulation_v2_regenerate_budget: int = Field(default=1, ge=0, validation_alias="SIMULATION_V2_REGENERATE_BUDGET")
    simulation_v2_repair_budget: int = Field(default=2, ge=0, validation_alias="SIMULATION_V2_REPAIR_BUDGET")
    simulation_v2_report_budget: int = Field(default=2, ge=1, validation_alias="SIMULATION_V2_REPORT_BUDGET")
    # repair·regenerate 진입에 필요한 최소 남은 시간
    simulation_v2_min_remaining_s: float = Field(default=25.0, validation_alias="SIMULATION_V2_MIN_REMAINING_S")

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
