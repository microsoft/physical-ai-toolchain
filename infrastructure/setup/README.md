---
title: Cluster Setup
description: AKS cluster configuration with NVIDIA GPU operator, KAI Scheduler, and AzureML extension
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
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
3. `./03-deploy-osmo-control-plane.sh` — OSMO control plane
4. `./04-deploy-osmo-backend.sh` — OSMO backend services
5. `./05-deploy-dataviewer.sh` — Dataviewer container build and deploy

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

The mirror is enabled by default when an AzureML workspace exists in Terraform outputs. Run:

```bash
./04-deploy-osmo-backend.sh
```

To disable it explicitly:

```bash
./04-deploy-osmo-backend.sh --skip-azureml-pod-template
```

### Using

Submit a replay for any completed run:

```bash
./training/utils/replay-azureml.sh <run-id> [model-name]
```

### What it does

| Component       | Action                                                                                                                      |
|-----------------|-----------------------------------------------------------------------------------------------------------------------------|
| Deploy script   | Renders `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZUREML_WORKSPACE_NAME` into the `default_user` OSMO pod template |
| Replay workflow | Spawns an OSMO pod that reads the run's output directory                                                                    |
| `aml_mirror.py` | Uploads tensorboard logs + filtered final checkpoint, registers a new model version                                         |

### Disabling after deploy

```bash
./04-deploy-osmo-backend.sh --skip-azureml-pod-template
```

### Troubleshooting

| Symptom                          | Cause                            | Fix                                                                       |
|----------------------------------|----------------------------------|---------------------------------------------------------------------------|
| `aml_mirror: missing env vars`   | Pod template not extended        | Re-run deploy script without `--skip-azureml-pod-template`                |
| `AuthorizationFailed` on storage | Identity missing data-plane role | Re-apply Terraform                                                        |
| Upload timeout                   | Default 7200s exceeded           | Set `AZUREML_ARTIFACTS_DEFAULT_TIMEOUT` env var on submission             |
| `DefaultAzureCredential` failed  | Workload Identity not enabled    | Verify `azure.workload.identity/use: "true"` label and `osmo-workflow` SA |

## ➡️ Next Step

See [Deployment Scenarios](../../docs/infrastructure/cluster-setup.md#-deployment-scenarios) for advanced configurations.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
