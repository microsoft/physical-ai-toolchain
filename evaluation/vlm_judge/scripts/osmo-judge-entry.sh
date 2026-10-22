#!/bin/bash
# OSMO entry for the VLM judge. Base64-injected into the workflow by submit-osmo-judge.sh and decoded by the
# task bootstrap. Installs the judge's locked runtime, judges a LeRobot dataset or a directory of rollout videos,
# checks every verdict, and writes the JSONL to the task output.
set -euo pipefail

: "${JUDGE_INPUT_DIR:?JUDGE_INPUT_DIR is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
JUDGE_BACKEND="${JUDGE_BACKEND:-qwen3-vl}"
JUDGE_MODEL_ID="${JUDGE_MODEL_ID:-Qwen/Qwen3-VL-4B-Instruct}"
JUDGE_N_FRAMES="${JUDGE_N_FRAMES:-12}"
PAYLOAD_ROOT="${PAYLOAD_ROOT:-/workspace/judge_payload}"
# The workflow carries the instruction base64-encoded so quotes and backslashes survive its template.
JUDGE_INSTRUCTION="$(printf '%s' "${JUDGE_INSTRUCTION_B64:-}" | base64 -d)"

echo "=== VLM Judge ==="
echo "Input: ${JUDGE_INPUT_DIR}"
echo "Backend: ${JUDGE_BACKEND} (${JUDGE_MODEL_ID})"
echo "Frames per episode: ${JUDGE_N_FRAMES}"

# A LeRobot dataset brings its own episodes and instructions; anything else is a directory of rollout videos,
# which carry no task. Decide before the runtime install so a missing instruction fails fast.
if [[ -f "${JUDGE_INPUT_DIR}/meta/info.json" ]]; then
  input_kind=lerobot
elif [[ -n "${JUDGE_INSTRUCTION:-}" ]]; then
  input_kind=rollouts
else
  echo "ERROR: ${JUDGE_INPUT_DIR} is not a LeRobot dataset, and rollout videos need an instruction to judge against" >&2
  exit 1
fi

case "${JUDGE_BACKEND}" in
  qwen3-vl) judge_extras=(--extra qwen3-vl) ;;
  openai-compat) judge_extras=(--extra openai) ;;
  echo) judge_extras=() ;;
  *) echo "ERROR: unknown JUDGE_BACKEND ${JUDGE_BACKEND} (use qwen3-vl, openai-compat, or echo)" >&2; exit 1 ;;
esac

UV_VERSION=0.12.20
if ! command -v uv >/dev/null 2>&1 || [[ "$(uv --version)" != "uv ${UV_VERSION}" ]]; then
  case "$(uname -m)" in
    x86_64) uv_target=x86_64-unknown-linux-gnu; uv_sha256=6590717592ace991ff83a63fef799e3ad9d33ecc8f96c5d6bdd732496e79337f ;;
    aarch64|arm64) uv_target=aarch64-unknown-linux-gnu; uv_sha256=8a7aad7bc76a2fae5151566ff3e43eacce0b2a113d5e4de3e4afe3e58fa2441e ;;
    *) echo "ERROR: unsupported uv bootstrap architecture" >&2; exit 1 ;;
  esac
  uv_install="$(mktemp -d)"
  trap 'rm -rf -- "${uv_install}"' EXIT
  curl --fail --silent --show-error --location \
    "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${uv_target}.tar.gz" -o "${uv_install}/uv.tar.gz"
  printf '%s  %s\n' "${uv_sha256}" "${uv_install}/uv.tar.gz" | sha256sum -c -
  tar -xzf "${uv_install}/uv.tar.gz" -C "${uv_install}"
  export PATH="${uv_install}/uv-${uv_target}:${PATH}"
fi
[[ "$(uv --version)" == "uv ${UV_VERSION}" ]] || { echo "ERROR: incorrect uv bootstrap version" >&2; exit 1; }
uv python install 3.12
uv venv --python 3.12 /opt/judge-venv
# shellcheck disable=SC1091
source /opt/judge-venv/bin/activate

# The code archive arrives through the OSMO url: input; the standard library unpacks it.
mkdir -p "${PAYLOAD_ROOT}"
ARCHIVE_PATH="$(find "${OSMO_INPUT_0}" -maxdepth 2 -name '*.zip' | head -n1)"
if [[ -z "${ARCHIVE_PATH}" || ! -s "${ARCHIVE_PATH}" ]]; then
  echo "ERROR: no code archive found under the input mount; runtime payload is required." >&2
  exit 1
fi
python3 -m zipfile -e "${ARCHIVE_PATH}" "${PAYLOAD_ROOT}"

if [[ ! -f "${PAYLOAD_ROOT}/uv.lock" ]]; then
  echo "ERROR: root lockfile not found at ${PAYLOAD_ROOT}/uv.lock" >&2
  exit 1
fi
uv sync --active --frozen --no-default-groups --no-editable \
  --project "${PAYLOAD_ROOT}" --package physical-ai-vlm-judge "${judge_extras[@]}"
uv pip check

mkdir -p "${OUTPUT_DIR}"
JUDGMENTS="${OUTPUT_DIR}/judgments.jsonl"
# The judge writes under a partial name; only a complete, checked batch is promoted to judgments.jsonl, so a
# failed, interrupted, or killed run never leaves a file that looks complete.
PARTIAL_JUDGMENTS="${OUTPUT_DIR}/judgments.partial.jsonl"
judge_args=(
  --output "${PARTIAL_JUDGMENTS}"
  --backend "${JUDGE_BACKEND}"
  --model-id "${JUDGE_MODEL_ID}"
  --n-frames "${JUDGE_N_FRAMES}"
  --cache-dir ""
)
[[ -n "${JUDGE_MODEL_REVISION:-}" ]] && judge_args+=(--model-revision "${JUDGE_MODEL_REVISION}")
[[ -n "${JUDGE_BASE_URL:-}" ]] && judge_args+=(--base-url "${JUDGE_BASE_URL}")
[[ -n "${JUDGE_LIMIT:-}" ]] && judge_args+=(--limit "${JUDGE_LIMIT}")

judge_status=0
if [[ "${input_kind}" == "lerobot" ]]; then
  echo "Judging LeRobot episodes in ${JUDGE_INPUT_DIR}"
  # The attached form keeps argparse from reading an instruction that starts with "-" as another option.
  [[ -n "${JUDGE_INSTRUCTION:-}" ]] && judge_args+=("--instruction=${JUDGE_INSTRUCTION}")
  [[ -n "${JUDGE_INDICES:-}" ]] && judge_args+=(--indices "${JUDGE_INDICES}")
  python -m evaluation.vlm_judge.run --dataset "${JUDGE_INPUT_DIR}" "${judge_args[@]}" || judge_status=$?
else
  echo "Judging rollout videos under ${JUDGE_INPUT_DIR}"
  # --require-all fails the batch when any selected rollout could not be judged, as the dataset judge does.
  python -m evaluation.vlm_judge.policy_eval --rollout-root "${JUDGE_INPUT_DIR}" \
    "--instruction=${JUDGE_INSTRUCTION}" "${judge_args[@]}" --require-all || judge_status=$?
fi

# Every verdict must carry an outcome (true, false, or null for an inconclusive tie), a confidence, and per-frame
# progress. The check also runs after a judge failure, so the log reports whatever verdicts were written.
check_status=0
python - "${PARTIAL_JUDGMENTS}" << 'CHECK_VERDICTS' || check_status=$?
import json
import math
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
records = [json.loads(line) for line in lines if line.strip()]
if not records:
    sys.exit("ERROR: the judge wrote no verdicts")
for record in records:
    outcome = record.get("outcome_success")
    confidence = record.get("outcome_confidence")
    progress = record.get("progress_per_frame")
    if (
        "outcome_success" not in record
        or not (outcome is None or isinstance(outcome, bool))
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not isinstance(progress, list)
        or not progress
    ):
        sys.exit(f"ERROR: incomplete verdict for {record.get('episode_id')}: {record}")
successes = sum(record["outcome_success"] is True for record in records)
inconclusive = sum(record["outcome_success"] is None for record in records)
mean_confidence = sum(record["outcome_confidence"] for record in records) / len(records)
print(
    f"JUDGE_RESULT=episodes={len(records)} successes={successes} mean_confidence={mean_confidence:.3f} "
    f"inconclusive={inconclusive}"
)
for record in records:
    outcome = "inconclusive" if record["outcome_success"] is None else record["outcome_success"]
    print(f"  {record['episode_id']}: success={outcome} confidence={record['outcome_confidence']:.3f}")
CHECK_VERDICTS

if (( judge_status != 0 )); then
  if [[ -f "${PARTIAL_JUDGMENTS}" ]]; then
    echo "ERROR: the judge could not judge every selected input (exit ${judge_status}); the verdicts it wrote are in judgments.partial.jsonl" >&2
  else
    echo "ERROR: the judge could not judge every selected input (exit ${judge_status}) and wrote no verdicts" >&2
  fi
  exit 1
fi
if (( check_status != 0 )); then
  echo "ERROR: the judge's verdicts failed their check; they stay in judgments.partial.jsonl" >&2
  exit "${check_status}"
fi
mv "${PARTIAL_JUDGMENTS}" "${JUDGMENTS}"
echo "=== VLM Judge Complete: ${JUDGMENTS} ==="
