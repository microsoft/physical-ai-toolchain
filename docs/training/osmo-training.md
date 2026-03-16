---
sidebar_position: 7
title: OSMO Training Workflows
description: Submit Isaac Lab training jobs to NVIDIA OSMO on Azure Kubernetes Service
author: Microsoft Robotics-AI Team
ms.date: 2026-02-24
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
| OSMO control plane | Deployed via `03-deploy-osmo-control-plane.sh`                       |
| OSMO backend       | Installed via `04-deploy-osmo-backend.sh`                            |
| Storage            | Checkpoint storage configured                                        |
| OSMO CLI           | Installed and authenticated (see [Accessing OSMO](#-accessing-osmo)) |

## 📦 Available Templates

| Template             | Purpose                             | Submission Script                          |
|----------------------|-------------------------------------|--------------------------------------------|
| `train.yaml`         | Isaac Lab training (base64 inline)  | `scripts/submit-osmo-training.sh`          |
| `train-dataset.yaml` | Isaac Lab training (dataset upload) | `scripts/submit-osmo-dataset-training.sh`  |
| `lerobot-train.yaml` | LeRobot behavioral cloning          | `scripts/submit-osmo-lerobot-training.sh`  |
| `lerobot-infer.yaml` | LeRobot inference/evaluation        | `scripts/submit-osmo-lerobot-inference.sh` |

## ⚙️ Workflow Comparison

| Aspect      | train.yaml             | train-dataset.yaml    |
|-------------|------------------------|-----------------------|
| Payload     | Base64-encoded archive | Dataset folder upload |
| Size limit  | ~1MB                   | Unlimited             |
| Versioning  | None                   | Automatic             |
| Reusability | Per-run                | Across runs           |
| Setup       | None                   | Bucket configured     |

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
./scripts/submit-osmo-training.sh

# Override parameters
./scripts/submit-osmo-training.sh \
  --azure-subscription-id "your-subscription-id" \
  --azure-resource-group "rg-custom"
```

## 📂 Isaac Lab Dataset Training

Dataset folder injection via OSMO bucket system instead of base64-encoded archives. Training folder mounts at `/data/<dataset_name>/training`.

### Dataset Parameters

| Parameter            | Default         | Description                                       |
|----------------------|-----------------|---------------------------------------------------|
| `dataset_bucket`     | `training`      | OSMO bucket for training code                     |
| `dataset_name`       | `training-code` | Dataset name in bucket                            |
| `training_localpath` | (required)      | Local path to `src/training` relative to workflow |

### Submit Dataset Training

```bash
# Default configuration
./scripts/submit-osmo-dataset-training.sh

# Custom dataset bucket
./scripts/submit-osmo-dataset-training.sh \
  --dataset-bucket custom-bucket \
  --dataset-name my-training-code
```

## 🔧 Environment Variables

| Variable                | Description                               |
|-------------------------|-------------------------------------------|
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID                     |
| `AZURE_RESOURCE_GROUP`  | Resource group name                       |
| `WORKFLOW_TEMPLATE`     | Path to workflow template                 |
| `OSMO_CONFIG_DIR`       | OSMO configuration directory              |
| `OSMO_DATASET_BUCKET`   | Dataset bucket name (default: `training`) |
| `OSMO_DATASET_NAME`     | Dataset name (default: `training-code`)   |

## 🔌 Accessing OSMO

OSMO services deploy to the `osmo-control-plane` namespace. Access method depends on network configuration.

### Via VPN (Default Private Cluster)

| Service      | URL                   |
|--------------|-----------------------|
| UI Dashboard | `http://10.0.5.7`     |
| API Service  | `http://10.0.5.7/api` |

```bash
osmo login http://10.0.5.7 --method=dev --username=testuser
osmo info
```

> [!NOTE]
> Verify the internal load balancer IP: `kubectl get svc -n azureml azureml-nginx-ingress -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`

### Via Port-Forward (Public Cluster without VPN)

| Service      | Port-Forward Command                                                  | Local URL               |
|--------------|-----------------------------------------------------------------------|-------------------------|
| UI Dashboard | `kubectl port-forward svc/osmo-ui 3000:80 -n osmo-control-plane`      | `http://localhost:3000` |
| API Service  | `kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane` | `http://localhost:9000` |
| Router       | `kubectl port-forward svc/osmo-router 8080:80 -n osmo-control-plane`  | `http://localhost:8080` |

```bash
# Start port-forward in background
kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane &

# Login and verify
osmo login http://localhost:9000 --method=dev --username=testuser
osmo info
```

> [!NOTE]
> Port-forwarding does not support `osmo workflow exec` and `osmo workflow port-forward` commands. These require the router service accessible via ingress.

## 📊 Monitoring

Access the OSMO UI dashboard:

| Access Method | URL                                                                                              |
|---------------|--------------------------------------------------------------------------------------------------|
| VPN           | `http://10.0.5.7`                                                                                |
| Port-forward  | `http://localhost:3000` (after `kubectl port-forward svc/osmo-ui 3000:80 -n osmo-control-plane`) |

## 🚀 Quick Start

```bash
# Isaac Lab training with defaults
./scripts/submit-osmo-training.sh

# Isaac Lab training with custom parameters
./scripts/submit-osmo-training.sh \
  --task Isaac-Cartpole-v0 \
  --num-envs 512

# Dataset-based training
./scripts/submit-osmo-dataset-training.sh \
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
