---
title: Cluster Setup
description: AKS cluster configuration with NVIDIA GPU operator, KAI Scheduler, and AzureML extension
author: Microsoft Robotics-AI Team
ms.date: 2026-07-23
ms.topic: how-to
keywords:
  - cluster-setup
  - kubernetes
  - azureml
---

<!-- cspell:ignore kubeconfigs -->

AKS cluster configuration for robotics workloads. Deploys NVIDIA GPU operator, KAI Scheduler, AzureML extension, and OSMO components onto the AKS cluster provisioned in the infrastructure phase.

> [!NOTE]
> Complete setup walkthrough, deployment scenarios, and troubleshooting are in the [Cluster Setup](../../docs/infrastructure/cluster-setup.md) guide.

## 🚀 Quick Start

Each script writes AKS credentials to an isolated kubeconfig and requires an explicit context for Kubernetes and Helm operations.

Deployment order:

1. `./01-deploy-robotics-charts.sh` — GPU Operator, KAI Scheduler
2. `./02-deploy-azureml-extension.sh` — AzureML K8s extension, compute attach
3. `./03-deploy-osmo.sh` — OSMO control plane and backend operator

After an existing OSMO backend and pool are ready, a trusted environment owner uses `./04-prepare-osmo-hil-node.sh` to publish exact host-bound inputs. The Ubuntu path deploys only the local backend resources.

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

The generic bundle remains non-secret. Credentials, registry access, and public VPN exchange material use a separate host-bound HiL catalog rather than widening this allowlist.

Prepare the exact catalog from a trusted environment-operator host:

```bash
./04-prepare-osmo-hil-node.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --bundle-dir generated/<environment> \
  --service-url <approved-osmo-url> \
  --backend-name <existing-backend> \
  --pool-name <existing-pool> \
  --osmo-config-dir <protected-osmo-profile> \
  --registry-config-file <protected-pull-config> \
  --token-expiry <yyyy-mm-dd> \
  --config-preview
```

Key Vault is the only scripted protected-artifact transfer for the Ubuntu HiL journey. The publisher verifies the existing backend and pool, publishes exact artifacts, and writes the catalog last. It reuses the exact catalog-pinned OSMO token and token-metadata versions while they remain valid and unexpired.

`--renew-token` forces issuance, while an absent catalog or valid expired metadata issues a new token. A malformed or inaccessible catalog, or a token binding or digest mismatch, stops publication. The publisher does not delete token versions, grant roles, change Key Vault networking, or create remote desired state.

The Ubuntu consumer validates catalog structure, artifact digests, token metadata, token digest, backend binding, and expiry before changing local Kubernetes resources.

## 📖 Documentation

| Guide                                                                                      | Description                                                        |
|--------------------------------------------------------------------------------------------|--------------------------------------------------------------------|
| [Cluster Setup](../../docs/infrastructure/cluster-setup.md)                                | Full setup walkthrough and deployment scenarios                    |
| [Cluster Operations](../../docs/infrastructure/cluster-setup-advanced.md)                  | Advanced operations, scaling, and troubleshooting                  |
| [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md) | Progressive T3 host, optional VPN, backend, and validation journey |

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
