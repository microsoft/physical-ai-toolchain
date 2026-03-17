#!/usr/bin/env bash
# Deploy Grafana dashboards and alert rules for fleet monitoring
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy Grafana dashboards and alert rules for fleet monitoring.

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

if [[ "$config_preview" == "true" ]]; then
  echo "=== Configuration Preview ==="
  echo "Script Directory: $SCRIPT_DIR"
  echo "Status: Placeholder — awaiting Grafana dashboard integration"
  exit 0
fi

echo "ERROR: Not yet implemented — awaiting Grafana dashboard integration" >&2
exit 1
