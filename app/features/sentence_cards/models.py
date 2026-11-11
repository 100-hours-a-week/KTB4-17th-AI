"""DB 테이블 — 문장 판정 카드 (1단계).

sentences               : 판정할 문장 1개 = 1행. 답안지·다수결 판정은 여기 둔다. 사용자에게는 문장 id 가 나가지 않는다.
sentence_cards          : 유저 한 명에게 문장 하나를 보여 준 노출 1건 = 1행. id 가 API 의 card_id.
                          같은 문장은 유저당 한 번만 나간다 (sentence_id, user_id 유니크).
sentence_answer_batches : BE 가 모아 보낸 답변 묶음 1건 = 1행. 같은 batch_id 재전송에 처음 응답을 그대로 돌려준다.

user_id 는 백엔드 소유 id 라 FK 를 걸지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _sentence_id() -> str:
    return uuid.uuid4().hex


def _card_id() -> str:
    return f"sc_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


class Sentence(Base):
    __tablename__ = "sentences"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_sentence_id)
    family_id: Mapped[str] = mapped_column(String(64), index=True)  # 학습 · 평가 분리 단위
    sentence: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # synthetic | team_written | tagging_train
    question_type: Mapped[str] = mapped_column(String(10))  # DIRECTION | AXIS
    dimension: Mapped[str] = mapped_column(String(32))
    # 답안지. 사용자에게 내려가지 않는다
    expected_champion: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expected_teacher: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 검증 문항이면 정답이 확실한 값. 대충 누르는 사용자를 거른다
    is_check: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    check_answer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 다수결 결과 (묶음 수신 때 갱신)
    consensus: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verdict: Mapped[str] = mapped_column(String(10), default="PENDING", server_default="PENDING")
    agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    matches_teacher: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 더 내보내지 않을 문장 (문구 오류 · 누수 발견 등)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SentenceCard(Base):
    __tablename__ = "sentence_cards"
    __table_args__ = (
        UniqueConstraint("sentence_id", "user_id", name="uq_sentence_cards_sentence_user"),
        UniqueConstraint("shown_request_id", name="uq_sentence_cards_shown_request_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_card_id)
    sentence_id: Mapped[str] = mapped_column(String(32), ForeignKey("sentences.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # next 재시도 키. 같은 값이면 같은 카드를 돌려준다
    shown_request_id: Mapped[str] = mapped_column(String(36))
    question: Mapped[str] = mapped_column(Text)
    # 화면에 보낸 선택지 [{choice_id, label}] — 보낸 순서 그대로
    choices: Mapped[list[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="SHOWN", server_default="SHOWN")  # SHOWN | ANSWERED
    choice: Mapped[str | None] = mapped_column(String(16), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SentenceAnswerBatch(Base):
    __tablename__ = "sentence_answer_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
