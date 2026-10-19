"""v2 페르소나 메모리 DB 접근. service/graph 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserPersonaCore, UserPersonaFact

# 객관적 상태 변경만 과거이력 Fact로 강등 (자유서술 personality/love_style은 표현 흔들림 방지를 위해 제외)
CORE_DEMOTE_TEMPLATES: dict[str, str] = {
    "region": "과거에 {old}에 거주했었다",
    "job": "과거 직업 또는 하던 일은 {old}이었다",
    "marriage_plan": "과거에는 결혼/관계 계획이 '{old}'이었다",
}


def cosine_distance_py(vec_a: list[float], vec_b: list[float]) -> float:
    """코사인 거리(1 - cosine_similarity) 계산. 0.0이면 완전 동일, 1.0이면 직교."""
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-12:
        return 1.0
    return float(1.0 - np.dot(a, b) / denom)


def build_demoted_core_sentences(existing_profile: dict[str, Any], core_updates: dict[str, Any]) -> list[str]:
    """객관적 코어 슬롯(region, job, marriage_plan)의 값이 바뀔 때만 이전 값을 과거 시제 문장으로 변환한다."""
    demoted: list[str] = []
    for slot, template in CORE_DEMOTE_TEMPLATES.items():
        new_val = core_updates.get(slot)
        old_val = existing_profile.get(slot)
        if old_val and new_val and str(old_val).strip() != str(new_val).strip():
            demoted.append(template.format(old=str(old_val).strip()))
    return demoted


class PersonaMemoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @property
    def _is_postgres(self) -> bool:
        return self.db.get_bind().dialect.name == "postgresql"

    async def get_core(self, user_id: str) -> UserPersonaCore | None:
        return await self.db.get(UserPersonaCore, user_id)

    async def get_or_create_core(self, user_id: str) -> UserPersonaCore:
        core = await self.get_core(user_id)
        if core is None:
            core = UserPersonaCore(user_id=user_id, profile={}, last_processed_message_id=0)
            self.db.add(core)
            await self.db.flush()
        return core

    async def touch_user_activity(self, user_id: str) -> None:
        core = await self.get_or_create_core(user_id)
        core.last_activity_at = datetime.now(UTC)
        await self.db.flush()

    async def update_core_profile(
        self,
        user_id: str,
        core_updates: dict[str, Any],
        *,
        last_processed_message_id: int | None = None,
    ) -> UserPersonaCore:
        core = await self.get_or_create_core(user_id)
        merged = dict(core.profile or {})
        for k, v in core_updates.items():
            if v and str(v).strip():
                merged[k] = str(v).strip()
        core.profile = merged
        # 커서는 앞으로만 전진한다 (뒤로 가면 이미 처리한 메시지를 다시 추출한다)
        if last_processed_message_id is not None and last_processed_message_id > core.last_processed_message_id:
            core.last_processed_message_id = last_processed_message_id
        core.updated_at = datetime.now(UTC)
        await self.db.flush()
        return core

    async def search_confirmed_facts(
        self,
        user_id: str,
        query_vec: list[float],
        *,
        top_k: int = 5,
        max_distance: float = 0.65,
    ) -> list[tuple[str, str]]:
        """실시간 응답 경로: confirmed 상태의 Fact 중 코사인 거리 < max_distance 인 Top-K 를 (날짜, 문장) 으로 반환한다."""
        if self._is_postgres:
            dist_col = UserPersonaFact.embedding.cosine_distance(query_vec)
            stmt = (
                select(UserPersonaFact)
                .where(
                    UserPersonaFact.user_id == user_id,
                    UserPersonaFact.status == "confirmed",
                    UserPersonaFact.embedding.is_not(None),
                    dist_col < max_distance,
                )
                .order_by(dist_col.asc())
                .limit(top_k)
            )
            rows = list((await self.db.execute(stmt)).scalars())
        else:
            # SQLite(테스트)에는 벡터 연산이 없어 파이썬으로 계산한다
            stmt = select(UserPersonaFact).where(
                UserPersonaFact.user_id == user_id,
                UserPersonaFact.status == "confirmed",
            )
            all_rows = list((await self.db.execute(stmt)).scalars())
            scored = [(cosine_distance_py(query_vec, r.embedding), r) for r in all_rows if r.embedding is not None]
            scored = [item for item in scored if item[0] < max_distance]
            scored.sort(key=lambda x: x[0])
            rows = [r for _, r in scored[:top_k]]

        return [((r.updated_at or r.created_at).strftime("%Y-%m-%d"), r.fact) for r in rows]

    async def get_reconcile_candidates(
        self,
        user_id: str,
        new_vec: list[float],
        *,
        category: str | None,
        k: int = 3,
    ) -> list[tuple[UserPersonaFact, float]]:
        """confirmed + tentative 전체에서 유사도(1 - distance) 상위 k개와 같은 카테고리 상위 k개를 반환한다."""
        stmt = select(UserPersonaFact).where(
            UserPersonaFact.user_id == user_id,
            UserPersonaFact.status.in_(("confirmed", "tentative")),
        )
        rows = list((await self.db.execute(stmt)).scalars())
        if not rows:
            return []

        scored: list[tuple[UserPersonaFact, float]] = []
        for r in rows:
            # pgvector 는 numpy 배열을 돌려주므로 `if r.embedding` 은 ValueError — 반드시 `is not None`
            sim = 1.0 - cosine_distance_py(new_vec, r.embedding) if r.embedding is not None else 0.0
            scored.append((r, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_by_vec = scored[:k]
        seen_ids = {r.id for r, _ in top_by_vec}

        if category:
            same_cat = [(r, sim) for r, sim in scored if r.category == category and r.id not in seen_ids][:k]
            top_by_vec.extend(same_cat)

        top_by_vec.sort(key=lambda x: x[1], reverse=True)
        return top_by_vec

    async def add_fact(
        self,
        *,
        user_id: str,
        category: str | None,
        fact: str,
        confidence: float,
        status: str,
        embedding: list[float],
        embedding_model: str,
        source_type: str = "practice",
        source_message_id: int | None = None,
    ) -> UserPersonaFact:
        clean_fact = " ".join(fact.split())
        existing_stmt = select(UserPersonaFact).where(
            UserPersonaFact.user_id == user_id,
            UserPersonaFact.fact == clean_fact,
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            # 같은 문장이 이미 있으면 새 행을 만들지 않고 승격·신뢰도·시각만 갱신한다
            if status == "confirmed":
                existing.status = "confirmed"
            existing.confidence = max(existing.confidence or 0.0, confidence)
            existing.updated_at = datetime.now(UTC)
            await self.db.flush()
            return existing

        row = UserPersonaFact(
            user_id=user_id,
            category=category,
            fact=clean_fact,
            confidence=confidence,
            status=status,
            embedding=embedding,
            embedding_model=embedding_model,
            source_type=source_type,
            source_message_id=source_message_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def apply_reconcile_action(
        self,
        *,
        action: str,
        target_id: int | None,
        user_id: str,
        category: str | None,
        fact: str,
        confidence: float,
        status: str,
        embedding: list[float],
        embedding_model: str,
        source_type: str = "practice",
        source_message_id: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        if action == "NOOP":
            return
        if action == "ADD" or target_id is None:
            await self.add_fact(
                user_id=user_id,
                category=category,
                fact=fact,
                confidence=confidence,
                status=status,
                embedding=embedding,
                embedding_model=embedding_model,
                source_type=source_type,
                source_message_id=source_message_id,
            )
            return

        target = await self.db.get(UserPersonaFact, target_id)
        # 다른 사용자의 행이거나 없는 id(LLM 이 지어낸 값)면 아무것도 하지 않는다
        if target is None or target.user_id != user_id:
            return

        if action == "PROMOTE":
            target.status = "confirmed"
            target.confidence = max(target.confidence or 0.0, confidence)
            target.updated_at = now
        elif action == "UPDATE":
            target.category = category or target.category
            target.fact = " ".join(fact.split())
            target.confidence = confidence
            if status == "confirmed" or target.status == "confirmed":
                target.status = "confirmed"
            target.embedding = embedding
            target.embedding_model = embedding_model
            target.source_message_id = source_message_id
            target.updated_at = now
        elif action == "DELETE":
            await self.db.execute(delete(UserPersonaFact).where(UserPersonaFact.id == target_id))
        await self.db.flush()
