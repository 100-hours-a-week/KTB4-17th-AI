"""소재 매칭 (plan_topics) — 두 사람의 fact 임베딩으로 대화 소재와 대화 계획을 만든다. LLM 없음.

fetch_facts(db, user_id)          소재로 쓸 수 있는 fact 만 DB 에서 읽는다
match_topics(facts_a, facts_b)    유사도 행렬 → 공통 소재·단방향 소재·모드·대화 계획
build_segments(turns, items)      왕복 구간에 소재를 배정한다
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.v2.persona.models import UserPersonaFact
from app.v2.practice.memory import _sanitize as sanitize

from .schemas import TOPIC_CATEGORIES, FactRow, Mode, Segment, Speaker, TopicFact, TopicItem, TopicPlan, fact_ref

__all__ = ["build_segments", "fetch_facts", "match_topics", "sanitize"]


async def fetch_facts(db: AsyncSession, user_id: str | None, *, limit: int | None = None) -> list[FactRow]:
    """확정(confirmed)·허용 카테고리·같은 임베딩 모델 벡터가 있는 fact 만, 최신순."""
    if not user_id:
        return []
    stmt = (
        select(UserPersonaFact)
        .where(
            UserPersonaFact.user_id == user_id,
            UserPersonaFact.status == "confirmed",
            UserPersonaFact.category.in_(TOPIC_CATEGORIES),
            UserPersonaFact.embedding.is_not(None),
            UserPersonaFact.embedding_model == get_settings().embedding_model_name,
        )
        .order_by(UserPersonaFact.created_at.desc(), UserPersonaFact.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    out: list[FactRow] = []
    for r in rows:
        text = sanitize(r.fact)
        # SQLite(테스트)는 JSON null 을 'null' 로 저장할 수 있어 파이썬에서 한 번 더 거른다
        if not text or not r.embedding:
            continue
        out.append(
            FactRow(id=r.id, category=r.category or "", text=text, embedding=list(r.embedding), created_at=r.created_at)
        )
    return out


def _matrix(facts: list[FactRow]) -> np.ndarray:
    """L2 정규화된 (n, d) 행렬. 저장 시 정규화돼 있지만 안전하게 다시 한다."""
    m = np.asarray([f.embedding for f in facts], dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _latest_hobby(facts: list[FactRow], used: set[int]) -> FactRow | None:
    # facts 는 최신순으로 들어온다. 취미가 우선이고, 없으면 다른 카테고리라도 쓴다 — fact 가 있는데 persona_only 로 버리지 않게
    # (실측: v2 메모리 추출은 등산·요리 같은 취미도 '일상'으로 분류하는 경우가 많다)
    unused = [f for f in facts if f.id not in used]
    return next((f for f in unused if f.category == "취미"), unused[0] if unused else None)


def match_topics(
    facts_a: list[FactRow],
    facts_b: list[FactRow],
    *,
    turns: int,
    tau_shared: float | None = None,
    dedup_sim: float | None = None,
    max_shared: int | None = None,
    max_facts: int | None = None,
) -> TopicPlan:
    s = get_settings()
    tau = s.simulation_v2_tau_shared if tau_shared is None else tau_shared
    dedup = s.simulation_v2_topic_dedup_sim if dedup_sim is None else dedup_sim
    cap = s.simulation_v2_max_shared_topics if max_shared is None else max_shared
    per_user = s.simulation_v2_max_facts_per_user if max_facts is None else max_facts

    # ── 공통 소재: 유사도 내림차순 탐욕 선택 ──
    shared: list[tuple[FactRow, FactRow, float]] = []
    if facts_a and facts_b and cap > 0:
        ea, eb = _matrix(facts_a), _matrix(facts_b)
        sim = ea @ eb.T
        ii, jj = np.where(sim >= tau)
        order = np.argsort(-sim[ii, jj], kind="stable")
        picked_vecs: list[np.ndarray] = []
        used_a: set[int] = set()
        used_b: set[int] = set()
        for k in order:
            i, j = int(ii[k]), int(jj[k])
            if i in used_a or j in used_b:
                continue
            # 이미 고른 소재와 너무 비슷하면 같은 이야기라 건너뛴다
            if any(float(ea[i] @ v) >= dedup or float(eb[j] @ v) >= dedup for v in picked_vecs):
                continue
            shared.append((facts_a[i], facts_b[j], float(sim[i, j])))
            used_a.add(i)
            used_b.add(j)
            picked_vecs.extend([ea[i], eb[j]])
            if len(shared) >= cap:
                break

    # ── 단방향 소재: 공통이 2개 미만이면 각자 최신 취미 하나씩 ──
    solo: list[tuple[Speaker, FactRow]] = []
    if len(shared) < 2:
        used_ids_a = {fa.id for fa, _, _ in shared}
        used_ids_b = {fb.id for _, fb, _ in shared}
        if (ha := _latest_hobby(facts_a, used_ids_a)) is not None:
            solo.append(("a", ha))
        if (hb := _latest_hobby(facts_b, used_ids_b)) is not None:
            solo.append(("b", hb))

    mode: Mode = "shared" if shared else ("solo" if solo else "persona_only")

    # ── 프롬프트에 노출할 fact 목록: 소재 fact 먼저, 남는 자리는 최신 fact ──
    topic_rows_a = [fa for fa, _, _ in shared] + [f for o, f in solo if o == "a"]
    topic_rows_b = [fb for _, fb, _ in shared] + [f for o, f in solo if o == "b"]

    def exposed(owner: Speaker, topic_rows: list[FactRow], all_rows: list[FactRow]) -> list[TopicFact]:
        rows: list[FactRow] = []
        seen: set[int] = set()
        for f in [*topic_rows, *all_rows]:
            if f.id in seen:
                continue
            # 소재 fact 는 상한과 무관하게 넣는다 (상한이 소재 수보다 작아도 계획이 깨지지 않게)
            if len(rows) >= per_user and f not in topic_rows:
                continue
            rows.append(f)
            seen.add(f.id)
        return [
            TopicFact(ref=fact_ref(owner, n), fact_id=f.id, owner=owner, category=f.category, text=f.text)
            for n, f in enumerate(rows, start=1)
        ]

    tf_a = exposed("a", topic_rows_a, facts_a)
    tf_b = exposed("b", topic_rows_b, facts_b)
    by_id_a = {t.fact_id: t for t in tf_a}
    by_id_b = {t.fact_id: t for t in tf_b}

    items: list[TopicItem] = []
    for fa, fb, sv in shared:
        items.append(
            TopicItem(
                id=f"t{len(items) + 1}", kind="shared", a_fact=by_id_a[fa.id], b_fact=by_id_b[fb.id], sim=round(sv, 4)
            )
        )
    for owner, f in solo:
        items.append(
            TopicItem(
                id=f"t{len(items) + 1}",
                kind="solo",
                a_fact=by_id_a[f.id] if owner == "a" else None,
                b_fact=by_id_b[f.id] if owner == "b" else None,
            )
        )

    return TopicPlan(
        mode=mode,
        items=items,
        segments=build_segments(turns, items),
        facts_a=tf_a if mode != "persona_only" else [],
        facts_b=tf_b if mode != "persona_only" else [],
    )


def build_segments(turns: int, items: list[TopicItem]) -> list[Segment]:
    """1..turns 를 빈틈 없이 덮는 대화 계획. 마지막 왕복은 항상 마무리.

    10왕복 기준 비율: 인사 2 / 소재1 3 / 소재2(또는 연애 스타일) 3 / 소재3(또는 데이트 취향) 1 / 마무리 1.
    """
    if turns <= 0:
        return []
    if turns == 1:
        return [Segment(start_round=1, end_round=1, label="closing")]
    if turns == 2:
        return [
            Segment(start_round=1, end_round=1, label="greeting"),
            Segment(start_round=2, end_round=2, label="closing"),
        ]

    body = turns - 1  # 마무리를 뺀 왕복 수
    # 10왕복의 앞 9왕복 비율 2:3:3:1 을 body 에 맞춘다. 각 구간 최소 1, 짧으면 뒤 구간부터 생략
    weights = [2, 3, 3, 1]
    raw = [w * body / 9 for w in weights]
    sizes = [max(1, round(x)) for x in raw]
    # 합을 body 에 맞춘다: 넘치면 뒤에서부터 줄이고(0 이 되면 생략), 모자라면 소재1 부터 늘린다
    while sum(sizes) > body:
        for k in (3, 2, 0, 1):
            if sum(sizes) <= body:
                break
            if sizes[k] > 0:
                sizes[k] -= 1
    k = 1
    while sum(sizes) < body:
        sizes[k] += 1
        k = 1 if k == 2 else 2

    topic_ids = [it.id for it in items]

    def topic_or(idx: int, fallback: str) -> tuple[str, str | None]:
        if idx < len(topic_ids):
            return "topic", topic_ids[idx]
        return fallback, None

    plan = [
        ("greeting", None),
        topic_or(0, "style"),
        topic_or(1, "style"),
        topic_or(2, "date"),
    ]
    segments: list[Segment] = []
    start = 1
    for (label, tid), size in zip(plan, sizes, strict=True):
        if size <= 0:
            continue
        prev = segments[-1] if segments else None
        if prev is not None and label != "topic" and prev.label == label:
            # 소재가 없어 같은 주제(연애 스타일)가 이어지면 한 구간으로 합친다
            prev.end_round += size
            start += size
            continue
        segments.append(Segment(start_round=start, end_round=start + size - 1, label=label, topic_id=tid))  # type: ignore[arg-type]
        start += size
    segments.append(Segment(start_round=turns, end_round=turns, label="closing"))
    return segments
