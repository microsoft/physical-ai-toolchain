#!/usr/bin/env bash
# Install AzureML extension on AKS cluster and attach as compute target
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

CONFIG_DIR="$SCRIPT_DIR/config"
MANIFESTS_DIR="$SCRIPT_DIR/manifests"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Install Azure Machine Learning extension on AKS and attach as compute target.

OPTIONS:
    -h, --help                Show this help message
    -t, --tf-dir DIR          Terraform directory (default: $DEFAULT_TF_DIR)
    --compute-name NAME       Compute target name (default: k8s-<suffix>)
    --fast-prod               Set cluster purpose to FastProd with HA inference router
    --enforce-resource-validation
                              Enforce aml-operator resource validation (default: disabled).
                              Disabled is required for scale-to-zero GPU node pools; otherwise
                              the operator refuses jobs whose InstanceType exceeds the largest
                              currently-Ready node, blocking the autoscaler from ever scaling
                              the pool up. Enable only on fixed-capacity clusters where you
                              want misconfigured InstanceTypes to fail fast at submission.
    --enforce-volcano-capacity-check
                              Re-enable Volcano's enqueue-time capacity check (default: disabled).
                              Disabled is required for scale-from-zero GPU node pools; otherwise
                              the volcano-scheduler's overcommit/proportion plugins refuse to
                              enqueue PodGroups whose requests exceed currently-Ready cluster
                              capacity, blocking the AKS autoscaler from ever seeing a Pending
                              Pod. Enable only on multi-tenant clusters where queue-level
                              capacity fairness must be enforced at submit time.
    --skip-attach             Skip attaching cluster as compute target
    --skip-instance-types     Skip creating GPU instance types
    --config-preview          Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --fast-prod
    $(basename "$0") --skip-attach --skip-instance-types
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
compute_name=""
cluster_purpose="DevTest"
inference_ha="false"
allow_insecure="true"
install_volcano="true"
install_prom_op="false"
skip_resource_validation="true"
enforce_volcano_capacity_check=false
skip_attach=false
skip_instance_types=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                       show_help; exit 0 ;;
    -t|--tf-dir)                     tf_dir="$2"; shift 2 ;;
    --compute-name)                  compute_name="$2"; shift 2 ;;
    --fast-prod)                     cluster_purpose="FastProd"; inference_ha="true"; allow_insecure="false"; shift ;;
    --enforce-resource-validation)   skip_resource_validation="false"; shift ;;
    --enforce-volcano-capacity-check) enforce_volcano_capacity_check=true; shift ;;
    --skip-attach)                   skip_attach=true; shift ;;
    --skip-instance-types)           skip_instance_types=true; shift ;;
    --config-preview)                config_preview=true; shift ;;
    *)                               fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform kubectl jq envsubst
require_az_extension k8s-extension
require_az_extension ml

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
cluster_id=$(tf_require "$tf_output" "aks_cluster.value.id" "AKS cluster ID")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
ml_workspace=$(tf_get "$tf_output" "azureml_workspace.value.name")
ml_identity_id=$(tf_get "$tf_output" "ml_workload_identity.value.id")

extension_name="azureml-$cluster"
if [[ -z "$compute_name" ]]; then
  compute_name="k8s-${cluster#aks-}"
  compute_name="${compute_name:0:16}"
  compute_name="${compute_name%-}"
fi
[[ -n "$ml_identity_id" ]] && ml_identity_name="${ml_identity_id##*/}"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  print_kv "Resource Group" "$rg"
  print_kv "Extension Name" "$extension_name"
  print_kv "Compute Name" "$compute_name"
  print_kv "Cluster Purpose" "$cluster_purpose"
  print_kv "Skip Resource Validation" "$skip_resource_validation"
  print_kv "Enforce Volcano Capacity Check" "$enforce_volcano_capacity_check"
  print_kv "ML Workspace" "${ml_workspace:-<not configured>}"
  print_kv "ML Identity" "${ml_identity_name:-<not configured>}"
  exit 0
fi

#------------------------------------------------------------------------------
# Validate Required Files
#------------------------------------------------------------------------------

config_template="$CONFIG_DIR/azureml-aks-config.template.json"
instance_types_manifest="$MANIFESTS_DIR/azureml-instance-types.yaml"

[[ -f "$config_template" ]] || fatal "Config template not found: $config_template"
[[ "$skip_instance_types" == "true" || -f "$instance_types_manifest" ]] || fatal "Instance types manifest not found: $instance_types_manifest"

mkdir -p "$CONFIG_DIR/out"

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Install AzureML Extension
#------------------------------------------------------------------------------
section "Install AzureML Extension"

export INFERENCE_ROUTER_HA="$inference_ha"
export ALLOW_INSECURE_CONNECTIONS="$allow_insecure"
export CLUSTER_PURPOSE="$cluster_purpose"
export INSTALL_VOLCANO="$install_volcano"
export INSTALL_PROM_OP="$install_prom_op"
export SKIP_RESOURCE_VALIDATION="$skip_resource_validation"

envsubst < "$config_template" > "$CONFIG_DIR/out/azureml-aks-config.json"

if az k8s-extension show --name "$extension_name" --cluster-type managedClusters \
    --cluster-name "$cluster" --resource-group "$rg" &>/dev/null; then
  info "Extension '$extension_name' already installed"
else
  info "Installing AzureML extension..."
  az k8s-extension create \
    --name "$extension_name" \
    --extension-type Microsoft.AzureML.Kubernetes \
    --cluster-type managedClusters \
    --cluster-name "$cluster" \
    --resource-group "$rg" \
    --scope cluster \
    --release-namespace "$NS_AZUREML" \
    --release-train stable \
    --config-file "$CONFIG_DIR/out/azureml-aks-config.json"
  sleep 30
fi

#------------------------------------------------------------------------------
# Configure Volcano Scheduler for Scale-from-Zero
#------------------------------------------------------------------------------
# The AzureML extension ships a Volcano config whose enqueue-time overcommit
# and proportion plugins block PodGroups whose requests exceed currently-Ready
# cluster capacity. That deadlocks scale-from-zero GPU pools because the Pod
# is never created, so the AKS autoscaler never sees a Pending Pod. Replace
# the configmap with a permissive enqueue config (proportion/overcommit
# removed; gang scheduling preserved at allocate) and restart the scheduler.
# Opt out with --enforce-volcano-capacity-check on multi-tenant clusters.

if [[ "$install_volcano" == "true" && "$enforce_volcano_capacity_check" == "false" ]]; then
  section "Configure Volcano Scheduler for Scale-from-Zero"

  volcano_cfg_src="$MANIFESTS_DIR/volcano-scheduler-config-scale-from-zero.conf"
  [[ -f "$volcano_cfg_src" ]] || fatal "Volcano scheduler config not found: $volcano_cfg_src"

  info "Waiting for volcano-scheduler-configmap to exist..."
  retries=30
  while ! kubectl get cm -n "$NS_AZUREML" volcano-scheduler-configmap &>/dev/null; do
    (( --retries > 0 )) || fatal "volcano-scheduler-configmap did not appear within 5 minutes; cannot deliver scale-from-zero"
    sleep 10
  done

  info "Applying scale-from-zero Volcano scheduler config (server-side apply, field-manager=hex-azureml-volcano-patch)..."
  # Server-side apply with a dedicated field manager + --force-conflicts:
  # registers ownership of data.volcano-scheduler.conf so subsequent
  # Helm-driven extension upgrades observe a conflict and leave our patch in
  # place instead of silently reverting it. Other fields (labels, annotations,
  # Helm metadata) stay owned by the AzureML extension Helm release.
  kubectl create configmap volcano-scheduler-configmap \
    -n "$NS_AZUREML" \
    --from-file=volcano-scheduler.conf="$volcano_cfg_src" \
    --dry-run=client -o yaml \
    | kubectl apply --server-side --field-manager=hex-azureml-volcano-patch --force-conflicts -f -

  if kubectl get deploy -n "$NS_AZUREML" volcano-scheduler &>/dev/null; then
    info "Restarting volcano-scheduler to pick up new config..."
    kubectl rollout restart -n "$NS_AZUREML" deploy/volcano-scheduler
    kubectl rollout status -n "$NS_AZUREML" deploy/volcano-scheduler --timeout=2m
  else
    warn "volcano-scheduler deployment not found; configmap will be picked up on next start"
  fi
fi

#------------------------------------------------------------------------------
# Create GPU Instance Types
#------------------------------------------------------------------------------

if [[ "$skip_instance_types" == "false" ]]; then
  section "Create GPU Instance Types"

  info "Waiting for InstanceType CRD..."
  retries=30
  while ! kubectl get crd instancetypes.amlarc.azureml.com &>/dev/null; do
    (( --retries > 0 )) || { warn "InstanceType CRD not available after 5 minutes; skipping"; break; }
    sleep 10
  done

  if (( retries > 0 )); then
    kubectl apply -f "$instance_types_manifest"
    info "Instance types applied"
  fi
fi

#------------------------------------------------------------------------------
# Create Federated Identity Credentials
#------------------------------------------------------------------------------

if [[ "$skip_attach" == "false" && -n "$ml_identity_id" ]]; then
  section "Create Federated Identity Credentials"

  oidc_issuer=$(az aks show -g "$rg" -n "$cluster" --query "oidcIssuerProfile.issuerUrl" -o tsv)
  [[ -z "$oidc_issuer" ]] && fatal "OIDC issuer not enabled on cluster"

  for sa in default training; do
    fic_name="aml-${sa}-fic"
    if az identity federated-credential show --identity-name "$ml_identity_name" \
        --resource-group "$rg" --name "$fic_name" &>/dev/null; then
      info "Federated credential '$fic_name' exists"
    else
      info "Creating federated credential for azureml:$sa..."
      az identity federated-credential create \
        --identity-name "$ml_identity_name" \
        --resource-group "$rg" \
        --name "$fic_name" \
        --issuer "$oidc_issuer" \
        --subject "system:serviceaccount:azureml:$sa" \
        --audiences "api://AzureADTokenExchange"
    fi
  done
fi

#------------------------------------------------------------------------------
# Attach Compute Target
#------------------------------------------------------------------------------

if [[ "$skip_attach" == "false" ]]; then
  [[ -z "$ml_workspace" ]] && fatal "ML workspace not found in terraform outputs"

  section "Attach Compute Target"

  if az ml compute show --name "$compute_name" -g "$rg" -w "$ml_workspace" &>/dev/null; then
    info "Compute '$compute_name' already attached"
  else
    info "Attaching AKS cluster as compute target..."
    attach_args=(-g "$rg" -w "$ml_workspace" --type Kubernetes --name "$compute_name" --resource-id "$cluster_id" --namespace "$NS_AZUREML")
    [[ -n "$ml_identity_id" ]] && attach_args+=(--identity-type UserAssigned --user-assigned-identities "$ml_identity_id") || attach_args+=(--identity-type SystemAssigned)
    az ml compute attach "${attach_args[@]}"
  fi
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Cluster" "$cluster"
print_kv "Extension" "$extension_name"
print_kv "Compute" "$compute_name"
print_kv "Purpose" "$cluster_purpose"
print_kv "Skip Resource Validation" "$skip_resource_validation"
print_kv "Enforce Volcano Capacity Check" "$enforce_volcano_capacity_check"
print_kv "ML Workspace" "${ml_workspace:-<not configured>}"
echo
kubectl get pods -n "$NS_AZUREML" --no-headers 2>/dev/null | head -5 || true

info "AzureML extension deployment complete"
