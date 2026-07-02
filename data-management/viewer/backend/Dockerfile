# syntax=docker/dockerfile:1

FROM python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280 AS base

WORKDIR /app

# Install system dependencies (ffmpeg for HDF5 video generation)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv binary from its official image, pinned by digest for OSSF Scorecard.
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

# Copy dependency manifests
COPY pyproject.toml uv.lock ./

# Install production + azure dependencies into /app/.venv.
# The 'azure' extra includes azure-storage-blob and azure-identity for MSI support.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH
RUN uv sync --frozen --no-dev --extra azure --extra analysis --extra export --extra auth

# Copy application source
COPY src/ ./src/

# Run as an unprivileged user
RUN useradd --system --no-create-home appuser && chown -R appuser /app
USER appuser

# Expose the API port (override with BACKEND_PORT env var at runtime)
EXPOSE 8000

# Runtime defaults — override via environment variables in your deployment
# BACKEND_HOST=0.0.0.0 binds to all interfaces (required in containers)
ENV BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8000 \
    STORAGE_BACKEND=local \
    DATA_DIR=/data

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${BACKEND_PORT}/health')"

CMD ["sh", "-c", "uvicorn src.api.main:app --host ${BACKEND_HOST} --port ${BACKEND_PORT}"]
