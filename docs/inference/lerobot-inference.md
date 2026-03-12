---
sidebar_position: 2
title: LeRobot ACT Policy Inference
description: Run a trained ACT policy locally, on OSMO with MLflow plots, or on a UR10E robot via ROS2
author: Microsoft Robotics-AI Team
ms.date: 2026-02-27
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

The trained checkpoint is available from two sources.

**From Azure ML:**

```bash
az ml model download \
  --name hve-robo-act-train --version 1 \
  --download-path ./checkpoint \
  --resource-group rg-osmorbt3-dev-001 \
  --workspace-name mlw-osmorbt3-dev-001
```

**From HuggingFace Hub:**

```bash
pip install huggingface-hub
huggingface-cli download alizaidi/hve-robo-act-train --local-dir ./checkpoint/hve-robo-act-train
```

Both produce the same directory:

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

### Install Dependencies

```bash
uv pip install lerobot av pyarrow
```

### Run Offline Inference

Validate the model against recorded dataset observations:

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

Run batch evaluation across multiple episodes on OSMO with trajectory plots logged directly to AzureML Studio via MLflow.

### Submit with MLflow Enabled

```bash
scripts/submit-osmo-lerobot-inference.sh \
  --policy-repo-id alizaidi/hve-robo-act-train \
  --dataset-repo-id alizaidi/hve-robo-cell \
  --eval-episodes 10 \
  --mlflow-enable \
  --experiment-name lerobot-act-eval
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

| Parameter           | Default      | Description                                 |
|---------------------|--------------|---------------------------------------------|
| `--policy-repo-id`  | (required)   | HuggingFace policy repository               |
| `--dataset-repo-id` | (none)       | HuggingFace dataset for replay evaluation   |
| `--eval-episodes`   | `10`         | Number of episodes to evaluate              |
| `--mlflow-enable`   | `false`      | Log plots and metrics to AzureML via MLflow |
| `--experiment-name` | auto-derived | MLflow experiment name                      |
| `--register-model`  | (none)       | Register model to AzureML after evaluation  |

## 🤖 ROS2 Deployment

For real robot control, use the ROS2 inference node in `src/inference/scripts/act_inference_node.py`.

### Data Classes

`src/inference/robot_types.py` defines the interface between the robot and the policy:

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

- [MLflow Integration](../training/mlflow-integration.md) for experiment tracking during training
- [LeRobot Training Guide](../training/lerobot-training.md) for training workflow configuration
- [Workflows README](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/README.md) for training workflow definitions
- [Scripts Reference](../reference/scripts.md) for submission script usage

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
