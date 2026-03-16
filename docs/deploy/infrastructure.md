---
sidebar_position: 3
title: Infrastructure Deployment
description: Terraform configuration and deployment for AKS, Azure ML, storage, and OSMO backend services
author: Microsoft Robotics-AI Team
ms.date: 2026-03-02
ms.topic: how-to
keywords:
  - terraform
  - infrastructure
  - aks
  - azure-ml
---

Terraform configuration for the robotics reference architecture. Deploys Azure resources including AKS with GPU node pools, Azure ML workspace, storage, and OSMO backend services.

> [!NOTE]
> This page is part of the [deployment guide](README.md). Return there for the full deployment sequence.

## 📋 Prerequisites

| Tool         | Version         | Setup or Check                  |
|--------------|-----------------|---------------------------------|
| Azure CLI    | Latest          | `az login`                      |
| Terraform    | 1.5+            | `terraform version`             |
| GPU VM quota | Region-specific | e.g., `Standard_NV36ads_A10_v5` |

### Azure RBAC Permissions

| Role                                    | Scope                                                 |
|-----------------------------------------|-------------------------------------------------------|
| Contributor                             | Subscription (new RG) or Resource Group (existing RG) |
| Role Based Access Control Administrator | Subscription (new RG) or Resource Group (existing RG) |

Terraform creates role assignments for managed identities, requiring `Microsoft.Authorization/roleAssignments/write` permission. The Contributor role explicitly blocks this action; the RBAC Administrator role provides it.

> [!NOTE]
> Use subscription scope if creating a new resource group (`should_create_resource_group = true`). Use resource group scope if the resource group already exists.

**Alternative**: Owner role (grants more permissions than required).

## 🚀 Quick Start

```bash
cd deploy/001-iac
source ../000-prerequisites/az-sub-init.sh
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply -var-file=terraform.tfvars
```

> [!IMPORTANT]
> The default configuration creates a **private AKS cluster** (`should_enable_private_aks_cluster = true`). After deploying infrastructure, you must deploy the [VPN Gateway](vpn.md) and connect before running `kubectl` commands or [cluster setup](cluster-setup.md) scripts.

## ⚙️ Configuration

### Core Variables

| Variable          | Description                              | Required            |
|-------------------|------------------------------------------|---------------------|
| `environment`     | Deployment environment (dev, test, prod) | Yes                 |
| `resource_prefix` | Resource naming prefix                   | Yes                 |
| `location`        | Azure region                             | Yes                 |
| `instance`        | Instance identifier                      | No (default: "001") |
| `tags`            | Resource group tags                      | No (default: {})    |

### AKS System Node Pool

| Variable                               | Description                              | Default            |
|----------------------------------------|------------------------------------------|--------------------|
| `system_node_pool_vm_size`             | VM size for AKS system node pool         | `Standard_D8ds_v5` |
| `system_node_pool_node_count`          | Number of nodes for AKS system node pool | `1`                |
| `system_node_pool_zones`               | Availability zones for system node pool  | `null`             |
| `system_node_pool_enable_auto_scaling` | Enable auto-scaling for system node pool | `false`            |
| `system_node_pool_min_count`           | Minimum nodes when auto-scaling enabled  | `null`             |
| `system_node_pool_max_count`           | Maximum nodes when auto-scaling enabled  | `null`             |

### Feature Flags

| Variable                              | Description                                               | Default |
|---------------------------------------|-----------------------------------------------------------|---------|
| `should_enable_nat_gateway`           | Deploy NAT Gateway for outbound connectivity              | `true`  |
| `should_enable_private_endpoint`      | Deploy private endpoints and DNS zones for Azure services | `true`  |
| `should_enable_private_aks_cluster`   | Make AKS API endpoint private (requires VPN for kubectl)  | `true`  |
| `should_enable_public_network_access` | Allow public access to resources                          | `true`  |
| `should_deploy_postgresql`            | Deploy PostgreSQL Flexible Server for OSMO                | `true`  |
| `should_deploy_redis`                 | Deploy Azure Managed Redis for OSMO                       | `true`  |
| `should_deploy_grafana`               | Deploy Azure Managed Grafana dashboard                    | `true`  |
| `should_deploy_monitor_workspace`     | Deploy Azure Monitor Workspace for Prometheus metrics     | `true`  |
| `should_deploy_ampls`                 | Deploy Azure Monitor Private Link Scope and endpoint      | `true`  |
| `should_deploy_dce`                   | Deploy Data Collection Endpoint for observability         | `true`  |
| `should_deploy_aml_compute`           | Deploy AzureML managed GPU compute cluster                | `false` |
| `should_include_aks_dns_zone`         | Include AKS private DNS zone in core DNS zones            | `true`  |

### Network Configuration Modes

Three deployment modes are supported based on security requirements:

#### Full Private (Default)

All Azure services use private endpoints and AKS has a private control plane. Requires VPN for all access.

```hcl
# terraform.tfvars (default values)
should_enable_private_endpoint    = true
should_enable_private_aks_cluster = true
```

Deploy VPN Gateway after infrastructure: `cd vpn && terraform apply`

#### Hybrid: Private Services, Public AKS

Azure services (Storage, Key Vault, ACR, PostgreSQL, Redis) use private endpoints, but AKS control plane is publicly accessible. No VPN required for `kubectl` access.

```hcl
# terraform.tfvars
should_enable_private_endpoint    = true
should_enable_private_aks_cluster = false
```

This mode provides security for Azure resources while allowing cluster management without VPN.

#### Full Public

All endpoints are publicly accessible. Not recommended for production without additional hardening.

```hcl
# terraform.tfvars
should_enable_private_endpoint    = false
should_enable_private_aks_cluster = false
```

> [!WARNING]
> Public endpoints expose services to the internet. When using this configuration, you **must** secure cluster workloads:
>
> **AzureML Extension**: Configure HTTPS and restrict access via inference router settings. See [Secure online endpoints](https://learn.microsoft.com/azure/machine-learning/how-to-secure-kubernetes-online-endpoint) and [Inference routing](https://learn.microsoft.com/azure/machine-learning/how-to-kubernetes-inference-routing-azureml-fe).
>
> **OSMO UI**: Enable Keycloak authentication to protect the web interface. See [OSMO Keycloak configuration](https://nvidia.github.io/OSMO/main/deployment_guide/getting_started/deploy_service.html#step-2-configure-keycloak).

### OSMO Workload Identity

Enable managed identity for OSMO services (recommended for production):

```hcl
osmo_config = {
  should_enable_identity   = true
  should_federate_identity = true
  control_plane_namespace  = "osmo-control-plane"
  operator_namespace       = "osmo-operator"
  workflows_namespace      = "osmo-workflows"
}
```

See [variables.tf](https://github.com/microsoft/physical-ai-toolchain/blob/main/deploy/001-iac/variables.tf) for all configuration options.

## 🔗 Related

- [Infrastructure Reference](infrastructure-reference.md) — architecture, modules, outputs, troubleshooting
- [VPN Gateway](vpn.md) — point-to-site VPN for private cluster access
- [Cleanup and Destroy](cleanup.md) — resource teardown procedures

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
