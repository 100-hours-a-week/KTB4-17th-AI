"""실제 ASGI 앱의 HTTP 계측 계약. DB·LLM을 호출하는 lifespan은 실행하지 않는다."""

import asyncio

import pytest
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
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

    async def background(background_tasks: BackgroundTasks):
        async def after_response():
            test_client.background_snapshot = parse_samples(generate_latest().decode())
            await asyncio.sleep(0.05)

        background_tasks.add_task(after_response)
        return {"ok": True}

    async def stream():
        async def chunks():
            yield b"first"
            await asyncio.sleep(0.02)
            test_client.streaming_snapshot = parse_samples(generate_latest().decode())
            yield b"last"

        return StreamingResponse(chunks())

    app.add_api_route("/_metrics_probe/items/{item_id}", item, include_in_schema=False)
    app.add_api_route("/_metrics_probe/status/{status_code}", status, include_in_schema=False)
    app.add_api_route("/_metrics_probe/background", background, include_in_schema=False)
    app.add_api_route("/_metrics_probe/stream", stream, include_in_schema=False)
    nested = APIRouter(prefix="/_metrics_probe/prefixed")
    nested.add_api_route("/items/{item_id}", item, include_in_schema=False)
    app.include_router(nested)
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()
        app.router.routes[:] = original_routes


def parse_samples(text):
    return [sample for family in text_string_to_metric_families(text) for sample in family.samples]


def samples(client):
    response = client.get("/ai/api/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    return parse_samples(response.text)


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
    for path in ("/health", "/health/", "/ai/api/health", "/ai/api/health/", "/ai/api/metrics", "/ai/api/metrics/"):
        assert client.get(path).status_code == 200
    after = samples(client)
    assert value(after, "http_requests_total", {}) == value(before, "http_requests_total", {})
    assert value(after, "http_request_duration_seconds_count", {}) == value(
        before, "http_request_duration_seconds_count", {}
    )


def test_redirects_do_not_use_raw_ids_as_handler_labels(client):
    before = samples(client)
    for item_id in ("redirect-private-first", "redirect-private-second"):
        assert client.get(f"/_metrics_probe/items/{item_id}/", follow_redirects=False).status_code == 307
    after = samples(client)
    assert (
        value(after, "http_requests_total", {"status": "307"}) - value(before, "http_requests_total", {"status": "307"})
        == 2
    )
    assert all("redirect-private-" not in str(sample.labels) for sample in after)


def test_completed_http_request_is_recorded_before_background_work(client):
    before = samples(client)
    assert client.get("/_metrics_probe/background").status_code == 200
    labels = {"handler": "/_metrics_probe/background", "method": "GET"}
    assert (
        value(client.background_snapshot, "http_requests_total", labels) - value(before, "http_requests_total", labels)
        == 1
    )
    assert (
        value(client.background_snapshot, "http_request_duration_seconds_count", labels)
        - value(before, "http_request_duration_seconds_count", labels)
        == 1
    )
    after = samples(client)
    assert value(after, "http_request_duration_seconds_sum", labels) == value(
        client.background_snapshot, "http_request_duration_seconds_sum", labels
    )


def test_included_router_keeps_the_full_template_prefix(client):
    before = samples(client)
    assert client.get("/_metrics_probe/prefixed/items/private-prefix-id").status_code == 200
    after = samples(client)
    labels = {"handler": "/_metrics_probe/prefixed/items/{item_id}", "status": "200"}
    assert value(after, "http_requests_total", labels) - value(before, "http_requests_total", labels) == 1


def test_streaming_latency_includes_the_last_response_chunk(client):
    before = samples(client)
    response = client.get("/_metrics_probe/stream")
    assert response.status_code == 200
    assert response.content == b"firstlast"
    labels = {"handler": "/_metrics_probe/stream", "method": "GET"}
    assert value(client.streaming_snapshot, "http_requests_total", labels) == value(
        before, "http_requests_total", labels
    )
    after = samples(client)
    assert value(after, "http_requests_total", labels) - value(before, "http_requests_total", labels) == 1
    assert (
        value(after, "http_request_duration_seconds_sum", labels)
        - value(before, "http_request_duration_seconds_sum", labels)
        >= 0.02
    )
