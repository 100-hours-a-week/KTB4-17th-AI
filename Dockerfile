# syntax=docker/dockerfile:1.7

#멀티 스테이징 환경으로 변경

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /bin/uv

WORKDIR /code

ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev \
        --no-install-project

COPY scripts/download_models.py scripts/download_models.py
RUN python scripts/download_models.py /code/models --demo


FROM python:3.12-slim AS runtime

WORKDIR /code

ENV PYTHONPATH=/code \
    PATH="/code/.venv/bin:$PATH" \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /code/.venv /code/.venv
COPY --from=builder /code/models /code/models

COPY app app
COPY alembic alembic
COPY alembic.ini alembic.ini

EXPOSE 8000

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]