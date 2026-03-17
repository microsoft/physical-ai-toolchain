# OSMO LeRobot Training Reference

Detailed CLI commands, Python SDK patterns, inference evaluation, and troubleshooting for LeRobot training on OSMO.

## OSMO CLI Reference

### Workflow Submission

```bash
# Train from Azure Blob Storage with model registration
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

# Train from HuggingFace Hub
scripts/submit-osmo-lerobot-training.sh \
  -d user/dataset \
  -p act \
  --steps 50000 \
  -r my-model-name

# Larger batch for RTX PRO 6000 (48GB VRAM)
scripts/submit-osmo-lerobot-training.sh \
  -d my-robot-dataset \
  --from-blob \
  --storage-account mystorageaccount \
  --blob-prefix my-robot-dataset \
  --no-val-split \
  --steps 100000 \
  --batch-size 64 \
  --learning-rate 1e-4 \
  --save-freq 10000 \
  -j my-robot-train-rtx \
  --experiment-name my-robot-training \
  -r my-robot-act-model-rtx
```

### Workflow Monitoring

```bash
# List all workflows
osmo workflow list

# Query workflow status (returns task table with statuses)
osmo workflow query <workflow-id>

# Stream live logs
osmo workflow logs <workflow-id>

# Show last N lines of logs
osmo workflow logs <workflow-id> -n 100

# Show only error output
osmo workflow logs <workflow-id> --error

# Cancel a running workflow
osmo workflow cancel <workflow-id>

# Interactive shell into running container
osmo workflow exec <workflow-id> --task lerobot-train
```

### Log Parsing for Progress

Training log lines follow this pattern:

```text
step:10000 smpl:320000 ep:5000 epch:78.12 loss:0.1234 grdn:1.5678 lr:1.0000e-04 updt_s:0.123 data_s:0.012
```

Key fields to monitor:

- `step` / total steps = completion percentage
- `loss` trending downward = convergence
- `lr` should be `1e-04` (not `1e-05`, which indicates the learning rate fix is missing)
- `grdn` (gradient norm) > 10 may indicate instability

### Workflow Status Values

| Status            | Meaning                                |
| ----------------- | -------------------------------------- |
| `pending`         | Queued, awaiting resources             |
| `running`         | Actively executing                     |
| `completed`       | Finished successfully                  |
| `failed`          | Exited with error                      |
| `failed_canceled` | Manually canceled but was running fine |
| `cancelled`       | Canceled before starting               |

## Post-Training Inference Evaluation

### OSMO Inference (GPU)

Replay dataset episodes through the trained policy and compare predicted vs ground truth actions:

```bash
# Check registered model versions
source scripts/.env && az ml model list \
  --name my-robot-act-model \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --workspace-name "$AZUREML_WORKSPACE_NAME" \
  --query "[].{name:name, version:version, description:description}" -o table

# Submit inference with the latest checkpoint
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
```

### Local Inference (CPU/MPS)

Run inference locally for quick validation without GPU allocation:

```bash
python scripts/run-local-lerobot-inference.py \
  --model-name my-robot-act-model \
  --model-version 3 \
  --dataset-dir /path/to/local/dataset \
  --episodes 5 \
  --output-dir outputs/local-eval \
  --device cpu
```

Local inference handles:

- Auto-download from AzureML model registry via `--model-name`/`--model-version`
- Stripping incompatible config fields (`use_peft`, `pretrained_path`) from older checkpoints
- Loading normalizer stats from preprocessor safetensors files
- Both v3.0 (`file-NNN`) and v2.1 (`episode_NNNNNN`) dataset formats
- Per-episode trajectory plots and aggregate metrics

### Inference Output

Inference produces:

- Per-episode `.npz` files with predicted/ground_truth/inference_times arrays
- Per-episode trajectory plots (action deltas, summary panel)
- Aggregate `eval_results.json` with MSE, MAE, throughput metrics
- MLflow plots and metrics (when `--mlflow-enable` is set)

### Periodic Evaluation Schedule

For long training runs, use `scripts/poll-and-eval-checkpoints.sh` to automatically evaluate each checkpoint as it is registered. Launch it in the background immediately after submitting training:

```bash
nohup scripts/poll-and-eval-checkpoints.sh \
  --model-name my-robot-act-model \
  --training-workflow-id lerobot-training-32 \
  --blob-prefix my-robot-dataset \
  --job-prefix my-robot-eval \
  --experiment-name my-robot-inference \
  --poll-interval 60 \
  --max-concurrent 2 \
  > /tmp/my-robot-eval.log 2>&1 & disown
```

The poller:

- Polls AzureML every `--poll-interval` seconds for new versions of `--model-name`
- Submits `submit-osmo-lerobot-inference.sh` for each new version
- Caps concurrent inference workflows at `--max-concurrent`
- Stops automatically when the training workflow reaches a terminal state
- Tracks submitted versions in `/tmp/<model-name>-submitted-versions.txt`
- Logs all activity to `/tmp/<model-name>-eval.log`

For manual evaluation of specific checkpoints without the poller:

| Checkpoint                       | When to Evaluate                   | Purpose                                          |
| -------------------------------- | ---------------------------------- | ------------------------------------------------ |
| First (e.g., step 10,000)        | After first `--save-freq` interval | Sanity check — policy produces non-zero actions  |
| Mid-training (e.g., step 50,000) | ~50% completion                    | Convergence check — loss should be declining     |
| Final (last registered version)  | After training completes           | Full evaluation — compare to earlier checkpoints |

## AzureML Portal Navigation (Playwright)

Step-by-step Playwright navigation for the Azure ML portal. Use these patterns to open training metrics and inference trajectory plots during active workflows.

### URL Construction

Build portal deep-links from variables in `scripts/.env`:

| Page                 | URL Pattern                                                                                                                                                                                                            |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Experiment runs list | `https://ml.azure.com/experiments/{experiment_name}?wsid=/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}/providers/Microsoft.MachineLearningServices/workspaces/{AZUREML_WORKSPACE_NAME}` |
| Direct run           | `https://ml.azure.com/runs/{run_id}?wsid=/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}/providers/Microsoft.MachineLearningServices/workspaces/{AZUREML_WORKSPACE_NAME}`                 |

Resolve the URL before navigating:

```bash
source scripts/.env
echo "https://ml.azure.com/experiments/${EXPERIMENT_NAME}?wsid=/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.MachineLearningServices/workspaces/${AZUREML_WORKSPACE_NAME}"
```

If the deep link does not load (portal prompts for sign-in), navigate to `https://ml.azure.com` first to establish the browser session, then re-navigate to the experiment URL.

### Training Metrics Navigation

```text
1. mcp_playwright_browser_navigate  → experiment page URL
2. mcp_playwright_browser_snapshot  → locate run table; most recent run is first row
3. mcp_playwright_browser_click     → click run name link (first row)
4. mcp_playwright_browser_snapshot  → confirm run detail page (look for "Job detail" heading or run ID)
5. mcp_playwright_browser_click     → click "Metrics" tab
6. mcp_playwright_browser_snapshot  → confirm metric charts loaded (train/loss chart visible)
7. mcp_playwright_browser_take_screenshot → show training curves to user
```

Expected metrics in the Metrics pane:

| Metric                | Expected Behaviour                                 |
| --------------------- | -------------------------------------------------- |
| `train/loss`          | Rapid descent early, gradual convergence           |
| `train/learning_rate` | Flat at `1e-04` (flag `1e-05` as misconfiguration) |
| `train/grad_norm`     | Stable; spikes > 10 indicate instability           |
| `gpu_percent`         | Sustained high utilization (>70%)                  |
| `gpu_memory_percent`  | Below VRAM limit                                   |

### Inference Job Plots Navigation

```text
1. mcp_playwright_browser_navigate  → inference experiment page URL
2. mcp_playwright_browser_snapshot  → locate run table; latest run = most recently submitted checkpoint eval
3. mcp_playwright_browser_click     → click run name link (first row)
4. mcp_playwright_browser_snapshot  → confirm run detail page loaded
5. mcp_playwright_browser_click     → click "Images" tab
6. mcp_playwright_browser_snapshot  → confirm trajectory plot images have loaded
7. mcp_playwright_browser_take_screenshot → show plots to user
```

Expected images in the Images pane:

| Image                        | Content                                                          |
| ---------------------------- | ---------------------------------------------------------------- |
| `episode_NNN_trajectory.png` | Per-episode action delta overlay (predicted vs ground truth)     |
| `eval_summary.png`           | Aggregate summary panel (MSE, MAE across all evaluated episodes) |

If images are absent, the OSMO inference workflow is still running. Check status and wait:

```bash
osmo workflow query <inference-workflow-id>
# Wait for status: completed
```

### Polling for New Eval Runs

When the checkpoint poller is active, new inference runs appear in AzureML after each `--save-freq` interval. Workflow to track progress:

1. Check the poller log for newly submitted inference workflows:

   ```bash
   tail -n 30 /tmp/<model-name>-eval.log | grep -E "Submitting|Workflow ID|version"
   ```

2. Refresh the inference experiment page by calling `mcp_playwright_browser_navigate` again with the same URL.
3. The new run will appear at the top of the run table — click it → **Images** tab → screenshot.
4. Repeat after each new checkpoint is registered (every `--save-freq` training steps).

## Azure ML Metric Retrieval

### Python SDK Pattern

```python
from azure.identity import DefaultAzureCredential
from azure.ai.ml import MLClient
import mlflow

credential = DefaultAzureCredential()
ml_client = MLClient(credential, subscription_id, resource_group, workspace_name)
workspace = ml_client.workspaces.get(workspace_name)
mlflow.set_tracking_uri(workspace.mlflow_tracking_uri)

# Search for training runs
runs = mlflow.search_runs(
    experiment_names=["my-robot-training"],
    order_by=["start_time DESC"],
    max_results=10,
)

# Get metric history for a specific run
client = mlflow.MlflowClient()
history = client.get_metric_history(run_id, "train/loss")
```

### Key Metrics Logged

| Metric                | Description                                   |
| --------------------- | --------------------------------------------- |
| `train/loss`          | Training loss per step                        |
| `train/grad_norm`     | Gradient norm                                 |
| `train/learning_rate` | Current learning rate                         |
| `train/samples`       | Cumulative samples processed                  |
| `train/episodes`      | Cumulative episodes processed                 |
| `train/epoch`         | Current epoch number                          |
| `train/update_time_s` | Time per training step                        |
| `train/data_time_s`   | Data loading time per step                    |
| `val/loss`            | Validation loss (when val split enabled)      |
| `gpu_percent`         | GPU utilization (when system metrics enabled) |
| `gpu_memory_percent`  | GPU memory usage                              |
| `cpu_percent`         | CPU utilization                               |
| `ram_percent`         | RAM usage                                     |

### Model Registry

```bash
# List model versions
source scripts/.env && az ml model list \
  --name my-robot-act-model \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --workspace-name "$AZUREML_WORKSPACE_NAME" -o table

# Models are registered at each --save-freq checkpoint
# Higher version numbers correspond to later training steps
# Version descriptions include the checkpoint step number
```

## Training Progress Interpretation

### Loss Curve Analysis

- ACT policy: Expect rapid initial descent over first 5-10k steps (loss 6→2), then gradual convergence (loss 2→0.1). Typical convergence at 50-100k steps.
- Diffusion policy: Slower convergence, loss may plateau and resume descent. Requires 50-100k steps minimum.
- If `train/learning_rate` shows `1e-05` instead of specified `1e-04`, the learning rate mapping fix is missing.

### Checkpoint Registration Timeline

Checkpoints are registered to AzureML at each `--save-freq` interval during training:

- Step 5,000 → model version 1
- Step 10,000 → model version 2
- Training complete → final `register_final_checkpoint()` for the last checkpoint

The training wrapper (`train.py`) scans for new checkpoints every 60 seconds and uploads them incrementally via `upload_new_checkpoints()`.

### Spot GPU Eviction

Training on spot instances may be interrupted by VM eviction. The training pipeline is designed for resilience:

- Checkpoints already registered to AzureML survive eviction
- The latest registered model version is available for inference regardless of training completion
- Resubmit the same job to continue from the next unregistered step

## LeRobot v0.3.x API Notes

### Learning Rate Configuration

LeRobot 0.3.x uses a preset system where `TrainPipelineConfig.__post_init__` overrides `optimizer`/`scheduler` with policy presets when `use_policy_training_preset=True` (default). The correct way to set learning rate is `--policy.optimizer_lr` (not `--optimizer.lr`). The submission script maps `LEARNING_RATE` → `--policy.optimizer_lr` in `train.py`.

ACT policy default: `optimizer_lr: float = 1e-5`. This is 10x lower than the typical `1e-4` specified by users.

### Dataset Format Conversion

v3.0 datasets from Azure Blob use `{chunk_index}/{file_index}` path templates. LeRobot v0.3.x expects `{episode_chunk}/{episode_index}`. The `download_dataset.py` pipeline handles conversion:

1. `patch_info_paths()` — splits monolithic parquet, reorganizes videos, updates path templates, sets `codebase_version = "v2.1"`
2. `patch_image_stats()` — adds ImageNet normalization stats for video/image features
3. `fix_video_timestamps()` — resets cumulative timestamps to per-episode
4. `ensure_tasks_jsonl()` — creates required metadata files
5. `ensure_episodes_stats()` — computes per-episode statistics

### Inference API

LeRobot 0.3.x does NOT have `PolicyProcessorPipeline`. The `ACTPolicy.select_action()` method calls `normalize_inputs()` and `unnormalize_outputs()` internally. Pass raw tensors with batch dimension directly:

```python
obs = {
    "observation.state": torch.from_numpy(state).float().unsqueeze(0).to(device),
    image_key: (torch.from_numpy(image).float().permute(2, 0, 1) / 255.0).unsqueeze(0).to(device),
}
action = policy.select_action(obs)
action_np = action.squeeze(0).cpu().numpy()
```

### Checkpoint Compatibility

Older checkpoints may have incompatible `config.json` fields or missing normalizer buffers:

- Strip `use_peft`, `pretrained_path`, `peft_config` from config.json before loading
- Load normalizer stats from `policy_preprocessor_step_3_normalizer_processor.safetensors` into policy buffers
- The local inference script handles both automatically

## Common Issues

| Symptom                                | Likely Cause                                        | Resolution                                                         |
| -------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------ |
| `lr: 1e-05` in training logs           | LEARNING_RATE not mapped to `--policy.optimizer_lr` | Update `train.py` env_arg_map                                      |
| `KeyError: 'chunk_index'`              | v3.0 path templates not converted                   | Verify `patch_info_paths()` runs during dataset prep               |
| `KeyError: 'file_index'`               | Partial template fix                                | Check both `data_path` and `video_path` templates                  |
| Video `MISSING` in verification        | Videos renamed but not moved to correct chunk dirs  | Verify video reorganization in `patch_info_paths()`                |
| `codebase_version` warning             | Dataset marked v3.0 after conversion                | Set `info["codebase_version"] = "v2.1"`                            |
| `CUDA_ERROR_NO_DEVICE`                 | MIG strategy misconfigured on vGPU                  | Set `mig.strategy: single` for RTX PRO 6000                        |
| `ImportError: PolicyProcessorPipeline` | Using old inference API                             | Remove preprocessor/postprocessor, use `select_action()` directly  |
| `DecodingError: use_peft not valid`    | Old checkpoint config.json                          | Strip `use_peft`, `pretrained_path` from config.json               |
| `AssertionError: mean is infinity`     | Normalizer buffers missing                          | Load stats from preprocessor safetensors files                     |
| `ImportError: patch_info_paths`        | Payload missing training fixes                      | Ensure `training/il/` is on a branch with dataset conversion code |
| VM eviction during training            | Spot GPU preempted                                  | Checkpoints already registered survive; resubmit job               |
| MLflow connection timeout              | Token refresh failure                               | Check `MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES`                      |
| OOM during training                    | Batch size too large for GPU                        | 32 for 24GB (A10), 64 for 48GB (RTX PRO 6000)                      |

## Troubleshooting

### OSMO Workflow Debugging

```bash
# Check error logs
osmo workflow logs <workflow-id> --error

# Check all workflows (training and inference)
osmo workflow list

# Grep training logs for learning rate
osmo workflow logs <workflow-id> 2>&1 | grep "lr:"

# Grep for checkpoint registration
osmo workflow logs <workflow-id> 2>&1 | grep "Registered"

# Check dataset preparation
osmo workflow logs <workflow-id> 2>&1 | grep -E "Patched|Reorganized|Split"
```

### Azure ML Connectivity

```bash
# Verify workspace access
source scripts/.env && az ml workspace show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZUREML_WORKSPACE_NAME"

# List registered models
source scripts/.env && az ml model list \
  --name my-robot-act-model \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --workspace-name "$AZUREML_WORKSPACE_NAME" -o table
```
