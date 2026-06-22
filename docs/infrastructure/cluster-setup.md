---
sidebar_position: 5
title: Cluster Setup
description: Kubernetes service deployment, AzureML extension, and OSMO platform configuration
author: Microsoft Robotics-AI Team
ms.date: 2026-06-19
ms.topic: how-to
keywords:
  - cluster-setup
  - kubernetes
  - azureml
  - osmo
---

AKS cluster configuration for robotics workloads with AzureML and NVIDIA OSMO.

> [!NOTE]
> This page is part of the [deployment guide](README.md). Return there for the full deployment sequence.

## 📋 Prerequisites

- Terraform infrastructure deployed (`cd infrastructure/terraform && terraform apply`)
- VPN connected (if using default private AKS cluster)
- Azure CLI authenticated (`az login`)
- kubectl, Helm 3.x, jq installed
- OSMO CLI (`osmo`) for OSMO deployment

> [!NOTE]
> Scripts automatically install required Azure CLI extensions (`k8s-extension`, `ml`) if missing.

<!-- -->

> [!IMPORTANT]
> The default infrastructure deploys a **private AKS cluster**. You must deploy the VPN Gateway and connect before running these scripts. See [VPN Gateway](vpn.md) for setup instructions. Without VPN, `kubectl` commands fail with `no such host` errors.
>
> To skip VPN, set `should_enable_private_aks_cluster = false` in your Terraform configuration. See [Network Configuration Modes](infrastructure.md#network-configuration-modes).

### Azure RBAC Permissions

| Role                                       | Scope           | Purpose                           |
|--------------------------------------------|-----------------|-----------------------------------|
| Azure Kubernetes Service Cluster User Role | AKS Cluster     | Get cluster credentials           |
| Contributor                                | Resource Group  | Extension and FIC creation        |
| Key Vault Secrets User                     | Key Vault       | Read PostgreSQL/Redis credentials |
| Storage Blob Data Contributor              | Storage Account | Create workflow containers        |

## 🚀 Quick Start

```bash
# Connect to cluster (values from terraform output)
az aks get-credentials --resource-group <rg> --name <aks>

# Verify connectivity (requires VPN for private clusters)
kubectl cluster-info
# Expected: Kubernetes control plane is running at https://...
# If you see "no such host" errors, connect to VPN first

# Deploy GPU infrastructure (required for all paths)
./01-deploy-robotics-charts.sh

# Choose your path:
# - AzureML: ./02-deploy-azureml-extension.sh
# - OSMO:    ./03-deploy-osmo.sh
```

> [!IMPORTANT]
> **Do not re-run `03-deploy-osmo.sh` against a Postgres database that already holds OSMO state from a previous AKS cluster.** Script 03 mints a fresh Master Encryption Key on every run; the new key cannot decrypt rows wrapped by the previous one, and OSMO will fail with `jwcrypto` `InvalidJWEData` / `InvalidTag` errors on login and on workflow submission.
>
> If you destroyed and re-created AKS while preserving the Postgres flexible server, first drop and re-create the `osmo` database (or `TRUNCATE` the `configs`, `credential`, `ueks`, and `backends` tables) before running script 03 again.

<!-- -->

> [!NOTE]
> **Supported OSMO version.** This repository targets a single current OSMO release — **6.3** (chart `1.3.0`, image `6.3.0`; see [Component Inventory](../contributing/component-updates.md#component-inventory)). Support tracks the current upstream release and may change as OSMO advances; older versions are not maintained here.

<!-- -->

> [!WARNING]
> **Upgrading from OSMO 6.2?** A direct rerun is not supported. OSMO 6.3 folds the standalone `router` and `web-ui` charts into the `service` chart, and `03-deploy-osmo.sh` now installs a single Helm release named `osmo` (replacing the previous `service`, `router`, and `ui` releases). It also defaults to ConfigMap mode (`services.configs.enabled: true`), under which CLI/API config writes return HTTP 409. Before deploying 6.3:
>
> 1. Export any database-stored config to Helm values with NVIDIA's `deployments/upgrades/export_configs_to_helm.py`, then fold it into `infrastructure/setup/values/osmo-platforms.yaml` (ConfigMap mode replaces the `osmo config` API).
> 2. Remove the legacy Helm releases so the new `osmo` release installs cleanly (adjust names/namespace to your install):
>    `helm uninstall web-ui router service -n osmo-control-plane`
> 3. Run `infrastructure/setup/03-deploy-osmo.sh`.
>
> See NVIDIA's [OSMO 6.3.0 release notes](https://github.com/NVIDIA/OSMO/blob/main/releases/6.3.0.md) for the full list of breaking changes (router/web-ui consolidation, squid-proxy sidecar removal, ConfigMap mode).

## 🔐 Deployment Scenarios

Two OSMO deployment configurations are supported. Use workload identity by default. Add ACR only when you need private registry pulls.

### Default: Workload Identity

Use Azure Workload Identity for key-less authentication.

```bash
# terraform.tfvars
osmo_config = {
  should_enable_identity   = true
  should_federate_identity = true
  control_plane_namespace  = "osmo-control-plane"
  operator_namespace       = "osmo-operator"
  workflows_namespace      = "osmo-workflows"
}
```

```bash
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo.sh
```

Script `03-deploy-osmo.sh` auto-detects the OSMO managed identity from Terraform outputs and configures ServiceAccount annotations for the service and backend operator.

### Workload Identity + Private ACR (Air-Gapped)

Enterprise deployment using private Azure Container Registry.

Prerequisite: import images to ACR before deployment.

```bash
# Get ACR name and import images
cd ../001-iac
ACR_NAME=$(terraform output -json container_registry | jq -r '.value.name')
az acr login --name "$ACR_NAME"

# Set versions
OSMO_VERSION="${OSMO_VERSION:-6.3.0}"
CHART_VERSION="${CHART_VERSION:-1.3.0}"

OSMO_IMAGES=(
  service worker logger agent
  backend-listener backend-worker client
  delayed-job-monitor init-container
)
for img in "${OSMO_IMAGES[@]}"; do
  az acr import --name "$ACR_NAME" \
    --source "nvcr.io/nvidia/osmo/${img}:${OSMO_VERSION}" \
    --image "osmo/${img}:${OSMO_VERSION}"
done

# Import Helm charts
for chart in osmo backend-operator; do
  helm pull "oci://nvcr.io/nvidia/osmo/${chart}" --version "$CHART_VERSION"
  helm push "${chart}-${CHART_VERSION}.tgz" "oci://${ACR_NAME}.azurecr.io/helm"
  rm "${chart}-${CHART_VERSION}.tgz"
done
```

```bash
cd ../002-setup
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo.sh --use-acr
```

### Scenario Comparison

|              | Workload Identity | Workload Identity + ACR |
|--------------|:-----------------:|:-----------------------:|
| Storage Auth | Workload Identity |    Workload Identity    |
| Registry     |      nvcr.io      |       Private ACR       |
| Air-Gap      |         ✗         |            ✓            |

## 🔒 Security Considerations

When deploying with `should_enable_private_endpoint = false`, cluster endpoints are publicly accessible. Secure the following components:

### AzureML Extension

The AzureML inference router (`azureml-fe`) handles incoming requests. For public deployments:

- Enable HTTPS with TLS certificates (`allowInsecureConnections=False`)
- Configure `sslSecret` or provide certificate files
- Consider using `internalLoadBalancerProvider=azure` for internal-only access

See [Secure Kubernetes online endpoints](https://learn.microsoft.com/azure/machine-learning/how-to-secure-kubernetes-online-endpoint) and [Inference routing configuration](https://learn.microsoft.com/azure/machine-learning/how-to-kubernetes-inference-routing-azureml-fe).

## 📜 Scripts

| Script                           | Purpose                                         |
|----------------------------------|-------------------------------------------------|
| `01-deploy-robotics-charts.sh`   | GPU Operator, KAI Scheduler                     |
| `02-deploy-azureml-extension.sh` | AzureML K8s extension, compute attach           |
| `03-deploy-osmo.sh`              | OSMO service, backend operator, platform config |

### Script Flags

| Flag               | Scripts             | Description                      |
|--------------------|---------------------|----------------------------------|
| `--use-acr`        | `03-deploy-osmo.sh` | Pull from Terraform-deployed ACR |
| `--acr-name NAME`  | `03-deploy-osmo.sh` | Specify alternate ACR            |
| `--skip-backend`   | `03-deploy-osmo.sh` | Skip backend operator deployment |
| `--config-preview` | All                 | Print config and exit            |

## ⚙️ Configuration

Scripts read from Terraform outputs in `infrastructure/terraform/`. Override with environment variables:

| Variable                | Description        |
|-------------------------|--------------------|
| `AZURE_SUBSCRIPTION_ID` | Azure subscription |
| `AZURE_RESOURCE_GROUP`  | Resource group     |
| `AKS_CLUSTER_NAME`      | Cluster name       |

## ✅ Verification

```bash
# Check pods
kubectl get pods -n gpu-operator
kubectl get pods -n azureml
kubectl get pods -n osmo-control-plane
kubectl get pods -n osmo-operator

# Workload identity (if enabled)
kubectl get sa -n osmo-control-plane osmo-control-plane -o yaml | grep azure.workload.identity
```

## 🔗 Related

- [Cluster Operations](cluster-setup-advanced.md) — accessing OSMO, troubleshooting, optional scripts
- [Cleanup and Destroy](cleanup.md) — resource teardown procedures
