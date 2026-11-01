"""실제 ASGI 앱의 HTTP 계측 계약. DB·LLM을 호출하는 lifespan은 실행하지 않는다."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.main import app


@pytest.fixture(scope="module")
def client():
    original_routes = list(app.router.routes)

    async def item(item_id: str):
        return {"item_id": item_id}

    async def status(status_code: int):
        await asyncio.sleep(0.001)
        if status_code == 500:
            raise RuntimeError("synthetic failure")
        if status_code != 200:
            raise HTTPException(status_code)
        return {"ok": True}

    app.add_api_route("/_metrics_probe/items/{item_id}", item, include_in_schema=False)
    app.add_api_route("/_metrics_probe/status/{status_code}", status, include_in_schema=False)
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()
        app.router.routes[:] = original_routes


def samples(client):
    response = client.get("/ai/api/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    return [sample for family in text_string_to_metric_families(response.text) for sample in family.samples]


def value(samples, name, labels):
    return sum(
        sample.value
        for sample in samples
        if sample.name == name and all(sample.labels.get(key) == val for key, val in labels.items())
    )


def test_metrics_endpoint_is_available_but_not_in_openapi(client):
    samples(client)
    assert "/ai/api/metrics" not in app.openapi()["paths"]


def test_counts_exact_success_validation_and_server_error_statuses(client):
    before = samples(client)
    handler = "/_metrics_probe/status/{status_code}"
    for status in (200, 422, 500):
        assert client.get(f"/_metrics_probe/status/{status}").status_code == status
    after = samples(client)
    for status in (200, 422, 500):
        labels = {"handler": handler, "method": "GET", "status": str(status)}
        assert value(after, "http_requests_total", labels) - value(before, "http_requests_total", labels) == 1
    latency_labels = {"handler": handler, "method": "GET"}
    assert (
        value(after, "http_request_duration_seconds_count", latency_labels)
        - value(before, "http_request_duration_seconds_count", latency_labels)
        == 3
    )
    assert (
        value(after, "http_request_duration_seconds_sum", latency_labels)
        - value(before, "http_request_duration_seconds_sum", latency_labels)
        > 0
    )


def test_resource_ids_share_a_template_instead_of_creating_metric_labels(client):
    before = samples(client)
    assert client.get("/_metrics_probe/items/first-user", params={"session_id": "private-session"}).status_code == 200
    assert client.get("/_metrics_probe/items/second-user").status_code == 200
    after = samples(client)
    labels = {"handler": "/_metrics_probe/items/{item_id}", "method": "GET", "status": "200"}
    assert value(after, "http_requests_total", labels) - value(before, "http_requests_total", labels) == 2
    assert all(
        not any(secret in str(sample.labels) for secret in ("first-user", "second-user", "private-session"))
        for sample in after
    )


def test_health_and_scrape_requests_do_not_inflate_application_traffic(client):
    before = samples(client)
    for path in ("/health", "/ai/api/health", "/ai/api/metrics", "/ai/api/metrics"):
        assert client.get(path).status_code == 200
    after = samples(client)
    assert value(after, "http_requests_total", {}) == value(before, "http_requests_total", {})
    assert value(after, "http_request_duration_seconds_count", {}) == value(
        before, "http_request_duration_seconds_count", {}
    )
