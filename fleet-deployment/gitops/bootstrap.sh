#!/usr/bin/env bash
# Bootstrap FluxCD on a target Kubernetes cluster for fleet deployment
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Bootstrap FluxCD on a target Kubernetes cluster.

OPTIONS:
    -h, --help               Show this help message
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --config-preview
EOF
}

config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo "================================================================"
echo " Configuration Preview"
echo "================================================================"
echo "  Script Dir: $SCRIPT_DIR"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi

echo "================================================================"
echo " FluxCD Bootstrap"
echo "================================================================"
echo "  FluxCD bootstrap is not yet implemented."
echo "  This script will install Flux components and configure GitOps reconciliation."

echo "================================================================"
echo " Summary"
echo "================================================================"
echo "  Status: placeholder — not yet implemented"
