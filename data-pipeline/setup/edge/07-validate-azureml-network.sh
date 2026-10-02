#!/usr/bin/env bash
# Validate operator and K3s workload network paths required by Azure ML VLA jobs.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

GENERATED_ROOT="$REPO_ROOT/infrastructure/setup/generated"
PREFLIGHT_IMAGE="curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Validate operator-side Azure and upload access separately from workload-side
DNS and TLS access on Arc-enabled K3s.

OPTIONS:
    -h, --help                         Show this help message
    --subscription-id ID              Arc cluster subscription ID (required)
    --cluster-resource-group NAME      Arc cluster resource group (required)
    --cluster-name NAME                Arc-enabled Kubernetes name (required)
    --workspace-subscription-id ID     Azure ML subscription ID (required)
    --workspace-resource-group NAME    Azure ML resource group (required)
    --workspace-name NAME              Azure ML workspace name (required)
    --hf-repo-id ID                    Hugging Face repository ID (required)
    --hf-revision SHA                  Full Hugging Face commit (required)
    --connectivity-mode MODE           direct|arc-ssh (default: direct)
    --kubeconfig PATH                  Protected K3s kubeconfig (direct mode)
    --context NAME                     Explicit K3s context (direct mode)
    --arc-server-resource-group NAME   Arc server resource group (arc-ssh mode)
    --arc-server-name NAME             Arc-enabled server name (arc-ssh mode)
    --bundle-dir DIR                   Generated environment bundle (required)
    --skip-upload-probe                Skip temporary workspace Blob upload
    --inject-invalid-destinations      Add controlled operator and workload DNS failures
    --config-preview                   Print configuration and exit

EXAMPLES:
    $(basename "$0") \
      --subscription-id <arc-subscription-id> \
      --cluster-resource-group <arc-resource-group> \
      --cluster-name <arc-cluster-name> \
      --workspace-subscription-id <workspace-subscription-id> \
      --workspace-resource-group <workspace-resource-group> \
      --workspace-name <workspace-name> \
      --hf-repo-id <organization/model> \
      --hf-revision <40-character-commit> \
      --connectivity-mode arc-ssh \
      --arc-server-resource-group <server-resource-group> \
      --arc-server-name <server-name> \
      --bundle-dir infrastructure/setup/generated/dev-001
EOF
}

record_result() {
  local side="$1" name="$2" result="$3" detail="$4"

  jq -nc \
    --arg side "$side" \
    --arg name "$name" \
    --arg result "$result" \
    --arg detail "$detail" \
    '{side: $side, name: $name, result: $result, detail: $detail}' >> "$results_file"

  if [[ "$result" == "pass" ]]; then
    info "[$side:$name] PASS: $detail"
  else
    error "[$side:$name] FAIL: $detail"
    ((failures += 1))
  fi
}

run_kubectl() {
  if [[ "$connectivity_mode" == "arc-ssh" ]]; then
    az ssh arc \
      --subscription "$subscription_id" \
      --resource-group "$arc_server_resource_group" \
      --name "$arc_server_name" \
      -- sudo -n k3s kubectl "$@"
  else
    kubectl --kubeconfig "$kubeconfig" --context "$context" "$@"
  fi
}

probe_operator_tls() {
  local name="$1" host="$2" url="$3" require_private="${4:-false}"
  local addresses="" curl_exit=0 curl_detail="" remote_ip=""

  if addresses=$(getent ahosts "$host" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd, -); then
    if [[ -n "$addresses" ]]; then
      if [[ "$require_private" == "true" && ! "$addresses" =~ (^|,)(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.) ]]; then
        record_result operator "${name}-dns" fail "category=dns,reason=public-address"
      else
        record_result operator "${name}-dns" pass "$host resolved"
      fi
    else
      record_result operator "${name}-dns" fail "category=dns,reason=no-addresses"
    fi
  else
    record_result operator "${name}-dns" fail "category=dns,reason=resolution-failed"
  fi

  curl_detail=$(curl --silent --show-error --output /dev/null \
    --write-out 'http=%{http_code},remote_ip=%{remote_ip}' \
    --connect-timeout 10 --max-time 30 "$url") || curl_exit=$?
  if (( curl_exit == 0 )); then
    remote_ip="${curl_detail##*remote_ip=}"
    if [[ "$require_private" == "true" && ! "$remote_ip" =~ ^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.) ]]; then
      record_result operator "${name}-tls" fail "category=dns,reason=public-remote-address"
    else
      record_result operator "${name}-tls" pass "$curl_detail"
    fi
  else
    case "$curl_exit" in
      6) category=dns ;;
      35|51|53|58|59|60|64|66|77|80|82|83|90|91) category=tls ;;
      *) category=connectivity ;;
    esac
    record_result operator "${name}-tls" fail \
      "category=$category,curl_exit=$curl_exit"
  fi
}

probe_operator_failure() {
  local curl_exit=0

  curl --silent --show-error --output /dev/null \
    --connect-timeout 10 --max-time 30 https://invalid.operator.invalid/ || curl_exit=$?
  case "$curl_exit" in
    0) category=unexpected-success ;;
    6) category=dns ;;
    35|51|53|58|59|60|64|66|77|80|82|83|90|91) category=tls ;;
    *) category=connectivity ;;
  esac
  if (( curl_exit == 0 )); then
    record_result operator failure-injection fail "category=$category"
  else
    record_result operator failure-injection fail \
      "category=$category,curl_exit=$curl_exit"
  fi
}

cleanup() {
  if [[ -n "${probe_file:-}" && -f "$probe_file" ]]; then
    rm -f "$probe_file"
  fi
  if [[ -n "${pod_name:-}" ]]; then
    run_kubectl delete pod "$pod_name" --namespace "$namespace" \
      --ignore-not-found --wait=false >/dev/null 2>&1 || true
  fi
}

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
cluster_resource_group="${ARC_RESOURCE_GROUP:-}"
cluster_name="${ARC_CLUSTER_NAME:-}"
workspace_subscription_id="${AZUREML_SUBSCRIPTION_ID:-}"
workspace_resource_group="${AZUREML_RESOURCE_GROUP:-}"
workspace_name="${AZUREML_WORKSPACE_NAME:-}"
hf_repo_id="${HF_MODEL_REPO_ID:-}"
hf_revision="${HF_MODEL_REVISION:-}"
connectivity_mode="${AZUREML_ARC_CONNECTIVITY_MODE:-direct}"
kubeconfig="${EDGE_KUBECONFIG:-}"
context="${EDGE_K3S_CONTEXT:-}"
arc_server_resource_group="${ARC_SERVER_RESOURCE_GROUP:-}"
arc_server_name="${ARC_SERVER_NAME:-}"
bundle_dir="${ENVIRONMENT_BUNDLE_DIR:-}"
namespace="${AZUREML_NAMESPACE:-azureml}"
skip_upload_probe=false
inject_invalid_destinations=false
config_preview=false
failures=0
results_file=""
probe_file=""
pod_name=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                       show_help; exit 0 ;;
    --subscription-id)               subscription_id="$2"; shift 2 ;;
    --cluster-resource-group)        cluster_resource_group="$2"; shift 2 ;;
    --cluster-name)                  cluster_name="$2"; shift 2 ;;
    --workspace-subscription-id)     workspace_subscription_id="$2"; shift 2 ;;
    --workspace-resource-group)      workspace_resource_group="$2"; shift 2 ;;
    --workspace-name)                workspace_name="$2"; shift 2 ;;
    --hf-repo-id)                    hf_repo_id="$2"; shift 2 ;;
    --hf-revision)                   hf_revision="$2"; shift 2 ;;
    --connectivity-mode)             connectivity_mode="$2"; shift 2 ;;
    --kubeconfig)                    kubeconfig="$2"; shift 2 ;;
    --context)                       context="$2"; shift 2 ;;
    --arc-server-resource-group)     arc_server_resource_group="$2"; shift 2 ;;
    --arc-server-name)               arc_server_name="$2"; shift 2 ;;
    --bundle-dir)                    bundle_dir="$2"; shift 2 ;;
    --skip-upload-probe)             skip_upload_probe=true; shift ;;
    --inject-invalid-destinations)   inject_invalid_destinations=true; shift ;;
    --config-preview)                config_preview=true; shift ;;
    *)                               fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$cluster_resource_group" ]] || fatal "--cluster-resource-group is required"
[[ -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ -n "$workspace_subscription_id" ]] || fatal "--workspace-subscription-id is required"
[[ -n "$workspace_resource_group" ]] || fatal "--workspace-resource-group is required"
[[ -n "$workspace_name" ]] || fatal "--workspace-name is required"
[[ -n "$hf_repo_id" ]] || fatal "--hf-repo-id is required"
require_hf_pin "$hf_repo_id" "$hf_revision" "--hf-revision"
[[ -n "$bundle_dir" ]] || fatal "--bundle-dir is required"
[[ "$connectivity_mode" == "direct" || "$connectivity_mode" == "arc-ssh" ]] || \
  fatal "--connectivity-mode must be direct or arc-ssh"
if [[ "$connectivity_mode" == "direct" ]]; then
  [[ -n "$kubeconfig" ]] || fatal "--kubeconfig is required in direct mode"
  [[ -n "$context" ]] || fatal "--context is required in direct mode"
else
  [[ -n "$arc_server_resource_group" ]] || \
    fatal "--arc-server-resource-group is required in arc-ssh mode"
  [[ -n "$arc_server_name" ]] || fatal "--arc-server-name is required in arc-ssh mode"
fi

if [[ "$bundle_dir" != /* ]]; then
  bundle_dir="$REPO_ROOT/$bundle_dir"
fi
[[ "$bundle_dir" == "$GENERATED_ROOT/"* ]] || \
  fatal "--bundle-dir must be under infrastructure/setup/generated"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Arc Kubernetes" "$cluster_name"
  print_kv "Arc Resource Group" "$cluster_resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Workspace Resource Group" "$workspace_resource_group"
  print_kv "Hugging Face Source" "${hf_repo_id}@${hf_revision}"
  print_kv "Connectivity" "$connectivity_mode"
  print_kv "Namespace" "$namespace"
  print_kv "Upload Probe" "$([[ "$skip_upload_probe" == "true" ]] && echo skipped || echo enabled)"
  print_kv "Failure Injection" "$([[ "$inject_invalid_destinations" == "true" ]] && echo enabled || echo disabled)"
  print_kv "Preflight Image" "$PREFLIGHT_IMAGE"
  print_kv "Generated Bundle" "$bundle_dir"
  info "Config preview mode - exiting without changes"
  exit 0
fi

require_tools az curl getent jq mktemp
require_az_extension connectedk8s
require_az_extension ml
if [[ "$connectivity_mode" == "arc-ssh" ]]; then
  require_az_extension ssh
else
  require_tools kubectl
  [[ -f "$kubeconfig" && ! -L "$kubeconfig" ]] || \
    fatal "Kubeconfig must be a regular non-symlink file: $kubeconfig"
fi

mkdir -p "$bundle_dir"
results_file=$(mktemp)
trap cleanup EXIT

section "Operator-Side Preflight"
active_subscription=$(az account show --query id --output tsv 2>/dev/null || true)
if [[ "${active_subscription,,}" == "${subscription_id,,}" ]]; then
  record_result operator azure-session pass "Active subscription matches the Arc target"
else
  record_result operator azure-session fail "Active subscription does not match the Arc target"
fi

cluster_status=$(az connectedk8s show \
  --subscription "$subscription_id" \
  --resource-group "$cluster_resource_group" \
  --name "$cluster_name" \
  --query connectivityStatus \
  --output tsv 2>/dev/null || true)
if [[ "$cluster_status" == "Connected" ]]; then
  record_result operator arc-control-plane pass "Arc Kubernetes reports Connected"
else
  record_result operator arc-control-plane fail "Arc Kubernetes state is ${cluster_status:-unavailable}"
fi

workspace_id=$(az ml workspace show \
  --subscription "$workspace_subscription_id" \
  --resource-group "$workspace_resource_group" \
  --name "$workspace_name" \
  --query id \
  --output tsv 2>/dev/null || true)
if [[ -n "$workspace_id" ]]; then
  record_result operator azureml-workspace pass "Workspace metadata is readable"
else
  record_result operator azureml-workspace fail "Workspace metadata is unavailable"
fi

datastore_json=$(az ml datastore show \
  --subscription "$workspace_subscription_id" \
  --resource-group "$workspace_resource_group" \
  --workspace-name "$workspace_name" \
  --name workspaceblobstore \
  --output json 2>/dev/null || true)
storage_account=$(jq -r '.account_name // empty' <<< "$datastore_json")
storage_container=$(jq -r '.container_name // empty' <<< "$datastore_json")
if [[ -n "$storage_account" && -n "$storage_container" ]]; then
  record_result operator workspace-datastore pass "Default Blob datastore metadata is readable"
else
  record_result operator workspace-datastore fail "Default Blob datastore metadata is incomplete"
fi

workspace_json=$(az ml workspace show \
  --subscription "$workspace_subscription_id" \
  --resource-group "$workspace_resource_group" \
  --name "$workspace_name" \
  --output json 2>/dev/null || true)
registry_resource_id=$(jq -r '.container_registry // empty' <<< "$workspace_json")
registry_name="${registry_resource_id##*/}"
if [[ -n "$registry_resource_id" && -n "$registry_name" ]]; then
  record_result operator workspace-registry pass "Workspace registry metadata is readable"
else
  record_result operator workspace-registry fail "Workspace registry metadata is incomplete"
fi
key_vault_resource_id=$(jq -r '.key_vault // empty' <<< "$workspace_json")
key_vault_name="${key_vault_resource_id##*/}"
if [[ -n "$key_vault_resource_id" && -n "$key_vault_name" ]]; then
  record_result operator workspace-key-vault pass "Workspace Key Vault metadata is readable"
else
  fatal "Workspace Key Vault metadata is incomplete"
fi

storage_host="${storage_account}.blob.core.windows.net"
registry_host="${registry_name}.azurecr.io"
key_vault_host="${key_vault_name}.vault.azure.net"
storage_url="https://${storage_host}/${storage_container}?restype=container"
registry_url="https://${registry_host}/v2/"
key_vault_url="https://${key_vault_host}/"
hf_url="https://huggingface.co/${hf_repo_id}/resolve/${hf_revision}/config.json"

if [[ -n "$storage_account" && -n "$storage_container" ]]; then
  probe_operator_tls workspace-storage "$storage_host" "$storage_url"
fi
if [[ -n "$registry_name" ]]; then
  probe_operator_tls workspace-registry "$registry_host" "$registry_url"
fi
probe_operator_tls hugging-face huggingface.co "$hf_url"
probe_operator_tls key-vault "$key_vault_host" "$key_vault_url" true
if [[ "$inject_invalid_destinations" == "true" ]]; then
  probe_operator_failure
fi

if [[ "$skip_upload_probe" == "true" ]]; then
  record_result operator workspace-upload pass "Temporary Blob upload probe was explicitly skipped"
elif [[ -n "$storage_account" && -n "$storage_container" ]]; then
  probe_file=$(mktemp)
  printf 'azureml-network-preflight\n' > "$probe_file"
  probe_blob="network-preflight/operator-${RANDOM}-${RANDOM}.txt"
  if az storage blob upload \
      --account-name "$storage_account" \
      --container-name "$storage_container" \
      --name "$probe_blob" \
      --file "$probe_file" \
      --auth-mode login \
      --overwrite \
      --output none; then
    record_result operator workspace-upload pass "Temporary Blob upload succeeded"
    if ! az storage blob delete \
        --account-name "$storage_account" \
        --container-name "$storage_container" \
        --name "$probe_blob" \
        --auth-mode login \
        --output none; then
      record_result operator workspace-upload-cleanup fail "Temporary Blob cleanup failed"
    fi
  else
    record_result operator workspace-upload fail "Temporary Blob upload failed"
  fi
fi

section "Workload-Side Preflight"
pod_name="vla-network-preflight-$(date -u +%Y%m%d%H%M%S)"
cat <<EOF | run_kubectl apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: $pod_name
  namespace: $namespace
  labels:
    app.kubernetes.io/name: vla-network-preflight
spec:
  restartPolicy: Never
  containers:
    - name: preflight
      image: $PREFLIGHT_IMAGE
      imagePullPolicy: IfNotPresent
      command: ["/bin/sh", "-c"]
      args:
        - |
          failures=0
          check_url() {
            name="\$1"
            url="\$2"
            require_private="\${3:-false}"
            output=""
            exit_code=0
            output=\$(curl --silent --show-error --output /dev/null \
              --write-out 'http=%{http_code},remote_ip=%{remote_ip},tls=%{ssl_verify_result}' \
              --connect-timeout 10 --max-time 30 "\$url") || exit_code=\$?
            if [ "\$exit_code" -eq 0 ]; then
              remote_ip="\${output##*remote_ip=}"
              remote_ip="\${remote_ip%%,*}"
              if [ "\$require_private" = 'true' ]; then
                case "\$remote_ip" in
                  10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
                  *)
                    printf 'CHECK|%s|fail|category=dns,reason=public-remote-address\n' "\$name"
                    failures=1
                    return
                    ;;
                esac
              fi
              printf 'CHECK|%s|pass|%s\n' "\$name" "\$output"
              return
            fi
            case "\$exit_code" in
              6) category=dns ;;
              35|51|53|58|59|60|64|66|77|80|82|83|90|91) category=tls ;;
              *) category=connectivity ;;
            esac
            printf 'CHECK|%s|fail|category=%s,curl_exit=%s\n' \
              "\$name" "\$category" "\$exit_code"
            failures=1
          }
          check_url workspace-storage '$storage_url'
          check_url workspace-registry '$registry_url'
          check_url hugging-face '$hf_url'
          check_url key-vault '$key_vault_url' true
          if [ '$inject_invalid_destinations' = 'true' ]; then
            check_url failure-injection 'https://invalid.workload.invalid/'
          fi
          exit "\$failures"
EOF

pod_phase=""
for ((attempt = 1; attempt <= 30; attempt++)); do
  pod_json=$(run_kubectl get pod "$pod_name" --namespace "$namespace" --output json 2>/dev/null || true)
  pod_phase=$(jq -r '.status.phase // empty' <<< "$pod_json")
  [[ "$pod_phase" == "Succeeded" || "$pod_phase" == "Failed" ]] && break
  sleep 2
done
workload_failures_before="$failures"
workload_logs=$(run_kubectl logs "$pod_name" --namespace "$namespace" 2>/dev/null || true)
while IFS='|' read -r marker name result detail; do
  [[ "$marker" == "CHECK" ]] || continue
  record_result workload "$name" "$result" "$detail"
done <<< "$workload_logs"

if [[ "$pod_phase" != "Succeeded" && "$inject_invalid_destinations" != "true" && \
    "$failures" -eq "$workload_failures_before" ]]; then
  record_result workload pod-completion fail "Ephemeral preflight pod ended in ${pod_phase:-unknown}"
fi

if [[ "$inject_invalid_destinations" == "true" ]]; then
  report_path="$bundle_dir/azureml-network-preflight-failure-injection.json"
else
  report_path="$bundle_dir/azureml-network-preflight.json"
fi
jq -s \
  --arg schemaVersion "1" \
  --arg cluster "$cluster_name" \
  --arg workspace "$workspace_name" \
  --arg image "$PREFLIGHT_IMAGE" \
  --argjson failureCount "$failures" \
  '{
    schema_version: ($schemaVersion | tonumber),
    cluster: $cluster,
    workspace: $workspace,
    workload_image: $image,
    failure_count: $failureCount,
    checks: .
  }' "$results_file" > "$report_path"

section "Validation Summary"
print_kv "Operator Checks" "$(jq '[.checks[] | select(.side == "operator")] | length' "$report_path")"
print_kv "Workload Checks" "$(jq '[.checks[] | select(.side == "workload")] | length' "$report_path")"
print_kv "Failures" "$failures"
print_kv "Report" "$report_path"

(( failures == 0 )) || fatal "Azure ML network preflight failed $failures named check(s)"
info "Azure ML network preflight complete"
