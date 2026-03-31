---
title: Inference Guide
description: Run trained robotics policies in local, OSMO, and ROS 2 deployment modes.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-31
ms.topic: overview
keywords:
  - inference
  - lerobot
  - osmo
  - ros2
  - evaluation
---

Use this section to run and validate trained policies after training completes. Inference workflows support local replay, OSMO-managed SIL execution, and ROS 2 integration for robot control.

## Inference Paths

| Path          | Primary use                                          | Starting document                                             |
| ------------- | ---------------------------------------------------- | ------------------------------------------------------------- |
| Local replay  | Fast functional validation against recorded episodes | [LeRobot Inference](lerobot-inference.md)                     |
| OSMO workflow | SIL evaluation with orchestration, logs, and metrics | [OSMO Evaluation Workflows](../evaluation/osmo-evaluation.md) |
| ROS 2 runtime | Online robot policy execution                        | [LeRobot Inference](lerobot-inference.md#ros-2-runtime)       |

## Quick Start

Run OSMO inference and evaluation:

```bash
cd training/il/scripts
./submit-osmo-lerobot-inference.sh \
  --policy-repo-id <huggingface-policy-repo> \
  --dataset-repo-id <huggingface-dataset-repo>
```

Run local replay:

```bash
python scripts/test-lerobot-inference.py \
  --policy-repo <huggingface-or-local-policy> \
  --dataset-dir <local-dataset-path> \
  --episode 0 --num-steps 30
```

## Related Documentation

- [LeRobot Inference](lerobot-inference.md)
- [LeRobot ACT Policy Inference](../evaluation/lerobot-evaluation.md)
- [OSMO Evaluation Workflows](../evaluation/osmo-evaluation.md)
- [Training Guide](../training/README.md)
- [Script Reference](../reference/scripts.md)
