# IL Training

Imitation learning training using LeRobot for behavioral cloning with ACT and Diffusion policies.

## Status

Active — secondary training approach for manipulation tasks.

## Components

| Component        | Description                                           |
|------------------|-------------------------------------------------------|
| LeRobot          | Hugging Face imitation learning framework             |
| ACT              | Action Chunking with Transformers policy architecture |
| Diffusion Policy | Diffusion-based action prediction                     |
| MLflow           | Experiment tracking for training metrics              |

## Runtime Environment

LeRobot is runtime-installed via `uv pip` inside the Isaac Lab container. Training uses the same base container as RL training with additional Python dependencies.

| Setting        | Value                                       |
|----------------|---------------------------------------------|
| Container      | `nvcr.io/nvidia/isaac-lab:2.3.2`            |
| Framework      | LeRobot (installed at runtime via `uv pip`) |
| Dataset format | Hugging Face LeRobot-compatible             |

## Submission Paths

| Platform | Script                                                   | Workflow                                           |
|----------|----------------------------------------------------------|----------------------------------------------------|
| AzureML  | `training/il/scripts/submit-azureml-lerobot-training.sh` | `training/il/workflows/azureml/lerobot-train.yaml` |
| OSMO     | `training/il/scripts/submit-osmo-lerobot-training.sh`    | `training/il/workflows/osmo/lerobot-train.yaml`    |

## Dataset Injection

OSMO supports two payload strategies for dataset delivery:

| Strategy                 | Size Limit | Mechanism                                   |
|--------------------------|------------|---------------------------------------------|
| Base64-encoded archive   | ~1 MB      | Embedded in workflow YAML                   |
| Dataset folder injection | Unlimited  | Versioned folder, name in workflow env vars |
