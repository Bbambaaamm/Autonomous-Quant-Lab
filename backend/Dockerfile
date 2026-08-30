FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
FROM python:3.12.14-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
COPY --chmod=0555 backend/bin/quantlab-worker ./.venv/bin/quantlab-worker
COPY --chmod=0555 backend/bin/quantlab-alpaca-events ./.venv/bin/quantlab-alpaca-events
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* \
    /usr/local/lib/python3.12/ensurepip \
    /usr/local/lib/python3.12/lib-dynload/_curses*.so \
    /usr/local/lib/python3.12/lib-dynload/readline*.so \
    /usr/local/bin/pip* \
    /usr/local/bin/uv \
    && mkdir /runtime-libs \
    && find /usr/local /app/backend/.venv -type f -exec ldd {} \; 2>/dev/null \
      | awk '{ for (i = 1; i <= NF; i++) if ($i ~ "^/") print $i }' \
      | sort -u > /tmp/runtime-libs \
    && test -s /tmp/runtime-libs \
    && while IFS= read -r library; do \
      case "$library" in \
        /lib/*|/lib64/*) destination="/runtime-libs/usr$library" ;; \
        *) destination="/runtime-libs$library" ;; \
      esac; \
      mkdir -p "$(dirname "$destination")"; \
      cp -L "$library" "$destination"; \
    done < /tmp/runtime-libs

FROM gcr.io/distroless/static-debian13:nonroot
WORKDIR /app
COPY --from=build /runtime-libs/usr/ /usr/
COPY --from=build /usr/local /usr/local
COPY --from=build --chown=65532:65532 /app/backend /app/backend
COPY --chown=65532:65532 alembic.ini /app/alembic.ini
COPY --chown=65532:65532 alembic /app/alembic
ENV PATH=/app/backend/.venv/bin:$PATH PYTHONPATH=/app/backend/src
USER 65532:65532
EXPOSE 8000
HEALTHCHECK CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
ENTRYPOINT []
CMD ["/app/backend/.venv/bin/python","-m","uvicorn","quantlab.api:app","--host","0.0.0.0","--port","8000","--workers","1","--no-proxy-headers"]
