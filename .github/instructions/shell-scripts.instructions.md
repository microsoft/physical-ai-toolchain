---
description: Required instructions for shell script implementation in physical-ai-toolchain
applyTo: "**/*.sh"
---

# Shell Scripts Instructions

<!-- <important-script-template> -->

## Script Template

```bash
#!/usr/bin/env bash
# Brief description of what the script does
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../shared/lib/common.sh
source "$REPO_ROOT/shared/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Description of script purpose.

OPTIONS:
    -h, --help               Show this help message
    -t, --tf-dir DIR         Terraform directory (default: $DEFAULT_TF_DIR)
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --tf-dir ../001-iac
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    -t|--tf-dir)       tf_dir="$2"; shift 2 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform kubectl

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

tf_output=$(read_terraform_outputs "$tf_dir")
cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  exit 0
fi

#------------------------------------------------------------------------------
# Main Logic
#------------------------------------------------------------------------------
section "Main Section"

# Implementation here

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Cluster" "$cluster"
info "Operation complete"
```

<!-- </important-script-template> -->

## Section Order

1. Shebang + description + `set -o errexit -o nounset`
2. `SCRIPT_DIR` + `REPO_ROOT` resolution and library sourcing
3. `show_help()` function
4. Default variables
5. Argument parsing (`while [[ $# -gt 0 ]]`)
6. `require_tools` validation
7. Gather Configuration
8. Config preview check
9. Main logic sections (with comment blocks)
10. Summary section

## Conventions

<!-- <important-conventions> -->

**Arguments:**

- Short: `-h`, `-t` | Long: `--help`, `--tf-dir`
- Value options: `shift 2` | Flags: `shift`
- Unknown options: `fatal "Unknown option: $1"`

**Repository Root:**

- Always derive `REPO_ROOT` with git fallback: `REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"`
- Adjust the `cd` traversal depth to match the script's location relative to the repo root
- Source all shared libraries via `$REPO_ROOT/shared/lib/` — never via symlinks or `$SCRIPT_DIR/lib/`

**Variables:**

- Always quote: `"$var"`, `"${array[@]}"`
- Defaults: `var="${ENV_VAR:-default}"`
- Booleans: `true`/`false` strings, test with `[[ "$var" == "true" ]]`

**Output:**

- Progress: `info "message"`
- Warnings: `warn "message"`
- Fatal errors: `fatal "message"`
- Sections: `section "Title"`
- Summaries: `print_kv "Key" "$value"`

**Idempotent operations:**

```bash
kubectl create ... --dry-run=client -o yaml | kubectl apply -f -
helm repo add name url 2>/dev/null || true
```

**Conditional output:**

```bash
print_kv "Status" "$([[ $skip == true ]] && echo 'Skipped' || echo "$version")"
```

**Array building:**

```bash
args=(--version "$ver" --namespace "$ns")
[[ -n "$extra" ]] && args+=(--set "$extra")
command "${args[@]}"
```

<!-- </important-conventions> -->

## Library Functions (`shared/lib/common.sh`)

| Function                           | Purpose                       |
|------------------------------------|-------------------------------|
| `info`, `warn`, `error`, `fatal`   | Colored logging               |
| `require_tools tool1 tool2`        | Validate CLI tools exist      |
| `read_terraform_outputs "$dir"`    | Read terraform JSON           |
| `tf_get "$json" "path" "default"`  | Extract optional value        |
| `tf_require "$json" "path" "desc"` | Extract required value        |
| `connect_aks "$rg" "$cluster"`     | Get AKS credentials           |
| `ensure_namespace "$ns"`           | Create namespace idempotently |
| `section "Title"`                  | Print section header          |
| `print_kv "Key" "$val"`            | Print key-value pair          |
