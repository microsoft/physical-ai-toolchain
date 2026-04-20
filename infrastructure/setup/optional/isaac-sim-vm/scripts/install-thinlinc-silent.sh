#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSWERS_FILE="${SCRIPT_DIR}/thinlinc-answers.hconf"
TL_VERSION="${TL_VERSION:-4.20.0}"
TL_SHA256="9dd537fb3b95018888affbd4431672308c433e7f5611143e060462190ed768d3"
DEFAULT_ZIP_NAME="tl-${TL_VERSION}-server.zip"
THINLINC_ZIP="${1:-${DEFAULT_ZIP_NAME}}"
THINLINC_DOWNLOAD_URL="${TL_DOWNLOAD_URL:-https://www.cendio.com/downloads/server/${DEFAULT_ZIP_NAME}}"
WORK_DIR="$(mktemp -d)"

# Optional environment overrides (applied only if matching keys exist
# in the generated answer file):
#   TL_MASTER_HOSTNAME
#   TL_AGENT_HOSTNAME
#   TL_AGENT_HOSTNAME_SOURCE=primary-ip|auto|fqdn|public-ip
#   TL_AGENT_HOSTNAME_CMD="<shell command that prints hostname/IP>"
#   TL_WEBACCESS_LOGIN_PAGE
#   TL_ADMIN_EMAIL
#   TL_WEBADM_PASSWORD_HASH (empty by default)

cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

if [[ ! -f "${THINLINC_ZIP}" ]]; then
  # Auto-download when using a local filename (not an explicit path).
  if [[ "${THINLINC_ZIP}" != */* ]]; then
    echo "ThinLinc ZIP not found locally. Downloading ${THINLINC_DOWNLOAD_URL} ..."
    if command -v wget >/dev/null 2>&1; then
      wget -O "${THINLINC_ZIP}" "${THINLINC_DOWNLOAD_URL}"
    elif command -v curl >/dev/null 2>&1; then
      curl -fL "${THINLINC_DOWNLOAD_URL}" -o "${THINLINC_ZIP}"
    else
      echo "Neither wget nor curl is available to download ${THINLINC_DOWNLOAD_URL}"
      exit 1
    fi
    echo "${TL_SHA256}  ${THINLINC_ZIP}" | sha256sum -c --quiet -
  else
    echo "ThinLinc server ZIP not found: ${THINLINC_ZIP}"
    echo "Pass the ZIP path as first argument, e.g.:"
    echo "  ./install-tl-server.sh /path/to/tl-4.20.0-server.zip"
    exit 1
  fi
fi

set_answer_if_present() {
  local key="$1"
  local value="$2"

  if grep -Eq "^[[:space:]]*#?[[:space:]]*${key}[[:space:]]*=" "${ANSWERS_FILE}"; then
    sed -Ei "s|^[[:space:]]*#?[[:space:]]*${key}[[:space:]]*=.*$|${key}=${value}|" "${ANSWERS_FILE}"
    echo "Applied: ${key} = ${value}"
    return 0
  fi

  return 1
}

set_first_matching_key() {
  local value="$1"
  shift

  local key=""
  for key in "$@"; do
    if set_answer_if_present "${key}" "${value}"; then
      return 0
    fi
  done

  return 1
}

resolve_agent_hostname() {
  local source="${TL_AGENT_HOSTNAME_SOURCE:-primary-ip}"
  local resolved=""

  if [[ -n "${TL_AGENT_HOSTNAME_CMD:-}" ]]; then
    resolved="$(bash -lc "${TL_AGENT_HOSTNAME_CMD}" 2>/dev/null | head -n 1 | tr -d '\r')"
    if [[ -n "${resolved}" ]]; then
      echo "${resolved}"
      return 0
    fi
  fi

  case "${source}" in
    auto|fqdn)
      resolved="$(hostname -f 2>/dev/null || true)"
      if [[ -n "${resolved}" ]]; then
        echo "${resolved}"
        return 0
      fi
      ;;
  esac

  case "${source}" in
    auto|primary-ip)
      resolved="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
      if [[ -n "${resolved}" ]]; then
        echo "${resolved}"
        return 0
      fi
      ;;
  esac

  if [[ "${source}" == "public-ip" || "${source}" == "auto" ]]; then
    for service in \
      "https://api.ipify.org" \
      "https://ifconfig.me/ip" \
      "https://icanhazip.com"; do
      resolved="$(curl -fsS --max-time 5 "${service}" 2>/dev/null | head -n 1 | tr -d '\r')"
      if [[ -n "${resolved}" ]]; then
        echo "${resolved}"
        return 0
      fi
    done
  fi

  return 1
}

unzip -q "${THINLINC_ZIP}" -d "${WORK_DIR}"
SERVER_DIR="$(find "${WORK_DIR}" -maxdepth 1 -type d -name 'tl-*-server' | head -n 1)"

if [[ -z "${SERVER_DIR}" ]]; then
  echo "Could not locate extracted ThinLinc server directory in ${WORK_DIR}"
  exit 1
fi

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${SERVER_DIR}"/packages/*.deb

sudo /opt/thinlinc/sbin/tl-setup -g "${ANSWERS_FILE}"

# Required for silent mode: accept license and provide base behavior.
set_first_matching_key "yes" "accept-eula" >/dev/null || true
set_first_matching_key "master" "server-type" >/dev/null || true
set_first_matching_key "parameters" "migrate-conf" >/dev/null || true
set_first_matching_key "yes" "install-required-libs" >/dev/null || true
set_first_matching_key "yes" "install-nfs" >/dev/null || true
set_first_matching_key "yes" "install-sshd" >/dev/null || true
set_first_matching_key "no" "install-gtk" >/dev/null || true
set_first_matching_key "no" "install-python-ldap" >/dev/null || true
set_first_matching_key "ip" "agent-hostname-choice" >/dev/null || true
set_first_matching_key "nono@example.com" "email-address" >/dev/null || true
set_first_matching_key "no" "setup-thinlocal" >/dev/null || true
set_first_matching_key "no" "setup-nearest" >/dev/null || true
set_first_matching_key "no" "setup-firewall-ssh" >/dev/null || true
set_first_matching_key "no" "setup-firewall-tlwebaccess" >/dev/null || true
set_first_matching_key "no" "setup-firewall-tlwebadm" >/dev/null || true
set_first_matching_key "no" "setup-firewall-tlmaster" >/dev/null || true
set_first_matching_key "no" "setup-firewall-tlagent" >/dev/null || true
set_first_matching_key "no" "setup-selinux" >/dev/null || true
set_first_matching_key "no" "setup-apparmor" >/dev/null || true
set_first_matching_key "${TL_WEBADM_PASSWORD_HASH:-}" "tlwebadm-password" >/dev/null || true
set_first_matching_key "abort" "missing-answer" >/dev/null || true

echo "Discovered answer keys in ${ANSWERS_FILE}:"
grep -E "^[[:space:]]*[^#%\[][^=]*=" "${ANSWERS_FILE}" || true

if [[ -n "${TL_MASTER_HOSTNAME:-}" ]]; then
  set_first_matching_key "${TL_MASTER_HOSTNAME}" \
    "/vsmagent/master_hostname" \
    "vsmagent/master_hostname" \
    "master_hostname" || echo "Warning: could not find a master hostname key in ${ANSWERS_FILE}"
fi

if [[ "${TL_AGENT_HOSTNAME:-}" == "auto" ]]; then
  if TL_AGENT_HOSTNAME="$(resolve_agent_hostname)"; then
    echo "Resolved TL_AGENT_HOSTNAME=${TL_AGENT_HOSTNAME}"
  else
    echo "Warning: TL_AGENT_HOSTNAME=auto but no value could be resolved"
    TL_AGENT_HOSTNAME=""
  fi
fi

if [[ -n "${TL_AGENT_HOSTNAME:-}" ]]; then
  set_first_matching_key "${TL_AGENT_HOSTNAME}" \
    "manual-agent-hostname" \
    "/vsmagent/agent_hostname" \
    "vsmagent/agent_hostname" \
    "agent_hostname" || echo "Warning: could not find an agent hostname/IP key in ${ANSWERS_FILE}"
fi

if [[ -n "${TL_WEBACCESS_LOGIN_PAGE:-}" ]]; then
  set_first_matching_key "${TL_WEBACCESS_LOGIN_PAGE}" \
    "/webaccess/login_page" \
    "webaccess/login_page" \
    "login_page" || echo "Warning: could not find a web access login page key in ${ANSWERS_FILE}"
fi

if [[ -n "${TL_ADMIN_EMAIL:-}" ]]; then
  set_first_matching_key "${TL_ADMIN_EMAIL}" \
    "email-address" \
    "/vsmserver/admin_email" \
    "vsmserver/admin_email" \
    "admin_email" || echo "Warning: could not find an admin email key in ${ANSWERS_FILE}"
fi

sudo /opt/thinlinc/sbin/tl-setup -a "${ANSWERS_FILE}"

echo "ThinLinc silent setup complete."
echo "Service status:"
sudo systemctl --no-pager --full status vsmserver vsmagent tlwebaccess || true
