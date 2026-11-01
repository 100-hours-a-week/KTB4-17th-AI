"""페르소나 카드 JSON 데이터를 로컬 PostgreSQL DB에 적재하는 스크립트.

1. 신규 persona_cards 테이블에 전체 Persona Card 구조화 JSON 적재
2. 기존 서비스 호환을 위해 onboarding_sessions 및 personas 테이블에도 매핑 데이터 적재
"""

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base, engine, SessionLocal
from app.features.persona.models import OnboardingSession, PersonaCardRecord, PersonaRecord


JSON_PATH = Path("페르소나 데이터/persona_cards.json")


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


async def init_tables():
    """persona_cards 테이블이 없으면 생성."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables initialized (including persona_cards).")


async def load_persona_cards():
    """persona_cards.json 파일의 데이터를 DB에 적재."""
    if not JSON_PATH.exists():
        raise FileNotFoundError(f"JSON file not found: {JSON_PATH}")

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        cards_data = json.load(f)

    print(f"Loaded {len(cards_data)} persona cards from {JSON_PATH}")

    async with SessionLocal() as db:  # type: AsyncSession
        for data in cards_data:
            persona_id = data["persona_id"]
            user_id = data["user_id"]
            name = data["name"]
            mbti = data.get("mbti_reference")

            # 1. persona_cards 테이블 적재 / 업데이트
            stmt = select(PersonaCardRecord).where(PersonaCardRecord.persona_id == persona_id)
            result = await db.execute(stmt)
            existing_card = result.scalar_one_or_none()

            if existing_card:
                print(f"[persona_cards] Updating: {persona_id} ({name})")
                for key, val in data.items():
                    if hasattr(existing_card, key):
                        setattr(existing_card, key, val)
            else:
                print(f"[persona_cards] Inserting: {persona_id} ({name})")
                new_card = PersonaCardRecord(
                    persona_id=data["persona_id"],
                    user_id=data["user_id"],
                    name=data["name"],
                    gender=data.get("gender", "unspecified"),
                    generation_mode=data["generation_mode"],
                    mbti_reference=mbti,
                    traits=data["traits"],
                    imputed_scores=data.get("imputed_scores", []),
                    unknown_origin_scores=data.get("unknown_origin_scores", []),
                    available_summaries=data.get("available_summaries", []),
                    missing_summaries=data.get("missing_summaries", []),
                    behavior_traits=data["behavior_traits"],
                    partner_preferences=data["partner_preferences"],
                    communication_style=data["communication_style"],
                    conflict_style=data["conflict_style"],
                    relationship_style=data["relationship_style"],
                    personality_rules=data["personality_rules"],
                    dynamic_state=data["dynamic_state"],
                    dialogue_examples=data.get("dialogue_examples", []),
                    evidence=data.get("evidence", []),
                    creative_elements=data.get("creative_elements", []),
                    conflicts=data.get("conflicts", []),
                    quality_checks=data["quality_checks"],
                )
                db.add(new_card)

            # 2. 기존 personas & onboarding_sessions 테이블 호환 적재
            # 세션 확인
            session_stmt = select(OnboardingSession).where(OnboardingSession.user_id == user_id)
            session_res = await db.execute(session_stmt)
            existing_session = session_res.scalars().first()

            if not existing_session:
                session_id = _uuid()
                new_session = OnboardingSession(
                    id=session_id,
                    user_id=user_id,
                    nickname=name,
                    total_turns=10,
                    turn_index=10,
                    mbti=mbti,
                    status="completed",
                )
                db.add(new_session)
            else:
                session_id = existing_session.id

            # 기존 personas 레코드 확인
            persona_stmt = select(PersonaRecord).where(PersonaRecord.user_id == user_id)
            persona_res = await db.execute(persona_stmt)
            existing_persona = persona_res.scalars().first()

            # scores 딕셔너리 추출 (dimension: effective_score)
            scores_dict = {dim: t_info["effective_score"] for dim, t_info in data["traits"].items()}
            if "summaries_data" in data and data["summaries_data"]:
                summaries_list = data["summaries_data"]
            else:
                summaries_list = [
                    {"category": cat, "title": f"{cat} 요약", "content": f"{name}의 {cat} 성향"}
                    for cat in data.get("available_summaries", [])
                ]
            narrative_dict = {
                "headline": f"{name} ({mbti}) - {data['behavior_traits']['primary_traits'][0] if data['behavior_traits']['primary_traits'] else ''}",
                "body": f"{data['communication_style']['speech_level']} 화법과 {data['conflict_style']['primary_strategy']} 중심의 캐릭터",
                "traits": data["behavior_traits"].get("primary_traits", []),
            }

            if existing_persona:
                print(f"[personas] Updating compatible record: {user_id} ({name})")
                existing_persona.scores = scores_dict
                existing_persona.narrative = narrative_dict
                existing_persona.summaries = summaries_list
                existing_persona.conversation_style = data["communication_style"]
                existing_persona.mbti = mbti
                existing_persona.is_confirmed = True
                existing_persona.confirmed_at = _now()
            else:
                print(f"[personas] Inserting compatible record: {user_id} ({name})")
                new_persona = PersonaRecord(
                    session_id=session_id,
                    user_id=user_id,
                    scores=scores_dict,
                    texts={"interests": [], "routine": [], "date_prefer": [], "date_avoid": []},
                    confidence={dim: "HIGH" for dim in scores_dict},
                    narrative=narrative_dict,
                    summaries=summaries_list,
                    conversation_style=data["communication_style"],
                    version=1,
                    is_confirmed=True,
                    confirmed_at=_now(),
                    mbti=mbti,
                    source="llm",
                )
                db.add(new_persona)

        await db.commit()
        print("All records committed successfully.")

        # 검증 조회
        stmt_all_cards = select(PersonaCardRecord.persona_id, PersonaCardRecord.name, PersonaCardRecord.mbti_reference)
        all_cards = (await db.execute(stmt_all_cards)).fetchall()
        print(f"\n[Verification: persona_cards] Total: {len(all_cards)}")
        for r in all_cards:
            print(f"- {r.persona_id} | {r.name} | {r.mbti_reference}")

        stmt_all_personas = select(PersonaRecord.id, PersonaRecord.user_id, PersonaRecord.mbti, PersonaRecord.is_confirmed)
        all_personas = (await db.execute(stmt_all_personas)).fetchall()
        print(f"\n[Verification: personas] Total: {len(all_personas)}")
        for r in all_personas:
            print(f"- {r.id} | user_id: {r.user_id} | mbti: {r.mbti} | confirmed: {r.is_confirmed}")


async def main():
    await init_tables()
    await load_persona_cards()


if __name__ == "__main__":
    asyncio.run(main())
