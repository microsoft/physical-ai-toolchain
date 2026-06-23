---
title: Cluster Setup
description: AKS cluster configuration with NVIDIA GPU operator, KAI Scheduler, and AzureML extension
author: Microsoft Robotics-AI Team
ms.date: 2026-06-11
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

```bash
az aks get-credentials --resource-group <rg> --name <aks>
kubectl cluster-info
```

Deployment order:

1. `./01-deploy-robotics-charts.sh` — GPU Operator, KAI Scheduler
2. `./02-deploy-azureml-extension.sh` — AzureML K8s extension, compute attach
3. `./03-deploy-osmo.sh` — OSMO control plane and backend operator

## 📖 Documentation

| Guide                                                                     | Description                                       |
|---------------------------------------------------------------------------|---------------------------------------------------|
| [Cluster Setup](../../docs/infrastructure/cluster-setup.md)               | Full setup walkthrough and deployment scenarios   |
| [Cluster Operations](../../docs/infrastructure/cluster-setup-advanced.md) | Advanced operations, scaling, and troubleshooting |

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

| Symptom                          | Cause                            | Fix                                                                                                     |
|----------------------------------|----------------------------------|---------------------------------------------------------------------------------------------------------|
| `aml_mirror: missing env vars`   | Workflow YAML missing AzureML vars | Add `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZUREML_WORKSPACE_NAME` to workflow environment |
| `AuthorizationFailed` on storage | Identity missing data-plane role | Re-apply Terraform                                                                                      |
| Upload timeout                   | Default 7200s exceeded           | Set `AZUREML_ARTIFACTS_DEFAULT_TIMEOUT` env var on submission                                           |
| `DefaultAzureCredential` failed  | Workload Identity not enabled    | Verify `azure.workload.identity/use: "true"` label and `osmo-workflow` SA                               |

## ➡️ Next Step

See [Deployment Scenarios](../../docs/infrastructure/cluster-setup.md#-deployment-scenarios) for advanced configurations.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
