"""내부 API 키 검사(require_internal_key) 테스트."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import require_internal_key


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", "secret")
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_internal_key)])
    def protected() -> dict:
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize("header", ["X-Internal-Api-Key", "X-API-Key"])
def test_accepts_either_header_name(client, header):
    assert client.get("/protected", headers={header: "secret"}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Internal-Api-Key": "wrong"}, {"X-API-Key": "wrong"}],
)
def test_rejects_missing_or_wrong_key(client, headers):
    r = client.get("/protected", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid internal api key"}


def test_skips_check_when_key_not_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", "")
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_internal_key)])
    def protected() -> dict:
        return {"ok": True}

    assert TestClient(app).get("/protected").status_code == 200
