#!/usr/bin/env bash
# Install pinned prerequisite tools for Ubuntu edge setup.
# cspell:ignore dearmor keyrings kubelogin keyserver
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

Install reusable Ubuntu packages plus pinned Azure CLI, Helm, kubelogin, and OSMO clients.
This script does not enable SSH, authenticate Azure, or configure VPN, K3s, or Arc.

OPTIONS:
    -h, --help           Show this help message
    --config-preview     Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0")
EOF
}

config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools awk dpkg grep uname

[[ "$(uname -s)" == "Linux" ]] || fatal "Prerequisite installation supports Ubuntu Linux only"
[[ -r /etc/os-release ]] || fatal "Cannot identify the operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] || \
  fatal "Supported operating systems are Ubuntu 22.04 and 24.04"

architecture="$(uname -m)"
case "$architecture" in
  x86_64)
    helm_target="linux-amd64"
    helm_sha256="$EDGE_HELM_SHA256_AMD64"
    kubelogin_target="linux-amd64"
    kubelogin_archive_dir="linux_amd64"
    kubelogin_sha256="$EDGE_KUBELOGIN_SHA256_AMD64"
    osmo_arch="x86_64"
    osmo_sha256="$EDGE_OSMO_SHA256_AMD64"
    ;;
  aarch64|arm64)
    helm_target="linux-arm64"
    helm_sha256="$EDGE_HELM_SHA256_ARM64"
    kubelogin_target="linux-arm64"
    kubelogin_archive_dir="linux_arm64"
    kubelogin_sha256="$EDGE_KUBELOGIN_SHA256_ARM64"
    osmo_arch="arm64"
    osmo_sha256="$EDGE_OSMO_SHA256_ARM64"
    ;;
  *) fatal "Unsupported architecture: $architecture" ;;
esac

base_packages=(
  apt-transport-https
  ca-certificates
  curl
  gettext-base
  git
  gnupg
  iproute2
  jq
  lsb-release
  openssl
  python3
  unzip
)

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Operating System" "Ubuntu $VERSION_ID"
  print_kv "Architecture" "$architecture"
  print_kv "Packages" "${base_packages[*]}"
  print_kv "Azure CLI" "Microsoft signed apt repository"
  print_kv "Microsoft Key" "$MICROSOFT_PACKAGES_KEY_FINGERPRINT"
  print_kv "Helm" "$EDGE_HELM_VERSION ($helm_target)"
  print_kv "kubelogin" "$EDGE_KUBELOGIN_VERSION ($kubelogin_target)"
  print_kv "OSMO" "$EDGE_OSMO_VERSION ($osmo_arch)"
  exit 0
fi

require_tools apt-get curl gpg install sha256sum sudo tar unzip

work_dir=$(mktemp -d)
cleanup_prerequisites() {
  rm -rf "$work_dir"
}
trap cleanup_prerequisites EXIT
chmod 0700 "$work_dir"

section "Install Ubuntu Packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends "${base_packages[@]}"

section "Configure Azure CLI Repository"
key_url="https://packages.microsoft.com/keys/microsoft.asc"
key_file="$work_dir/microsoft.asc"
# pinning-ignore: The Microsoft apt repository key is verified by its published GPG fingerprint before installation.
curl --fail --silent --show-error --location "$key_url" --output "$key_file"
actual_fingerprint=$(gpg --show-keys --with-colons "$key_file" | awk -F: '$1 == "fpr" {print $10; exit}')
[[ "$actual_fingerprint" == "$MICROSOFT_PACKAGES_KEY_FINGERPRINT" ]] || \
  fatal "Microsoft package signing key fingerprint does not match the published value"
gpg --dearmor --yes --output "$work_dir/microsoft.gpg" "$key_file"

keyring_path="/etc/apt/keyrings/microsoft.gpg"
source_path="/etc/apt/sources.list.d/azure-cli.sources"
source_file="$work_dir/azure-cli.sources"
cat > "$source_file" <<EOF
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: $(lsb_release -cs)
Components: main
Architectures: $(dpkg --print-architecture)
Signed-by: $keyring_path
EOF

if sudo test -e "$keyring_path"; then
  sudo cmp --silent "$work_dir/microsoft.gpg" "$keyring_path" || \
    fatal "Existing Microsoft apt keyring differs from the verified key: $keyring_path"
else
  sudo install -d -m 0755 /etc/apt/keyrings
  sudo install -m 0644 "$work_dir/microsoft.gpg" "$keyring_path"
fi
if sudo test -e "$source_path"; then
  sudo cmp --silent "$source_file" "$source_path" || \
    fatal "Existing Azure CLI apt source differs from the expected configuration: $source_path"
else
  sudo install -m 0644 "$source_file" "$source_path"
fi
sudo apt-get update
sudo apt-get install -y --no-install-recommends azure-cli

section "Install Pinned Helm"
helm_archive="$work_dir/helm.tar.gz"
curl --fail --silent --show-error --location \
  "https://get.helm.sh/helm-v${EDGE_HELM_VERSION}-${helm_target}.tar.gz" --output "$helm_archive"
printf '%s  %s\n' "$helm_sha256" "$helm_archive" | sha256sum -c -
tar -xzf "$helm_archive" -C "$work_dir"
sudo install -m 0755 "$work_dir/$helm_target/helm" /usr/local/bin/helm

section "Install Pinned kubelogin"
kubelogin_archive="$work_dir/kubelogin.zip"
curl --fail --silent --show-error --location \
  "https://github.com/Azure/kubelogin/releases/download/v${EDGE_KUBELOGIN_VERSION}/kubelogin-${kubelogin_target}.zip" \
  --output "$kubelogin_archive"
printf '%s  %s\n' "$kubelogin_sha256" "$kubelogin_archive" | sha256sum -c -
unzip -q "$kubelogin_archive" -d "$work_dir/kubelogin"
sudo install -m 0755 "$work_dir/kubelogin/bin/$kubelogin_archive_dir/kubelogin" /usr/local/bin/kubelogin

section "Install Pinned OSMO Client"
osmo_installer="osmo-client-installer-${EDGE_OSMO_VERSION}-linux-${osmo_arch}.sh"
osmo_installer_path="$work_dir/$osmo_installer"
curl --fail --silent --show-error --location \
  "https://github.com/NVIDIA/OSMO/releases/download/${EDGE_OSMO_VERSION}/${osmo_installer}" \
  --output "$osmo_installer_path"
printf '%s  %s\n' "$osmo_sha256" "$osmo_installer_path" | sha256sum -c -
sudo bash "$osmo_installer_path"

require_tools az helm kubelogin osmo
actual_helm=$(helm version --short)
actual_kubelogin=$(kubelogin --version 2>&1 | head -1)
actual_osmo=$(osmo --version 2>&1 | head -1)

section "Deployment Summary"
print_kv "Operating System" "Ubuntu $VERSION_ID"
print_kv "Architecture" "$architecture"
print_kv "Azure CLI" "$(az version --query '"azure-cli"' -o tsv)"
print_kv "Helm" "$actual_helm"
print_kv "kubelogin" "$actual_kubelogin"
print_kv "OSMO" "$actual_osmo"
info "Ubuntu edge prerequisites installed"
