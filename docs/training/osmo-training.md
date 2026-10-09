---
sidebar_position: 7
title: OSMO Training Workflows
description: Submit Isaac Lab training jobs to NVIDIA OSMO on Azure Kubernetes Service
author: Microsoft Robotics-AI Team
ms.date: 2026-10-08
ms.topic: how-to
keywords:
  - osmo
  - training
  - isaac lab
  - nvidia
---

Submit distributed Isaac Lab training jobs through NVIDIA OSMO workflow orchestration on Azure Kubernetes Service. OSMO provides multi-GPU scheduling, automatic checkpointing, and a monitoring dashboard.

## 📋 Prerequisites

| Component          | Requirement                                                          |
|--------------------|----------------------------------------------------------------------|
| OSMO control plane | Deployed via `03-deploy-osmo.sh`                                     |
| OSMO backend       | Installed via `03-deploy-osmo.sh`                                    |
| Storage            | Checkpoint storage configured                                        |
| OSMO CLI           | Installed and authenticated (see [Accessing OSMO](#-accessing-osmo)) |

## 📦 Available Templates

| Template             | Purpose                             | Submission Script                                     |
|----------------------|-------------------------------------|-------------------------------------------------------|
| `train.yaml`         | Isaac Lab RL training               | `training/rl/scripts/submit-osmo-training.sh`         |
| `train-dataset.yaml` | Isaac Lab training (dataset upload) | `training/rl/scripts/submit-osmo-dataset-training.sh` |
| `lerobot-train.yaml` | LeRobot behavioral cloning          | `training/il/scripts/submit-osmo-lerobot-training.sh` |
| `groot-train.yaml`   | GR00T-N1.5 / N1.7 fine-tuning (VLA) | `vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh`  |
| `lerobot-eval.yaml`  | LeRobot inference/evaluation        | `evaluation/sil/scripts/submit-osmo-lerobot-eval.sh`  |

## ⚙️ Workflow Comparison

| Aspect      | train.yaml              | train-dataset.yaml    |
|-------------|-------------------------|-----------------------|
| Payload     | Object-storage archive  | Dataset folder upload |
| Size limit  | Unlimited               | Unlimited             |
| Versioning  | Content-hash per submit | Automatic             |
| Reusability | Per-run                 | Across runs           |
| Setup       | Storage account         | Bucket configured     |

## 🏋️ Isaac Lab Training

Multi-GPU distributed training with KAI Scheduler / Volcano integration, automatic checkpointing, and OSMO UI monitoring.

### Training Parameters

| Parameter               | Description           |
|-------------------------|-----------------------|
| `azure_subscription_id` | Azure subscription ID |
| `azure_resource_group`  | Resource group name   |
| `azure_workspace_name`  | ML workspace name     |
| `task`                  | Isaac Lab task name   |
| `num_envs`              | Parallel environments |
| `max_iterations`        | Training iterations   |

### Submit Training

```bash
# Default configuration from Terraform outputs
./training/rl/scripts/submit-osmo-training.sh

# Override parameters
./training/rl/scripts/submit-osmo-training.sh \
  --azure-subscription-id "your-subscription-id" \
  --azure-resource-group "rg-custom"
```

## 📂 Isaac Lab Dataset Training

Dataset folder injection via OSMO bucket system. Training folder mounts at `/data/<dataset_name>/training`.

### Dataset Parameters

| Parameter            | Default         | Description                                    |
|----------------------|-----------------|------------------------------------------------|
| `dataset_bucket`     | `training`      | OSMO bucket for training code                  |
| `dataset_name`       | `training-code` | Dataset name in bucket                         |
| `training_localpath` | (required)      | Local path to `training/` relative to workflow |

### Submit Dataset Training

```bash
# Default configuration
./training/rl/scripts/submit-osmo-dataset-training.sh

# Custom dataset bucket
./training/rl/scripts/submit-osmo-dataset-training.sh \
  --dataset-bucket custom-bucket \
  --dataset-name my-training-code
```

## 🛌 Scale-from-zero GPU Pools

OSMO GPU workflows can target AKS pools that sit at zero nodes. A task becomes a pending pod, KAI Scheduler marks it unschedulable, and the cluster autoscaler adds a node to the pool whose template the pod matches. On H100 and A10 pools, a workflow started running 8 to 12 minutes after submission, and the pool scaled back down about 10 minutes after the work finished. (For the AzureML equivalent, see [AzureML Scale-from-zero GPU Pools](azureml-training.md#-scale-from-zero-gpu-pools).)

Three parts of the OSMO configuration decide whether a workflow can wake an empty pool:

| Setting              | Requirement for pools that scale from zero                                                                                                                                                          |
|----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Resource validations | Static rules only, such as `static_cpu`, `static_memory`, `static_storage`, and `static_gpu_non_negative` in [`osmo-control-plane.yaml`](../../infrastructure/setup/values/osmo-control-plane.yaml) |
| Pod templates        | Select `agentpool: <pool key>` and `node.kubernetes.io/instance-type`, tolerate the pool's taints, and request `nvidia.com/gpu: "{{USER_GPU}}"`                                                     |
| GPU count            | `gpu` set on every GPU task                                                                                                                                                                         |

Rules that compare with `{{K8_CPU}}`, `{{K8_MEMORY}}`, or `{{K8_STORAGE}}` read the capacity of running nodes, so OSMO rejects a workflow for an empty pool with `There are no resources in platform ...` and nothing scales up. When two pools share a VM size, such as a Spot and an on-demand H100 pool, an instance-type selector alone matches both, and the autoscaler chooses between them.

OSMO exposes GPUs only to tasks whose spec sets `gpu` above zero: for a task that omits `gpu` or sets `gpu: 0`, it sets `NVIDIA_VISIBLE_DEVICES` to an empty value, so a GPU that the pod template requests stays unusable. Without a `USER_GPU` platform default, such a task requests zero GPUs, but the platform's pod template still selects the GPU pool, so the task wakes a GPU node it can't use. Point CPU-only tasks at a CPU platform.

[`osmo-platforms.yaml`](../../infrastructure/setup/values/osmo-platforms.yaml) shows the platform pattern. `gpu_platform` binds the `gpu_tpl` pod template, which pins the GPU size, tolerates the GPU and Spot taints, and requests `{{USER_GPU}}` GPUs, with a platform default of one. A task that omits `gpu` therefore passes `gpu_gpu_required` (`USER_GPU >= 1`) and holds a GPU it can't see, so set `gpu` explicitly in GPU workflows.

Static validations don't compare a request with node size, so a task that needs more GPUs, CPU, or memory than one node offers waits in the queue until its `queue_timeout` instead of failing at submit.

KAI Scheduler gang-schedules multi-GPU workflows: all of a job's pods wait until the requested GPU count is simultaneously available, so a partially-scaled pool never starts a job that cannot complete. To add or resize a GPU pool, edit `osmo-platforms.yaml` and rerun `infrastructure/setup/03-deploy-osmo.sh` (see [Manage Node Pools](../infrastructure/manage-node-pools.md)).

## 🤖 GR00T VLA Fine-Tuning

Fine-tune NVIDIA Isaac-GR00T (N1.5 or N1.7) on a LeRobot dataset hosted in Azure Blob Storage. The submission script selects the GR00T codebase ref and the matching config injection path based on `--vla-version`.

| Version | Config path                                  | Auto-resolved from `--data-config`                      |
|---------|----------------------------------------------|---------------------------------------------------------|
| N1.5    | `--data-config-file` (appended at runtime)   | `training/vla/configs/groot/${name}_data_config.py`     |
| N1.7    | `--modality-config-file` (loaded at runtime) | `training/vla/configs/groot/${name}_modality_config.py` |

Reference templates for both versions live in [`training/vla/configs/groot/examples/`](https://github.com/microsoft/physical-ai-toolchain/blob/main/training/vla/configs/groot/examples/README.md).

### Submit GR00T-N1.5

```bash
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --job-name groot-n15-example \
  --vla-version 1.5 \
  --base-model nvidia/GR00T-N1.5-3B \
  --data-config example \
  --data-config-file training/vla/configs/groot/examples/data_config.py \
  --blob-url https://<account>.blob.core.windows.net/<container>/<dataset> \
  --max-steps 500 \
  --batch-size 4
```

### Submit GR00T-N1.7

```bash
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --job-name groot-n17-example \
  --vla-version 1.7 \
  --base-model nvidia/GR00T-N1.7-3B \
  --data-config example \
  --modality-config-file training/vla/configs/groot/examples/modality_config.py \
  --blob-url https://<account>.blob.core.windows.net/<container>/<dataset> \
  --max-steps 500 \
  --batch-size 4
```

When `--vla-version 1.7` is set the script auto-resolves `${name}_modality_config.py` from `training/vla/configs/groot/`; pass `--modality-config-file` explicitly to override.

### Optional: Mirror checkpoint to ACR

Append `--acr-registry <name>` to push the final checkpoint as an OCI artifact tagged `run-<timestamp>-step<N>` under `models/groot`:

```bash
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --vla-version 1.7 \
  --base-model nvidia/GR00T-N1.7-3B \
  --data-config example \
  --modality-config-file training/vla/configs/groot/examples/modality_config.py \
  --blob-url https://<account>.blob.core.windows.net/<container>/<dataset> \
  --acr-registry <acr-name> \
  --acr-model-repo models/groot
```

See [LeRobot Training — GR00T VLA Fine-Tuning](lerobot-training.md#-gr00t-vla-fine-tuning) for the full parameter table and Azure ML mirror workflow.

## 🔧 Environment Variables

| Variable                 | Description                               |
|--------------------------|-------------------------------------------|
| `AZURE_SUBSCRIPTION_ID`  | Azure subscription ID                     |
| `AZURE_RESOURCE_GROUP`   | Resource group name                       |
| `AZUREML_WORKSPACE_NAME` | Azure ML workspace name                   |
| `OSMO_DATASET_BUCKET`    | Dataset bucket name (default: `training`) |
| `OSMO_DATASET_NAME`      | Dataset name (default: `training-code`)   |

## 🔌 Accessing OSMO

OSMO services deploy to the `osmo-control-plane` namespace. Access method depends on network configuration.

### Via VPN (Default Private Cluster)

| Service      | URL                   |
|--------------|-----------------------|
| UI Dashboard | `http://10.0.5.7`     |
| API Service  | `http://10.0.5.7/api` |

```bash
osmo login http://10.0.5.7 --method=dev --username=admin
osmo version
```

> [!NOTE]
> Verify the internal load balancer IP: `kubectl get svc -n azureml azureml-ingress-nginx-internal-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`

### Via Port-Forward (Public Cluster without VPN)

| Service | Port-Forward Command                                                  | Local URL               |
|---------|-----------------------------------------------------------------------|-------------------------|
| Gateway | `kubectl port-forward svc/osmo-gateway 9000:80 -n osmo-control-plane` | `http://localhost:9000` |

```bash
# Start port-forward in background
kubectl port-forward svc/osmo-gateway 9000:80 -n osmo-control-plane &

# Login and configure default pool
osmo login http://localhost:9000 --method=dev --username=admin
osmo profile set pool default
osmo version
```

> [!NOTE]
> Port-forwarding does not support `osmo workflow exec` and `osmo workflow port-forward` commands. These require the gateway service accessible via ingress.

## 📊 Monitoring

Access the OSMO UI dashboard:

| Access Method | URL                                                                                                   |
|---------------|-------------------------------------------------------------------------------------------------------|
| VPN           | `http://10.0.5.7`                                                                                     |
| Port-forward  | `http://localhost:8080` (after `kubectl port-forward svc/osmo-gateway 8080:80 -n osmo-control-plane`) |

## 🚀 Quick Start

```bash
# Isaac Lab training with defaults
./training/rl/scripts/submit-osmo-training.sh

# Isaac Lab training with custom parameters
./training/rl/scripts/submit-osmo-training.sh \
  --task Isaac-Cartpole-v0 \
  --num-envs 512

# Dataset-based training
./training/rl/scripts/submit-osmo-dataset-training.sh \
  --dataset-bucket training \
  --dataset-name my-code
```

## 📚 Related Documentation

- [LeRobot Training](lerobot-training.md)
- [Azure ML Training](azureml-training.md)
- [MLflow Integration](mlflow-integration.md)
- [Training Guide](README.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
