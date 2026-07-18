#!/usr/bin/env bash
# Prepare a supported Ubuntu host for the selected HiL transfer path.
# cspell:ignore coreutils dearmor diffutils keyrings procps
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Prepare Ubuntu for one progressive T3 HiL journey.
Key Vault is the default transfer. Select SCP before setup to omit Azure CLI.

OPTIONS:
    -h, --help               Show this help message
    --transport TRANSPORT    keyvault|scp (default: keyvault)
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0") --transport scp
EOF
}

transport="keyvault"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --transport)       transport="$2"; shift 2 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

[[ "$transport" == "keyvault" || "$transport" == "scp" ]] || \
  fatal "--transport must be keyvault or scp"

base_packages=(ca-certificates coreutils curl diffutils gawk gnupg iproute2 jq openssl procps python3 sudo tar)

case "$(uname -m 2>/dev/null || true)" in
  x86_64)
    architecture="x86_64"
    helm_target="linux-amd64"
    helm_sha256="$EDGE_HELM_SHA256_AMD64"
    osmo_arch="x86_64"
    osmo_sha256="$EDGE_OSMO_SHA256_AMD64"
    ;;
  aarch64|arm64)
    architecture="arm64"
    helm_target="linux-arm64"
    helm_sha256="$EDGE_HELM_SHA256_ARM64"
    osmo_arch="arm64"
    osmo_sha256="$EDGE_OSMO_SHA256_ARM64"
    ;;
  *)
    architecture="unsupported"
    helm_target="unsupported"
    helm_sha256=""
    osmo_arch="unsupported"
    osmo_sha256=""
    ;;
esac

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "host-ready"
  print_kv "Supported Host" "Ubuntu 22.04 or 24.04"
  print_kv "Architecture" "$architecture"
  print_kv "Transfer" "$transport"
  print_kv "Packages" "${base_packages[*]}"
  print_kv "Azure CLI" "$([[ $transport == keyvault ]] && echo 'Microsoft signed apt repository' || echo 'not installed')"
  print_kv "Helm" "$EDGE_HELM_VERSION ($helm_target)"
  print_kv "OSMO" "$EDGE_OSMO_VERSION ($osmo_arch)"
  print_kv "Authentication" "not performed"
  print_kv "Next" "01-install-k3s.sh or vpn/00-request-vpn-access.sh when private reachability is required"
  exit 0
fi

operation="validate supported host"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: host-ready"
  exit "$status"
}
trap report_failure ERR

[[ "$(uname -s)" == "Linux" ]] || fatal "Host preparation supports Ubuntu Linux only"
[[ -r /etc/os-release ]] || fatal "Cannot identify the operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] || \
  fatal "Supported operating systems are Ubuntu 22.04 and 24.04"
[[ "$architecture" != "unsupported" ]] || fatal "Unsupported architecture: $(uname -m)"

require_tools apt-get sudo
for destination in /etc/apt/keyrings/microsoft.gpg /etc/apt/sources.list.d/azure-cli.sources \
  /usr/local/bin/helm /usr/local/bin/osmo; do
  require_no_symlink_path "$destination"
  [[ ! -L "$destination" ]] || fatal "Installation destination must not be a symlink: $destination"
done
work_dir=$(mktemp -d)
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT
chmod 0700 "$work_dir"

operation="install Ubuntu packages"
section "Install Ubuntu Packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends "${base_packages[@]}"

if [[ "$transport" == "keyvault" ]]; then
  operation="install Azure CLI"
  section "Install Azure CLI"
  require_tools curl dpkg gpg
  key_file="$work_dir/microsoft.asc"
  # pinning-ignore: The Microsoft apt key is verified by its published fingerprint.
  curl --fail --silent --show-error --location \
    https://packages.microsoft.com/keys/microsoft.asc --output "$key_file"
  actual_fingerprint=$(gpg --show-keys --with-colons "$key_file" | awk -F: '$1 == "fpr" {print $10; exit}')
  [[ "$actual_fingerprint" == "$MICROSOFT_PACKAGES_KEY_FINGERPRINT" ]] || \
    fatal "Microsoft package signing key fingerprint does not match the published value"
  gpg --dearmor --yes --output "$work_dir/microsoft.gpg" "$key_file"
  keyring_path=/etc/apt/keyrings/microsoft.gpg
  source_path=/etc/apt/sources.list.d/azure-cli.sources
  source_file="$work_dir/azure-cli.sources"
  cat > "$source_file" <<EOF
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: ${VERSION_CODENAME:?Ubuntu version codename is unavailable}
Components: main
Architectures: $(dpkg --print-architecture)
Signed-by: $keyring_path
EOF
  sudo install -d -m 0755 /etc/apt/keyrings
  if sudo test -e "$keyring_path"; then
    sudo cmp --silent "$work_dir/microsoft.gpg" "$keyring_path" || \
      fatal "Existing Microsoft keyring differs from the verified key: $keyring_path"
  else
    sudo install -m 0644 "$work_dir/microsoft.gpg" "$keyring_path"
  fi
  if sudo test -e "$source_path"; then
    sudo cmp --silent "$source_file" "$source_path" || \
      fatal "Existing Azure CLI source differs from the expected configuration: $source_path"
  else
    sudo install -m 0644 "$source_file" "$source_path"
  fi
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends azure-cli
fi

operation="install pinned Helm"
section "Install Pinned Helm"
helm_archive="$work_dir/helm.tar.gz"
curl --fail --silent --show-error --location \
  "https://get.helm.sh/helm-v${EDGE_HELM_VERSION}-${helm_target}.tar.gz" --output "$helm_archive"
printf '%s  %s\n' "$helm_sha256" "$helm_archive" | sha256sum -c -
tar -xzf "$helm_archive" -C "$work_dir"
sudo install -m 0755 "$work_dir/$helm_target/helm" /usr/local/bin/helm

operation="install pinned OSMO client"
section "Install Pinned OSMO Client"
osmo_installer="osmo-client-installer-${EDGE_OSMO_VERSION}-linux-${osmo_arch}.sh"
osmo_installer_path="$work_dir/$osmo_installer"
curl --fail --silent --show-error --location \
  "https://github.com/NVIDIA/OSMO/releases/download/${EDGE_OSMO_VERSION}/${osmo_installer}" \
  --output "$osmo_installer_path"
printf '%s  %s\n' "$osmo_sha256" "$osmo_installer_path" | sha256sum -c -
sudo bash "$osmo_installer_path"

operation="verify installed clients"
require_tools helm osmo
[[ "$transport" != "keyvault" ]] || require_tools az

trap - ERR
section "Deployment Summary"
print_kv "Milestone" "host-ready"
print_kv "Ubuntu" "$VERSION_ID ($architecture)"
print_kv "Transfer" "$transport"
print_kv "Azure CLI" "$([[ $transport == keyvault ]] && az version --query '"azure-cli"' -o tsv || echo 'not installed by this path')"
print_kv "Helm" "$(helm version --short)"
print_kv "OSMO" "$(osmo version 2>&1 | head -1)"
print_kv "Next" "Run $SCRIPT_DIR/01-install-k3s.sh"
print_kv "Optional VPN" "$SCRIPT_DIR/vpn/00-request-vpn-access.sh"
info "Ubuntu host is ready"
