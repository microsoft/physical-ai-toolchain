# syntax=docker/dockerfile:1
ARG BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel@sha256:0cf3402e946b7c384ba943ee05c90b4c5a4a05227923921f2b0918c011cfaf56
FROM ${BASE_IMAGE}

ARG ISAAC_GROOT_REF=23ace64f17aa5015259b8609d371eb61a357c776
ARG UV_VERSION=0.8.14

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    GROOT_PREBUILT_ROOT=/opt/Isaac-GR00T \
    UV_LINK_MODE=copy

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      build-essential \
      cmake \
      curl \
      ffmpeg \
      git \
      git-lfs \
      libgl1-mesa-glx \
      libglib2.0-0 \
      libsm6 \
      libvulkan-dev \
      libxext6 \
      libxrender-dev \
      python3.10-dev \
      wget \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir "uv==${UV_VERSION}"

RUN mkdir -p "${GROOT_PREBUILT_ROOT}" \
    && cd "${GROOT_PREBUILT_ROOT}" \
    && git init \
    && git remote add origin https://github.com/NVIDIA/Isaac-GR00T.git \
    && git fetch --depth 1 origin "${ISAAC_GROOT_REF}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${ISAAC_GROOT_REF}" \
    && git lfs install --system \
    && UV_PREVIEW=1 UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4 \
      uv sync --frozen --no-install-project --no-dev --no-cache \
    && uv pip install --python "${GROOT_PREBUILT_ROOT}/.venv/bin/python" \
      -e . --no-deps

COPY write-image-metadata.py /tmp/write-image-metadata.py
RUN "${GROOT_PREBUILT_ROOT}/.venv/bin/python" /tmp/write-image-metadata.py \
      --isaac-groot-ref "${ISAAC_GROOT_REF}" \
      --uv-version "${UV_VERSION}" \
      --output /opt/groot-n17-image.json \
    && rm /tmp/write-image-metadata.py

ENV VIRTUAL_ENV=/opt/Isaac-GR00T/.venv
ENV PATH=/opt/Isaac-GR00T/.venv/bin:${PATH}

LABEL org.opencontainers.image.title="GR00T N1.7 Azure ML training runtime" \
      org.opencontainers.image.description="Dependency-only runtime; model weights and credentials are mounted at job execution" \
      org.opencontainers.image.source="https://github.com/NVIDIA/Isaac-GR00T" \
      org.opencontainers.image.revision="${ISAAC_GROOT_REF}"
