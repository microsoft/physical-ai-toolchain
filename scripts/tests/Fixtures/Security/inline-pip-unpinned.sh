#!/usr/bin/env bash
# Fixture: non-compliant inline installs in a shell script (shell-inline-pip ecosystem).
# Expected violations (3): requests, mlflow, torch (via uv run --with).
set -o errexit -o nounset

pip install --quiet requests
uv pip install "mlflow>=2.8.0,<3.0.0" --system
uv run --with torch python train.py
