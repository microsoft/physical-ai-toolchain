---
sidebar_position: 4
title: Troubleshooting Guide
description: Symptom-based resolution guide for common errors in the robotics reference architecture
author: Microsoft Robotics-AI Team
ms.date: 2026-03-09
ms.topic: troubleshooting
keywords:
  - troubleshooting
  - errors
  - debugging
  - gpu
  - deployment
  - kubernetes
---

Find the symptom you are experiencing, then follow the resolution steps. Start with the quick diagnostics checklist to narrow down the failure category.

## Quick Diagnostics Checklist

| Check              | Command                                        | Expected           |
|--------------------|------------------------------------------------|--------------------|
| Cluster reachable  | `kubectl get nodes`                            | Node list returned |
| GPU available      | `kubectl describe node \| grep nvidia.com/gpu` | GPU count > 0      |
| AzureML extension  | `kubectl get pods -n azureml`                  | All pods Running   |
| OSMO control plane | `kubectl get pods -n osmo-control-plane`       | All pods Running   |
| VPN connected      | `ping <private-endpoint-ip>`                   | Response received  |

## Connection Errors

### kubectl commands hang or return "Unable to connect to the server"

**Cause:** The default deployment creates a private AKS cluster. The API server is not reachable without an active VPN connection.

**Resolution:**

1. Verify VPN connection status in the Azure portal under the VPN Gateway resource.
2. Reconnect using the VPN client profile downloaded during [VPN setup](../deploy/vpn.md).
3. Confirm connectivity with `kubectl get nodes`.

### DNS resolution fails for private endpoints

**Cause:** Private DNS zones are not linked to the VPN virtual network, or the client DNS resolver is not forwarding to Azure DNS.

**Resolution:**

1. Verify the private DNS zone link exists: `az network private-dns link vnet list --zone-name <zone> -g <rg>`.
2. On the client machine, flush DNS cache and retry.
3. For persistent failures, add manual host entries from the private endpoint IP addresses.

### kubectl returns "Unauthorized" or "Forbidden"

**Cause:** Azure RBAC role assignment is missing or the kubeconfig token has expired.

**Resolution:**

1. Refresh credentials: `az aks get-credentials --resource-group <rg> --name <cluster> --overwrite-existing`.
2. Verify your Azure AD identity has `Azure Kubernetes Service Cluster User Role` on the cluster resource.

### OSMO UI not reachable at expected URL

**Cause:** DNS zone for the OSMO service URL is not configured, or the ingress controller internal load balancer has no IP assigned.

**Resolution:**

1. Check the ingress service IP: `kubectl get svc -n osmo-control-plane`.
2. Verify DNS records in the private DNS zone match the load balancer IP.
3. See [Private DNS](../deploy/dns.md) for DNS zone deployment.

## GPU and CUDA Errors

### CUDA_ERROR_NO_DEVICE on RTX PRO 6000 nodes

**Cause:** MIG strategy is set to `none` instead of `single`. Azure vGPU hosts enable MIG, and `strategy: none` causes `NVIDIA_VISIBLE_DEVICES` to receive bare GPU UUIDs instead of MIG device UUIDs.

**Resolution:**

Set `mig.strategy: single` in the GPU Operator Helm values for RTX PRO 6000 node pools. See [GPU Configuration](../reference/gpu-configuration.md) for node-specific settings.

> [!WARNING]
> RTX PRO 6000 nodes require `mig.strategy: single`. Using `none` causes all GPU workloads on these nodes to fail with `CUDA_ERROR_NO_DEVICE`.

### GPU Operator attempts to install drivers on GRID driver nodes

**Cause:** Nodes with pre-installed Azure GRID drivers (`580.105.08-grid-azure`) do not need the GPU Operator datacenter driver. Installing both causes conflicts.

**Resolution:**

Label GRID driver nodes with `nvidia.com/gpu.deploy.driver=false` to prevent the GPU Operator from deploying its own driver DaemonSet.

### Vulkan initialization fails in Isaac Sim containers

**Cause:** The `NVIDIA_DRIVER_CAPABILITIES` environment variable is not set to `all`. Isaac Sim requires Vulkan capability for rendering.

**Resolution:**

Set `NVIDIA_DRIVER_CAPABILITIES=all` in the job environment variables. This is required for all Isaac Sim workloads regardless of GPU type.

### nvidia-smi shows no GPUs inside the container

**Cause:** The container runtime is not configured with the NVIDIA runtime class, or GPU resource requests are missing from the pod spec.

**Resolution:**

1. Verify the pod spec includes `resources.limits: nvidia.com/gpu: 1`.
2. Confirm the NVIDIA device plugin is running: `kubectl get pods -n gpu-operator`.
3. Check node allocatable GPU count: `kubectl describe node <node> | grep nvidia.com/gpu`.

### Driver version mismatch between host and container

**Cause:** The GPU Operator installed a driver version incompatible with the CUDA toolkit version in the container image.

**Resolution:**

1. Check the host driver version: `nvidia-smi` on the node.
2. Verify compatibility with the [CUDA compatibility matrix](https://docs.nvidia.com/deploy/cuda-compatibility/).
3. Pin the GPU Operator driver version to match container requirements in the Helm values.

## Deployment Failures

### Terraform provider registration fails

**Cause:** Required Azure resource providers are not registered on the subscription.

**Resolution:**

Run `source deploy/000-prerequisites/az-sub-init.sh` to register all required providers. The script reads from `deploy/000-prerequisites/robotics-azure-resource-providers.txt`.

### Terraform plan fails with "subscription not configured"

**Cause:** The `ARM_SUBSCRIPTION_ID` environment variable is not set.

**Resolution:**

Run `source deploy/000-prerequisites/az-sub-init.sh` before any `terraform` commands. This script exports `ARM_SUBSCRIPTION_ID` and validates Azure CLI authentication.

### Helm chart installation fails with connection refused

**Cause:** The VPN is not connected, or the deploy scripts are running before the VPN Gateway deployment completes.

**Resolution:**

1. Complete VPN deployment: `deploy/001-iac/vpn/`.
2. Connect the VPN client.
3. Re-run deploy scripts in order: `01-deploy-robotics-charts.sh` through `04-deploy-osmo-backend.sh`.

### AzureML extension pods stuck in CrashLoopBackOff

**Cause:** Identity or RBAC misconfiguration for the AzureML managed identity, or resource quota exceeded.

**Resolution:**

1. Check pod logs: `kubectl logs <pod> -n azureml`.
2. Verify the managed identity has federated credentials for the `azureml:default` and `azureml:training` service accounts.
3. Check subscription quota: `az vm list-usage --location <region> -o table`.

### OSMO backend deployment returns oauth2Proxy errors

**Cause:** `oauth2Proxy.enabled` is set to `true` but no OIDC provider is configured.

**Resolution:**

Set `oauth2Proxy.enabled: false` in the OSMO Helm values when no OIDC provider is available. See `deploy/002-setup/04-deploy-osmo-backend.sh` for the configuration.

### Resource group creation fails with quota errors

**Cause:** Subscription-level resource group limit or regional capacity constraints.

**Resolution:**

1. Check current limits: `az account list-locations` and `az vm list-usage --location <region>`.
2. Request quota increases through the Azure portal for the target region.

## Training and Inference Errors

### Isaac Sim job fails with EULA not accepted

**Cause:** The environment variables `ACCEPT_EULA` and `PRIVACY_CONSENT` are not set to `Y`.

**Resolution:**

Add both variables to the job definition:

```yaml
environment_variables:
  ACCEPT_EULA: "Y"
  PRIVACY_CONSENT: "Y"
```

### AzureML model download fails with authentication error

**Cause:** Workload identity auth failure in the `data-capability` sidecar when using `ro_mount` mode.

**Resolution:**

Switch model validation mode from `ro_mount` to `download` in the AzureML job YAML. This is a known workaround for workload identity compatibility.

### numpy ImportError or ABI mismatch in Isaac Sim container

**Cause:** numpy 2.x is installed but Isaac Sim 4.x requires numpy < 2.0.0 for ABI compatibility with its bundled libraries.

**Resolution:**

The `train.sh` script pins numpy to `>=1.26.0,<2.0.0`. Verify this pin is present. If using a custom entrypoint, add:

```bash
uv pip install "numpy>=1.26.0,<2.0.0"
```

### Isaac Sim process hangs after training completes

**Cause:** Isaac Sim 4.x hangs after `env.close()` on vGPU nodes due to a shutdown bug.

**Resolution:**

Use `simulation_shutdown.py` which stops the simulation timeline and applies a SIGKILL watchdog to force process termination.

### Checkpoint upload fails silently

**Cause:** The `TRAINING_CHECKPOINT_OUTPUT` environment variable is not set or points to a non-existent directory.

**Resolution:**

1. Verify the environment variable is set in the job definition.
2. Confirm the output path is writable during training.
3. Check job logs for upload errors after training completes.

## Workflow Runtime Errors

### OSMO workflow submission fails with payload too large

**Cause:** Base64-encoded archive exceeds the ~1 MB payload limit.

**Resolution:**

Switch from inline payload to dataset folder injection. Upload files as an OSMO dataset and reference the dataset folder name in the workflow environment variables.

### OSMO workflow YAML template rendering fails

**Cause:** OSMO uses Jinja templates (`{{ }}`). Helm Go template syntax (`{{ .Values }}`) causes parse errors.

**Resolution:**

Convert all template expressions to Jinja syntax. For variable substitution, use `{{ env_var }}` patterns.

### KAI scheduler rejects multi-GPU job

**Cause:** Coscheduling (gang-scheduling) requirements are not met. Either insufficient GPU resources or the PodGroup configuration is missing.

**Resolution:**

1. Verify available GPU capacity across nodes: `kubectl describe nodes | grep nvidia.com/gpu`.
2. Confirm the KAI scheduler is installed and configured for coscheduling in the OSMO backend.
3. Reduce GPU request count or wait for node autoscaling to provide capacity.

### OSMO dataset injection fails

**Cause:** The dataset folder name in the workflow YAML does not match the registered dataset name, or the dataset version is not published.

**Resolution:**

1. List available datasets: `osmo config list DATASET`.
2. Verify the dataset name and version in the workflow environment variables match a published dataset.

### OSMO workflow pods stuck in Pending

**Cause:** The `osmo-workflows` namespace lacks resource quota or node affinity rules prevent scheduling.

**Resolution:**

1. Check pod events: `kubectl describe pod <pod> -n osmo-workflows`.
2. Verify node taints and tolerations match the pod spec.
3. Check namespace resource quotas: `kubectl get resourcequota -n osmo-workflows`.

## Additional Resources

- [GPU Configuration](../reference/gpu-configuration.md)
- [AzureML Validation Job Debugging](./azureml-validation-job-debugging.md)
- [Security Guide](security-guide.md)
- [Deployment Validation](../contributing/deployment-validation.md)
- [NVIDIA CUDA Compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/)
- [Azure AKS Troubleshooting](https://learn.microsoft.com/azure/aks/troubleshooting)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
