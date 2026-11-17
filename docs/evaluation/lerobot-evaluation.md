---
sidebar_position: 2
title: LeRobot ACT Policy Inference
description: Run a trained ACT policy locally, on OSMO with MLflow plots, or on a UR10E robot via ROS2
author: Microsoft Robotics-AI Team
ms.date: 2026-10-15
ms.topic: how-to
keywords:
  - lerobot
  - act
  - inference
  - ros2
  - ur10e
---

Run a trained ACT (Action Chunking with Transformers) policy locally against dataset observations or on a live UR10E robot via ROS2.

## 📋 Prerequisites

| Tool              | Version | Install                    |
|-------------------|---------|----------------------------|
| Python            | 3.10+   | System or `pyenv`          |
| `uv` or `pip`     | Latest  | `pip install uv`           |
| Azure CLI         | 2.50+   | `uv pip install azure-cli` |
| `az ml` extension | 2.22+   | `az extension add -n ml`   |

## 🚀 Quick Start

### Pull the Model

Use a model registered in your Azure ML workspace or the public Hugging Face example below.

**From Azure ML:**

Replace the placeholders with your model registration and workspace details.

```bash
az ml model download \
  --name "<model-name>" --version "<model-version>" \
  --download-path ./checkpoint \
  --resource-group "<resource-group>" \
  --workspace-name "<workspace-name>"
```

**From HuggingFace Hub:**

```bash
pip install huggingface-hub
huggingface-cli download alizaidi/hve-robo-act-train --local-dir ./checkpoint/hve-robo-act-train
```

Downloaded LeRobot checkpoints have a structure similar to this public example:

```text
hve-robo-act-train/
├── config.json                                                # Policy architecture config
├── model.safetensors                                          # Trained weights (197 MB)
├── policy_preprocessor.json                                   # Input normalization pipeline
├── policy_preprocessor_step_3_normalizer_processor.safetensors
├── policy_postprocessor.json                                  # Output unnormalization pipeline
├── policy_postprocessor_step_0_unnormalizer_processor.safetensors
└── train_config.json                                          # Training hyperparameters
```

> [!IMPORTANT]
> LeRobot checkpoints trained before 0.6 require migration before processor-aware inference. Do not evaluate migrated weights without loading the corresponding preprocessor and postprocessor pipelines. See [Migrate LeRobot Checkpoints](../training/lerobot-checkpoint-migration.md).

### Install Dependencies

```bash
uv pip install lerobot av pyarrow
```

### Run Offline Inference

Evaluate the model against recorded dataset observations:

```bash
python scripts/test-lerobot-inference.py \
  --policy-repo alizaidi/hve-robo-act-train \
  --dataset-dir /path/to/hve-robo-cell \
  --episode 0 --start-frame 100 --num-steps 30 \
  --device cuda
```

Use `--policy-repo ./checkpoint/hve-robo-act-train` when loading from a local path instead of HuggingFace Hub.

Expected output:

```text
Episode 0: 668 frames, starting at frame 100, testing 30 steps
  step   0: pred=[  0.001,   0.002,  -0.001,  -0.004,  -0.019,   0.000]  gt=[  0.001,   0.002,  -0.002,  -0.005,  -0.019,   0.000]

============================================================
Inference Results
============================================================
  Steps evaluated:    30
  MSE (all joints):   0.000004
  MAE (all joints):   0.001173
  Throughput:         130.0 steps/s
  Realtime capable:   yes (need 30 Hz)
```

## ⚙️ Configuration

### Inference Script Parameters

| Parameter       | Default                       | Description                             |
|-----------------|-------------------------------|-----------------------------------------|
| `--policy-repo` | `alizaidi/hve-robo-act-train` | HuggingFace repo ID or local path       |
| `--dataset-dir` | (required)                    | LeRobot v3 dataset root directory       |
| `--episode`     | `0`                           | Episode index for test observations     |
| `--start-frame` | `0`                           | Starting frame within the episode       |
| `--num-steps`   | `30`                          | Number of inference steps               |
| `--device`      | `cuda`                        | Inference device (`cuda`, `cpu`, `mps`) |
| `--output`      | (none)                        | Save predictions to `.npz` file         |

### Model Details

| Property          | Value                                   |
|-------------------|-----------------------------------------|
| Policy type       | ACT (Action Chunking with Transformers) |
| Parameters        | 51.6M                                   |
| State dim         | 6 (UR10E joint positions in radians)    |
| Action dim        | 6 (joint position deltas)               |
| Image input       | 480 x 848 RGB                           |
| Control frequency | 30 Hz                                   |
| Backbone          | ResNet-18                               |

## 📊 OSMO Evaluation with MLflow Plots

Run batch evaluation across multiple episodes on OSMO with trajectory plots logged through MLflow. Plots go to AzureML Studio by default; set `TRACKING_BACKEND=mlflow` and `MLFLOW_TRACKING_URI` in the job environment to log to a self-hosted MLflow server instead, or `TRACKING_BACKEND=none` to keep results in the output directory. See [Experiment Tracking](../training/experiment-tracking.md#-tracking-backends).

### Submit with MLflow Enabled

```bash
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --policy-repo-id alizaidi/hve-robo-act-train \
  --policy-revision "<policy-commit-sha>" \
  --dataset-repo-id alizaidi/hve-robo-cell \
  --dataset-revision "<dataset-commit-sha>" \
  --eval-episodes 10 \
  --mlflow-enable \
  --experiment-name lerobot-act-eval
```

### Policy Sources

The replay evaluation feeds each dataset frame's joint state and every recorded camera to the policy and compares its actions with the recorded ones. Pick one policy source:

| Option             | Policy                                                                                                                           |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------|
| `--policy-repo-id` | A Hugging Face repository at `--policy-revision`, or a path inside the task                                                      |
| `--policy-uri`     | A model registered with the tracking backend: `models:/<name>/<version>` or `runs:/<id>/<path>`                                  |
| `--policy-image`   | A runnable carrier from `fleet-deployment/setup/build-model-image.sh`, run as the task image; evaluates `/policy`                |
| `--from-aml-model` | A model in the Azure ML registry, with `--model-name` and `--model-version`                                                      |
| `--builtin-policy` | An untrained baseline: a one-step mint for `act` and `diffusion`, or the pinned `lerobot/smolvla_base`, unadapted, for `smolvla` |

A trained policy should show lower action error than the untrained baseline of its family on the same episodes. On the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md), `--local-dataset` evaluates a dataset from the workstation's datasets directory, and `--policy-uri` reads the local MLflow registry:

```bash
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --profile local \
  --policy-uri models:/so101-act/1 \
  --local-dataset so101-learner \
  --mlflow-enable
```

### SmolVLA

`-p smolvla` evaluates SmolVLA policies with the VLA lock, which carries the `smolvla` extra, and passes each frame's task text to the policy. A SmolVLA policy fine-tuned with `--rename-map` keeps that mapping in its saved preprocessor. The unadapted base needs the mapping at evaluation, so pass the same `--rename-map` that training used:

```bash
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --profile local \
  -p smolvla \
  --builtin-policy \
  --local-dataset my-learner-dataset \
  --rename-map '{"observation.images.front": "observation.images.camera1"}'
```

### Viewing Plots in AzureML Studio

Navigate to **AzureML Studio > Jobs > (run name) > Images**. The left panel shows a folder tree organized by episode, and plots render inline with tab navigation across all images.

Each episode produces four plots plus one aggregate summary across all episodes:

| Plot                       | Description                                                       |
|----------------------------|-------------------------------------------------------------------|
| `action_deltas.png`        | Per-joint predicted vs ground truth action overlays               |
| `cumulative_positions.png` | Reconstructed absolute joint positions                            |
| `error_heatmap.png`        | Time x joint absolute error heatmap                               |
| `summary_panel.png`        | 2x2 panel: all joints, error boxplots, latency, MAE bars          |
| `aggregate_summary.png`    | Cross-episode comparison of MAE, MSE, throughput, per-joint error |

Numeric metrics are on the **Metrics** tab: per-episode values (`ep0_mse`, `ep0_mae`, `ep0_throughput_hz`) and aggregate summaries (`aggregate_mse`, `aggregate_mae`).

### OSMO Inference Script Parameters

| Parameter           | Default      | Description                                                           |
|---------------------|--------------|-----------------------------------------------------------------------|
| `--policy-repo-id`  | (none)       | HuggingFace policy repository; see Policy Sources for the others      |
| `-p, --policy-type` | `act`        | Policy architecture: `act`, `diffusion`, or `smolvla`                 |
| `--rename-map`      | (none)       | JSON map from dataset feature keys to the policy's keys               |
| `--dataset-repo-id` | (none)       | HuggingFace dataset for replay evaluation                             |
| `--local-dataset`   | (none)       | Dataset folder in the local datasets directory (local profile)        |
| `--eval-episodes`   | `10`         | Number of episodes to evaluate                                        |
| `--mlflow-enable`   | `false`      | Log plots and metrics through MLflow to the selected tracking backend |
| `--experiment-name` | auto-derived | MLflow experiment name                                                |
| `--register-model`  | (none)       | Register model to AzureML after evaluation (`azureml` backend only)   |

## 🤖 ROS2 Deployment

For real robot control, use the ROS2 inference node in `fleet-deployment/inference/act_inference_node.py`.

### Data Classes

`evaluation/sil/robot_types.py` defines the interface between the robot and the policy:

| Type                               | Maps to                    | Shape                 |
|------------------------------------|----------------------------|-----------------------|
| `RobotObservation.joint_positions` | `observation.state`        | `(6,)` radians        |
| `RobotObservation.color_image`     | `observation.images.color` | `(480, 848, 3)` uint8 |
| `JointPositionCommand.positions`   | `action`                   | `(6,)` radians        |

### Dry Run (No Robot Commands)

```bash
ros2 run lerobot_inference act_inference_node \
  --ros-args -p policy_repo:=alizaidi/hve-robo-act-train \
             -p device:=cuda \
             -p enable_control:=false
```

Monitor predictions on `/lerobot/status`.

### Live Control

```bash
ros2 run lerobot_inference act_inference_node \
  --ros-args -p policy_repo:=alizaidi/hve-robo-act-train \
             -p device:=cuda \
             -p enable_control:=true \
             -p action_mode:=delta
```

> [!WARNING]
> Set `enable_control:=false` first and verify predictions on `/lerobot/status` are reasonable before enabling live robot commands.

### ROS2 Node Parameters

| Parameter            | Default                       | Description                            |
|----------------------|-------------------------------|----------------------------------------|
| `policy_repo`        | `alizaidi/hve-robo-act-train` | Model source                           |
| `device`             | `cuda`                        | Inference device                       |
| `control_hz`         | `30.0`                        | Control loop frequency                 |
| `action_mode`        | `delta`                       | `delta` (add to current) or `absolute` |
| `enable_control`     | `false`                       | Publish commands to the robot          |
| `camera_topic`       | `/camera/color/image_raw`     | RGB image topic                        |
| `joint_states_topic` | `/joint_states`               | Joint state topic                      |

### ROS2 Topics

| Topic                     | Type                              | Direction |
|---------------------------|-----------------------------------|-----------|
| `/joint_states`           | `sensor_msgs/JointState`          | Subscribe |
| `/camera/color/image_raw` | `sensor_msgs/Image`               | Subscribe |
| `/lerobot/joint_commands` | `trajectory_msgs/JointTrajectory` | Publish   |
| `/lerobot/status`         | `std_msgs/String`                 | Publish   |

## 🔗 Related Documentation

- [Migrate LeRobot Checkpoints](../training/lerobot-checkpoint-migration.md) for pre-0.6 checkpoint conversion
- [MLflow Integration](../training/mlflow-integration.md) for experiment tracking during training
- [LeRobot Training Guide](../training/lerobot-training.md) for training workflow configuration
- [Workflows README](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/README.md) for training workflow definitions
- [Scripts Reference](../reference/scripts.md) for submission script usage

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
