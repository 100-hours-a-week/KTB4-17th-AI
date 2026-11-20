"""DB 테이블 — 원천 대화, 학습 샘플, 어댑터 버전 (MLOps 설계서 4절).

열·키·인덱스는 persona-mlops 의 src/training/infra/sql_models.py 와 같다. 배치가 그 모델로 이 테이블을 읽고 쓰므로
한쪽을 바꾸면 다른 쪽도 같이 바꾼다. 다른 점은 두 가지다.
  - source 에 'upload'(카톡 업로드)를 더했다 (설계서 4-2절). mlops 는 아직 practice, chat 두 값이다.
  - JSON 열은 PostgreSQL 에서 jsonb 다 (설계서 4-2절, 이 저장소의 다른 JSON 열과 같은 방식). mlops 모델의 JSON 타입으로
    읽고 쓸 수 있다.

user_id 는 FK 없는 varchar(64) 다 (백엔드가 소유). 시각은 모두 timestamptz(UTC).
API 서버는 persona_adapter 의 ACTIVE 행만 읽는다 (app/features/mlops/serving.py). 나머지 두 테이블은 배치만 쓴다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

JSONBCompat = JSON().with_variant(JSONB(), "postgresql")
# SQLite 는 INTEGER PRIMARY KEY 만 자동 증가한다 (테스트용)
BigIntId = BigInteger().with_variant(Integer(), "sqlite")

SOURCES = ("practice", "upload", "chat")
VERDICTS = ("pass", "masked", "dropped")
# training → ready → active 순서로만 넘어가고, 평가에서 떨어지면 failed (설계서 7-3절)
ADAPTER_STATUSES = ("training", "ready", "active", "failed", "deleted")
ACTIVE = "active"

# 사용자당 active 어댑터는 하나뿐이다 (status='active' 인 행에 대한 user_id 부분 유니크 인덱스)
ACTIVE_ADAPTER_INDEX = "uq_persona_adapter_active_user"
_ACTIVE_ONLY = text(f"status = '{ACTIVE}'")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class PersonaRawConversation(Base):
    """정제 전 원천 대화. 대화 하나가 한 행. 같은 대화라도 사람마다 본인 시점이 달라 (user_id, conversation_id) 가 키다.

    turns 는 [{"speaker": "self" | "other", "text", "name"?, "at"?}] 마스킹 전 원문이다.
    """

    __tablename__ = "persona_raw_conversation"
    __table_args__ = (
        UniqueConstraint("user_id", "conversation_id", name="uq_persona_raw_conversation_user_conversation"),
        Index("ix_persona_raw_conversation_user_id_started_at", "user_id", "started_at"),
        CheckConstraint(_one_of("source", SOURCES), name="ck_persona_raw_conversation_source"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    turn_count: Mapped[int] = mapped_column(Integer)
    self_turn_count: Mapped[int] = mapped_column(Integer)
    turns: Mapped[list[dict[str, str]]] = mapped_column(JSONBCompat)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonaTrainingSample(Base):
    """본인 발화 하나가 한 행. 판정 결과(pass·masked·dropped)와 사유, 마스킹된 문맥과 정답.

    id 는 넣은 순서라 대화 안의 순서를 보존한다. 창 밖으로 밀린 행은 지우지 않고 in_window=false 로 남긴다.
    """

    __tablename__ = "persona_training_sample"
    __table_args__ = (
        Index("ix_persona_training_sample_user_id_verdict", "user_id", "verdict"),
        CheckConstraint(_one_of("source", SOURCES), name="ck_persona_training_sample_source"),
        CheckConstraint(_one_of("verdict", VERDICTS), name="ck_persona_training_sample_verdict"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    context: Mapped[list[dict[str, str]]] = mapped_column(JSONBCompat)
    target: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(16))
    drop_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    in_window: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True)


class PersonaAdapter(Base):
    """사용자별 어댑터 버전. (user_id, version) 이 키다. 서빙 이름은 "{user_id}-v{version}".

    base_model_id 는 학습에 쓴 공통 베이스(HF 저장소 ID와 리비전). 서빙 중인 베이스와 같을 때만 쓸 수 있다.
    artifact_uri 는 가중치 파일 위치 (hf://…).
    """

    __tablename__ = "persona_adapter"
    __table_args__ = (
        Index(ACTIVE_ADAPTER_INDEX, "user_id", unique=True, postgresql_where=_ACTIVE_ONLY, sqlite_where=_ACTIVE_ONLY),
        CheckConstraint(_one_of("status", ADAPTER_STATUSES), name="ck_persona_adapter_status"),
    )

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(16))
    base_model_id: Mapped[str] = mapped_column(String(255))
    sample_count: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONBCompat)
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
