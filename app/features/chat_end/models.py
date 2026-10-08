"""DB 테이블 — 채팅 종료 스크립트(초안)와 최종 종료 메시지.

chat_end_drafts   : drafts 호출 1건 = 1행. 1·2·3번째 답변(turns). PENDING 으로 만들어지고 확정하면 CONFIRMED.
                    같은 위임(delegation_id)의 CONFIRMED 는 1개뿐이다 (부분 유니크 인덱스).
chat_end_messages : messages 호출 1건 = 1행. 어떤 스크립트(draft_id)의 어떤 문장(direction)을 방향으로 썼는지 남긴다.
recent_messages(상대방 발화 포함 대화 원문)는 저장하지 않는다 — API 설계 문서의 저장 범위를 따른다.
room_id·delegation_id 는 백엔드 소유 id 라 FK 를 걸지 않고, 64bit 일 수 있어 BigInteger 로 둔다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


# PK 로 쓸 32자리 hex uuid 생성
def _uuid() -> str:
    return uuid.uuid4().hex


# 타임존 포함 현재 시각 (created_at 기본값)
def _now() -> datetime:
    return datetime.now(UTC)


# 스크립트 호출 한 건 — 1·2·3번째 답변. id 가 응답의 draft_id
class ChatEndDraft(Base):
    __tablename__ = "chat_end_drafts"
    __table_args__ = (
        # 같은 방·위임에 CONFIRMED 는 1개만. PENDING 은 몇 개든 쌓인다
        Index(
            "uq_chat_end_drafts_confirmed",
            "room_id",
            "delegation_id",
            unique=True,
            postgresql_where=text("status = 'CONFIRMED'"),
            sqlite_where=text("status = 'CONFIRMED'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    room_id: Mapped[int] = mapped_column(BigInteger, index=True)
    delegation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    requester_user_id: Mapped[str] = mapped_column(String(64), index=True)
    end_type: Mapped[str] = mapped_column(String(16))
    turns: Mapped[list[str]] = mapped_column(JSON)  # [1번째, 2번째, 3번째] 답변
    source: Mapped[str] = mapped_column(String(8))  # llm | fallback
    status: Mapped[str] = mapped_column(String(10), default="PENDING", server_default="PENDING")  # PENDING | CONFIRMED
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# 종료 메시지 호출 한 건 — id 가 응답의 message_id
class ChatEndMessage(Base):
    __tablename__ = "chat_end_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(BigInteger, index=True)
    delegation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    target_user_id: Mapped[str] = mapped_column(String(64))
    end_type: Mapped[str] = mapped_column(String(16))
    draft_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 방향을 가져온 스크립트
    direction: Mapped[str | None] = mapped_column(Text, nullable=True)  # 이번 턴에 프롬프트로 보낸 N번째 문장
    ai_response: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8))  # SUCCESS | FAILED
    end_turns: Mapped[int] = mapped_column(Integer)
    end_reason: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(8))  # llm | fallback
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
