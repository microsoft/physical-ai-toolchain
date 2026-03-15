---
name: osmo-lerobot-training
description: 'Submit, monitor, analyze, and evaluate LeRobot imitation learning training jobs on OSMO with Azure ML MLflow integration and inference evaluation - Brought to you by microsoft/physical-ai-toolchain'
---

# OSMO LeRobot Training

Submit, monitor, analyze, and evaluate LeRobot behavioral cloning training workflows on the OSMO platform. Covers the full lifecycle: job submission, log streaming, Azure ML metric retrieval, training summary generation, and post-training inference evaluation.

Read the skill file `.github/skills/osmo-lerobot-training/SKILL.md` for parameter defaults, GPU configuration, and training duration estimates. Read [references/DEFAULTS.md](references/DEFAULTS.md) for known datasets, GPU profiles, and Azure environment auto-resolution.

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| `osmo` CLI | Workflow submission and monitoring |
| `az` CLI | Azure authentication and model registry |
| `terraform` | Infrastructure output resolution |
| `zip`, `base64` | Training payload packaging |
| Python 3.11+ with `azure-ai-ml`, `mlflow` | Metric retrieval from Azure ML |

Authentication must be configured before any OSMO or Azure ML operations:

```bash
az login
osmo login <service-url> --method dev --username guest
```

## Quick Start

### Train from Azure Blob Storage (typical production flow)

```bash
scripts/submit-osmo-lerobot-training.sh \
  -d my-robot-dataset \
  --from-blob \
  --storage-account mystorageaccount \
  --blob-prefix my-robot-dataset \
  --no-val-split \
  --steps 100000 \
  --batch-size 32 \
  --learning-rate 1e-4 \
  --save-freq 10000 \
  -j my-robot-act-train \
  --experiment-name my-robot-training \
  -r my-robot-act-model
```

### Train from HuggingFace Hub

```bash
scripts/submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human
```

### Run Continuous Eval During Training (preferred)

Start the background poller immediately after submitting training. It watches AzureML for new checkpoint versions and submits an inference job per version automatically, stopping when training reaches a terminal state.

```bash
# Launch in the background — runs until training completes
nohup scripts/poll-and-eval-checkpoints.sh \
  --model-name my-robot-act-model \
  --training-workflow-id lerobot-training-32 \
  --blob-prefix my-robot-dataset \
  --job-prefix my-robot-eval \
  --experiment-name my-robot-inference \
  --poll-interval 60 \
  --max-concurrent 2 \
  > /tmp/my-robot-eval.log 2>&1 & disown

# Monitor the poller
tail -f /tmp/my-robot-eval.log
```

The poller caps concurrent inference workflows at `--max-concurrent` (default 2) to avoid cluster saturation. Submitted versions are tracked in `/tmp/<model-name>-submitted-versions.txt`.

### Run a Single Inference Job

```bash
# OSMO inference (GPU, evaluates against the same dataset)
scripts/submit-osmo-lerobot-inference.sh \
  --from-aml-model \
  --model-name my-robot-act-model \
  --model-version 3 \
  --from-blob-dataset \
  --storage-account mystorageaccount \
  --blob-prefix my-robot-dataset \
  --mlflow-enable \
  --eval-episodes 10 \
  -j my-robot-eval \
  --experiment-name my-robot-inference

# Local inference (CPU/MPS, for quick validation)
python scripts/run-local-lerobot-inference.py \
  --model-name my-robot-act-model \
  --model-version 3 \
  --dataset-dir /path/to/local/dataset \
  --episodes 5 \
  --output-dir outputs/local-eval \
  --device cpu
```

## Post-Submission Browser Monitoring

After every successful training or inference submission, open the OSMO workflow page in VS Code's SimpleBrowser so the user can track progress and access logs directly.

**Steps:**

1. Capture the workflow ID from the submission output (the line `Workflow ID - <id>`).
2. Construct the URL: `http://10.0.5.7/workflows/<workflow-id>`.
3. Open it with the `open_browser_page` tool (VS Code SimpleBrowser).
4. Tell the user that the **Logs** tab on that page streams live output per task (e.g., `lerobot-train`, `lerobot-infer`).

**Example — after training submission output:**

```text
Workflow ID - lerobot-training-31
Workflow Overview - http://10.0.5.7/workflows/lerobot-training-31
```

Open: `http://10.0.5.7/workflows/lerobot-training-31`

**Example — after inference submission output:**

```text
Workflow ID - lerobot-inference-20
Workflow Overview - http://10.0.5.7/workflows/lerobot-inference-20
```

Open: `http://10.0.5.7/workflows/lerobot-inference-20`

> The page has a **Logs** tab with per-task log streams. For training, select the `lerobot-train` task. For inference, select the `lerobot-infer` task. Use the OSMO CLI (`osmo workflow logs <id> -t <task> -n 100`) as a fallback when the browser is not reachable.

## Azure ML Portal Monitoring (Playwright)

After submitting a training job, and whenever the background eval poller reports a new inference job, open the Azure ML portal with Playwright to view live metrics and trajectory plots. Use `mcp_playwright_browser_navigate`, `mcp_playwright_browser_snapshot`, `mcp_playwright_browser_click`, and `mcp_playwright_browser_take_screenshot`.

### Training Metrics — Open Immediately After Submission

After the training job is submitted, navigate to the training experiment page and open the **Metrics** tab:

1. Construct the experiment URL from Azure environment variables in `scripts/.env`:

   ```text
   https://ml.azure.com/experiments/{experiment_name}?wsid=/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}/providers/Microsoft.MachineLearningServices/workspaces/{AZUREML_WORKSPACE_NAME}
   ```

2. Call `mcp_playwright_browser_navigate` with that URL.
3. Call `mcp_playwright_browser_snapshot` to confirm the page loaded and identify the latest run row in the table.
4. Click the first (most recent) run link.
5. On the run detail page, call `mcp_playwright_browser_snapshot` to locate the **Metrics** tab.
6. Click **Metrics**.
7. Call `mcp_playwright_browser_take_screenshot` and show the live training curves to the user.

Key metrics to surface: `train/loss`, `train/learning_rate` (confirm `1e-04`, not `1e-05`), `train/grad_norm`, `gpu_percent`.

Refresh by calling `mcp_playwright_browser_navigate` again on the same URL at any time.

> See [references/REFERENCE.md](references/REFERENCE.md) for exact click paths, tab selectors, and screenshot guidance.

### Inference / Eval Plots — Open When Poller Submits a Job

While the background eval poller is running, monitor the poller log and navigate to Azure ML to view trajectory plots as each inference job completes:

1. Tail the poller log to detect a new inference submission:

   ```bash
   tail -n 30 /tmp/<model-name>-eval.log | grep -E "Submitting|Workflow ID"
   ```

2. Construct the inference experiment URL using the `--experiment-name` passed to the poller:

   ```text
   https://ml.azure.com/experiments/{inference_experiment_name}?wsid=/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}/providers/Microsoft.MachineLearningServices/workspaces/{AZUREML_WORKSPACE_NAME}
   ```

3. Call `mcp_playwright_browser_navigate` with that URL.
4. Call `mcp_playwright_browser_snapshot` to identify the latest run row (most recently submitted checkpoint eval).
5. Click that run.
6. On the run detail page, click the **Images** tab.
7. Call `mcp_playwright_browser_take_screenshot` and show the trajectory plots to the user.

> The **Images** tab contains per-episode trajectory plots logged by the inference job (`episode_NNN_trajectory.png` and `eval_summary.png`). They appear after the OSMO inference workflow reaches `completed` status. If images are not yet present, check `osmo workflow query <inference-workflow-id>` and wait for `completed`.

## Parameters Reference

### Training Submission Parameters

| Parameter | Flag | Default | Description |
|-----------|------|---------|-------------|
| Dataset repo ID | `-d`, `--dataset` | (required) | HuggingFace dataset or blob dataset name |
| Policy type | `-p`, `--policy` | `act` | `act` or `diffusion` |
| Job name | `-j`, `--job-name` | `lerobot-act-training` | Unique job identifier |
| Training steps | `--steps` | `100000` | Total training iterations |
| Batch size | `--batch-size` | `32` | Training batch size (64 for 48GB GPUs) |
| Learning rate | `--learning-rate` | `1e-4` | Maps to `--policy.optimizer_lr` internally |
| Save frequency | `--save-freq` | `5000` | Checkpoint interval (model registered at each) |
| Validation split | `--val-split` | `0.1` | Ratio for train/val split |
| No val split | `--no-val-split` | — | Disable validation splitting |
| Register checkpoint | `-r` | (none) | Model name for Azure ML registration |
| From blob | `--from-blob` | `false` | Use Azure Blob Storage as data source |
| Storage account | `--storage-account` | (terraform) | Azure Storage account name |
| Blob prefix | `--blob-prefix` | (none) | Blob path prefix for dataset |

### Inference Submission Parameters

| Parameter | Flag | Default | Description |
|-----------|------|---------|-------------|
| Policy repo ID | `--policy-repo-id` | (required) | HuggingFace repo, or use `--from-aml-model` |
| From AML model | `--from-aml-model` | `false` | Load from AzureML model registry |
| Model name | `--model-name` | (none) | AzureML model registry name |
| Model version | `--model-version` | (none) | AzureML model version |
| Dataset repo ID | `-d`, `--dataset-repo-id` | (none) | HuggingFace dataset |
| From blob dataset | `--from-blob-dataset` | `false` | Download dataset from Azure Blob |
| Eval episodes | `--eval-episodes` | `10` | Number of episodes to evaluate |
| MLflow enable | `--mlflow-enable` | `false` | Log trajectory plots to AzureML |

### Continuous Evaluation Parameters (`poll-and-eval-checkpoints.sh`)

| Parameter | Flag | Default | Description |
|-----------|------|---------|-------------|
| Model name | `--model-name` | (required) | AzureML model registry name to watch |
| Training workflow | `--training-workflow-id` | (required) | OSMO workflow ID of the training job |
| Blob prefix | `--blob-prefix` | (required) | Blob path prefix for the evaluation dataset |
| Storage account | `--storage-account` | (from .env) | Azure Storage account |
| Eval episodes | `--eval-episodes` | `10` | Episodes per inference run |
| Job prefix | `--job-prefix` | (from model name) | Prefix for inference job names |
| Experiment name | `--experiment-name` | (from model name) | MLflow experiment for inference runs |
| Poll interval | `--poll-interval` | `60` | Seconds between AzureML registry polls |
| Max concurrent | `--max-concurrent` | `2` | Max simultaneous inference workflows |

### GPU Configuration Guidelines

| GPU | VRAM | Recommended Batch Size | Notes |
|-----|------|----------------------|-------|
| A10 | 24GB | 32 | Standard configuration |
| RTX PRO 6000 | 48GB | 64 | Requires `mig.strategy: single` |
| H100 | 80GB | 128 | Standard MIG disabled |

### Azure ML Context

Resolved from CLI flags > environment variables > Terraform outputs:

| Variable | Flag | Env Var |
|----------|------|---------|
| Subscription ID | `--azure-subscription-id` | `AZURE_SUBSCRIPTION_ID` |
| Resource group | `--azure-resource-group` | `AZURE_RESOURCE_GROUP` |
| Workspace name | `--azure-workspace-name` | `AZUREML_WORKSPACE_NAME` |

## Training Completion Estimation

Estimate training duration based on dataset and configuration:

| Dataset Size | Steps | GPU | Approximate Duration |
|-------------|-------|-----|---------------------|
| 20K frames / 64 episodes | 10,000 | A10 | ~30 minutes |
| 20K frames / 64 episodes | 100,000 | A10 | ~5 hours |
| 80K frames / 174 episodes | 100,000 | A10 | ~8 hours |
| 20K frames / 64 episodes | 100,000 | RTX PRO 6000 | ~3 hours |

Checkpoints are registered to AzureML at every `--save-freq` interval. Jobs may be evicted on spot GPU instances — checkpoints already registered remain available for inference even if the job is interrupted.

## OSMO CLI Reference

See [references/REFERENCE.md](references/REFERENCE.md) for full CLI and SDK documentation.

```bash
osmo workflow query <workflow-id>
osmo workflow logs <workflow-id> -n 100
osmo workflow logs <workflow-id> --error
osmo workflow list
osmo workflow cancel <workflow-id>
```

### Checkpoint Poller Commands

```bash
# Start continuous eval loop in background
nohup scripts/poll-and-eval-checkpoints.sh \
  --model-name <model-name> \
  --training-workflow-id <workflow-id> \
  --blob-prefix <dataset-blob-prefix> \
  > /tmp/<model-name>-eval.log 2>&1 & disown

# Monitor poller
tail -f /tmp/<model-name>-eval.log

# Check which versions have been submitted
cat /tmp/<model-name>-submitted-versions.txt

# Stop the poller early
pkill -f poll-and-eval-checkpoints
```

## Key Metrics Logged

| Metric | Description |
|--------|-------------|
| `train/loss` | Training loss per step |
| `train/grad_norm` | Gradient norm |
| `train/learning_rate` | Current learning rate (verify `1e-4` not `1e-5`) |
| `val/loss` | Validation loss (when val split enabled) |
| `gpu_percent` | GPU utilization (when system metrics enabled) |

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| `lr: 1e-05` in logs | `LEARNING_RATE` not mapped | Verify `train.py` maps to `--policy.optimizer_lr` |
| `KeyError: chunk_index` | v3.0 dataset not converted | Verify `download_dataset.py` has `patch_info_paths()` |
| `codebase_version` warning | Dataset still marked v3.0 | Verify `patch_info_paths()` sets `codebase_version = "v2.1"` |
| `CUDA_ERROR_NO_DEVICE` | MIG strategy misconfigured | Set `mig.strategy: single` for vGPU nodes |
| VM eviction mid-training | Spot GPU preempted | Checkpoints already registered to AML survive eviction |
| `ImportError: patch_info_paths` | Payload missing training fixes | Ensure `training/il/` includes `download_dataset.py` with `patch_info_paths` |
| OOM during training | Batch size too large | Reduce `--batch-size` (32 for 24GB, 64 for 48GB) |
| Poller exits immediately | Training workflow already terminal | Check `osmo workflow query <id>`; rerun poller or submit inference manually |
| Poller stalls at max-concurrent | Inference jobs not finishing | Check inference workflow status; increase `--max-concurrent` or cancel stuck jobs |
| Many pending inference jobs after stopping poller | Poller submitted jobs faster than cluster could drain | `osmo workflow list` only returns the last 12 — iterate over expected ID range to cancel all: `for id in $(seq <first> <last>); do osmo workflow cancel lerobot-inference-$id; done` |
| `info: command not found` in poller | `common.sh` not sourced | Verify `shared/lib/common.sh` exists and is readable |

See [references/REFERENCE.md](references/REFERENCE.md) for detailed debugging commands.

> Brought to you by microsoft/physical-ai-toolchain
