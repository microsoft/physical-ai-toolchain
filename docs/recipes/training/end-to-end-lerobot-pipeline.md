# End-to-End LeRobot Pipeline

Run the full LeRobot pipeline — train a policy, evaluate it against simulation episodes, and register the model to Azure ML — in a single command. The pipeline script handles workflow submission, status polling, and stage transitions automatically.

> [!NOTE]
> Complete [Your First LeRobot Training Job](your-first-lerobot-training-job.md) before this recipe to verify that single-stage submission works.

## 📋 Prerequisites

| Requirement          | Details                                                   |
|----------------------|-----------------------------------------------------------|
| Infrastructure       | Azure resources deployed via Terraform                    |
| OSMO                 | Control plane and backend running                         |
| Basic LeRobot recipe | Single-stage training verified successfully               |
| HuggingFace account  | Write access to a policy repo for pushing trained weights |

## 🚀 Steps

### Step 1: Understand the pipeline stages

The `run-lerobot-pipeline.sh` script orchestrates three stages:

```text
Train → Wait → Evaluate → Register
  │              │           │
  │              │           └── Register model to Azure ML
  │              └── Submit inference/eval workflow
  └── Submit training workflow, poll until complete
```

Each stage submits an OSMO workflow and polls for completion before advancing.

### Step 2: Preview the pipeline configuration

```bash
cd training/pipelines
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id <your-hf-username>/aloha-act-policy \
  --config-preview
```

The preview shows both training and inference configurations, polling intervals, and timeout settings.

### Step 3: Run the full pipeline

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id <your-hf-username>/aloha-act-policy \
  -r my-aloha-act-model
```

This command:

1. Submits an ACT training job with the ALOHA sim insertion dataset
2. Polls OSMO every 60 seconds until training completes (default timeout: 720 minutes)
3. Submits an evaluation workflow against the trained policy
4. Registers the model as `my-aloha-act-model` in Azure ML

### Step 4: Customize pipeline parameters

Adjust training and evaluation settings:

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id <your-hf-username>/aloha-act-policy \
  --policy-type act \
  --training-steps 50000 \
  --save-freq 5000 \
  --eval-episodes 20 \
  --poll-interval 120 \
  --timeout 360 \
  -r my-aloha-act-model
```

### Step 5: Run training only (skip evaluation)

Use `--skip-inference` when iterating on training hyperparameters:

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id <your-hf-username>/aloha-act-policy \
  --skip-inference
```

### Step 6: Run in async mode

Submit training without waiting for completion:

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id <your-hf-username>/aloha-act-policy \
  --skip-wait
```

Check status manually through the OSMO UI or pod logs.

## ✅ Verify

The recipe succeeded when:

- Training pod completed successfully
- Evaluation pod completed with success-rate metrics logged to MLflow
- Model appears in Azure ML registry:

```bash
az ml model show \
  --name my-aloha-act-model \
  --resource-group <your-resource-group> \
  --workspace-name <your-workspace>
```

## ⚙️ Configuration Reference

| Parameter               | Default        | Description                                |
|-------------------------|----------------|--------------------------------------------|
| `-d, --dataset-repo-id` | (required)     | HuggingFace dataset repository             |
| `--policy-repo-id`      | (required)     | HuggingFace repo for trained policy        |
| `--policy-type`         | `act`          | Policy architecture (`act` or `diffusion`) |
| `--training-steps`      | (task default) | Total training iterations                  |
| `--eval-episodes`       | `10`           | Evaluation episodes                        |
| `--poll-interval`       | `60`           | Status check interval in seconds           |
| `--timeout`             | `720`          | Training timeout in minutes                |
| `--skip-inference`      | (disabled)     | Skip evaluation stage                      |
| `--skip-wait`           | (disabled)     | Async mode — submit without waiting        |
| `-r, --register-model`  | (none)         | Model name for Azure ML registration       |

See [Scripts Reference](../../reference/scripts.md) for the full parameter table.

## 🔗 Related Recipes

- [Your First LeRobot Training Job](your-first-lerobot-training-job.md) — single-stage training
- [Your First RL Training Job](your-first-rl-training-job.md) — reinforcement learning alternative
- [Preparing Datasets for Training](../data-collection/preparing-datasets-for-training.md) — dataset download and validation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
