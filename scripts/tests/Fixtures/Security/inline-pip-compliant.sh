#!/usr/bin/env bash
# Fixture: compliant inline installs in a shell script (shell-inline-pip ecosystem).
set -o errexit -o nounset

pip install --quiet --break-system-packages uv==0.7.12
uv pip install -e ".[dev,analysis,export]"
uv export --frozen --no-hashes --no-emit-project | uv pip install --no-deps -r -
uv pip install --no-cache-dir --project "${PROJ}" --requirement "${REQS}"
pip install --upgrade pip setuptools wheel
uv run --with azure-identity==1.25.3 python job.py

# An intentional, exempted range (e.g. an ABI constraint):
pip install "numpy>=1.26.0,<2.0.0"  # pinning-ignore: base-image ABI constraint
