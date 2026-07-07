---
sidebar_position: 3
title: OSMO Inference Workflows
description: Execute trained robotics policy inference using NVIDIA OSMO with Isaac Lab and LeRobot frameworks
author: Microsoft Robotics-AI Team
ms.date: 2026-07-01
ms.topic: how-to
keywords:
  - OSMO
  - inference
  - Isaac Lab
  - LeRobot
  - checkpoint
  - workflow
---

Run trained policy inference through NVIDIA OSMO workflows on GPU-accelerated Kubernetes clusters. OSMO supports Isaac Lab and LeRobot frameworks with configurable checkpoint sources and automated resource scheduling.

## 📋 Prerequisites

| Tool      | Version | Purpose                            |
|-----------|---------|------------------------------------|
| OSMO CLI  | Latest  | Workflow submission and monitoring |
| Azure CLI | 2.65+   | Azure authentication               |
| kubectl   | 1.28+   | Cluster access                     |
| Helm      | 3.14+   | Chart management                   |

## 🚀 Quick Start

Submit an Isaac Lab inference workflow:

```bash
osmo workflow submit \
  --file workflows/osmo/infer.yaml \
  --set checkpoint_uri=runs:/<run-id>/model \
  --set task=Isaac-Cartpole-v0
```

## ⚖️ Workflow Comparison

| Feature            | Isaac Lab                   | LeRobot                                |
|--------------------|-----------------------------|----------------------------------------|
| Config file        | `infer.yaml`                | `lerobot-eval.yaml`                   |
| Checkpoint format  | ONNX, TorchScript           | PyTorch (.pt)                          |
| Task specification | `--task` (Isaac Gym env)    | `--policy-type` (model arch)           |
| Video recording    | `--video-length`            | `--record-video`                       |
| Evaluation         | `--num-envs`, `--max-steps` | `--eval-episodes`, `--eval-batch-size` |

## 🔬 Isaac Lab Inference

### Checkpoint URI Formats

| Format       | Example                                       | Use Case             |
|--------------|-----------------------------------------------|----------------------|
| MLflow run   | `runs:/<run-id>/model`                        | Direct from training |
| MLflow model | `models:/<name>/<version>`                    | Model registry       |
| Azure Blob   | `https://<account>.blob.core.windows.net/...` | External storage     |
| HTTP(S)      | `https://<url>/model.onnx`                    | Public endpoints     |

### Supported Model Formats

| Format      | Extension       | Frameworks         |
|-------------|-----------------|--------------------|
| ONNX        | `.onnx`         | Isaac Lab          |
| TorchScript | `.pt`           | Isaac Lab, LeRobot |
| Both        | `.onnx` + `.pt` | Full compatibility |

### Isaac Lab CLI Parameters

| Parameter              | Required | Default       | Description                |
|------------------------|----------|---------------|----------------------------|
| `-c, --checkpoint-uri` | Yes      | —             | Checkpoint location URI    |
| `--task`               | No       | from workflow | Isaac Gym environment name |
| `--format`             | No       | `onnx`        | Model format               |
| `--num-envs`           | No       | `1`           | Parallel environments      |
| `--max-steps`          | No       | `1000`        | Maximum simulation steps   |
| `--video-length`       | No       | `200`         | Video recording frames     |

### Locating Checkpoints

```bash
osmo workflow list
osmo workflow logs <workflow-id> | grep "checkpoint"
```

### Configuration Resolution Order

| Priority    | Source                | Example                    |
|-------------|-----------------------|----------------------------|
| 1 (highest) | CLI arguments         | `--set checkpoint_uri=...` |
| 2           | Environment variables | `CHECKPOINT_URI=...`       |
| 3 (lowest)  | Terraform outputs     | Auto-detected from state   |

### Workflow Outputs

| Artifact      | Path               | Description          |
|---------------|--------------------|----------------------|
| `policy.onnx` | `outputs/`         | Exported ONNX policy |
| `policy.pt`   | `outputs/`         | TorchScript policy   |
| Metrics JSON  | `outputs/metrics/` | Evaluation results   |
| Videos        | `outputs/videos/`  | Recorded episodes    |

## 🤖 LeRobot Inference

### LeRobot CLI Parameters

| Parameter           | Required | Default | Description                                     |
|---------------------|----------|---------|-------------------------------------------------|
| `--policy-repo-id`  | One of*  | —       | HuggingFace model repo                          |
| `--policy-revision` | Cond.†   | —       | HuggingFace commit SHA pinning the policy repo  |
| `--builtin-policy`  | One of*  | —       | Mint a base policy from LeRobot's built-in arch |
| `--policy-type`     | No       | `act`   | Policy architecture                             |
| `--dataset-repo-id` | No       | —       | Evaluation dataset                              |
| `--dataset-revision`| Cond.‡   | —       | HuggingFace commit SHA pinning the dataset repo |
| `--eval-episodes`   | No       | `10`    | Number of evaluation episodes                   |
| `--eval-batch-size` | No       | `1`     | Batch size for evaluation                       |
| `--record-video`    | No       | `false` | Enable video recording                          |

*Exactly one policy source is required: `--policy-repo-id`, `--from-aml-model`, or `--builtin-policy`. `--builtin-policy` mints a self-contained base policy from the local dataset and requires `--from-blob-dataset`.

†Required with `--policy-repo-id`: a Hub download without a pinned commit SHA resolves a mutable HEAD and is rejected. ‡Required with `--dataset-repo-id`, for the same reason.

### Usage Examples

Basic evaluation:

```bash
osmo workflow submit \
  --file workflows/osmo/lerobot-eval.yaml \
  --set policy_repo_id=<hf-repo-id> \
  --set policy_revision=<commit-sha>
```

With video recording:

```bash
osmo workflow submit \
  --file workflows/osmo/lerobot-eval.yaml \
  --set policy_repo_id=<hf-repo-id> \
  --set policy_revision=<commit-sha> \
  --set record_video=true \
  --set eval_episodes=20
```

Model registration:

```bash
osmo workflow submit \
  --file workflows/osmo/lerobot-eval.yaml \
  --set policy_repo_id=<hf-repo-id> \
  --set policy_revision=<commit-sha> \
  --set register_model=true
```

## 🔑 Credential Configuration

```bash
osmo credential set hf_token <token>
osmo credential set hf_token <token>
```

## 📺 Monitoring

```bash
osmo workflow logs <workflow-id>
osmo workflow logs <workflow-id> --follow
osmo workflow status <workflow-id>
```

## 🔗 Related Documentation

- [Inference Hub](README.md)
- [Training Guide](../training/README.md)
- [Workflow Templates](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/README.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
