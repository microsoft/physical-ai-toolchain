---
title: "Quickstart: Clone to First Training Job"
description: Deploy infrastructure and submit your first robotics training job in 8 steps
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

| Requirement             | Details                                          |
|-------------------------|--------------------------------------------------|
| Azure subscription      | Contributor + User Access Administrator roles    |
| GPU quota               | `Standard_NC24ads_A100_v4` in target region      |
| NVIDIA NGC account      | Sign up at <https://ngc.nvidia.com/> for API key |
| Development environment | Devcontainer (recommended) or local tools        |

See [Prerequisites](../contributing/prerequisites.md) for installation commands and version requirements.

## Step 1: Clone and Set Up Environment

Clone the repository and initialize the development environment.

```bash
git clone https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture.git
cd azure-nvidia-robotics-reference-architecture
```

Use the devcontainer (recommended) or run local setup:

```bash
./setup-dev.sh
```

## Step 2: Configure Azure Subscription

Authenticate with Azure and register required resource providers.

```bash
source deploy/000-prerequisites/az-sub-init.sh
bash deploy/000-prerequisites/register-azure-providers.sh
```

Verify your subscription:

```bash
az account show --query "{name:name, id:id}" -o table
```

## Step 3: Configure Terraform Variables

Create a Terraform variables file for the full-public deployment path. From the repository root:

```bash
cd deploy/001-iac
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with these values:

```hcl
project_name = "robotics"
environment  = "dev"
location     = "eastus"
gpu_vm_size  = "Standard_NC24ads_A100_v4"

enable_azure_ml    = true
enable_osmo        = true
enable_vpn_gateway = false
enable_private_dns = false
```

> [!TIP]
> For private networking, set `enable_vpn_gateway = true` and `enable_private_dns = true`. See the [Infrastructure Guide](../../deploy/001-iac/README.md) for details.

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
  --resource-group "$(terraform output -raw resource_group_name)" \
  --name "$(terraform output -raw aks_cluster_name)"
```

## Step 5: Configure AKS Cluster

Deploy GPU Operator, KAI Scheduler, and the AzureML extension. From the repository root:

```bash
cd deploy/002-setup
bash 01-deploy-robotics-charts.sh
bash 02-deploy-azureml-extension.sh
```

Verify GPU operator pods:

```bash
kubectl get pods -n gpu-operator
```

## Step 6: Deploy OSMO Components

Deploy the OSMO control plane and backend using Access Keys authentication.

```bash
bash 03-deploy-osmo-control-plane.sh
bash 04-deploy-osmo-backend.sh --use-access-keys
```

Verify OSMO pods:

```bash
kubectl get pods -n osmo-control-plane
```

## Step 7: Submit First Training Job

Navigate to the scripts directory and submit a training job. From the repository root:

```bash
cd scripts
bash submit-osmo-training.sh
```

Scripts auto-detect configuration from Terraform outputs. Override values with CLI arguments or environment variables as needed. See [Scripts Reference](../reference/scripts.md) for all submission options.

## Step 8: Verify Results

Confirm the training job is running:

```bash
kubectl get pods -n osmo-control-plane --watch
```

Check OSMO training status through the OSMO web UI or query pod logs:

```bash
kubectl logs -n osmo-control-plane -l app=osmo-training --tail=50
```

## Cleanup

Destroy all infrastructure when finished to stop incurring costs. From the repository root:

```bash
cd deploy/001-iac
terraform destroy
```

See [Cost Considerations](../contributing/cost-considerations.md) for detailed pricing.

## Next Steps

| Resource                                                | Description                               |
|---------------------------------------------------------|-------------------------------------------|
| [LeRobot Inference](../inference/lerobot-inference.md)  | Run inference with trained LeRobot models |
| [MLflow Integration](../training/mlflow-integration.md) | Track experiments with MLflow             |
| [Deployment Guide](../../deploy/README.md)              | Full deployment reference and options     |
| [Contributing Guide](../contributing/README.md)         | Development workflow and code standards   |
