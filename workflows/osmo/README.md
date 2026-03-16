---
title: OSMO Workflows
description: NVIDIA OSMO workflow templates for distributed robotics training
author: Edge AI Team
ms.date: 2026-03-16
ms.topic: reference
---

NVIDIA OSMO workflow templates for distributed Isaac Lab training on Azure Kubernetes Service.

## 📜 Available Templates

| Template                                 | Purpose                               | Submission Script                          |
|------------------------------------------|---------------------------------------|--------------------------------------------|
| [train.yaml](train.yaml)                 | Distributed training (base64 inline)  | `scripts/submit-osmo-training.sh`          |
| [train-dataset.yaml](train-dataset.yaml) | Distributed training (dataset upload) | `scripts/submit-osmo-dataset-training.sh`  |
| [lerobot-train.yaml](lerobot-train.yaml) | LeRobot behavioral cloning            | `scripts/submit-osmo-lerobot-training.sh`  |
| [lerobot-infer.yaml](lerobot-infer.yaml) | LeRobot inference/evaluation          | `scripts/submit-osmo-lerobot-inference.sh` |

## ⚖️ Workflow Comparison

| Aspect      | train.yaml             | train-dataset.yaml    |
|-------------|------------------------|-----------------------|
| Payload     | Base64-encoded archive | Dataset folder upload |
| Size limit  | ~1MB                   | Unlimited             |
| Versioning  | None                   | Automatic             |
| Reusability | Per-run                | Across runs           |
| Setup       | None                   | Bucket configured     |

## 🏋️ Training Workflow (`train.yaml`)

Submits Isaac Lab distributed training through OSMO's workflow orchestration engine.

### Training Features

* Multi-GPU distributed training coordination
* KAI Scheduler / Volcano integration
* Automatic checkpointing and recovery
* OSMO UI monitoring dashboard

### Workflow Parameters

Parameters are passed as key=value pairs through the submission script:

| Parameter               | Description           |
|-------------------------|-----------------------|
| `azure_subscription_id` | Azure subscription ID |
| `azure_resource_group`  | Resource group name   |
| `azure_workspace_name`  | ML workspace name     |
| `task`                  | Isaac Lab task name   |
| `num_envs`              | Parallel environments |
| `max_iterations`        | Training iterations   |

### Usage

```bash
# Default configuration from Terraform outputs
./scripts/submit-osmo-training.sh

# Override parameters
./scripts/submit-osmo-training.sh \
  --azure-subscription-id "your-subscription-id" \
  --azure-resource-group "rg-custom"
```

## 💾 Dataset Training Workflow (`train-dataset.yaml`)

Submits Isaac Lab training using OSMO dataset folder injection instead of base64-encoded archives.

### Dataset Features

* Dataset versioning and reusability
* No payload size limits
* Training folder mounted at `/data/<dataset_name>/training`
* All features from `train.yaml`

### Dataset Parameters

| Parameter            | Default         | Description                                     |
|----------------------|-----------------|-------------------------------------------------|
| `dataset_bucket`     | `training`      | OSMO bucket for training code                   |
| `dataset_name`       | `training-code` | Dataset name in bucket                          |
| `training_localpath` | (required)      | Local path to training/ relative to workflow |

### Dataset Usage

```bash
# Default configuration
./scripts/submit-osmo-dataset-training.sh

# Custom dataset bucket
./scripts/submit-osmo-dataset-training.sh \
  --dataset-bucket custom-bucket \
  --dataset-name my-training-code
```

## 🤖 LeRobot Training Workflow (`lerobot-train.yaml`)

Submits LeRobot behavioral cloning training for ACT and Diffusion policy architectures. Uses HuggingFace Hub datasets and installs LeRobot dynamically at runtime via `uv pip install`.

### LeRobot Features

* ACT and Diffusion policy architectures
* HuggingFace Hub dataset integration
* Azure MLflow logging backend
* Automatic checkpoint registration to Azure ML
* No source payload packaging required

### LeRobot Parameters

| Parameter         | Default                                         | Description                                |
|-------------------|-------------------------------------------------|--------------------------------------------|
| `dataset_repo_id` | (required)                                      | HuggingFace dataset (e.g., `user/dataset`) |
| `policy_type`     | `act`                                           | Policy architecture: `act`, `diffusion`    |
| `job_name`        | `lerobot-act-training`                          | Unique job identifier                      |
| `image`           | `pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime` | Container image                            |
| `training_steps`  | (LeRobot default)                               | Total training iterations                  |
| `batch_size`      | (LeRobot default)                               | Training batch size                        |
| `save_freq`       | `5000`                                          | Checkpoint save frequency                  |
| `wandb_enable`    | `true`                                          | Enable WANDB logging                       |
| `mlflow_enable`   | `false`                                         | Enable Azure ML MLflow logging             |

### LeRobot Usage

```bash
# ACT training with WANDB logging
./scripts/submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human

# Diffusion policy with MLflow logging
./scripts/submit-osmo-lerobot-training.sh \
  -d user/custom-dataset \
  -p diffusion \
  --mlflow-enable \
  -r my-diffusion-model

# Fine-tune from existing policy
./scripts/submit-osmo-lerobot-training.sh \
  -d user/dataset \
  --policy-repo-id user/pretrained-act \
  --training-steps 50000
```

### Credential Configuration

The workflow uses OSMO credential injection for HuggingFace and WANDB authentication:

```bash
# Set HuggingFace token (required for private datasets)
osmo credential set hf_token --generic --value "hf_..."

# Set WANDB API key (required when wandb_enable=true)
osmo credential set wandb_api_key --generic --value "..."
```

## 📦 LeRobot Dataset Training Workflow (`lerobot-train-dataset.yaml`)

Trains LeRobot policies using OSMO dataset mounts instead of HuggingFace Hub downloads. Supports Azure Blob Storage datasets uploaded via OSMO's dataset bucket system.

### Dataset Training Features

* OSMO dataset versioning and reuse across runs
* Azure Blob Storage integration via `azure://` URLs
* Falls back to HuggingFace Hub if no dataset mount is available
* All features from `lerobot-train.yaml`

### Dataset Training Parameters

| Parameter           | Default            | Description                            |
|---------------------|--------------------|----------------------------------------|
| `dataset_bucket`    | `lerobot-datasets` | OSMO bucket for training data          |
| `dataset_name`      | `training-data`    | Dataset name in bucket                 |
| `dataset_localpath` | (required)         | Local path to dataset relative to YAML |

### Dataset Training Usage

```bash
# Train with local dataset uploaded via OSMO
./scripts/submit-osmo-lerobot-training.sh \
  -w workflows/osmo/lerobot-train-dataset.yaml \
  -d user/fallback-dataset \
  --dataset-bucket my-bucket \
  --dataset-name my-lerobot-data
```

## 🔬 LeRobot Inference Workflow (`lerobot-infer.yaml`)

Evaluates trained LeRobot policies from HuggingFace Hub repositories. Downloads the policy checkpoint, runs evaluation, and optionally registers the model to Azure ML.

### Inference Features

* Policy download from HuggingFace Hub
* Model artifact extraction and validation
* Optional Azure ML model registration
* ACT and Diffusion policy support

### Inference Parameters

| Parameter         | Default    | Description                             |
|-------------------|------------|-----------------------------------------|
| `policy_repo_id`  | (required) | HuggingFace policy repository           |
| `policy_type`     | `act`      | Policy architecture: `act`, `diffusion` |
| `eval_episodes`   | `10`       | Number of evaluation episodes           |
| `eval_batch_size` | `10`       | Evaluation batch size                   |
| `register_model`  | (none)     | Model name for Azure ML registration    |
| `record_video`    | `false`    | Record evaluation videos                |

### Inference Usage

```bash
# Evaluate a trained policy
./scripts/submit-osmo-lerobot-inference.sh \
  --policy-repo-id user/trained-act-policy

# Evaluate with Azure ML model registration
./scripts/submit-osmo-lerobot-inference.sh \
  --policy-repo-id user/trained-act-policy \
  -r my-evaluated-model

# Diffusion policy with more episodes
./scripts/submit-osmo-lerobot-inference.sh \
  --policy-repo-id user/diffusion-policy \
  -p diffusion \
  --eval-episodes 50
```

## ⚙️ Environment Variables

| Variable                | Description                             |
|-------------------------|-----------------------------------------|
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID                   |
| `AZURE_RESOURCE_GROUP`  | Resource group name                     |
| `WORKFLOW_TEMPLATE`     | Path to workflow template               |
| `OSMO_CONFIG_DIR`       | OSMO configuration directory            |
| `OSMO_DATASET_BUCKET`   | Dataset bucket name (default: training) |
| `OSMO_DATASET_NAME`     | Dataset name (default: training-code)   |

## 📋 Prerequisites

1. OSMO control plane deployed (`03-deploy-osmo-control-plane.sh`)
2. OSMO backend operator installed (`04-deploy-osmo-backend.sh`)
3. Storage configured for checkpoints
4. OSMO CLI installed and authenticated (see [Accessing OSMO](#-accessing-osmo))

## 🔌 Accessing OSMO

OSMO services are deployed to the `osmo-control-plane` namespace. Access method depends on your network configuration.

### Via VPN (Default Private Cluster)

When connected to VPN, OSMO is accessible via the internal load balancer:

| Service      | URL                   |
|--------------|-----------------------|
| UI Dashboard | `http://10.0.5.7`     |
| API Service  | `http://10.0.5.7/api` |

```bash
osmo login http://10.0.5.7 --method=dev --username=testuser
osmo info
```

> [!NOTE]
> Verify the internal load balancer IP with: `kubectl get svc -n azureml azureml-nginx-ingress -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`

### Via Port-Forward (Public Cluster without VPN)

If `should_enable_private_aks_cluster = false` and not using VPN:

| Service      | Port-Forward Command                                                  | Local URL               |
|--------------|-----------------------------------------------------------------------|-------------------------|
| UI Dashboard | `kubectl port-forward svc/osmo-ui 3000:80 -n osmo-control-plane`      | `http://localhost:3000` |
| API Service  | `kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane` | `http://localhost:9000` |
| Router       | `kubectl port-forward svc/osmo-router 8080:80 -n osmo-control-plane`  | `http://localhost:8080` |

```bash
# Start port-forward in background (or separate terminal)
kubectl port-forward svc/osmo-service 9000:80 -n osmo-control-plane &

# Login to OSMO (dev mode for local access)
osmo login http://localhost:9000 --method=dev --username=testuser

# Verify connection
osmo info
osmo backend list
```

> [!NOTE]
> When accessing OSMO through port-forwarding, `osmo workflow exec` and `osmo workflow port-forward` commands are not supported. These require the router service to be accessible via ingress.

## 📺 Monitoring

Access the OSMO UI dashboard:

* **VPN**: Open `http://10.0.5.7` in your browser
* **Port-forward**: Run `kubectl port-forward svc/osmo-ui 3000:80 -n osmo-control-plane` then open `http://localhost:3000`

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
