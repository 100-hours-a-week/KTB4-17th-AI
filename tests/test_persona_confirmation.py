import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.features.persona import api, lookup
from app.features.persona.models import OnboardingSession, PersonaRecord
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import ConfirmPersonaRequest, ConfirmPersonaResponse, PersonaRef
from app.features.persona.service import (
    OnboardingNotFinished,
    OnboardingService,
    PersonaAlreadyConfirmed,
    PersonaConfirmationConflict,
    PersonaDraftNotFound,
    persona_response,
)

NOW = datetime(2026, 9, 26, 12, 30, tzinfo=UTC)


def test_confirmation_columns_and_constraints_are_in_metadata():
    persona_columns = PersonaRecord.__table__.columns
    constraint_names = {constraint.name for constraint in PersonaRecord.__table__.constraints}

    assert "is_confirmed" in persona_columns
    assert "confirmed_at" in persona_columns
    assert "mbti" in persona_columns
    assert "uq_personas_session_version" in constraint_names
    assert "ck_personas_confirmation_consistent" in constraint_names
    assert "ck_personas_mbti_confirmed_only" in constraint_names
    assert "ck_personas_mbti_valid" in constraint_names
    assert "user_profiles" not in Base.metadata.tables


def test_build_rejects_unfinished_onboarding_before_llm_call():
    service = OnboardingService.__new__(OnboardingService)
    session = SimpleNamespace(pending_topic_id="contact", turn_index=3, total_turns=10)

    with pytest.raises(OnboardingNotFinished):
        asyncio.run(service.build_persona(session))


def test_build_reuses_latest_unconfirmed_draft():
    draft = SimpleNamespace(is_confirmed=False, previous_id=None, source="llm")

    async def latest_persona(session_id):
        return draft

    service = OnboardingService.__new__(OnboardingService)
    service.repo = SimpleNamespace(latest_persona=latest_persona)
    service._to_response = lambda session, record, previous: "existing-draft"
    session = SimpleNamespace(id="session-1", pending_topic_id=None, turn_index=10, total_turns=10)

    result = asyncio.run(service.build_draft(session))

    assert result == "existing-draft"


def test_build_rejects_session_whose_latest_persona_is_confirmed():
    async def latest_persona(session_id):
        return SimpleNamespace(is_confirmed=True)

    service = OnboardingService.__new__(OnboardingService)
    service.repo = SimpleNamespace(latest_persona=latest_persona)
    session = SimpleNamespace(id="session-1", pending_topic_id=None, turn_index=10, total_turns=10)

    with pytest.raises(PersonaAlreadyConfirmed):
        asyncio.run(service.build_draft(session))


def test_confirm_request_normalizes_and_validates_mbti():
    req = ConfirmPersonaRequest(
        persona_id="persona-123",
        is_confirmed=True,
        mbti=" intp ",
        confirmed_at="2026-09-26T12:30:00Z",
    )

    assert req.mbti == "INTP"
    assert req.confirmed_at == NOW


def test_persona_response_includes_stored_mbti():
    record = SimpleNamespace(
        id="persona-123",
        version=1,
        is_confirmed=True,
        confirmed_at=NOW,
        mbti="INTP",
        source="llm",
        scores={},
        confidence={},
        narrative=None,
        texts={},
        created_at=NOW,
    )

    assert persona_response(record).mbti == "INTP"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_confirmed", False),
        ("mbti", "NOPE"),
        ("confirmed_at", "2026-09-26T12:30:00"),
    ],
)
def test_confirm_request_rejects_invalid_values(field, value):
    data = {
        "persona_id": "persona-123",
        "is_confirmed": True,
        "mbti": "INTP",
        "confirmed_at": "2026-09-26T12:30:00Z",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        ConfirmPersonaRequest(**data)


def _service(record):
    calls = []

    async def get_persona_for_update(persona_id):
        calls.append(("get", persona_id))
        return record

    async def save_confirmation(target, mbti, confirmed_at):
        calls.append(("save", target.id, mbti, confirmed_at))
        target.is_confirmed = True
        target.confirmed_at = confirmed_at
        target.mbti = mbti
        return target

    service = OnboardingService.__new__(OnboardingService)
    service.repo = SimpleNamespace(
        get_persona_for_update=get_persona_for_update,
        save_confirmation=save_confirmation,
    )
    return service, calls


def test_confirm_persona_saves_draft_with_its_user_id():
    record = SimpleNamespace(
        id="persona-123",
        user_id="user-1",
        is_confirmed=False,
        confirmed_at=None,
        mbti=None,
    )
    service, calls = _service(record)

    result = asyncio.run(service.confirm_persona(record.id, "INTP", NOW))

    assert result == ConfirmPersonaResponse(
        persona_id="persona-123",
        user_id="user-1",
        is_confirmed=True,
        mbti="INTP",
        confirmed_at=NOW,
    )
    assert ("save", "persona-123", "INTP", NOW) in calls


def test_confirm_persona_is_idempotent_for_same_mbti():
    record = SimpleNamespace(
        id="persona-123",
        user_id="user-1",
        is_confirmed=True,
        confirmed_at=NOW,
        mbti="INTP",
    )
    service, calls = _service(record)

    result = asyncio.run(service.confirm_persona(record.id, "INTP", NOW))

    assert result.is_confirmed is True
    assert not any(call[0] == "save" for call in calls)


def test_confirm_persona_fills_empty_legacy_persona_mbti():
    record = SimpleNamespace(
        id="persona-123",
        user_id="user-1",
        is_confirmed=True,
        confirmed_at=NOW,
        mbti=None,
    )
    service, calls = _service(record)

    result = asyncio.run(service.confirm_persona(record.id, "INTP", NOW))

    assert result.mbti == "INTP"
    assert ("save", "persona-123", "INTP", NOW) in calls


def test_confirm_persona_rejects_conflicting_mbti():
    record = SimpleNamespace(
        id="persona-123",
        user_id="user-1",
        is_confirmed=True,
        confirmed_at=NOW,
        mbti="ENFP",
    )
    service, _ = _service(record)

    with pytest.raises(PersonaConfirmationConflict):
        asyncio.run(service.confirm_persona(record.id, "INTP", NOW))


def test_confirm_persona_rejects_unknown_draft():
    service, _ = _service(None)

    with pytest.raises(PersonaDraftNotFound):
        asyncio.run(service.confirm_persona("missing", "INTP", NOW))


def _client():
    calls = []

    class FakeService:
        async def confirm_persona(self, persona_id, mbti, confirmed_at):
            calls.append((persona_id, mbti, confirmed_at))
            return ConfirmPersonaResponse(
                persona_id=persona_id,
                user_id="user-1",
                is_confirmed=True,
                mbti=mbti,
                confirmed_at=confirmed_at,
            )

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def test_confirm_api_rejects_path_body_persona_id_mismatch():
    client, calls = _client()

    response = client.post(
        "/v1/persona/persona-123/confirm",
        json={
            "persona_id": "persona-456",
            "is_confirmed": True,
            "mbti": "intp",
            "confirmed_at": "2026-09-26T12:30:00Z",
        },
    )

    assert response.status_code == 409
    assert calls == []


def test_confirm_api_commits_normalized_mbti():
    client, calls = _client()

    response = client.post(
        "/v1/persona/persona-123/confirm",
        json={
            "persona_id": "persona-123",
            "is_confirmed": True,
            "mbti": "intp",
            "confirmed_at": "2026-09-26T12:30:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["mbti"] == "INTP"
    assert calls == [("persona-123", "INTP", NOW), ("commit",)]


def test_load_persona_hides_unconfirmed_draft(monkeypatch):
    draft = SimpleNamespace(id="persona-123", is_confirmed=False)

    class FakeRepository:
        def __init__(self, db):
            pass

        async def get_persona(self, persona_id):
            return draft

    monkeypatch.setattr(lookup, "PersonaRepository", FakeRepository)

    result = asyncio.run(lookup.load_persona(SimpleNamespace(), PersonaRef(persona_id="persona-123")))

    assert result is None


def test_confirmation_persists_mbti_on_confirmed_persona_atomically():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            onboarding = OnboardingSession(
                id="session-1",
                user_id="user-1",
                nickname="민수",
                total_turns=10,
                turn_index=10,
                pending_topic_id=None,
                used_topic_ids=[],
                coverage={},
                status="completed",
                turns=[],
            )
            draft = PersonaRecord(
                id="persona-123",
                session_id=onboarding.id,
                user_id=onboarding.user_id,
                scores={},
                texts={},
                confidence={},
                narrative=None,
                version=1,
                previous_id=None,
                is_confirmed=False,
                confirmed_at=None,
                mbti=None,
            )
            db.add_all([onboarding, draft])
            await db.commit()

            service = OnboardingService(PersonaRepository(db))
            response = await service.confirm_persona(draft.id, "INTP", NOW)
            await db.commit()

        async with session_factory() as verify_db:
            verify_repo = PersonaRepository(verify_db)
            stored = await verify_db.get(PersonaRecord, draft.id)
            latest = await verify_repo.latest_persona_for_user(onboarding.user_id)

        await engine.dispose()
        return response, stored, latest

    response, stored, latest = asyncio.run(scenario())

    assert response.user_id == "user-1"
    assert stored.is_confirmed is True
    stored_at = stored.confirmed_at.replace(tzinfo=UTC) if stored.confirmed_at.tzinfo is None else stored.confirmed_at
    assert stored_at == NOW
    assert stored.mbti == "INTP"
    assert latest.id == "persona-123"
