# syntax=docker/dockerfile:1

FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS base

WORKDIR /app

# Install system dependencies (ffmpeg for HDF5 video generation)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv binary from its official image, pinned by digest for OSSF Scorecard.
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

FROM base AS build

COPY pyproject.toml uv.lock ./

# Runtime feature extras, separated by commas or spaces.
ARG BACKEND_EXTRAS="azure,analysis,export,auth,yolo"
# Keep installation caches out of the runtime image.
RUN extras=""; \
    for extra in $(echo "${BACKEND_EXTRAS}" | tr ',' ' '); do extras="${extras} --extra ${extra}"; done; \
    uv sync --frozen --no-dev ${extras}

FROM base AS runtime

RUN useradd --system --no-create-home appuser && chown appuser /app

COPY --from=build --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser pyproject.toml uv.lock logging.json ./
COPY --chown=appuser:appuser src/ ./src/
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
