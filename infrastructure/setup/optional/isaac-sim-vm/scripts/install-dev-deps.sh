#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
# Azure CustomScript can run without HOME set; ensure global tools (git, uv) work.
export HOME="${HOME:-/root}"
ADMIN_USER="${1:-azureuser}"

if ! id "$ADMIN_USER" >/dev/null 2>&1; then
  echo "Admin user does not exist: $ADMIN_USER" >&2
  exit 1
fi

ADMIN_USER_HOME="$(getent passwd "$ADMIN_USER" | cut -d: -f6)"

if [[ -z "$ADMIN_USER_HOME" || ! -d "$ADMIN_USER_HOME" ]]; then
  echo "Admin user home directory does not exist: $ADMIN_USER_HOME" >&2
  exit 1
fi

configure_admin_git() {
  sudo -H -u "$ADMIN_USER" env HOME="$ADMIN_USER_HOME" bash -lc \
    'cd "$HOME" && git config --global core.editor "code-insiders --wait"'
}

install_admin_azure_cli_extension() {
  local extension_name="$1"

  sudo -H -u "$ADMIN_USER" env HOME="$ADMIN_USER_HOME" bash -lc \
    "az extension add --name '${extension_name}' --yes"
}

wait_for_apt_locks() {
  local timeout_seconds=900
  local wait_interval=5
  local waited=0

  while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
      sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
      sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || \
      sudo fuser /var/cache/apt/archives/lock >/dev/null 2>&1; do
    if [ "$waited" -ge "$timeout_seconds" ]; then
      echo "Timed out waiting for apt/dpkg locks after ${timeout_seconds}s" >&2
      return 1
    fi
    echo "apt/dpkg lock held, sleeping ${wait_interval}s... (${waited}s elapsed)"
    sleep "$wait_interval"
    waited=$((waited + wait_interval))
  done
}

apt_get() {
  wait_for_apt_locks
  sudo apt-get -o DPkg::Lock::Timeout=600 "$@"
}

dpkg_install() {
  wait_for_apt_locks
  sudo dpkg -i "$1"
}

repair_dpkg_state() {
  local running_kernel
  local pkg
  local -a broken_linux_pkgs=()

  wait_for_apt_locks

  # First attempt a normal repair path.
  if sudo dpkg --configure -a; then
    apt_get -f install -y || true
    return
  fi

  # If dpkg is blocked by failed future kernel/header config scripts (dkms),
  # purge only the broken kernel/header packages not matching the running kernel.
  running_kernel="$(uname -r)"
  while IFS= read -r pkg; do
    if [ -n "$pkg" ] && [[ "$pkg" != *"$running_kernel"* ]]; then
      broken_linux_pkgs+=("$pkg")
    fi
  done < <(sudo dpkg --audit | grep -oE 'linux-(image|headers)-[0-9][^ ,]+' | sort -u)

  if [ "${#broken_linux_pkgs[@]}" -gt 0 ]; then
    echo "Purging broken kernel/header packages: ${broken_linux_pkgs[*]}"
    apt_get remove --purge -y "${broken_linux_pkgs[@]}" || true
  fi

  sudo dpkg --configure -a || true
  apt_get -f install -y || true
}

prevent_kernel_upgrades_during_provisioning() {
  # Avoid kernel/header transitions during provisioning, which can trigger dkms
  # rebuild failures and leave apt in an error state.
  #
  # 1) Put installed meta-packages on hold.
  # 2) Add apt pinning so unattended-upgrades and any later apt invocations
  #    in this VM won't pull Azure kernel transitions.
  sudo apt-mark hold \
    linux-azure \
    linux-image-azure \
    linux-headers-azure \
    linux-tools-azure \
    linux-cloud-tools-azure || true

  sudo tee /etc/apt/preferences.d/99-hold-azure-kernel.pref >/dev/null <<'EOF'
Package: linux-azure
Pin: release *
Pin-Priority: -1

Package: linux-image-azure
Pin: release *
Pin-Priority: -1

Package: linux-headers-azure
Pin: release *
Pin-Priority: -1

Package: linux-tools-azure
Pin: release *
Pin-Priority: -1

Package: linux-cloud-tools-azure
Pin: release *
Pin-Priority: -1
EOF
}

# Prevent kernel transitions as early as possible.
prevent_kernel_upgrades_during_provisioning

apt_get update
repair_dpkg_state

## Install Node.js 22 LTS
NODESOURCE_GPG_SHA256="b42e0321dabdc24e892115da705cf061167eac12a317f23d329862d0aa0a271d"
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL -o /tmp/nodesource-repo.gpg.key https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key
echo "${NODESOURCE_GPG_SHA256}  /tmp/nodesource-repo.gpg.key" | sha256sum -c --quiet -
sudo gpg --dearmor --batch --yes -o /etc/apt/keyrings/nodesource.gpg < /tmp/nodesource-repo.gpg.key
rm -f /tmp/nodesource-repo.gpg.key
sudo chmod go+r /etc/apt/keyrings/nodesource.gpg
sudo tee /etc/apt/sources.list.d/nodesource.sources >/dev/null <<'EOF'
Types: deb
URIs: https://deb.nodesource.com/node_22.x
Suites: nodistro
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/nodesource.gpg
EOF
apt_get update
apt_get install -y --no-install-recommends nodejs

UV_VERSION="0.10.9"
UV_INSTALLER_SHA256="7fc46e39cb97290b57169c0c813a17970585ac519139f19006453c99b5f2f45f"
curl -fsSL -o /tmp/uv-install.sh "https://astral.sh/uv/${UV_VERSION}/install.sh"
echo "${UV_INSTALLER_SHA256}  /tmp/uv-install.sh" | sha256sum -c --quiet -
sudo env UV_INSTALL_DIR="/usr/local/bin" sh /tmp/uv-install.sh
rm -f /tmp/uv-install.sh
if ! command -v uv >/dev/null 2>&1; then
  echo "uv installation failed or is not on PATH" >&2
  exit 1
fi
MICROSOFT_GPG_SHA256="2fa9c05d591a1582a9aba276272478c262e95ad00acf60eaee1644d93941e3c6"
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL -o /tmp/microsoft.asc https://packages.microsoft.com/keys/microsoft.asc
echo "${MICROSOFT_GPG_SHA256}  /tmp/microsoft.asc" | sha256sum -c --quiet -
sudo gpg --dearmor --batch --yes -o /etc/apt/keyrings/microsoft.gpg < /tmp/microsoft.asc
rm -f /tmp/microsoft.asc
sudo chmod go+r /etc/apt/keyrings/microsoft.gpg
sudo tee /etc/apt/sources.list.d/azure-cli.sources >/dev/null <<EOF
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: noble
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/microsoft.gpg
EOF
apt_get update
apt_get install -y --no-install-recommends azure-cli ripgrep
install_admin_azure_cli_extension "ml"

# shellcheck disable=SC1091
. /etc/os-release
DISTRO=$ID
VERSION=$VERSION_ID
# URL is dynamic (distro/version-dependent); trust relies on the GPG-signed repository.
curl -sSL -O "https://packages.microsoft.com/config/${DISTRO}/${VERSION}/packages-microsoft-prod.deb"
dpkg_install packages-microsoft-prod.deb
rm packages-microsoft-prod.deb
apt_get update
apt_get install -y azcopy

## Install CUDA
VERSION_NO_DOT="${VERSION//./}"
# URL is dynamic (distro/version-dependent); trust relies on the GPG-signed repository.
wget "https://developer.download.nvidia.com/compute/cuda/repos/${DISTRO}${VERSION_NO_DOT}/x86_64/cuda-keyring_1.1-1_all.deb" && dpkg_install cuda-keyring_1.1-1_all.deb
apt_get update
apt_get install -y cuda-toolkit-12-6

## Use Docker without sudo
sudo usermod -aG docker "$ADMIN_USER"

## Install NVidia Container Toolkit
NVIDIA_CTK_GPG_SHA256="c880576d6cf75a48e5027a871bac70fd0421ab07d2b55f30877b21f1c87959c9"
curl -fsSL -o /tmp/nvidia-ctk.gpg.key https://nvidia.github.io/libnvidia-container/gpgkey
echo "${NVIDIA_CTK_GPG_SHA256}  /tmp/nvidia-ctk.gpg.key" | sha256sum -c --quiet -
sudo gpg --dearmor --batch --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg < /tmp/nvidia-ctk.gpg.key
rm -f /tmp/nvidia-ctk.gpg.key
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt_get update
apt_get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker


# Install VSCode insiders
VSCODE_GPG_SHA256="2fa9c05d591a1582a9aba276272478c262e95ad00acf60eaee1644d93941e3c6"
echo "code code/add-microsoft-repo boolean true" | sudo debconf-set-selections
apt_get install -y wget gpg
curl -fsSL -o /tmp/vscode-microsoft.asc https://packages.microsoft.com/keys/microsoft.asc
echo "${VSCODE_GPG_SHA256}  /tmp/vscode-microsoft.asc" | sha256sum -c --quiet -
gpg --dearmor --batch --yes < /tmp/vscode-microsoft.asc > microsoft.gpg
rm -f /tmp/vscode-microsoft.asc
sudo install -D -o root -g root -m 644 microsoft.gpg /usr/share/keyrings/microsoft.gpg
rm -f microsoft.gpg

sudo tee /etc/apt/sources.list.d/vscode.sources > /dev/null <<EOF
Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: amd64,arm64,armhf
Signed-By: /usr/share/keyrings/microsoft.gpg
EOF

apt_get install -y apt-transport-https &&
apt_get update &&
apt_get install -y code-insiders
configure_admin_git


## Install PowerShell

apt_get update
apt_get install -y powershell
