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
readonly VOLCANO_SCALE_FROM_ZERO_CONFIGMAP="volcano-scheduler-scale-from-zero"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Install Azure Machine Learning extension on AKS and attach as compute target.

OPTIONS:
    -h, --help                Show this help message
    -t, --tf-dir DIR          Terraform directory (default: $DEFAULT_TF_DIR)
    --kubeconfig PATH         Isolated AKS kubeconfig output
    --context NAME            Explicit AKS context (default: cluster name)
    --compute-name NAME       Compute target name (default: k8s-<suffix>)
    --instance-types-manifest PATH
                  InstanceType manifest (default: manifests/azureml-instance-types.yaml)
    --fast-prod               Set cluster purpose to FastProd
    --enforce-resource-validation
                              Enforce aml-operator resource validation (default: disabled,
                              amloperator.skipResourceValidation=true).
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
                              capacity fairness must be enforced at submit time. By default the
                              extension reads the $VOLCANO_SCALE_FROM_ZERO_CONFIGMAP
                              configmap; this option resets it to the chart's config.
    --skip-attach             Skip attaching cluster as compute target
    --skip-instance-types     Skip creating GPU instance types
    --config-preview          Print configuration and exit

The deployment-profile and scale-from-zero choices are extension settings.
Reruns compare them with the live extension and update only the keys that differ,
including on extensions installed before these settings existed.

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --fast-prod
    $(basename "$0") --skip-attach --skip-instance-types
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
kubeconfig=""
context=""
compute_name=""
instance_types_manifest="$MANIFESTS_DIR/azureml-instance-types.yaml"
cluster_purpose="DevTest"
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
    --kubeconfig)                    kubeconfig="$2"; shift 2 ;;
    --context)                       context="$2"; shift 2 ;;
    --compute-name)                  compute_name="$2"; shift 2 ;;
    --instance-types-manifest)       instance_types_manifest="$2"; shift 2 ;;
    --fast-prod)                     cluster_purpose="FastProd"; shift ;;
    --enforce-resource-validation)   skip_resource_validation="false"; shift ;;
    --enforce-volcano-capacity-check) enforce_volcano_capacity_check=true; shift ;;
    --skip-attach)                   skip_attach=true; shift ;;
    --skip-instance-types)           skip_instance_types=true; shift ;;
    --config-preview)                config_preview=true; shift ;;
    *)                               fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform kubectl jq envsubst

desired_volcano_configmap=""
if [[ "$install_volcano" == "true" && "$enforce_volcano_capacity_check" == "false" ]]; then
  desired_volcano_configmap="$VOLCANO_SCALE_FROM_ZERO_CONFIGMAP"
fi

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
cluster_id=$(tf_require "$tf_output" "aks_cluster.value.id" "AKS cluster ID")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
kubeconfig="${kubeconfig:-$HOME/.kube/physical-ai-toolchain/${cluster}.yaml}"
context="${context:-$cluster}"
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
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Context" "$context"
  print_kv "Resource Group" "$rg"
  print_kv "Extension Name" "$extension_name"
  print_kv "Compute Name" "$compute_name"
  print_kv "Cluster Purpose" "$cluster_purpose"
  print_kv "Nginx Ingress" "Enabled for OSMO"
  print_kv "Skip Resource Validation" "$skip_resource_validation"
  print_kv "Enforce Volcano Capacity Check" "$enforce_volcano_capacity_check"
  print_kv "Volcano Config Map" "${desired_volcano_configmap:-chart default}"
  print_kv "Extension Settings" "Compared with the live extension at run time"
  print_kv "ML Workspace" "${ml_workspace:-<not configured>}"
  print_kv "ML Identity" "${ml_identity_name:-<not configured>}"
  print_kv "Instance Types" "$instance_types_manifest"
  exit 0
fi

verify_aks_resource_id "$tf_output" "$cluster_id"
verify_existing_aks_kubeconfig "$kubeconfig" "$context" "$cluster_id"
require_az_extension k8s-extension
require_az_extension ml

#------------------------------------------------------------------------------
# Validate Required Files
#------------------------------------------------------------------------------

config_template="$CONFIG_DIR/azureml-aks-config.template.json"

[[ -f "$config_template" ]] || fatal "Config template not found: $config_template"
[[ "$skip_instance_types" == "true" || (-f "$instance_types_manifest" && ! -L "$instance_types_manifest") ]] || \
  fatal "Instance types manifest must be a regular non-symlink file: $instance_types_manifest"

mkdir -p "$CONFIG_DIR/out"

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster" "$kubeconfig" "$context"

#------------------------------------------------------------------------------
# Install AzureML Extension
#------------------------------------------------------------------------------
section "Install AzureML Extension"

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
# Reconcile AzureML Extension Settings
#------------------------------------------------------------------------------
# The repository deployment profile requires these extension settings:
# - enableInference=false, so the extension does not deploy azureml-fe.
# - nginxIngress.enabled=true, so OSMO can use the extension-managed ingress.
# Scale-from-zero GPU pools also need two settings:
# - amloperator.skipResourceValidation=true, so the operator admits jobs whose
#   InstanceType exceeds the largest currently-Ready node instead of failing them.
# - volcanoScheduler.schedulerConfigMap naming a dedicated configmap whose enqueue
#   config drops the overcommit and proportion checks (gang scheduling stays at
#   allocate), so the autoscaler sees Pending pods.
# Extension settings survive the extension's automatic Helm upgrades, which
# restore chart-owned configmaps. New and existing extensions converge here, and
# the update names only the keys that differ.
section "Reconcile AzureML Extension Settings"

settings_updated="none"
volcano_configmap_changed=false

if [[ -n "$desired_volcano_configmap" ]]; then
  volcano_cfg_src="$MANIFESTS_DIR/volcano-scheduler-config-scale-from-zero.conf"
  [[ -f "$volcano_cfg_src" ]] || fatal "Volcano scheduler config not found: $volcano_cfg_src"
  ensure_namespace "$kubeconfig" "$context" "$NS_AZUREML"
  info "Applying configmap $desired_volcano_configmap..."
  configmap_apply_output=$(kubectl create configmap "$desired_volcano_configmap" \
    -n "$NS_AZUREML" \
    --from-file=volcano-scheduler.conf="$volcano_cfg_src" \
    --dry-run=client -o yaml | kubectl apply -f -)
  echo "$configmap_apply_output"
  [[ "$configmap_apply_output" == *" unchanged" ]] || volcano_configmap_changed=true
fi

current_settings=$(az k8s-extension show --name "$extension_name" --cluster-type managedClusters \
  --cluster-name "$cluster" --resource-group "$rg" --query configurationSettings -o json)
desired_enable_inference=$(jq -r '.enableInference' "$CONFIG_DIR/out/azureml-aks-config.json")
desired_nginx_ingress=$(jq -r '.["nginxIngress.enabled"]' "$CONFIG_DIR/out/azureml-aks-config.json")
current_enable_inference=$(jq -r '.enableInference // ""' <<< "$current_settings")
current_nginx_ingress=$(jq -r '.["nginxIngress.enabled"] // ""' <<< "$current_settings")
current_skip_resource_validation=$(jq -r '.["amloperator.skipResourceValidation"] // ""' <<< "$current_settings")
current_volcano_configmap=$(jq -r '.["volcanoScheduler.schedulerConfigMap"] // ""' <<< "$current_settings")

settings_patch='{}'
if [[ "$current_enable_inference" != "$desired_enable_inference" ]]; then
  settings_patch=$(jq -c --arg value "$desired_enable_inference" \
    '. + {"enableInference": $value}' <<< "$settings_patch")
fi
if [[ "$current_nginx_ingress" != "$desired_nginx_ingress" ]]; then
  settings_patch=$(jq -c --arg value "$desired_nginx_ingress" \
    '. + {"nginxIngress.enabled": $value}' <<< "$settings_patch")
fi
if [[ "$current_skip_resource_validation" != "$skip_resource_validation" ]]; then
  settings_patch=$(jq -c --arg value "$skip_resource_validation" \
    '. + {"amloperator.skipResourceValidation": $value}' <<< "$settings_patch")
fi
if [[ "$install_volcano" == "true" && "$current_volcano_configmap" != "$desired_volcano_configmap" ]]; then
  settings_patch=$(jq -c --arg value "$desired_volcano_configmap" \
    '. + {"volcanoScheduler.schedulerConfigMap": $value}' <<< "$settings_patch")
fi

if [[ "$settings_patch" == "{}" ]]; then
  info "Extension settings already match"
else
  # A settings file carries empty values, which reset a key to the chart default.
  settings_file="$CONFIG_DIR/out/azureml-extension-settings-update.json"
  printf '%s\n' "$settings_patch" > "$settings_file"
  settings_updated=$(jq -r 'to_entries | map("\(.key)=\(.value)") | join(", ")' "$settings_file")
  info "Updating extension settings: $settings_updated"
  az k8s-extension update \
    --name "$extension_name" \
    --cluster-type managedClusters \
    --cluster-name "$cluster" \
    --resource-group "$rg" \
    --config-file "$settings_file" \
    --yes \
    --output none
fi

# The extension update rolls the Volcano pods when it changes the configmap
# reference; otherwise restart them so a changed configmap takes effect.
if [[ "$volcano_configmap_changed" == "true" ]] && \
    ! jq -e 'has("volcanoScheduler.schedulerConfigMap")' <<< "$settings_patch" >/dev/null; then
  if kubectl get deploy -n "$NS_AZUREML" volcano-scheduler &>/dev/null; then
    info "Restarting volcano-scheduler to pick up the updated config..."
    kubectl rollout restart -n "$NS_AZUREML" deploy/volcano-scheduler
    kubectl rollout status -n "$NS_AZUREML" deploy/volcano-scheduler --timeout=2m
  else
    warn "volcano-scheduler deployment not found; the configmap is read on next start"
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

  compute_state=$(az ml compute show --name "$compute_name" -g "$rg" -w "$ml_workspace" --query "provisioning_state" -o tsv 2>/dev/null || echo "NotFound")
  if [[ "$compute_state" == "Succeeded" ]]; then
    info "Compute '$compute_name' already attached"
  else
    if [[ "$compute_state" == "Failed" ]]; then
      info "Detaching failed compute '$compute_name'..."
      az ml compute detach --name "$compute_name" -g "$rg" -w "$ml_workspace" -y
    fi

    # az ml compute attach calls listClusterAdminCredential on the AML backend,
    # which requires local accounts. Temporarily enable if disabled.
    local_accounts_disabled=$(az aks show -g "$rg" -n "$cluster" --query "disableLocalAccounts" -o tsv 2>/dev/null || echo "false")
    if [[ "$local_accounts_disabled" == "true" ]]; then
      info "Temporarily enabling local accounts for compute attachment..."
      az aks update -g "$rg" -n "$cluster" --enable-local-accounts -o none
      # Safety net: re-disable on any exit (including errexit failures).
      trap 'az aks update -g "$rg" -n "$cluster" --disable-local-accounts -o none 2>/dev/null || true; trap - EXIT' EXIT
    fi

    info "Attaching AKS cluster as compute target..."
    attach_args=(-g "$rg" -w "$ml_workspace" --type Kubernetes --name "$compute_name" --resource-id "$cluster_id" --namespace "$NS_AZUREML")
    [[ -n "$ml_identity_id" ]] && attach_args+=(--identity-type UserAssigned --user-assigned-identities "$ml_identity_id") || attach_args+=(--identity-type SystemAssigned)
    az ml compute attach "${attach_args[@]}"

    if [[ "$local_accounts_disabled" == "true" ]]; then
      info "Re-disabling local accounts..."
      az aks update -g "$rg" -n "$cluster" --disable-local-accounts -o none
      trap - EXIT  # normal path handled re-disable; clear safety trap
    fi
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
print_kv "Volcano Config Map" "${desired_volcano_configmap:-chart default}"
print_kv "Settings Updated" "$settings_updated"
print_kv "ML Workspace" "${ml_workspace:-<not configured>}"
print_kv "Instance Types" "$instance_types_manifest"
echo
kubectl get pods -n "$NS_AZUREML" --no-headers 2>/dev/null | head -5 || true

info "AzureML extension deployment complete"
