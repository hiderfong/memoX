# syntax=docker/dockerfile:1

FROM node:20-bookworm-slim AS frontend-build
WORKDIR /app/frontend_wip
COPY frontend_wip/package*.json ./
RUN npm ci
COPY frontend_wip/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    MEMOX_CONFIG_PATH=/app/config.yaml \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src/ ./src/
COPY config.example.yaml ./config.yaml
COPY --from=frontend-build /app/frontend_wip/dist ./frontend_wip/dist

RUN uv sync --frozen --no-dev \
    && mkdir -p /app/data /app/workspace /app/backups

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health >/dev/null || exit 1

CMD ["memox"]
