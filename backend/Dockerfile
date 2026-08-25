FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
FROM python:3.12.11-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src

FROM python:3.12.11-slim-bookworm
RUN apt-get update \
    && apt-get upgrade --yes \
    && groupadd --system quantlab \
    && useradd --system --gid quantlab --home /app quantlab \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build --chown=quantlab:quantlab /app/backend /app/backend
COPY --chown=quantlab:quantlab alembic.ini /app/alembic.ini
COPY --chown=quantlab:quantlab alembic /app/alembic
ENV PATH=/app/backend/.venv/bin:$PATH PYTHONPATH=/app/backend/src
USER quantlab
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["uvicorn","quantlab.api:app","--host","0.0.0.0","--port","8000","--workers","1","--no-proxy-headers"]
