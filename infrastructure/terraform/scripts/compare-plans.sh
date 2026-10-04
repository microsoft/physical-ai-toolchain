#!/usr/bin/env bash
# Compare a Terraform stack's plan at a base ref with its plan at a head ref against the
# locally deployed state, read-only. Never applies and never writes the stack's state,
# lock file, or .terraform directory. Exit codes: 0 no change beyond the base, 1 the
# change sets differ or the comparison failed, 3 not run (state, tfvars, or tools missing).
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

readonly EXIT_DIFFERENT=1
readonly EXIT_NOT_RUN=3

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Plan a Terraform stack at two refs against the same deployed state and compare
the planned change sets. Each ref is extracted with git archive into a private
temporary directory and initialized there with -backend=false; plans use
-lock=false and read the stack's state and tfvars in place. Output lists
resource addresses, actions, and changed attribute names only, never values.

OPTIONS:
    -b, --base REF           Base ref (default: origin/main)
        --head REF           Head ref (default: HEAD)
    -s, --stack NAME         Stack: root, vpn, automation, or dns (default: root)
        --state PATH         State file (default: <stack>/terraform.tfstate)
        --var-file PATH      Variables file (default: <stack>/terraform.tfvars)
    -o, --output-dir DIR     Summary directory (default: logs/terraform-plans/<UTC timestamp>)
    -h, --help               Show this help message
        --config-preview     Print configuration and exit

EXIT CODES:
    0  The head plan changes nothing beyond the base plan
    1  The change sets differ, or a plan failed
    3  Not run: state, tfvars, terraform, or jq is missing

Plans contact Azure: sign in with az login and connect to the network or VPN that
reaches private resources. Terraform prints a deprecation warning for -state; it
is expected.

EXAMPLES:
    $(basename "$0") --stack root
    $(basename "$0") --base origin/main --head HEAD --stack vpn
EOF
}

not_run() {
  warn "$1; comparison not run"
  exit "$EXIT_NOT_RUN"
}

# Summarize a plan JSON as sorted change, drift, and output lines without values.
summarize_plan() {
  jq '
    def changed_keys($change):
      if ($change.before | type) == "object" and ($change.after | type) == "object" then
        [($change.before + $change.after) | keys[] as $key
          | select($change.before[$key] != $change.after[$key]) | $key] | unique | join(",")
      else "" end;
    def describe: "\(.address) \(.change.actions | join("/")) [\(changed_keys(.change))]";
    {
      changes: ([.resource_changes[]? | select(.change.actions != ["no-op"]) | describe] | sort),
      drift: ([.resource_drift[]? | describe] | sort),
      outputs: ([(.output_changes // {}) | to_entries[] | select(.value.actions != ["no-op"])
        | "\(.key) \(.value.actions | join("/"))"] | sort)
    }'
}

# Defaults
base_ref="origin/main"
head_ref="HEAD"
stack="root"
state_file=""
var_file=""
output_dir=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    -b|--base)         base_ref="$2"; shift 2 ;;
    --head)            head_ref="$2"; shift 2 ;;
    -s|--stack)        stack="$2"; shift 2 ;;
    --state)           state_file="$2"; shift 2 ;;
    --var-file)        var_file="$2"; shift 2 ;;
    -o|--output-dir)   output_dir="$2"; shift 2 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

case "$stack" in
  root)                   stack_path="infrastructure/terraform" ;;
  vpn | automation | dns) stack_path="infrastructure/terraform/$stack" ;;
  *)                      fatal "Unknown stack: $stack (expected root, vpn, automation, or dns)" ;;
esac

for ref in "$base_ref" "$head_ref"; do
  git -C "$REPO_ROOT" rev-parse --verify --quiet "${ref}^{commit}" >/dev/null || fatal "Unknown ref: $ref"
done

stack_dir="$REPO_ROOT/$stack_path"
state_file="${state_file:-$stack_dir/terraform.tfstate}"
var_file="${var_file:-$stack_dir/terraform.tfvars}"
output_dir="${output_dir:-$REPO_ROOT/logs/terraform-plans/$(date -u +%Y%m%dT%H%M%SZ)}/$stack"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Stack" "$stack ($stack_path)"
  print_kv "Base" "$base_ref ($(git -C "$REPO_ROOT" rev-parse --short "$base_ref"))"
  print_kv "Head" "$head_ref ($(git -C "$REPO_ROOT" rev-parse --short "$head_ref"))"
  print_kv "State" "$([[ -f "$state_file" ]] && echo present || echo missing)"
  print_kv "Variables" "$([[ -f "$var_file" ]] && echo present || echo missing)"
  print_kv "Output Directory" "$output_dir"
  exit 0
fi

command -v terraform &>/dev/null || not_run "terraform is not installed"
command -v jq &>/dev/null || not_run "jq is not installed"
[[ -f "$state_file" ]] || not_run "No state file for the $stack stack"
[[ -f "$var_file" ]] || not_run "No variables file for the $stack stack"
state_file="$(cd "$(dirname "$state_file")" && pwd)/$(basename "$state_file")"
var_file="$(cd "$(dirname "$var_file")" && pwd)/$(basename "$var_file")"

#------------------------------------------------------------------------------
# Main Logic
#------------------------------------------------------------------------------

umask 077
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/compare-plans.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
mkdir -p "$output_dir" "$work_dir/plugin-cache"
export TF_PLUGIN_CACHE_DIR="$work_dir/plugin-cache" TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1

# Plan one ref inside its own archived copy and write a value-free summary.
plan_ref() {
  local ref="$1" label="$2" copy="$work_dir/$2" plan_file="$work_dir/$2.tfplan" status=0
  section "Plan: $label ($ref)"
  mkdir -p "$copy"
  git -C "$REPO_ROOT" archive "$ref" infrastructure/terraform | tar -x -C "$copy" ||
    fatal "Cannot extract infrastructure/terraform at $ref"
  local dir="$copy/$stack_path"

  if ! terraform -chdir="$dir" init -backend=false -input=false -no-color > "$work_dir/$label.init.log" 2>&1; then
    tail -n 20 "$work_dir/$label.init.log" >&2
    fatal "terraform init failed for $label"
  fi
  terraform -chdir="$dir" plan -lock=false -input=false -no-color -detailed-exitcode \
    -state="$state_file" -var-file="$var_file" -out="$plan_file" > "$work_dir/$label.plan.log" 2>&1 || status=$?
  if [[ "$status" -ne 0 && "$status" -ne 2 ]]; then
    tail -n 20 "$work_dir/$label.plan.log" >&2
    fatal "terraform plan failed for $label (exit $status)"
  fi
  print_kv "Plan" "$(grep -m1 -E '^(Plan:|No changes\.)' "$work_dir/$label.plan.log" || echo "exit $status")"

  terraform -chdir="$dir" show -json "$plan_file" | summarize_plan > "$output_dir/$label-summary.json"
  rm -f "$plan_file"
}

plan_ref "$base_ref" base
plan_ref "$head_ref" head

base_summary="$output_dir/base-summary.json"
head_summary="$output_dir/head-summary.json"
result=0
section "Comparison"
for field in changes outputs; do
  if ! diff -u --label "base $field" --label "head $field" \
    <(jq -r ".${field}[]" "$base_summary") <(jq -r ".${field}[]" "$head_summary"); then
    result="$EXIT_DIFFERENT"
  fi
done
if ! diff -q <(jq -r '.drift[]' "$base_summary") <(jq -r '.drift[]' "$head_summary") >/dev/null; then
  warn "Detected drift differs between the base and head plans; review $output_dir"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Summary"
print_kv "Stack" "$stack"
print_kv "Base Changes" "$(jq '.changes | length' "$base_summary")"
print_kv "Head Changes" "$(jq '.changes | length' "$head_summary")"
print_kv "Summaries" "$output_dir"
print_kv "Result" "$([[ "$result" -eq 0 ]] && echo 'no change beyond base' || echo 'change sets differ')"
exit "$result"
