---
sidebar_position: 2
title: "Quickstart: Clone to First Training Job"
description: Deploy infrastructure and submit your first robotics training job in 9 steps
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: tutorial
keywords:
  - quickstart
  - deployment
  - training
  - tutorial
---

Deploy the full Azure NVIDIA Robotics stack and submit a training job in ~1.5-2 hours. This guide uses full-public networking and Access Keys authentication for the simplest path.

> [!NOTE]
> This guide expands on the [Getting Started hub](README.md).

## Prerequisites

| Requirement             | Details                                                                                             |
|-------------------------|-----------------------------------------------------------------------------------------------------|
| Azure subscription      | Contributor + User Access Administrator roles                                                       |
| GPU quota               | `Standard_NV36ads_A10_v5` (A10 Spot, default) or `Standard_NC40ads_H100_v5` (H100) in target region |
| NVIDIA NGC account      | Sign up at <https://ngc.nvidia.com/> for API key                                                    |
| Development environment | Devcontainer (recommended) or local tools                                                           |

See [Prerequisites](../contributing/prerequisites.md) for installation commands and version requirements.

## Step 1: Clone and Set Up Environment

Clone the repository and initialize the development environment.

```bash
git clone https://github.com/microsoft/physical-ai-toolchain.git
cd physical-ai-toolchain
```

Use the devcontainer (recommended) or run local setup:

```bash
./setup-dev.sh
```

## Step 2: Configure Azure Subscription

Authenticate with Azure and register required resource providers.

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
bash infrastructure/terraform/prerequisites/register-azure-providers.sh
```

Verify your subscription:

```bash
az account show --query "{name:name, id:id}" -o table
```

## Step 3: Configure Terraform Variables

Create a Terraform variables file for the full-public deployment path. From the repository root:

```bash
cd infrastructure/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your values:

```hcl
environment     = "dev"
location        = "westus3"
resource_prefix = "yourprefix"
instance        = "001"

// Full-public networking (simplest path)
should_enable_private_endpoint    = false
should_enable_private_aks_cluster = false

// Single GPU pool (Spot A10)
node_pools = {
  gpu = {
    vm_size                    = "Standard_NV36ads_A10_v5"
    subnet_address_prefixes    = ["10.0.7.0/24"]
    node_taints                = ["nvidia.com/gpu:NoSchedule", "kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
    gpu_driver                 = "Install"
    priority                   = "Spot"
    should_enable_auto_scaling = true
    min_count                  = 1
    max_count                  = 1
    zones                      = []
    eviction_policy            = "Delete"
  }
}

// OSMO Backend Services
should_deploy_postgresql = true
should_deploy_redis      = true
```

> [!WARNING]
> `resource_prefix` must be lowercase, alphanumeric, and short (6-8 characters recommended). It feeds into Key Vault (`kv{prefix}{env}{instance}`) and Storage Account names that have 24-character limits and must be globally unique.

<!-- markdownlint-disable-next-line MD028 -->

> [!TIP]
> For private networking, set `should_enable_private_endpoint = true` and `should_enable_private_aks_cluster = true`, then deploy the VPN from `infrastructure/terraform/vpn/` before running any `kubectl` commands. See the [Infrastructure Guide](../infrastructure/README.md) for details.

## Step 4: Deploy Infrastructure

Initialize and apply the Terraform configuration. This step takes ~30-40 minutes.

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Verify deployment:

```bash
terraform output
```

Connect to the AKS cluster:

```bash
az aks get-credentials \
  --resource-group "$(terraform output -json resource_group | jq -r '.name')" \
  --name "$(terraform output -json aks_cluster | jq -r '.name')"
```

## Step 5: Set NGC API Key

Export your NVIDIA NGC API key for OSMO backend deployment. Obtain a key from <https://ngc.nvidia.com/>.

```bash
export NGC_API_KEY="<your-ngc-api-key>"
```

## Step 6: Configure AKS Cluster

Deploy GPU Operator, KAI Scheduler, and the AzureML extension. From the repository root:

```bash
cd infrastructure/setup
bash 01-deploy-robotics-charts.sh --config-preview
bash 01-deploy-robotics-charts.sh
bash 02-deploy-azureml-extension.sh --config-preview
bash 02-deploy-azureml-extension.sh
```

> [!TIP]
> All setup scripts support `--config-preview` to print configuration and exit without changes. Run it before each real deployment to verify values.

Verify GPU operator pods:

```bash
kubectl get pods -n gpu-operator
```

## Step 7: Deploy OSMO Components

Deploy the OSMO control plane and backend using Access Keys authentication.

```bash
bash 03-deploy-osmo-control-plane.sh --config-preview
bash 03-deploy-osmo-control-plane.sh
bash 04-deploy-osmo-backend.sh --use-access-keys --config-preview
bash 04-deploy-osmo-backend.sh --use-access-keys
```

Verify OSMO pods:

```bash
kubectl get pods -n osmo-control-plane
```

## Step 8: Submit First Training Job

Submit a training job from the repository root:

```bash
bash training/rl/scripts/submit-osmo-training.sh
```

Scripts auto-detect configuration from Terraform outputs. Override values with CLI arguments or environment variables as needed. See [Scripts Reference](../reference/scripts.md) for all submission options.

## Step 9: Verify Results

Confirm the training job is running:

```bash
kubectl get pods -n osmo-workflows --watch
```

Check OSMO training status through the OSMO web UI or query pod logs:

```bash
kubectl logs -n osmo-workflows -l app=osmo-training --tail=50
```

## Cleanup

Remove OSMO Helm releases before destroying infrastructure to avoid orphaned resources:

```bash
cd infrastructure/setup
helm uninstall backend-operator -n osmo-operator --ignore-not-found
helm uninstall osmo-service osmo-router osmo-web-ui -n osmo-control-plane --ignore-not-found
```

Destroy all infrastructure when finished to stop incurring costs. From the repository root:

```bash
cd infrastructure/terraform
terraform destroy
```

See [Cost Considerations](../contributing/cost-considerations.md) for detailed pricing.

## Next Steps

| Resource                                                | Description                             |
|---------------------------------------------------------|-----------------------------------------|
| [MLflow Integration](../training/mlflow-integration.md) | Track experiments with MLflow           |
| [Infrastructure Guide](../infrastructure/README.md)     | Full deployment reference and options   |
| [Contributing Guide](../contributing/README.md)         | Development workflow and code standards |
