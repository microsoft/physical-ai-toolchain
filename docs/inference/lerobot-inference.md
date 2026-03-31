---
title: LeRobot Inference
description: Execute trained LeRobot policies with local replay, OSMO SIL workflows, and ROS 2 runtime integration.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-31
ms.topic: how-to
keywords:
  - lerobot
  - inference
  - sil
  - osmo
  - ros2
---

LeRobot inference in this repository follows three execution modes: local replay for quick checks, OSMO workflows for SIL evaluation, and ROS 2 runtime deployment for robot control loops.

## Prerequisites

| Component      | Requirement                                                             |
| -------------- | ----------------------------------------------------------------------- |
| Trained policy | HuggingFace repo ID or local checkpoint directory                       |
| Dataset        | LeRobot-compatible dataset for replay and SIL evaluation                |
| Runtime tools  | Python 3.10+, Azure CLI for Azure ML access, OSMO CLI for workflow mode |
| Infrastructure | OSMO deployed for SIL mode, ROS 2 stack configured for robot mode       |

## Mode 1: Local Replay

Use local replay to confirm policy loading, tensor shapes, and action generation before consuming cluster resources.

```bash
python scripts/test-lerobot-inference.py \
  --policy-repo <huggingface-or-local-policy> \
  --dataset-dir <dataset-root> \
  --episode 0 --start-frame 0 --num-steps 30
```

## Mode 2: OSMO SIL Evaluation

Use OSMO for repeatable inference and evaluation in Kubernetes-managed GPU environments.

```bash
cd training/il/scripts
./submit-osmo-lerobot-inference.sh \
  --policy-repo-id <huggingface-policy-repo> \
  --dataset-repo-id <huggingface-dataset-repo> \
  --eval-episodes 10
```

Monitor progress:

```bash
osmo workflow list
osmo workflow logs <workflow-id> --follow
```

## ROS 2 Runtime

Use ROS 2 runtime for online deployment to robot control topics after SIL validation passes.

```bash
ros2 run lerobot_inference act_inference_node \
  --ros-args -p policy_repo:=<huggingface-policy-repo> \
             -p device:=cuda \
             -p enable_control:=false
```

> [!WARNING]
> Validate with `enable_control:=false` before publishing live commands.

## Output and Verification

| Check         | Expected result                                                    |
| ------------- | ------------------------------------------------------------------ |
| Local replay  | Stable inference throughput and sane action values                 |
| OSMO workflow | Workflow reaches `Completed` and metrics are published             |
| ROS 2 runtime | Status topic updates and command topic output follows control rate |

## Related Documentation

- [LeRobot ACT Policy Inference](../evaluation/lerobot-evaluation.md)
- [OSMO Evaluation Workflows](../evaluation/osmo-evaluation.md)
- [LeRobot Training](../training/lerobot-training.md)
- [Script Reference](../reference/scripts.md)
