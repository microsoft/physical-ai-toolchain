---
sidebar_position: 5
title: Cluster Setup
description: Kubernetes service deployment, AzureML extension, and OSMO platform configuration
author: Microsoft Robotics-AI Team
ms.date: 2026-10-03
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
# - OSMO:    ./03-deploy-osmo.sh --private-service-ip <unused-aks-subnet-ip>
```

> [!IMPORTANT]
> **Do not re-run `03-deploy-osmo.sh` against a Postgres database that already holds OSMO state from a previous AKS cluster.** On a cluster without a `mek-config` ConfigMap, script 03 creates a new Master Encryption Key. The new key cannot decrypt rows wrapped by the previous one, and OSMO will fail with `jwcrypto` `InvalidJWEData` / `InvalidTag` errors on login and on workflow submission. Reruns on the same cluster keep the existing key.
>
> If you destroyed and re-created AKS while preserving the Postgres flexible server, first drop and re-create the `osmo` database (or `TRUNCATE` the `configs`, `credential`, `ueks`, and `backends` tables) before running script 03 again.

<!-- -->

> [!NOTE]
> **Supported OSMO version.** This repository targets one OSMO release, **6.3** (chart `1.3.1`, image `6.3.1`; see [Component Inventory](../contributing/component-updates.md#component-inventory)). Support follows the current upstream release, and older versions aren't maintained here. To move an older install to 6.3, see [Upgrade from Earlier OSMO Releases](#-upgrade-from-earlier-osmo-releases).

## 🔐 Deployment Scenarios

Two OSMO deployment configurations are supported. Use workload identity by default. Add ACR only when you need private registry pulls.

### Default: Workload Identity

Use Azure Workload Identity for key-less authentication.

```bash
# terraform.tfvars
osmo_config = {
  should_enable_identity   = true
  should_federate_identity = true
  should_create_secret     = true
  control_plane_namespace  = "osmo-control-plane"
  operator_namespace       = "osmo-operator"
  workflows_namespace      = "osmo-workflows"
}
```

```bash
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo.sh --private-service-ip <unused-aks-subnet-ip>
```

Script `03-deploy-osmo.sh` auto-detects the OSMO managed identity from Terraform outputs and configures ServiceAccount annotations for the service and backend operator.

On a new cluster, pass `--private-service-ip` with a free address in the AKS subnet (`10.0.5.0/24` by default). Script 03 gives that address to the internal load balancer in front of OSMO, which VPN clients, the private DNS record, and HiL backends use. Pick an address that no node or other resource holds, outside the Kubernetes service CIDR and the five addresses Azure reserves in every subnet.

Later runs reuse the address when you omit the flag. Passing a different one moves the load balancer, so update the DNS record and HiL backends if you do.

### Workload Identity + Private ACR (Air-Gapped)

Enterprise deployment using private Azure Container Registry.

Before deploying, import the pinned OSMO images and Helm charts into the Terraform-deployed ACR. From `infrastructure/setup/`:

```bash
./import-osmo-to-acr.sh --environment <environment> --config-preview
./import-osmo-to-acr.sh --environment <environment>
```

The script imports the 11 OSMO images from `nvcr.io` with `az acr import`. It pulls the `service` and `backend-operator` charts from the NGC Helm repository, checks them against the SHA-256 values pinned in `defaults.conf`, and pushes them to `oci://<registry>/helm`. Every imported tag is locked against writes and deletes, and the image digests go into `generated/<environment>/osmo-images.json`.

Reruns reuse locked tags. Pushing charts and reading tags need data-plane access to the registry, so connect to the VPN first if the registry blocks public access.

```bash
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo.sh --use-acr \
  --image-manifest generated/<environment>/osmo-images.json \
  --private-service-ip <unused-aks-subnet-ip>
```

The OSMO gateway's Envoy image still comes from Docker Hub, so a cluster without internet access needs that image mirrored separately.

### Scenario Comparison

| Setting      | Workload Identity | Workload Identity + ACR |
|--------------|:-----------------:|:-----------------------:|
| Storage Auth | Workload Identity |    Workload Identity    |
| Registry     |      nvcr.io      |       Private ACR       |
| Air-Gap      |         ✗         |            ✓            |

## 🔄 Upgrade from Earlier OSMO Releases

`03-deploy-osmo.sh` stops when `osmo-control-plane` holds a pre-6.3 install: separate releases of the `service`, `router`, or `web-ui` charts, or an `osmo` release on an older chart line. Upgrade those installs, from 6.0-era builds through 6.2, with `infrastructure/setup/optional/upgrade-osmo.sh`, one confirmed stage per run:

| Path      | Stages, in order                                                 |
|-----------|------------------------------------------------------------------|
| Keep data | `backup`, `hop-6.2`, `tokens`, `export`, `hop-6.3`, and `verify` |
| Fresh     | `backup`, `reset`, `hop-6.3`, and `verify`                       |

Both paths end in ConfigMap mode, where config writes through the CLI or API return HTTP 409 and pools change through Helm values. See [OSMO Upgrade from Pre-6.3 Releases](osmo-upgrade.md) for prerequisites, the config review, HiL token renewal, and the rollback runbook.

## 🔒 Security Considerations

When deploying with `should_enable_private_endpoint = false`, cluster endpoints are publicly accessible. Secure the following components:

### AzureML Extension

The AzureML inference router (`azureml-fe`) handles incoming requests. For public deployments:

- Enable HTTPS with TLS certificates (`allowInsecureConnections=False`)
- Configure `sslSecret` or provide certificate files
- Consider using `internalLoadBalancerProvider=azure` for internal-only access

See [Secure Kubernetes online endpoints](https://learn.microsoft.com/azure/machine-learning/how-to-secure-kubernetes-online-endpoint) and [Inference routing configuration](https://learn.microsoft.com/azure/machine-learning/how-to-kubernetes-inference-routing-azureml-fe).

## 📜 Scripts

| Script                           | Purpose                                              |
|----------------------------------|------------------------------------------------------|
| `01-deploy-robotics-charts.sh`   | GPU Operator, KAI Scheduler                          |
| `02-deploy-azureml-extension.sh` | AzureML K8s extension, compute attach                |
| `03-deploy-osmo.sh`              | OSMO service, backend operator, platform config      |
| `import-osmo-to-acr.sh`          | Pinned OSMO images and charts in ACR, image manifest |

### Script Flags

| Flag                    | Scripts             | Description                                                            |
|-------------------------|---------------------|------------------------------------------------------------------------|
| `--use-acr`             | `03-deploy-osmo.sh` | Pull from Terraform-deployed ACR                                       |
| `--acr-name NAME`       | `03-deploy-osmo.sh` | Specify alternate ACR                                                  |
| `--image-manifest PATH` | `03-deploy-osmo.sh` | Image manifest from `import-osmo-to-acr.sh`, required with `--use-acr` |
| `--skip-backend`        | `03-deploy-osmo.sh` | Skip backend operator deployment                                       |
| `--config-preview`      | All                 | Print config and exit                                                  |

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

- [Cluster Operations](cluster-setup-advanced.md): accessing OSMO, troubleshooting, optional scripts
- [OSMO Upgrade from Pre-6.3 Releases](osmo-upgrade.md): staged upgrade, backups, and rollback
- [Cleanup and Destroy](cleanup.md): resource teardown procedures

---

🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.
