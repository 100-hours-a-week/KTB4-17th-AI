FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /bin/

WORKDIR /code
ENV PYTHONPATH=/code

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY ./app ./app

EXPOSE 8000

ENTRYPOINT ["/code/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]