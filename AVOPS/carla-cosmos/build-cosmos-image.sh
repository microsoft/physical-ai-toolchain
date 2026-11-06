#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COSMOS_REPO_URL="https://github.com/nvidia-cosmos/cosmos-transfer2.5.git"
COSMOS_REF="${COSMOS_REF:-2ff49d0717af02057ae79bc75c00fbff9da1b4e7}"
COSMOS_IMAGE="${COSMOS_IMAGE:-cosmos-transfer2.5:local}"

for command_name in docker git git-lfs; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        exit 1
    fi
done

SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cosmos-transfer2.5.XXXXXX")"
cleanup() {
    rm -rf -- "$SOURCE_DIR"
}
trap cleanup EXIT

printf 'Cloning NVIDIA Cosmos Transfer 2.5 (%s)...\n' "$COSMOS_REF"
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --branch "$COSMOS_REF" "$COSMOS_REPO_URL" "$SOURCE_DIR"
git -C "$SOURCE_DIR" lfs install
git -C "$SOURCE_DIR" lfs pull

# Upstream pins Python 3.13, but its locked flash-attn wheels exist only for 3.10 (cp310), so the
# upstream Docker build fails with the pin. Override with COSMOS_PYTHON_VERSION if upstream changes.
printf '%s\n' "${COSMOS_PYTHON_VERSION:-3.10}" > "$SOURCE_DIR/.python-version"

COSMOS_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
printf 'Building upstream Cosmos image from commit %s...\n' "$COSMOS_COMMIT"
docker build \
    --ulimit nofile=131071:131071 \
    --build-arg STANDALONE=true \
    --label "org.opencontainers.image.source=$COSMOS_REPO_URL" \
    --label "org.opencontainers.image.revision=$COSMOS_COMMIT" \
    --tag "$COSMOS_IMAGE" \
    "$SOURCE_DIR"

printf 'Building CARLA pipeline image...\n'
COSMOS_IMAGE="$COSMOS_IMAGE" docker compose \
    --project-directory "$SCRIPT_DIR" \
    -f "$SCRIPT_DIR/compose.yaml" \
    build carla pipeline

printf 'Built %s, carla-cosmos-simulator:0.9.15, and carla-cosmos-pipeline:local\n' "$COSMOS_IMAGE"
