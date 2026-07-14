---
title: Cluster Setup
description: AKS cluster configuration with NVIDIA GPU operator, KAI Scheduler, and AzureML extension
author: Microsoft Robotics-AI Team
ms.date: 2026-07-14
ms.topic: how-to
keywords:
  - cluster-setup
  - kubernetes
  - azureml
---

AKS cluster configuration for robotics workloads. Deploys NVIDIA GPU operator, KAI Scheduler, AzureML extension, and OSMO components onto the AKS cluster provisioned in the infrastructure phase.

> [!NOTE]
> Complete setup walkthrough, deployment scenarios, and troubleshooting are in the [Cluster Setup](../../docs/infrastructure/cluster-setup.md) guide.

## 🚀 Quick Start

Each script writes AKS credentials to an isolated kubeconfig and requires an explicit context for Kubernetes and Helm operations.

Deployment order:

1. `./01-deploy-robotics-charts.sh` — GPU Operator, KAI Scheduler
2. `./02-deploy-azureml-extension.sh` — AzureML K8s extension, compute attach
3. `./03-deploy-osmo.sh` — OSMO control plane and backend operator
4. `./04-deploy-osmo-external-backend.sh` — Optional external Ubuntu K3s backend

## 📦 Environment Bundles

Generate environment-specific deployment details with the `environment-deployment` agent skill. The skill reads Terraform outputs and uses available Azure CLI, kubectl, Helm, and OSMO read-only commands to create a validated bundle under the gitignored `infrastructure/setup/generated/<environment>/` directory.

The bundle contains non-secret metadata and generated manifests. It never contains Terraform state, kubeconfigs, OSMO profiles, tokens, registry credentials, or VPN credentials.

Run each deployment preview with explicit generated inputs:

```bash
./02-deploy-azureml-extension.sh \
  --instance-types-manifest generated/<environment>/azureml-instance-types.yaml \
  --config-preview

./03-deploy-osmo.sh \
  --platform-values generated/<environment>/osmo-platforms.yaml \
  --use-acr \
  --image-manifest generated/<environment>/osmo-images.json \
  --config-preview
```

Upload the allowlisted bundle to Key Vault from the trusted deployment host:

```bash
./upload-environment-bundle.sh --environment <environment> --config-preview
./upload-environment-bundle.sh --environment <environment>
```

Download it to a protected directory on the HiL host, then configure isolated AKS and OSMO profiles:

```bash
./download-environment-bundle.sh \
  --environment <environment> \
  --resource-group <resource-group> \
  --config-preview

./download-environment-bundle.sh \
  --environment <environment> \
  --resource-group <resource-group>

./connect-environment.sh --environment <environment> --config-preview
./connect-environment.sh --environment <environment>
```

The upload identity requires Key Vault secret write permission. The HiL identity requires the Key Vault Secrets User role and network access to private Azure endpoints. These scripts do not grant roles or modify deployed services.

## 📖 Documentation

| Guide                                                                                      | Description                                       |
|--------------------------------------------------------------------------------------------|---------------------------------------------------|
| [Cluster Setup](../../docs/infrastructure/cluster-setup.md)                                | Full setup walkthrough and deployment scenarios   |
| [Cluster Operations](../../docs/infrastructure/cluster-setup-advanced.md)                  | Advanced operations, scaling, and troubleshooting |
| [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md) | Private external K3s backend                      |

## ☁️ Azure ML Mirror (Optional)

Mirror completed OSMO training runs to an Azure ML workspace as new model versions.

### When to use

- You need a versioned, governed home for trained policies outside the cluster
- You want to share checkpoints with teammates who do not have cluster access
- You need the AzureML model registry for deployment gating

### Prerequisites

- AzureML workspace deployed via Terraform (default in this repo)
- OSMO managed identity with `AzureML Data Scientist` and `Storage Blob Data Contributor` roles (provisioned by Terraform)
- Workload Identity enabled on the cluster

### Enabling

AzureML integration is configured in the OSMO workflow YAML directly. The workflow template (`training/il/workflows/osmo/lerobot-train.yaml`) passes `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZUREML_WORKSPACE_NAME` as environment variables. No special deploy-time flag is required.

### Using

Submit a replay for any completed run:

```bash
./training/utils/replay-azureml.sh <run-id> [model-name]
```

### What it does

| Component       | Action                                                                     |
|-----------------|----------------------------------------------------------------------------|
| Workflow YAML   | Passes AzureML workspace coordinates as env vars to the training container |
| Replay workflow | Spawns an OSMO pod that reads the run's output directory                   |
| `aml_mirror.py` | Uploads tensorboard logs + filtered final checkpoint                       |

### Troubleshooting

| Symptom                          | Cause                              | Fix                                                                                                   |
|----------------------------------|------------------------------------|-------------------------------------------------------------------------------------------------------|
| `aml_mirror: missing env vars`   | Workflow YAML missing AzureML vars | Add `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZUREML_WORKSPACE_NAME` to workflow environment |
| `AuthorizationFailed` on storage | Identity missing data-plane role   | Re-apply Terraform                                                                                    |
| Upload timeout                   | Default 7200s exceeded             | Set `AZUREML_ARTIFACTS_DEFAULT_TIMEOUT` env var on submission                                         |
| `DefaultAzureCredential` failed  | Workload Identity not enabled      | Verify `azure.workload.identity/use: "true"` label and `osmo-workflow` SA                             |

## ➡️ Next Step

See [Deployment Scenarios](../../docs/infrastructure/cluster-setup.md#-deployment-scenarios) for advanced configurations.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
