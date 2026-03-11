---
sidebar_position: 10
title: Cleanup and Destroy
description: Remove cluster components, destroy Azure infrastructure, and clean up development environment
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: how-to
keywords:
  - cleanup
  - destroy
  - terraform
  - resource-group
---

Remove deployed cluster components, destroy Azure infrastructure, and clean up resources. Component cleanup preserves Azure infrastructure by default; destroy operations remove Terraform-managed resources.

> [!NOTE]
> This guide is part of the [Deploy Hub](README.md). Return there for the full deployment lifecycle.

## 📋 Cleanup Order

Run component cleanup before destroying infrastructure. Follow this order to avoid dependency issues.

| Step | Action                       | Detail                                |
|------|------------------------------|---------------------------------------|
| 1    | Uninstall OSMO Backend       | Backend operator, workflow namespaces |
| 2    | Uninstall OSMO Control Plane | OSMO service, router, web-ui          |
| 3    | Uninstall AzureML Extension  | ML extension, compute target, FICs    |
| 4    | Uninstall GPU Infrastructure | GPU Operator, KAI Scheduler           |
| 5    | Destroy VPN (if deployed)    | VPN Gateway, connections              |
| 6    | Destroy Main Infrastructure  | All Terraform-managed Azure resources |

## 🧹 Component Cleanup

Cleanup scripts remove Kubernetes resources from the AKS cluster without affecting Azure infrastructure.

| Script                                    | Removes                               |
|-------------------------------------------|---------------------------------------|
| `cleanup/uninstall-osmo-backend.sh`       | Backend operator, workflow namespaces |
| `cleanup/uninstall-osmo-control-plane.sh` | OSMO service, router, web-ui          |
| `cleanup/uninstall-azureml-extension.sh`  | ML extension, compute target, FICs    |
| `cleanup/uninstall-robotics-charts.sh`    | GPU Operator, KAI Scheduler           |

Run scripts from the `deploy/002-setup/cleanup/` directory:

```bash
cd deploy/002-setup/cleanup

./uninstall-osmo-backend.sh
./uninstall-osmo-control-plane.sh
./uninstall-azureml-extension.sh
./uninstall-robotics-charts.sh
```

### 📊 Data Preservation

Uninstall scripts preserve data by default. Use flags for complete removal.

| Script                            | Flag                  | Description                                    |
|-----------------------------------|-----------------------|------------------------------------------------|
| `uninstall-osmo-backend.sh`       | `--delete-container`  | Deletes blob container with workflow artifacts |
| `uninstall-osmo-control-plane.sh` | `--delete-mek`        | Removes encryption key ConfigMap               |
| `uninstall-osmo-control-plane.sh` | `--purge-postgres`    | Drops OSMO tables from PostgreSQL              |
| `uninstall-osmo-control-plane.sh` | `--purge-redis`       | Flushes OSMO keys from Redis                   |
| `uninstall-robotics-charts.sh`    | `--delete-namespaces` | Removes gpu-operator, kai-scheduler namespaces |
| `uninstall-robotics-charts.sh`    | `--delete-crds`       | Removes GPU Operator CRDs                      |

Full cleanup including all data:

```bash
cd deploy/002-setup/cleanup

./uninstall-osmo-backend.sh --delete-container
./uninstall-osmo-control-plane.sh --purge-postgres --purge-redis --delete-mek
./uninstall-azureml-extension.sh --force
./uninstall-robotics-charts.sh --delete-namespaces --delete-crds
```

Selective cleanup for specific components:

```bash
# OSMO only (preserve AzureML and GPU infrastructure)
./uninstall-osmo-backend.sh
./uninstall-osmo-control-plane.sh

# AzureML only (preserve OSMO)
./uninstall-azureml-extension.sh
```

## 🗑️ Destroy Infrastructure

After removing cluster components, destroy Azure infrastructure using one of two approaches.

### Terraform Destroy

Recommended approach. Preserves state files and allows clean redeployment.

```bash
cd deploy/001-iac

# Destroy VPN first (if deployed)
cd vpn && terraform destroy -var-file=terraform.tfvars && cd ..

# Preview changes
terraform plan -destroy -var-file=terraform.tfvars

# Destroy main infrastructure
terraform destroy -var-file=terraform.tfvars
```

### Delete Resource Group

Fastest cleanup method. Removes all resources regardless of how they were created.

```bash
# Get resource group name from Terraform outputs
terraform output -raw resource_group | jq -r '.name'

# Delete resource group
az group delete --name <resource-group-name> --yes --no-wait
```

> [!WARNING]
> Resource group deletion removes everything in the group, including resources not managed by Terraform. Terraform state becomes orphaned after this operation.

## 🔍 Troubleshooting

### Destroy Takes a Long Time

Terraform removes resources in dependency order. Private Endpoints, AKS clusters, and PostgreSQL servers take 5-10 minutes each. Full destruction typically takes 20-30 minutes.

Monitor remaining resources during destruction:

```bash
az resource list --resource-group <resource-group> \
  --query "[].{name:name, type:type}" -o table
```

### Soft-Deleted Resources Block Redeployment

Azure retains certain deleted resources in a soft-deleted state. Redeployment fails when Terraform creates a resource with the same name as a soft-deleted one.

| Resource           | Soft Delete      | Retention Period         | Blocks Redeployment             |
|--------------------|------------------|--------------------------|---------------------------------|
| Key Vault          | Mandatory        | 7-90 days (configurable) | Yes                             |
| Azure ML Workspace | Mandatory        | 14 days (fixed)          | Yes                             |
| Container Registry | Opt-in (preview) | 1-90 days (configurable) | No (disabled by default)        |
| Storage Account    | Recovery only    | 14 days                  | No (same-name creation allowed) |

Purge soft-deleted Key Vault:

```bash
az keyvault list-deleted --subscription <subscription-id> \
  --resource-type vault -o table

az keyvault purge --subscription <subscription-id> \
  --name <key-vault-name>
```

> [!NOTE]
> Key Vaults with `purge_protection_enabled = true` cannot be purged and must wait for retention expiry. This configuration defaults to `should_enable_purge_protection = false`.

Purge soft-deleted Azure ML Workspace:

```bash
az ml workspace delete \
  --name <workspace-name> \
  --resource-group <resource-group> \
  --permanently-delete
```

### Terraform State Mismatch

Resources manually deleted or created outside Terraform cause state mismatches.

Refresh state for resources deleted outside Terraform:

```bash
cd deploy/001-iac
terraform refresh -var-file=terraform.tfvars
terraform plan -var-file=terraform.tfvars
```

Import resources created outside Terraform into state:

```bash
terraform plan -var-file=terraform.tfvars

terraform import -var-file=terraform.tfvars \
  '<resource_address>' '<azure_resource_id>'
```

After import, run `terraform plan` to verify the imported resource matches configuration.

### Resource Locks Prevent Deletion

Management locks block deletion operations:

```bash
az lock list --resource-group <resource-group> -o table

az lock delete --name <lock-name> --resource-group <resource-group>
```

## 🔗 Related

* [Infrastructure Deployment](infrastructure.md)
* [Cluster Setup](cluster-setup.md)
* [Contributing Guide](../../CONTRIBUTING.md)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
