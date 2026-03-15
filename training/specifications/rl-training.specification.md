# RL Training

Reinforcement learning training for locomotion and manipulation policies using NVIDIA Isaac Lab simulation.

## Status

Active — primary training approach.

## Components

| Component | Description |
|-----------|-------------|
| SKRL | Primary RL framework for PPO-based policy training |
| RSL-RL | Alternative RL framework for legged locomotion |
| Isaac Lab 2.3.2 | NVIDIA simulation environment (`nvcr.io/nvidia/isaac-lab:2.3.2`) |
| MLflow | Experiment tracking with monkey-patched agent metric interception |

## Runtime Environment

| Setting | Value |
|---------|-------|
| Container | `nvcr.io/nvidia/isaac-lab:2.3.2` |
| Python | `/isaac-sim/kit/python/bin/python3` via `isaaclab.sh -p` wrapper |
| numpy | `>=1.26.0,<2.0.0` (ABI compatibility with Isaac Sim) |
| EULA | `ACCEPT_EULA=Y`, `PRIVACY_CONSENT=Y` required |
| Vulkan | `NVIDIA_DRIVER_CAPABILITIES=all` |

## Submission Paths

| Platform | Script | Workflow |
|----------|--------|----------|
| AzureML | `training/rl/scripts/submit-azureml-training.sh` | `training/rl/workflows/azureml/train.yaml` |
| OSMO | `training/rl/scripts/submit-osmo-training.sh` | `training/rl/workflows/osmo/train.yaml` |

## Checkpoint Flow

Training writes checkpoints to local filesystem. The `TRAINING_CHECKPOINT_OUTPUT` environment variable controls the output path. AzureML uploads checkpoints as `uri_folder` artifacts.
