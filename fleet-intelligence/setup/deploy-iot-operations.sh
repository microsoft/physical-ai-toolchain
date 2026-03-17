#!/usr/bin/env bash
# Deploy Azure IoT Operations components for fleet telemetry collection
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy Azure IoT Operations for fleet telemetry collection.

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
  echo "Status: Placeholder — awaiting IoT Operations integration"
  exit 0
fi

echo "ERROR: Not yet implemented — awaiting IoT Operations integration" >&2
exit 1
