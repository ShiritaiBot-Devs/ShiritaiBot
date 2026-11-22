FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.13-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git fonts-noto-cjk
WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/data/.uv-cache \
    PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock .python-version LICENSE README.md ./
COPY src/ ./src/
RUN uv --no-cache sync --locked --no-dev
COPY --from=web /build/web/dist ./web/dist
COPY scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
CMD ["sh", "/app/docker-entrypoint.sh"]
