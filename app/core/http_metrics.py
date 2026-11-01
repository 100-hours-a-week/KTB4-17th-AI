"""HTTP 응답 완료 시점을 계측한다. 응답 후 BackgroundTasks는 지연시간에 포함하지 않는다."""

from time import perf_counter

from prometheus_fastapi_instrumentator import Instrumentator, metrics, routing
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

EXCLUDED_PATHS = {"/health", "/ai/api/health", "/ai/api/metrics"}


class HTTPMetricsMiddleware:
    def __init__(self, app: ASGIApp, instrumentations):
        self.app = app
        self.instrumentations = instrumentations

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        recorded = False

        def record() -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            request = Request(scope)
            handler = "none"
            if scope.get("route") is not None:
                handler = routing.get_route_name(request, should_include_root_path=False) or "none"
            elif status_code in (307, 308):
                # 라이브러리의 슬래시 리다이렉트 fallback은 원본 ID를 반환하므로
                # 리다이렉트 목적 경로를 먼저 정규 라우트 템플릿으로 해석한다.
                path = scope["path"]
                canonical = path.rstrip("/") if path.endswith("/") else path + "/"
                handler = (
                    routing.get_route_name(Request({**scope, "path": canonical}), should_include_root_path=False)
                    or "none"
                )
            info = metrics.Info(
                request=request,
                response=Response(status_code=status_code),
                method=scope["method"],
                modified_handler=handler,
                modified_status=str(status_code),
                modified_duration=max(perf_counter() - started, 0.0),
            )
            for instrumentation in self.instrumentations:
                instrumentation(info)

        async def send_response(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                record()

        try:
            await self.app(scope, receive, send_response)
        finally:
            # 응답을 만들기 전에 발생한 예외와 중단된 스트림도 누락하지 않는다.
            record()


def setup_http_metrics(app) -> None:
    instrumentations = (
        metrics.requests(),
        metrics.latency(
            should_include_status=False,
            buckets=(0.1, 0.5, 1, 2, 3, 5, 10, 20, 30, 60, 120),
        ),
    )
    app.add_middleware(HTTPMetricsMiddleware, instrumentations=instrumentations)
    Instrumentator().expose(app, endpoint="/ai/api/metrics", include_in_schema=False)
