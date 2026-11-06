# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

FROM python:3.11-slim

# System dependencies for video reading, OpenCV, and networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy ODD component modules and configurations
COPY odd_core/ /app/odd_core/
COPY configs/ /app/configs/
COPY run_odd_pipeline.py /app/
COPY entrypoint.sh /app/

RUN chmod +x /app/entrypoint.sh /app/run_odd_pipeline.py

# Create mount points for container volumes and grant broad permissions
RUN mkdir -p /input /output && chmod 777 /input /output

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    INPUT_DIR=/input \
    OUTPUT_DIR=/output \
    VLM_URL=http://host.docker.internal:8010/v1 \
    VLM_MODEL=nvidia/Cosmos-Reason2-2B \
    ODD_SCORE_THRESHOLD=0.8

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--input-dir", "/input", "--output-dir", "/output"]
