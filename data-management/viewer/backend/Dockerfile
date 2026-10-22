# syntax=docker/dockerfile:1

FROM python:3.13.16-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base

WORKDIR /app/data-management/viewer/backend

# Install system dependencies (ffmpeg for HDF5 video generation)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv binary from its official image, pinned by digest for OSSF Scorecard.
COPY --from=ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

FROM base AS build

COPY pyproject.toml uv.lock /app/
COPY data-management/viewer/backend/pyproject.toml ./
COPY data-management/viewer/backend/src/ ./src/
COPY evaluation/vlm_judge/ /app/evaluation/vlm_judge/
COPY data-management/curation/ /app/data-management/curation/
COPY data-management/dataset-io/ /app/data-management/dataset-io/

# Runtime feature extras, separated by commas. The yolo extra pulls CUDA torch,
# so Azure Container Apps builds pass a smaller list to fit the Consumption
# profile image limit.
ARG BACKEND_EXTRAS="azure,analysis,export,auth,yolo"
ARG UV_DEFAULT_INDEX="https://pypi.org/simple"
ARG JUDGE_EXTRAS=""
# Keep installation caches out of the runtime image.
RUN set -eu; extras=""; selection="--package lerobot-annotation-api"; \
    for extra in $(echo "${JUDGE_EXTRAS}" | tr ',' ' '); do \
        case "${extra}" in openai|qwen3-vl) ;; *) echo "Unsupported JUDGE_EXTRAS profile" >&2; exit 1 ;; esac; \
        selection="--all-packages"; \
        extras="${extras} --extra ${extra}"; \
    done; \
    for extra in $(echo "${BACKEND_EXTRAS}" | tr ',' ' '); do \
        case "${extra}" in \
            azure|analysis|export|auth|yolo) ;; \
            vlm-judge) echo "Select the judge runtime with JUDGE_EXTRAS" >&2; exit 1 ;; \
            *) echo "Unsupported BACKEND_EXTRAS profile" >&2; exit 1 ;; \
        esac; \
        extras="${extras} --extra ${extra}"; \
    done; \
    uv sync --project /app --frozen --no-default-groups --no-editable ${selection} ${extras}; \
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
