---
sidebar_position: 5
title: Cluster Setup
description: Kubernetes service deployment, AzureML extension, and OSMO platform configuration
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
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

- Terraform infrastructure deployed (`cd deploy/001-iac && terraform apply`)
- VPN connected (if using default private AKS cluster)
- Azure CLI authenticated (`az login`)
- kubectl, Helm 3.x, jq installed
- OSMO CLI (`osmo`) for backend deployment

> [!NOTE]
> Scripts automatically install required Azure CLI extensions (`k8s-extension`, `ml`) if missing.

<!-- markdownlint-disable-next-line MD028 -->

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
# - OSMO:    ./03-deploy-osmo-control-plane.sh && ./04-deploy-osmo-backend.sh
```

## 🔐 Deployment Scenarios

Three authentication and registry configurations are supported. Choose based on your security requirements.

### Scenario 1: Access Keys

Simplest setup using storage account keys and public NVIDIA registry.

```bash
# terraform.tfvars
osmo_config = {
  should_enable_identity   = false
  should_federate_identity = false
  control_plane_namespace  = "osmo-control-plane"
  operator_namespace       = "osmo-operator"
  workflows_namespace      = "osmo-workflows"
}
```

```bash
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo-control-plane.sh
./04-deploy-osmo-backend.sh --use-access-keys
```

### Scenario 2: Workload Identity

Secure, key-less authentication via Azure Workload Identity.

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
./03-deploy-osmo-control-plane.sh
./04-deploy-osmo-backend.sh
```

Scripts auto-detect the OSMO managed identity from Terraform outputs and configure ServiceAccount annotations.

### Scenario 3: Workload Identity + Private ACR (Air-Gapped)

Enterprise deployment using private Azure Container Registry.

**Pre-requisite**: Import images to ACR before deployment.

```bash
# Get ACR name and import images
cd ../001-iac
ACR_NAME=$(terraform output -json container_registry | jq -r '.value.name')
az acr login --name "$ACR_NAME"

# Set versions
OSMO_VERSION="${OSMO_VERSION:-6.0.0}"
CHART_VERSION="${CHART_VERSION:-1.0.0}"

OSMO_IMAGES=(
  service router web-ui worker logger agent
  backend-listener backend-worker client
  delayed-job-monitor init-container
)
for img in "${OSMO_IMAGES[@]}"; do
  az acr import --name "$ACR_NAME" \
    --source "nvcr.io/nvidia/osmo/${img}:${OSMO_VERSION}" \
    --image "osmo/${img}:${OSMO_VERSION}"
done

# Import Helm charts
for chart in osmo router ui backend-operator; do
  helm pull "oci://nvcr.io/nvidia/osmo/${chart}" --version "$CHART_VERSION"
  helm push "${chart}-${CHART_VERSION}.tgz" "oci://${ACR_NAME}.azurecr.io/helm"
  rm "${chart}-${CHART_VERSION}.tgz"
done
```

```bash
cd ../002-setup
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo-control-plane.sh --use-acr
./04-deploy-osmo-backend.sh --use-acr
```

### Scenario Comparison

|              | Access Keys | Workload Identity | Workload Identity + ACR |
|--------------|:-----------:|:-----------------:|:-----------------------:|
| Storage Auth | Access Keys | Workload Identity |    Workload Identity    |
| Registry     |   nvcr.io   |      nvcr.io      |       Private ACR       |
| Air-Gap      |      ✗      |         ✗         |            ✓            |

## 🔒 Security Considerations

When deploying with `should_enable_private_endpoint = false`, cluster endpoints are publicly accessible. Secure the following components:

### AzureML Extension

The AzureML inference router (`azureml-fe`) handles incoming requests. For public deployments:

- Enable HTTPS with TLS certificates (`allowInsecureConnections=False`)
- Configure `sslSecret` or provide certificate files
- Consider using `internalLoadBalancerProvider=azure` for internal-only access

See [Secure Kubernetes online endpoints](https://learn.microsoft.com/azure/machine-learning/how-to-secure-kubernetes-online-endpoint) and [Inference routing configuration](https://learn.microsoft.com/azure/machine-learning/how-to-kubernetes-inference-routing-azureml-fe).

### OSMO UI

The OSMO web interface requires authentication for public access:

- Enable Keycloak for user authentication and authorization
- Configure OIDC integration with Azure AD or other identity providers

See [OSMO Keycloak configuration](https://nvidia.github.io/OSMO/main/deployment_guide/getting_started/deploy_service.html#step-2-configure-keycloak).

## 📜 Scripts

| Script                            | Purpose                               |
|-----------------------------------|---------------------------------------|
| `01-deploy-robotics-charts.sh`    | GPU Operator, KAI Scheduler           |
| `02-deploy-azureml-extension.sh`  | AzureML K8s extension, compute attach |
| `03-deploy-osmo-control-plane.sh` | OSMO service, router, web-ui          |
| `04-deploy-osmo-backend.sh`       | Backend operator, workflow storage    |

### Script Flags

| Flag                | Scripts                                                        | Description                                       |
|---------------------|----------------------------------------------------------------|---------------------------------------------------|
| `--use-access-keys` | `04-deploy-osmo-backend.sh`                                    | Storage account keys instead of workload identity |
| `--use-acr`         | `03-deploy-osmo-control-plane.sh`, `04-deploy-osmo-backend.sh` | Pull from Terraform-deployed ACR                  |
| `--acr-name NAME`   | `03-deploy-osmo-control-plane.sh`, `04-deploy-osmo-backend.sh` | Specify alternate ACR                             |
| `--config-preview`  | All                                                            | Print config and exit                             |

## ⚙️ Configuration

Scripts read from Terraform outputs in `deploy/001-iac/`. Override with environment variables:

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

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
