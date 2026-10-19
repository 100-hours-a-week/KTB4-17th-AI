from app.main import app


def registered_paths():
    return set(app.openapi()["paths"].keys())


def test_v2_practice_start_is_mounted():
    assert "/ai/api/v2/practice/start" in registered_paths()


def test_v1_routes_still_mounted():
    paths = registered_paths()
    assert any(p.startswith("/ai/api/v1/persona") for p in paths)
    assert any(p.startswith("/ai/api/v1/practice") for p in paths)
    assert any(p.startswith("/ai/api/v1/simulation") for p in paths)
