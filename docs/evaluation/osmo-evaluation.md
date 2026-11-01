---
sidebar_position: 3
title: OSMO Inference Workflows
description: Execute trained robotics policy inference using NVIDIA OSMO with Isaac Lab and LeRobot frameworks
author: Microsoft Robotics-AI Team
ms.date: 2026-10-15
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

Evaluate an Isaac Lab checkpoint from the model registry:

```bash
evaluation/sil/scripts/submit-osmo-eval.sh \
  --checkpoint-uri models:/<name>/<version> \
  --task Isaac-Cartpole-v0
```

On the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md), add `--profile local`: the checkpoint comes from the local MLflow server, and the results are logged there.

## ⚖️ Workflow Comparison

| Feature            | Isaac Lab                                      | LeRobot                                |
|--------------------|------------------------------------------------|----------------------------------------|
| Workflow template  | `evaluation/sil/workflows/osmo/eval.yaml`      | `lerobot-eval.yaml`                    |
| Submission script  | `submit-osmo-eval.sh`                          | `submit-osmo-lerobot-eval.sh`          |
| Checkpoint format  | skrl or RSL-RL `.pt`, exported to ONNX and JIT | LeRobot pretrained model               |
| Task specification | `--task` (Isaac Lab environment)               | `--policy-type` (model arch)           |
| Video recording    | `--video-length`                               | `--record-video`                       |
| Evaluation         | Success gate over `--eval-episodes`            | `--eval-episodes`, `--eval-batch-size` |

## 🔬 Isaac Lab Evaluation

The workflow downloads the checkpoint, gates it on its success rate, exports it to ONNX and JIT, replays the exported policy with video, and logs the results through the tracking backend. It fails when the success rate is below the threshold, after the replay and uploads, so the videos of a failing policy stay available.

### Success Gate

An episode succeeds when the task reports `success` for it. For tasks that report none, an episode succeeds when it reaches its time limit without a failure termination. Isaac Lab reports time-outs as truncations, so a policy that keeps Cartpole balanced until the time limit succeeds, and one that drives the cart out of bounds fails.

The gate evaluates `--eval-episodes` episodes, 100 by default, across `--num-envs` environments and requires the `--success-threshold` success rate, 0.7 by default. `--framework` names the training library that wrote the checkpoint, `skrl` by default, or `rsl_rl`. The verdict, with the success rate, mean reward, and episode count, is logged as the run's metrics and as `metrics/eval_metrics.json`.

### Checkpoint Sources

| Source         | Example                                       | Use Case                                     |
|----------------|-----------------------------------------------|----------------------------------------------|
| MLflow run     | `runs:/<run-id>/<path>`                       | Direct from training                         |
| MLflow model   | `models:/<name>/<version>`                    | Model registry of the tracking backend       |
| Azure Blob     | `https://<account>.blob.core.windows.net/...` | External storage                             |
| HTTP(S)        | `https://<url>/model.pt`                      | Public endpoints, with `--checkpoint-sha256` |
| Container path | `/mnt/datasets/<dataset>/checkpoints`         | A path inside the task                       |
| Policy carrier | `--policy-image <image>@sha256:<digest>`      | Containerized policy, evaluated at `/policy` |

From a directory, the evaluation picks `best_agent.pt`, or else the highest-numbered checkpoint. `--policy-image` takes a runnable carrier built by [build-model-image.sh](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-deployment/setup/README.md#-local-profile) on the Isaac Lab image: the carrier runs as the task image, and the gate evaluates the policy it carries.

### Isaac Lab CLI Parameters

| Parameter              | Default                   | Description                                           |
|------------------------|---------------------------|-------------------------------------------------------|
| `-c, --checkpoint-uri` | `/policy` with a carrier  | Checkpoint source; required without `--policy-image`  |
| `--policy-image`       | —                         | Runnable policy carrier to evaluate as the task image |
| `-t, --task`           | `Isaac-Ant-v0`            | Isaac Lab environment name                            |
| `--framework`          | `skrl`                    | Training framework of the checkpoint                  |
| `--eval-episodes`      | `100`                     | Episodes the success gate evaluates                   |
| `--success-threshold`  | evaluator default (`0.7`) | Success rate required to pass                         |
| `-f, --format`         | `both`                    | Replay format: `onnx`, `jit`, or `both`               |
| `-n, --num-envs`       | `4`                       | Parallel environments for the gate and replay         |
| `-m, --max-steps`      | `500`                     | Maximum replay steps                                  |
| `-v, --video-length`   | `200`                     | Video recording steps                                 |
| `--output-dataset`     | —                         | Publish the videos and metrics as an OSMO dataset     |
| `--profile`            | `azure`                   | Platform profile: `azure` or `local`                  |

### Locating Checkpoints

```bash
osmo workflow list
osmo workflow logs <workflow-id> | grep "checkpoint"
```

### Configuration Resolution Order

| Priority    | Source                | Example                  |
|-------------|-----------------------|--------------------------|
| 1 (highest) | CLI arguments         | `--checkpoint-uri ...`   |
| 2           | Environment variables | `CHECKPOINT_URI=...`     |
| 3           | Platform profile      | `--profile local`        |
| 4 (lowest)  | Terraform outputs     | Auto-detected from state |

### Workflow Outputs

The tracking backend's run, in the `inference-<task>` experiment, receives:

| Artifact      | Run path                    | Description                     |
|---------------|-----------------------------|---------------------------------|
| `policy.onnx` | `exported_models/`          | Exported ONNX policy            |
| `policy.pt`   | `exported_models/`          | TorchScript policy              |
| Metrics JSON  | `metrics/`                  | Gate verdict and replay metrics |
| Videos        | `videos/onnx`, `videos/jit` | Recorded replay episodes        |

With `--output-dataset NAME`, the task also publishes the `videos/` and `metrics/` directories as a version of the OSMO dataset `training/NAME`, which a later workflow, such as the VLM judge, can take as input.

## 🤖 LeRobot Inference

### LeRobot CLI Parameters

| Parameter            | Required | Default | Description                                     |
|----------------------|----------|---------|-------------------------------------------------|
| `--policy-repo-id`   | One of*  | —       | HuggingFace model repo                          |
| `--policy-revision`  | Cond.†   | —       | HuggingFace commit SHA pinning the policy repo  |
| `--builtin-policy`   | One of*  | —       | Mint a base policy from LeRobot's built-in arch |
| `--policy-type`      | No       | `act`   | Policy architecture                             |
| `--dataset-repo-id`  | No       | —       | Evaluation dataset                              |
| `--dataset-revision` | Cond.‡   | —       | HuggingFace commit SHA pinning the dataset repo |
| `--eval-episodes`    | No       | `10`    | Number of evaluation episodes                   |
| `--eval-batch-size`  | No       | `1`     | Batch size for evaluation                       |
| `--record-video`     | No       | `false` | Enable video recording                          |

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
