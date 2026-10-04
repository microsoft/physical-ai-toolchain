#!/usr/bin/env bash
# Run the Docusaurus production browser tests in a Linux Playwright container on a
# clean git snapshot, and optionally report only failures that are new relative to a
# base ref. Exit codes: 0 passed or parity, 1 new failures, 3 not run.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

readonly EXIT_NEW_FAILURES=1
readonly EXIT_NOT_RUN=3
readonly DOCS_DIR="docs/docusaurus"
readonly REPORT_FILES=(playwright-results.json contrast-ledger.json site-crawl-summary.txt)

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Run the Docusaurus production browser tests (npm run ci:test:e2e) in a linux/amd64
Playwright container on a git-initialized snapshot of a ref. With --compare-base,
fail only for failures that the base ref does not also have.

OPTIONS:
    -r, --ref REF              Ref to test (default: HEAD)
    -b, --compare-base REF     Base ref; run it only when the tested ref fails
    -o, --output-dir DIR       Report directory (default: logs/docs-e2e/<UTC timestamp>)
        --image IMAGE          Playwright image (default: derived from the
                               @playwright/test version in each snapshot)
        --compare-reports HEAD_DIR BASE_DIR
                               Compare two saved report directories without Docker
    -h, --help                 Show this help message
        --config-preview       Print configuration and exit

EXIT CODES:
    0  Passed, or every failure also occurs on the base ref (parity)
    1  New failures relative to the base, or failures without a base
    3  Not run (Docker unavailable or not running)

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --compare-base origin/main
    $(basename "$0") --compare-reports logs/docs-e2e/run/head logs/docs-e2e/run/base
EOF
}

# Print sorted failure fingerprints from a report directory: failing Playwright
# tests (project, file, title, normalized first error line) and non-accepted
# contrast-ledger signatures.
failure_fingerprints() {
  local results="$1/playwright-results.json" ledger="$1/contrast-ledger.json"
  {
    if [[ -f "$results" ]]; then
      jq -r '
        [ .. | objects | select(has("specs")) | .specs[]? as $spec
          | ($spec.tests // [])[] | select(.status == "unexpected")
          | ([.results[]?.error?.message // empty] | last // "") as $message
          | ($message | gsub("\u001b\\[[0-9;]*m"; "") | split("\n")[0] | gsub("[0-9]+"; "N") | .[0:160]) as $error
          | "test|\(.projectName)|\($spec.file)|\($spec.title)|\($error)"
        ] | .[]' "$results"
    else
      echo "report|missing playwright-results.json"
    fi
    if [[ -f "$ledger" ]]; then
      jq -r '[.assessments[]? | select(.status != "accepted") | "contrast|\(.status)|\(.signature)"] | .[]' "$ledger"
    fi
  } | LC_ALL=C sort -u
}

# Compare head and base report directories; print the result and return its exit code.
compare_reports() {
  local head_dir="$1" base_dir="$2" head_failures base_failures new_failures
  [[ -d "$head_dir" ]] || fatal "Head report directory not found: $head_dir"
  [[ -d "$base_dir" ]] || fatal "Base report directory not found: $base_dir"
  head_failures="$(failure_fingerprints "$head_dir")"
  base_failures="$(failure_fingerprints "$base_dir")"
  new_failures="$(LC_ALL=C comm -23 <(printf '%s\n' "$head_failures" | sed '/^$/d') <(printf '%s\n' "$base_failures" | sed '/^$/d'))"

  section "Docs e2e comparison"
  print_kv "Head failures" "$(printf '%s\n' "$head_failures" | sed '/^$/d' | wc -l | tr -d ' ')"
  print_kv "Base failures" "$(printf '%s\n' "$base_failures" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [[ -z "$new_failures" ]]; then
    info "Parity: every head failure also occurs on the base"
    return 0
  fi
  error "New failures relative to the base:"
  printf '  %s\n' "$new_failures" >&2
  return "$EXIT_NEW_FAILURES"
}

# Defaults
ref="HEAD"
compare_base=""
output_dir=""
image=""
compare_head_dir=""
compare_base_dir=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    -r|--ref)           ref="$2"; shift 2 ;;
    -b|--compare-base)  compare_base="$2"; shift 2 ;;
    -o|--output-dir)    output_dir="$2"; shift 2 ;;
    --image)            image="$2"; shift 2 ;;
    --compare-reports)  compare_head_dir="$2"; compare_base_dir="$3"; shift 3 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

if [[ -n "$compare_head_dir" ]]; then
  require_tools jq comm
  compare_reports "$compare_head_dir" "$compare_base_dir"
  exit $?
fi

require_tools git jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

output_dir="${output_dir:-$REPO_ROOT/logs/docs-e2e/$(date -u +%Y%m%dT%H%M%SZ)}"
git -C "$REPO_ROOT" rev-parse --verify --quiet "${ref}^{commit}" >/dev/null || fatal "Unknown ref: $ref"
if [[ -n "$compare_base" ]]; then
  git -C "$REPO_ROOT" rev-parse --verify --quiet "${compare_base}^{commit}" >/dev/null ||
    fatal "Unknown base ref: $compare_base"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Ref" "$ref ($(git -C "$REPO_ROOT" rev-parse --short "$ref"))"
  print_kv "Compare Base" "${compare_base:-none}"
  print_kv "Image" "${image:-derived from @playwright/test in each snapshot}"
  print_kv "Output Directory" "$output_dir"
  exit 0
fi

if ! command -v docker &>/dev/null || ! docker info &>/dev/null; then
  warn "Docker is unavailable or not running; docs e2e not run"
  exit "$EXIT_NOT_RUN"
fi

#------------------------------------------------------------------------------
# Main Logic
#------------------------------------------------------------------------------

snapshot_root="$(mktemp -d "${TMPDIR:-/tmp}/docs-e2e.XXXXXX")"
trap 'rm -rf "$snapshot_root"' EXIT

# Run the docs e2e suite for one ref and copy its reports to $output_dir/<label>.
run_snapshot() {
  local ref_to_test="$1" label="$2" snapshot="$snapshot_root/$2" run_image="$image" status=0
  section "Docs e2e snapshot: $label ($ref_to_test)"
  mkdir -p "$snapshot" "$output_dir/$label"
  # This function runs inside conditionals, where errexit is suspended, so check each step.
  git -C "$REPO_ROOT" archive "$ref_to_test" | tar -x -C "$snapshot" || fatal "Cannot extract a snapshot of $ref_to_test"
  if ! { git -C "$snapshot" init -q && git -C "$snapshot" add -A &&
    git -C "$snapshot" -c user.name=docs-e2e -c user.email=docs-e2e@example.invalid commit -q -m snapshot; }; then
    fatal "Cannot initialize the snapshot repository for $ref_to_test"
  fi
  if [[ -z "$run_image" ]]; then
    local playwright_version
    playwright_version="$(jq -r '.devDependencies["@playwright/test"] // empty' "$snapshot/$DOCS_DIR/package.json")"
    [[ "$playwright_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
      fatal "Cannot derive the Playwright image from @playwright/test '$playwright_version'; pass --image"
    run_image="mcr.microsoft.com/playwright:v${playwright_version}-noble"
  fi
  print_kv "Image" "$run_image"

  docker run --rm --platform linux/amd64 --shm-size=2g \
    -v "$snapshot:/work" -w "/work/$DOCS_DIR" \
    -e CI=1 -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    "$run_image" bash -c '
      status=0
      command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git >/dev/null)
      git config --global --add safe.directory "*"
      npm ci --no-audit --no-fund && npx playwright install --with-deps chrome && npm run ci:test:e2e || status=$?
      chown -R "$HOST_UID:$HOST_GID" /work
      exit "$status"' > "$output_dir/$label/run.log" 2>&1 || status=$?

  local report
  for report in "${REPORT_FILES[@]}"; do
    if [[ -f "$snapshot/$DOCS_DIR/test-results/$report" ]]; then
      cp "$snapshot/$DOCS_DIR/test-results/$report" "$output_dir/$label/"
    fi
  done
  echo "$status" > "$output_dir/$label/exit-code"
  print_kv "Exit Code" "$status"
  return "$status"
}

result=0
if run_snapshot "$ref" head; then
  info "Docs e2e passed on $ref"
elif [[ -z "$compare_base" ]]; then
  error "Docs e2e failed on $ref; see $output_dir/head/run.log"
  result="$EXIT_NEW_FAILURES"
else
  run_snapshot "$compare_base" base || true
  compare_reports "$output_dir/head" "$output_dir/base" || result=$?
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Summary"
print_kv "Ref" "$ref"
print_kv "Compare Base" "${compare_base:-none}"
print_kv "Reports" "$output_dir"
print_kv "Result" "$([[ "$result" -eq 0 ]] && echo 'passed' || echo 'failed')"
exit "$result"
