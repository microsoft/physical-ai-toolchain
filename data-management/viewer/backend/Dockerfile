# syntax=docker/dockerfile:1

FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS base

WORKDIR /app/data-management/viewer/backend

# Install system dependencies (ffmpeg for HDF5 video generation)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv binary from its official image, pinned by digest for OSSF Scorecard.
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

FROM base AS build

COPY data-management/viewer/backend/pyproject.toml data-management/viewer/backend/uv.lock ./
COPY data-management/viewer/backend/src/ ./src/
COPY evaluation/vlm_judge/ /app/evaluation/vlm_judge/

# Runtime feature extras, separated by commas or spaces.
ARG BACKEND_EXTRAS="azure,analysis,export,auth,yolo"
ARG JUDGE_EXTRAS=""
# Keep installation caches out of the runtime image.
RUN set -eu; extras=""; \
    for extra in $(echo "${JUDGE_EXTRAS}" | tr ',' ' '); do \
        case "${extra}" in openai|qwen3-vl) ;; *) echo "Unsupported JUDGE_EXTRAS profile" >&2; exit 1 ;; esac; \
        extras="${extras} --extra ${extra}"; \
    done; \
    uv sync --project /app/evaluation/vlm_judge --frozen --no-dev --no-editable ${extras}; \
    extras=""; \
    for extra in $(echo "${BACKEND_EXTRAS}" | tr ',' ' '); do \
        case "${extra}" in vlm-judge) echo "Select the judge runtime with JUDGE_EXTRAS" >&2; exit 1 ;; esac; \
        extras="${extras} --extra ${extra}"; \
    done; \
    uv sync --frozen --no-dev --no-editable --inexact ${extras}; \
    uv pip check --python /app/.venv/bin/python

FROM base AS runtime

RUN useradd --system --no-create-home appuser \
    && mkdir -p /state/judge /scratch \
    && chown appuser /app /state /state/judge /scratch

COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser data-management/viewer/backend/logging.json ./
USER appuser

# Expose the API port (override with BACKEND_PORT env var at runtime)
EXPOSE 8000

# Runtime defaults — override via environment variables in your deployment
# BACKEND_HOST=0.0.0.0 binds to all interfaces (required in containers)
ENV BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8000 \
    LOG_LEVEL=info \
    STORAGE_BACKEND=local \
    DATA_DIR=/data \
    DETECTION_MODELS_DIR=/models

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${BACKEND_PORT}/health')"

CMD ["sh", "-c", "uvicorn src.api.main:app --log-config logging.json --log-level ${LOG_LEVEL} --host ${BACKEND_HOST} --port ${BACKEND_PORT}"]
