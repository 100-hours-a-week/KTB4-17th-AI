"""페르소나의 대화 스타일 — 스키마 정리 규칙, 응답 매핑, 추출 버전 저장."""

import asyncio
from datetime import UTC, datetime

from conftest import seed_persona, with_db

from app.features.persona.models import PersonaRecord
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import ConversationStyle, StyleExtraction
from app.features.persona.service import persona_response


def test_style_drops_unknown_speech_level_and_truncates_long_text():
    s = ConversationStyle.model_validate({"speech_level": "사투리", "summary": "가" * 500, "reaction": "나" * 200})

    assert s.speech_level is None
    assert len(s.summary) == 200
    assert len(s.reaction) == 80


def test_style_list_fields_accept_garbage_as_empty():
    s = ConversationStyle.model_validate({"frequent_phrases": "오 대박", "slang": None})

    assert s.frequent_phrases == []
    assert s.slang == []


def test_style_phrase_weights_validation_and_backward_compatibility():
    # 1. phrase_weights 가 없는 레거시 데이터 (하위 호환성)
    s_legacy = ConversationStyle.model_validate({"speech_level": "존댓말", "frequent_phrases": ["감사합니다"]})
    assert s_legacy.phrase_weights == {}

    # 2. 비정상 값 필터링 및 clamp
    s = ConversationStyle.model_validate(
        {
            "phrase_weights": {
                "~요": "0.85",
                "~음": 0.4,
                "이상한값": "not_a_number",
                "": 0.5,
                "범위초과": 1.5,
                "음수": -0.2,
            }
        }
    )
    assert s.phrase_weights["~요"] == 0.85
    assert s.phrase_weights["~음"] == 0.4
    assert s.phrase_weights["범위초과"] == 1.0
    assert s.phrase_weights["음수"] == 0.0
    assert "이상한값" not in s.phrase_weights
    assert "" not in s.phrase_weights


def test_extraction_scores_out_of_range_are_dropped_not_failed():
    r = StyleExtraction.model_validate({"style": {}, "disclosure": 140, "positivity": 60, "avoidance": 10})

    assert r.disclosure is None
    assert r.positivity == 60
    assert not hasattr(r, "avoidance")


def test_persona_response_carries_conversation_style():
    record = PersonaRecord(
        id="p1",
        session_id="s1",
        user_id="u1",
        scores={},
        texts={},
        confidence={},
        version=2,
        is_confirmed=True,
        confirmed_at=datetime(2026, 10, 5, tzinfo=UTC),
        source="kakao",
        conversation_style={"speech_level": "반말", "frequent_phrases": ["오 대박"]},
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
    )

    resp = persona_response(record)

    assert resp.source == "kakao"
    assert resp.conversation_style.speech_level == "반말"
    assert resp.conversation_style.frequent_phrases == ["오 대박"]


def test_save_extracted_version_confirms_new_version_on_top_of_base():
    async def body(factory):
        async with factory() as db:
            base = await seed_persona(
                db,
                persona_id="me",
                user_id="u-me",
                nickname="민수",
                mbti="ENFP",
                texts={"interests": ["러닝"]},
                scores={"disclosure": 70},
            )
            repo = PersonaRepository(db)

            record = await repo.save_extracted_version(
                base,
                source="kakao",
                scores={"disclosure": 61},
                confidence={},
                narrative=None,
                conversation_style={"speech_level": "반말"},
            )
            await db.commit()
            latest = await repo.latest_persona_for_user("u-me")

            assert latest.id == record.id
            assert (record.version, record.previous_id, record.source) == (2, "me", "kakao")
            assert record.is_confirmed and record.confirmed_at is not None
            assert record.mbti == "ENFP" and record.texts == {"interests": ["러닝"]}

    asyncio.run(with_db(body))


def test_save_reset_version_removes_style_and_restores_original_scores():
    async def body(factory):
        async with factory() as db:
            original = await seed_persona(
                db,
                persona_id="p-orig",
                user_id="u-reset",
                nickname="지은",
                mbti="INFP",
                texts={"interests": ["독서"]},
                scores={"disclosure": 50, "positivity": 60},
                confidence={"disclosure": "HIGH", "positivity": "HIGH"},
            )
            repo = PersonaRepository(db)
            extracted = await repo.save_extracted_version(
                original,
                source="practice",
                scores={"disclosure": 75, "positivity": 80},
                confidence={"disclosure": "HIGH", "positivity": "HIGH"},
                narrative=None,
                conversation_style={"speech_level": "존댓말"},
            )
            await db.commit()

            reset_record = await repo.save_reset_version(extracted, original)
            await db.commit()
            latest = await repo.latest_persona_for_user("u-reset")

            assert latest.id == reset_record.id
            assert latest.version == 3
            assert latest.source == "reset"
            assert latest.conversation_style is None
            assert latest.scores == {"disclosure": 50, "positivity": 60}
            assert latest.confidence == {"disclosure": "HIGH", "positivity": "HIGH"}

    asyncio.run(with_db(body))
