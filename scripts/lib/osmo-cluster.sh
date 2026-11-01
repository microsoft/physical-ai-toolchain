#!/usr/bin/env bash
# Shared helpers for deploying OSMO and the KAI Scheduler it requires onto a Kubernetes cluster. Used by
# the AKS setup (infrastructure/setup), HiL K3s edges (data-pipeline/setup/hil), and the local profile
# (infrastructure/local).
#
# Source after scripts/lib/common.sh and infrastructure/setup/defaults.conf.
# cspell:ignore crdupgrader upgrader

# Print Helm arguments, one per line, that pin KAI's operator and job images to this architecture's digests.
kai_scheduler_image_args() {
  local operator_sha crd_upgrader_sha
  case "$(uname -m)" in
    x86_64)
      operator_sha="$KAI_OPERATOR_SHA256_AMD64"
      crd_upgrader_sha="$KAI_CRD_UPGRADER_SHA256_AMD64"
      ;;
    aarch64|arm64)
      operator_sha="$KAI_OPERATOR_SHA256_ARM64"
      crd_upgrader_sha="$KAI_CRD_UPGRADER_SHA256_ARM64"
      ;;
    *) fatal "Unsupported KAI image architecture: $(uname -m)" ;;
  esac
  printf '%s\n' \
    --set-string "operator.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${operator_sha}" \
    --set-string "crdupgrader.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${crd_upgrader_sha}" \
    --set-string "postCleanup.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${crd_upgrader_sha}" \
    --set-string "kaiConfigDeployer.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${crd_upgrader_sha}" \
    --set-string "topologyMigration.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${crd_upgrader_sha}"
}

# Install or upgrade KAI Scheduler, then wait on KAI's own readiness signals. Helm's --wait can't be used:
# the operator creates a cluster-scoped SchedulingShard whose permanent Reconciling=True condition Helm 4
# reads as in progress, so --wait always times out on a healthy install.
# Usage: install_kai_scheduler <kubeconfig> <context> <chart> <values-file> [helm args...]
install_kai_scheduler() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local chart="${3:?chart required}" values="${4:?values file required}" waited=0
  shift 4

  kube_helm "$kubeconfig" "$context" upgrade --install kai-scheduler "$chart" \
    --namespace "$NS_KAI_SCHEDULER" --create-namespace -f "$values" --timeout "$TIMEOUT_DEPLOY" "$@"

  info "Waiting for KAI operator rollout..."
  kube_kubectl "$kubeconfig" "$context" rollout status deployment/kai-operator \
    -n "$NS_KAI_SCHEDULER" --timeout "$TIMEOUT_DEPLOY"

  # The operator creates the shard after it starts, so wait for it to exist before waiting on its condition.
  info "Waiting for KAI SchedulingShard to become available..."
  until kube_kubectl "$kubeconfig" "$context" get schedulingshard/default >/dev/null 2>&1; do
    (( waited < 180 )) || fatal "KAI operator did not create SchedulingShard default"
    sleep 3
    waited=$(( waited + 3 ))
  done
  kube_kubectl "$kubeconfig" "$context" wait --for=condition=Available schedulingshard/default \
    --timeout "$TIMEOUT_DEPLOY"
}

# Print present or absent for the OSMO master encryption key ConfigMap. Any other read error fails, so a
# transient API or permission error never looks like a missing key that a caller would then replace.
# Usage: state=$(osmo_mek_state <namespace> <configmap-name>)
osmo_mek_state() {
  local namespace="${1:?namespace required}" name="${2:?ConfigMap name required}" found
  if ! found=$(kubectl get configmap "$name" -n "$namespace" --ignore-not-found -o name); then
    error "Could not read ConfigMap $namespace/$name, so whether the master encryption key exists is unknown"
    return 1
  fi
  if [[ -n "$found" ]]; then echo present; else echo absent; fi
}

# Print a ConfigMap holding a new random OSMO master encryption key (MEK) in the format the service chart
# mounts. Replacing an existing MEK makes data encrypted with it unrecoverable, so callers keep it.
osmo_mek_configmap() {
  local name="${1:?ConfigMap name required}" key jwk encoded
  key="$(openssl rand -base64 32 | tr -d '\n')"
  jwk="{\"k\":\"${key}\",\"kid\":\"key1\",\"kty\":\"oct\"}"
  encoded="$(echo -n "$jwk" | base64 | tr -d '\n')"
  cat <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: $name
data:
  mek.yaml: |
    currentMek: key1
    meks:
      key1: ${encoded}
EOF
}

# Create or update the incluster-kubeconfig ConfigMap that the dev-mode backend operator mounts.
# Dev mode calls load_kube_config(), not load_incluster_config(), and load_kube_config() reads a
# kubeconfig tokenFile once at startup, so a projected service-account token (about 1h TTL) would go
# stale and every API call would return 401. An exec credential plugin avoids that: the client
# re-invokes it when the cached credential expires, and the plugin re-reads the rotated token. Both the
# kubeconfig and the plugin are keys in the ConfigMap, mounted together at /etc/kubeconfig.
# Usage: osmo_apply_operator_kubeconfig <kubeconfig> <context> <namespace>
osmo_apply_operator_kubeconfig() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local namespace="${3:?namespace required}" work
  work=$(mktemp -d)

  cat > "$work/config" <<'KUBECONFIG_EOF'
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
    server: https://kubernetes.default.svc
  name: incluster
contexts:
- context:
    cluster: incluster
    user: sa
  name: incluster
current-context: incluster
users:
- name: sa
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1
      command: python3
      args:
      - /etc/kubeconfig/token-cred.py
      interactiveMode: Never
      provideClusterInfo: false
KUBECONFIG_EOF
  cat > "$work/token-cred.py" <<'PYTHON_EOF'
import base64, datetime, json
_t = open('/var/run/secrets/kubernetes.io/serviceaccount/token').read().strip()
# Expire the cached credential ~5min before the projected token does, so the next
# request re-invokes this plugin and re-reads the freshly-rotated on-disk token.
try:
    _seg = _t.split('.')[1]
    _seg += '=' * (-len(_seg) % 4)
    _exp = json.loads(base64.urlsafe_b64decode(_seg))['exp'] - 300
    _ts = datetime.datetime.fromtimestamp(_exp, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
except Exception:
    _ts = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
print(json.dumps({'apiVersion': 'client.authentication.k8s.io/v1', 'kind': 'ExecCredential',
                  'status': {'token': _t, 'expirationTimestamp': _ts}}))
PYTHON_EOF

  kube_kubectl "$kubeconfig" "$context" create configmap incluster-kubeconfig -n "$namespace" \
    --from-file=config="$work/config" \
    --from-file=token-cred.py="$work/token-cred.py" \
    --dry-run=client -o yaml | kube_kubectl "$kubeconfig" "$context" apply -f - >/dev/null
  rm -rf "$work"
}
