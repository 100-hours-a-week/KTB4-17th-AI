FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /bin/

WORKDIR /code
ENV PYTHONPATH=/code \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/matplotlib

# opencv-contrib-python 이 libGL·glib 을 동적 로드한다
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# 얼굴 검출·비교·합성 판별 모델. 코드보다 덜 바뀌므로 앞 레이어에 두고 SHA-256 으로 검증한다
COPY ./scripts/download_models.py ./scripts/download_models.py
RUN python scripts/download_models.py /code/models

COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini ./alembic.ini

EXPOSE 8000

ENTRYPOINT ["/code/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]
