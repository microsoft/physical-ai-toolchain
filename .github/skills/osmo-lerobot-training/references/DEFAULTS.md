# Training Defaults

Default configurations for LeRobot imitation learning training, resolved automatically by the OSMO Training Manager agent. Values here override hardcoded defaults when submitting training or inference jobs.

## Azure Environment

These values are loaded from `scripts/.env`. The agent should source this file before any `az` or `osmo` commands.

```yaml
env_file: scripts/.env
subscription_id: from AZURE_SUBSCRIPTION_ID
resource_group: from AZURE_RESOURCE_GROUP
workspace_name: from AZUREML_WORKSPACE_NAME
storage_account: from AZURE_STORAGE_ACCOUNT_NAME
storage_container: datasets
```

The agent does NOT need to pass `--azure-subscription-id`, `--azure-resource-group`, or `--azure-workspace-name` flags — the submission scripts resolve these automatically from `scripts/.env` and Terraform outputs.

## Known Datasets

Datasets available in Azure Blob Storage for training and inference:

| Dataset          | Blob Prefix      | Robot | Episodes | Frames | FPS | Action Dims | Camera Key                     |
| ---------------- | ---------------- | ----- | -------- | ------ | --- | ----------- | ------------------------------ |
| `ur10e_episodes` | `ur10e_episodes` | UR10e | 64       | 20,251 | 30  | 6           | `observation.images.il-camera` |

When a user references a known dataset by name, auto-populate `--from-blob`, `--storage-account`, `--blob-prefix`, and `--no-val-split`.

## GPU Training Profiles

Recommended training configurations per GPU type. Select based on available hardware.

### A10 (24GB VRAM)

Standard configuration for most training runs.

```yaml
batch_size: 32
learning_rate: 1e-4
save_freq: 10000
val_split: disabled (--no-val-split)
estimated_speed: ~2 steps/sec
notes: Default GPU for OSMO spot instances
```

### RTX PRO 6000 (48GB VRAM)

Higher throughput with larger batch sizes.

```yaml
batch_size: 64
learning_rate: 1e-4
save_freq: 10000
val_split: disabled (--no-val-split)
estimated_speed: ~4 steps/sec
notes: |
  Requires mig.strategy: single in GPU operator config.
  Azure vGPU host enables MIG; strategy: none causes CUDA_ERROR_NO_DEVICE.
  NVIDIA GPU Operator driver deployment must be disabled (Azure GRID drivers pre-installed).
```

### H100 (80GB VRAM)

Maximum throughput for large-scale training.

```yaml
batch_size: 128
learning_rate: 1e-4
save_freq: 10000
val_split: disabled (--no-val-split)
estimated_speed: ~8 steps/sec
notes: Standard datacenter driver via GPU Operator. MIG disabled.
```

## Training Duration Estimates

Based on observed runs with the ur10e_episodes dataset (64 episodes, 20K frames):

| Steps   | A10 (32 batch) | RTX PRO 6000 (64 batch) | H100 (128 batch) |
| ------- | -------------- | ----------------------- | ---------------- |
| 10,000  | ~80 min        | ~40 min                 | ~20 min          |
| 50,000  | ~7 hours       | ~3.5 hours              | ~1.7 hours       |
| 100,000 | ~14 hours      | ~7 hours                | ~3.5 hours       |

Spot GPU instances may be evicted during long runs. Checkpoints registered to AzureML at each `--save-freq` interval survive eviction.

## Naming Convention

The agent generates unique names using the dataset name and current date:

```yaml
job_name: "{dataset}-act-train-{MMDD}"
experiment_name: "{dataset}-act-training-{MMDD}"
model_name: "{dataset}-act-model-{MMDD}"
inference_job_name: "{dataset}-act-eval-{MMDD}"
inference_experiment_name: "{dataset}-act-inference-{MMDD}"
```

## Inference Defaults

Post-training evaluation defaults:

```yaml
eval_episodes: 10
mlflow_enable: true
policy_source: from-aml-model (use latest registered version)
dataset_source: same blob storage as training
local_device: cpu (for local inference)
```
