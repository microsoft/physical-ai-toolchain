# syntax=docker/dockerfile:1

FROM python:3.11-slim@sha256:b1b81d67b8df73bf6067191790c233ef9e598b863fc1ccf447956138ca466d99 AS base

WORKDIR /app

# Install system dependencies (ffmpeg for HDF5 video generation)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv==0.10.9

# Copy dependency manifests
COPY pyproject.toml uv.lock ./

# Install production + azure dependencies
# The 'azure' extra includes azure-storage-blob and azure-identity for MSI support.
RUN uv sync --no-dev --extra azure --extra analysis --extra export --extra auth

# Copy application source
COPY src/ ./src/

# Expose the API port (override with BACKEND_PORT env var at runtime)
EXPOSE 8000

# Runtime defaults — override via environment variables in your deployment
# BACKEND_HOST=0.0.0.0 binds to all interfaces (required in containers)
ENV BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8000 \
    HMI_STORAGE_BACKEND=local \
    HMI_DATA_PATH=/data

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${BACKEND_PORT}/health')"

CMD ["sh", "-c", "uv run uvicorn src.api.main:app --host ${BACKEND_HOST} --port ${BACKEND_PORT}"]
