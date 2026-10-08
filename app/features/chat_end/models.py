"""DB 테이블 — 채팅 종료 초안과 최종 종료 메시지.

호출 1건 = 1행. 같은 위임(delegation_id)에 재시도가 오면 행이 더 쌓인다(유니크 제약 없음).
recent_messages(상대방 발화 포함 대화 원문)는 저장하지 않는다 — API 설계 문서의 저장 범위를 따른다.
room_id·delegation_id 는 백엔드 소유 id 라 FK 를 걸지 않고, 64bit 일 수 있어 BigInteger 로 둔다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


# PK 로 쓸 32자리 hex uuid 생성
def _uuid() -> str:
    return uuid.uuid4().hex


# 타임존 포함 현재 시각 (created_at 기본값)
def _now() -> datetime:
    return datetime.now(UTC)


# 초안 호출 한 건 — 만들어 준 초안 3개
class ChatEndDraft(Base):
    __tablename__ = "chat_end_drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    room_id: Mapped[int] = mapped_column(BigInteger, index=True)
    delegation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    requester_user_id: Mapped[str] = mapped_column(String(64), index=True)
    end_type: Mapped[str] = mapped_column(String(16))
    ending_messages: Mapped[list[str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(8))  # llm | fallback
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
    ending_messages: Mapped[list[str]] = mapped_column(JSON)  # 사용자가 고른 방향 초안
    ai_response: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8))  # SUCCESS | FAILED
    end_turns: Mapped[int] = mapped_column(Integer)
    end_reason: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(8))  # llm | fallback
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
