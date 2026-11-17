#!/usr/bin/env bash
# Allow rootless Podman to stop pasta under Ubuntu's AppArmor profile
set -o errexit -o nounset

# Ubuntu 26.04 enforces a pasta AppArmor profile that rejects the SIGTERM
# Podman sends when it tears down the rootless network namespace, so
# `kind delete cluster` fails to remove its node
# (https://bugs.launchpad.net/bugs/2154379). The profile has no local override
# include, so the rule is added to the profile itself.
profile="${PASTA_APPARMOR_PROFILE:-/etc/apparmor.d/usr.bin.pasta}"
rule='  signal (receive) set=(term) peer=podman,'

if [ ! -f "$profile" ]; then
  echo "No pasta AppArmor profile at $profile; nothing to change"
  exit 0
fi
if ! command -v aa-enabled >/dev/null 2>&1 || ! aa-enabled --quiet 2>/dev/null; then
  echo "AppArmor is not reported as enabled; leaving $profile unchanged"
  exit 0
fi
if grep -Eq '^[[:space:]]*signal[^,]*receive[^,]*peer=podman' "$profile"; then
  echo "$profile already allows Podman to stop pasta"
  exit 0
fi

updated="$(mktemp)"
trap 'rm -f "$updated"' EXIT
if ! awk -v rule="$rule" '
  { lines[NR] = $0 }
  /^}/ { last = NR }
  END {
    if (!last) exit 1
    for (i = 1; i <= NR; i++) {
      if (i == last) print rule
      print lines[i]
    }
  }
' "$profile" >"$updated"; then
  echo "Cannot find the closing brace of the pasta profile in $profile" >&2
  exit 1
fi

sudo cp "$updated" "$profile"
sudo apparmor_parser -r "$profile"
echo "Allowed Podman to stop pasta in $profile"
