# End-to-End LeRobot Pipeline

Train from a HuggingFace dataset, register the checkpoint in Azure ML, and evaluate that exact model version in one command. The pipeline script handles OSMO submission, status polling, model-version resolution, and evaluation submission. The dataset revision pins evaluation; use the verified Azure release workflow when training and evaluation must share one immutable dataset input.

> [!NOTE]
> Complete [Your First LeRobot Training Job](your-first-lerobot-training-job.md) before this recipe to verify that single-stage submission works.

## 📋 Prerequisites

| Requirement          | Details                                                   |
|----------------------|-----------------------------------------------------------|
| Infrastructure       | Azure resources deployed via Terraform                    |
| OSMO                 | Control plane and backend running                         |
| Basic LeRobot recipe | Single-stage training verified successfully               |
| HuggingFace dataset  | Repository ID and evaluation commit SHA                   |
| Azure ML registry    | Workspace for the registered training checkpoint           |

## 🚀 Steps

### Step 1: Understand the pipeline stages

The `run-lerobot-pipeline.sh` script orchestrates three stages:

```text
Train and register → Wait → Resolve model version → Evaluate
  │                                      │
  │                                      └── Submit the current OSMO evaluation workflow
  └── Submit training and register its final checkpoint
```

Each stage submits an OSMO workflow and polls for completion before advancing.

### Step 2: Preview the pipeline configuration

```bash
cd training/pipelines
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --dataset-revision <dataset-commit-sha> \
  -r my-aloha-act-model \
  --config-preview
```

The preview shows training, registration, evaluation, polling, and timeout settings.

### Step 3: Run the full pipeline

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --dataset-revision <dataset-commit-sha> \
  -r my-aloha-act-model
```

This command:

1. Submits an ACT training job with the ALOHA sim insertion dataset
2. Polls OSMO every 60 seconds until training completes (default timeout: 720 minutes)
3. Resolves the Azure ML model version tagged with the training job name
4. Submits the current OSMO evaluation workflow against that version

### Step 4: Customize pipeline parameters

Adjust training and evaluation settings:

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --dataset-revision <dataset-commit-sha> \
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
  --skip-inference
```

### Step 6: Run in async mode

Submit training without waiting for completion:

```bash
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  -r my-aloha-act-model \
  --skip-wait
```

Check status manually through the OSMO UI or pod logs.

## ✅ Verify

The recipe succeeded when:

- Training pod completed successfully
- Evaluation pod completed with replay metrics logged to MLflow
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
| `--dataset-revision`    | (required*)    | Dataset commit SHA used by evaluation      |
| `--policy-repo-id`      | (none)         | Optional policy repository for fine-tuning |
| `--policy-type`         | `act`          | Policy architecture (`act` or `diffusion`) |
| `--training-steps`      | (task default) | Total training iterations                  |
| `--eval-episodes`       | `10`           | Evaluation episodes                        |
| `--poll-interval`       | `60`           | Status check interval in seconds           |
| `--timeout`             | `720`          | Training timeout in minutes                |
| `--skip-inference`      | (disabled)     | Skip evaluation stage                      |
| `--skip-wait`           | (disabled)     | Async mode — submit without waiting        |
| `-r, --register-model`  | (required*)    | Model name for registration and evaluation |

*Required unless evaluation is skipped. The wrapper supports HuggingFace dataset inputs. Use [Record Episodes for a Verified Experiment](../data-collection/record-to-verified-experiment.md) for Azure Viewer releases.

See [Scripts Reference](../../reference/scripts.md) for the full parameter table.

## 🔗 Related Recipes

- [Your First LeRobot Training Job](your-first-lerobot-training-job.md) — single-stage training
- [Your First RL Training Job](your-first-rl-training-job.md) — reinforcement learning alternative
- [Preparing Datasets for Training](../data-collection/preparing-datasets-for-training.md) — dataset download and validation
- [Record Episodes for a Verified Experiment](../data-collection/record-to-verified-experiment.md) — verified Azure release workflow

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
