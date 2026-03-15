---
title: AzureML Validation Job Debugging
sidebar_label: Validation Job Debugging
sidebar_position: 2
description: Troubleshooting guide for AzureML validation job failures and common issues.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-12
ms.topic: troubleshooting
---

## AzureML Validation Job Debugging Summary

**Date**: December 3, 2025
**Branch**: `feat/azureml-job-support`
**Status**: 🔄 In Progress - Blocked on Storage Authentication

## Objective

Enable policy validation workflow using `osmorobo-validate.sh` to validate trained IsaacLab policies registered in Azure Machine Learning.

## Original Problem

After successfully training an IsaacLab policy using `osmorobo-submit.sh`, the user attempted to validate the trained policy using `osmorobo-validate.sh`. The validation script submits an AzureML command job that:

1. Takes a registered model as input (`isaaclab-anymal-latest:2`)
2. Runs validation episodes using the IsaacLab container
3. Reports success/failure metrics

### Initial Error: Invalid YAML Schema

When first submitting the validation job, AzureML rejected the job YAML with schema validation errors:

```text
ValidationError: The value 'string' of input type is not valid for Command job.
Supported types are ['uri_file', 'uri_folder', 'mlflow_model', 'custom_model', 'mltable', 'triton_model']
```

**Cause**: The `isaaclab-validate.yaml` template was using typed literal inputs (`type: string`, `type: integer`) which are valid for AzureML Pipeline jobs but NOT for Command jobs. Command jobs expect literal inputs as simple key-value pairs without type declarations.

### Second Error: Empty String Not Allowed

After fixing the input types, a second error occurred:

```text
ValidationError: Empty string is not allowed for input value
```

**Cause**: The YAML had `task: ""` and `framework: ""` as default values. AzureML doesn't allow empty strings for input values.

### Third Error: Permission Denied on Model Mount

After fixing the YAML schema, the job submitted successfully but failed at runtime:

```text
Error Code: ScriptExecution.StreamAccess.Authentication
permission denied when access stream
```

**Cause**: The AzureML extension's `data-capability` sidecar couldn't authenticate to Azure Blob Storage to mount/download the registered model. This is due to the storage account having `allowSharedKeyAccess = false` and workload identity not being properly configured.

## Debugging Steps Taken

### Step 1: Fix YAML Input Schema

**Problem**: Command job doesn't support typed literal inputs.

**Investigation**: Researched AzureML command job YAML schema using Microsoft documentation. Discovered that command jobs use a different input format than pipeline jobs.

**Fix**: Changed from:

```yaml
inputs:
  task:
    type: string
    default: ""
```

To:

```yaml
inputs:
  task: auto  # Simple key-value, no type declaration
```

Also changed model input from `type: mlflow_model` to `type: custom_model` since the trained checkpoint isn't MLflow format.

### Step 2: Fix Empty String Values

**Problem**: AzureML rejects empty strings as input values.

**Fix**: Changed empty strings to sentinel values:

- `task: ""` → `task: auto`
- `framework: ""` → `framework: auto`

The validation script handles `auto` as "detect from model metadata".

### Step 3: Investigate Permission Denied Error

**Problem**: Jobs fail with `ScriptExecution.StreamAccess.Authentication` when trying to access the model from blob storage.

**Investigation Steps**:

1. **Checked ML Identity Role Assignments**:

   ```bash
   az role assignment list --assignee $ML_PRINCIPAL_ID --scope $STORAGE_ID
   ```

   Result: `Storage Blob Data Contributor` ✅ assigned

2. **Checked Federated Identity Credentials**:

   ```bash
   az identity federated-credential list --identity-name id-ml-osmorobo-tst-001
   ```

   Result: Empty `[]` - **No federated credentials existed!**

3. **Checked Terraform State**:

   ```bash
   terraform state list | grep federated
   ```

   Result: No matches - federated credentials weren't being created

4. **Root Cause Discovery**: The `azureml_config` object in `main.tf` was missing:
   - `should_install_extension` (defaults to `false`)
   - `should_federate_ml_identity` (defaults to `false`)

   This caused the federated credential resources to have `count = 0`.

### Step 4: Create Federated Identity Credentials

**Action**: Manually created federated credentials via Azure CLI:

```bash
AKS_OIDC_ISSUER=$(az aks show --name aks-osmorobo-tst-001 ... --query oidcIssuerProfile.issuerUrl)

az identity federated-credential create \
  --name "aml-default-fic" \
  --identity-name "id-ml-osmorobo-tst-001" \
  --issuer "$AKS_OIDC_ISSUER" \
  --subject "system:serviceaccount:azureml:default" \
  --audiences "api://AzureADTokenExchange"
```

**Result**: Credentials created, but jobs still failed.

### Step 5: Check AzureML Extension Configuration

**Investigation**:

```bash
az k8s-extension show ... --query "configurationSettings.identityType"
```

Result: `null` - Extension wasn't configured with user-assigned identity!

**Action**: Updated extension:

```bash
az k8s-extension update \
  --configuration-settings \
    "identityType=UserAssigned" \
    "userAssignedIdentityResourceId=..."
```

**Result**: Extension updated, but jobs still failed.

### Step 6: Annotate Kubernetes Service Accounts

**Investigation**: Checked if service accounts had workload identity annotations:

```bash
kubectl get serviceaccount -n azureml default -o yaml
```

Result: No `azure.workload.identity` annotations.

**Action**: Added annotations:

```bash
kubectl annotate serviceaccount -n azureml default \
  azure.workload.identity/client-id="$ML_CLIENT_ID" --overwrite
kubectl label serviceaccount -n azureml default \
  azure.workload.identity/use=true --overwrite
```

**Result**: Jobs still failed with same error.

### Step 7: Analyze Pod Logs

**Investigation**: Examined data-capability container logs:

```bash
kubectl logs -n azureml $POD -c data-capability
```

**Key Finding**:

```text
Failed to get symmetric key for getting AML token: 'failed to load certificate:
stat /tmp/azureml/cr/j/.../sha1-.pfx: no such file or directory'
```

This revealed that the data-capability container is trying to use certificate-based authentication rather than workload identity, despite all the correct configurations being in place.

### Step 8: Attempted Storage Key Access Enable (Blocked)

**Investigation**: Tried to enable shared key access as a workaround:

```bash
az storage account update --name stosmorobotst001 --allow-shared-key-access true
```

**Result**: Setting remains `false` - controlled by Terraform configuration (`should_enable_storage_shared_access_key = false` by default).

## Changes Made

### 1. Terraform Configuration Updates

#### [infrastructure/terraform/main.tf](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/terraform/main.tf)

Added missing fields to `azureml_config` to enable extension installation and workload identity federation:

```terraform
azureml_config = {
  should_integrate_aks               = var.should_integrate_aks_cluster
  should_install_extension           = var.should_integrate_aks_cluster  // NEW: Enable extension when integrating
  should_federate_ml_identity        = var.should_integrate_aks_cluster  // NEW: Enable workload identity federation
  aks_cluster_purpose                = var.aks_cluster_purpose
  inference_router_service_type      = var.inference_router_service_type
  workload_tolerations               = var.workload_tolerations
  cluster_integration_instance_types = var.cluster_integration_instance_types
}
```

**Impact**: These fields were previously missing, causing federated identity credentials to not be created (`count = 0`).

### 2. AzureML Job YAML Schema Fixes

#### [workflows/azureml/validate.yaml](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/azureml/validate.yaml)

Fixed input schema to comply with AzureML command job requirements:

| Change           | Before                              | After                                        |
|------------------|-------------------------------------|----------------------------------------------|
| Model input type | `type: mlflow_model`                | `type: custom_model`                         |
| Literal inputs   | Had `type: string`, `type: integer` | Removed type declarations (simple key-value) |
| Empty strings    | `task: ""`                          | `task: auto` (sentinel value)                |
| Mount mode       | `mode: ro_mount`                    | `mode: download` (attempted workaround)      |

**Rationale**: AzureML command jobs don't support typed literal inputs like pipeline jobs do.

### 3. Azure Infrastructure Changes (Manual via CLI)

#### Federated Identity Credentials Created

```bash
az identity federated-credential create \
  --name "aml-default-fic" \
  --identity-name "id-ml-osmorobo-tst-001" \
  --resource-group "rg-osmorobo-tst-001" \
  --issuer "https://westus3.oic.prod-aks.azure.com/..." \
  --subject "system:serviceaccount:azureml:default" \
  --audiences "api://AzureADTokenExchange"

az identity federated-credential create \
  --name "aml-training-fic" \
  --identity-name "id-ml-osmorobo-tst-001" \
  --resource-group "rg-osmorobo-tst-001" \
  --issuer "https://westus3.oic.prod-aks.azure.com/..." \
  --subject "system:serviceaccount:azureml:training" \
  --audiences "api://AzureADTokenExchange"
```

#### AzureML Extension Updated

```bash
az k8s-extension update \
  --cluster-name aks-osmorobo-tst-001 \
  --cluster-type managedClusters \
  --resource-group rg-osmorobo-tst-001 \
  --name azureml-aks-osmorobo-tst-001 \
  --configuration-settings \
    "identityType=UserAssigned" \
    "userAssignedIdentityResourceId=/subscriptions/.../id-ml-osmorobo-tst-001"
```

#### Kubernetes Service Account Annotations

```bash
kubectl annotate serviceaccount -n azureml default \
  azure.workload.identity/client-id="afbecdd1-1eb2-4fed-8043-b88a15f25154" --overwrite
kubectl label serviceaccount -n azureml default \
  azure.workload.identity/use=true --overwrite
```

## Current Issue

### Storage Authentication Failure

**Error Code**: `ScriptExecution.StreamAccess.Authentication`

```text
permission denied when access stream. Reason: None
PermissionDenied(None)
Error Message: Authentication failed when trying to access the stream.
```

**Root Cause Analysis**:

| Factor                                            | Status                           |
|---------------------------------------------------|----------------------------------|
| Storage account `allowSharedKeyAccess`            | `false` (security best practice) |
| ML Identity has `Storage Blob Data Contributor`   | ✅ Verified                       |
| Federated Identity Credentials exist              | ✅ Created                        |
| AzureML Extension has `identityType=UserAssigned` | ✅ Configured                     |
| Data-capability using workload identity           | ❌ **Not working**                |

The AzureML extension's `data-capability` sidecar container is not properly authenticating to Azure Blob Storage using workload identity federation. Despite all the correct configurations being in place, the token exchange isn't happening.

### Evidence from Pod Logs

```text
Failed to get symmetric key for getting AML token: 'failed to load certificate:
stat /tmp/azureml/cr/j/.../sha1-.pfx: no such file or directory'
```

This suggests the data-capability container is still trying to use certificate-based auth rather than workload identity.

## Jobs Submitted (All Failed)

| Job Name                  | Status | Error                                      |
|---------------------------|--------|--------------------------------------------|
| `sharp_pen_l66lnkmpfy`    | Failed | Permission denied (before FIC creation)    |
| `tough_brick_y9qw8npw1m`  | Failed | Permission denied (after FIC creation)     |
| `olden_table_61c5q9vx4k`  | Failed | Permission denied (after SA annotation)    |
| `frosty_arch_jtzh3fky15`  | Failed | Permission denied (after extension update) |
| `blue_cabbage_qjqy4kv8y9` | Failed | Permission denied (with download mode)     |

## Options to Resolve

### Option 1: Enable Storage Shared Key Access (Quick Fix)

Add to `terraform.tfvars`:

```terraform
should_enable_storage_shared_access_key = true
```

Then run:

```bash
cd infrastructure/terraform
terraform apply -var-file=terraform.tfvars
```

**Pros**: Quick, will unblock validation workflow
**Cons**: Reduces security posture (shared keys are less secure than managed identity)

### Option 2: Use AML SDK for Model Download (Code Change)

Modify `validate.sh` to use Python AzureML SDK to download the model within the container, which can leverage the pod's MSI endpoint:

```python
from azure.ai.ml import MLClient
from azure.identity import ManagedIdentityCredential

credential = ManagedIdentityCredential()
ml_client = MLClient(credential, subscription_id, resource_group, workspace_name)
ml_client.models.download(name="isaaclab-anymal-latest", version="2", download_path="/mnt/model")
```

**Pros**: Maintains security posture
**Cons**: Requires code changes, more complex

### Option 3: Escalate to Microsoft Support

The workload identity integration with AzureML Kubernetes extension may have a bug or require additional configuration not documented.

**Pros**: Proper fix
**Cons**: Time-consuming, may take days/weeks

### Option 4: Use AML Managed Compute Instead of AKS

Submit jobs to AzureML managed compute (e.g., `gpu-cluster`) instead of the attached Kubernetes compute.

**Pros**: Simpler authentication model
**Cons**: Loses GPU node pool flexibility, may need to provision separate GPU compute

## Recommended Next Steps

1. **Immediate**: Implement Option 1 (enable shared key access) to unblock validation testing
2. **Short-term**: File Microsoft support ticket for workload identity + AzureML extension issue
3. **Medium-term**: Implement Option 2 as a more secure long-term solution
4. **Documentation**: Update deployment docs to note this limitation

## Files Changed Summary

| File                              | Change Type                                                     |
|-----------------------------------|-----------------------------------------------------------------|
| `infrastructure/terraform/main.tf`          | Added `should_install_extension`, `should_federate_ml_identity` |
| `workflows/azureml/validate.yaml` | Fixed input schema, changed mount to download                   |

## Related Resources

- [AzureML Kubernetes Compute Troubleshooting](https://learn.microsoft.com/azure/machine-learning/how-to-attach-kubernetes-anywhere)
- [Workload Identity Federation](https://learn.microsoft.com/azure/aks/workload-identity-overview)
- [Storage Account Shared Key Disabled](https://learn.microsoft.com/azure/storage/common/shared-key-authorization-prevent)
