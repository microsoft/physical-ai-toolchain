# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG TARGETARCH
ARG UV_VERSION=0.8.17
ARG ARM64_TORCH_VERSION=2.11.0+cu130
ARG ARM64_TORCHVISION_VERSION=0.26.0+cu130
ARG ARM64_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu130

ENV DEBIAN_FRONTEND=noninteractive \
    HF_HOME=/home/lerobot/.cache/huggingface \
    HF_LEROBOT_HOME=/home/lerobot/.cache/huggingface/lerobot \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    NVIDIA_VISIBLE_DEVICES=all \
    NO_LSOF_CHECK=1 \
    PATH=/opt/lerobot/.venv/bin:/opt/xavier/lerobot/scripts:${PATH} \
    PYTHONUNBUFFERED=1 \
    TORCH_HOME=/home/lerobot/.cache/torch \
    TRITON_CACHE_DIR=/home/lerobot/.cache/triton

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      cmake \
      ffmpeg \
      git \
      libegl1 \
      libgeos-dev \
      libglib2.0-0 \
      libgl1 \
      libusb-1.0-0-dev \
      lsof \
      ninja-build \
      pkg-config \
      portaudio19-dev \
      speech-dispatcher \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && useradd --create-home --shell /bin/bash lerobot

WORKDIR /opt/lerobot

COPY --chown=lerobot:lerobot upstream/setup.py upstream/pyproject.toml \
  upstream/uv.lock upstream/README.md upstream/MANIFEST.in ./

RUN chown lerobot:lerobot /opt/lerobot

USER lerobot

RUN uv sync --locked \
      --extra core_scripts \
      --extra feetech \
      --extra training \
      --extra evaluation \
      --extra smolvla \
      --extra pusht \
      --no-install-project \
      --no-cache

COPY --chown=lerobot:lerobot upstream/ ./

RUN uv sync --locked \
      --extra core_scripts \
      --extra feetech \
      --extra training \
      --extra evaluation \
      --extra smolvla \
      --extra pusht \
      --no-cache

USER root

COPY --from=runtime / /opt/xavier/offload/runtime/
COPY docker/sitecustomize.py /opt/xavier/sitecustomize.py
COPY docker/raw_observation_inference.py /opt/xavier/lerobot/diagnostics/raw_observation_inference.py
COPY docker/rollout_timing.py /opt/xavier/lerobot/diagnostics/rollout_timing.py
COPY --chown=lerobot:lerobot scripts/ /opt/xavier/lerobot/scripts/
COPY docker/entrypoint.sh /usr/local/bin/lerobot-entrypoint

RUN uv pip install \
      --python /opt/lerobot/.venv/bin/python \
      /opt/xavier/offload/runtime \
    && if [ "${TARGETARCH}" = "arm64" ]; then \
      uv pip install \
        --python /opt/lerobot/.venv/bin/python \
        --index-url "${ARM64_TORCH_INDEX_URL}" \
        "torch==${ARM64_TORCH_VERSION}" \
        "torchvision==${ARM64_TORCHVISION_VERSION}"; \
    fi \
    && cp /opt/xavier/sitecustomize.py \
      "$(/opt/lerobot/.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')/sitecustomize.py" \
    && chmod +x /usr/local/bin/lerobot-entrypoint \
      /opt/xavier/lerobot/scripts/*.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/lerobot-entrypoint"]
CMD ["bash"]
