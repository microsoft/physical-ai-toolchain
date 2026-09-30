#!/usr/bin/env bash
# Install the smoke test's locked Azure ML runtime packages, then run it.
# Azure ML runs this from the job's code root, where training/ links to the snapshot.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SMOKE_DIR}/../.." && pwd)"

# Same uv release as training/rl/scripts/setup_isaac_runtime.sh. The image has no
# curl, so install the PyPI wheel with its hash pinned.
UV_VERSION="0.12.8"
UV_WHEEL_SHA256="9d63d046051d33b36260146df5aef03e9166b30b4546e9b6e554be152b69f9f9"

if ! command -v uv &>/dev/null; then
  echo "Installing uv ${UV_VERSION}..."
  python -m pip install --quiet --no-cache-dir --root-user-action=ignore --require-hashes --only-binary=:all: \
    --requirement /dev/stdin <<<"uv==${UV_VERSION} --hash=sha256:${UV_WHEEL_SHA256}"
fi

requirements="$(mktemp)"
trap 'rm -f "${requirements}"' EXIT

echo "Installing locked runtime packages from ${SMOKE_DIR}..."
uv export --frozen --no-hashes --no-emit-project --project "${SMOKE_DIR}" --output-file "${requirements}" >/dev/null
uv pip install --quiet --no-cache-dir --no-deps --system --python "$(command -v python)" \
  --requirement "${requirements}"
rm -f "${requirements}"
trap - EXIT

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec python -m training.smoke.scripts.azureml_gpu_smoke "$@"
