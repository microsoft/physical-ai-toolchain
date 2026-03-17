# Training

Policy training for physical AI using reinforcement learning, imitation learning, and vision-language-action approaches. Training runs in NVIDIA Isaac Lab containers on GPU nodes via AzureML or OSMO.

## 📁 Directory Structure

```text
training/
├── rl/                                # Reinforcement learning (SKRL, RSL-RL)
│   ├── scripts/                       # Training entry points and submission scripts
│   └── workflows/                     # AzureML and OSMO job definitions
├── il/                                # Imitation learning (LeRobot ACT/Diffusion)
│   ├── scripts/                       # LeRobot training and submission scripts
│   └── workflows/                     # AzureML and OSMO job definitions
├── vla/                               # Vision-Language-Action (planned)
│   ├── scripts/                       # Reserved for VLA training scripts
│   └── workflows/                     # Reserved for VLA job definitions
├── packaging/                         # Model export (ONNX, TensorRT)
│   └── scripts/                       # Export tooling
├── pipelines/                         # End-to-end training pipelines
├── setup/                             # Container preparation and dependency installation
├── specifications/                    # Training approach specifications
├── examples/                          # Example configurations
├── tests/                             # Unit tests for shared utilities
├── utils/                             # Shared training utilities (context, env, metrics)
├── stream.py                          # Shared streaming utilities
├── __init__.py                        # Package root
├── .amlignore                         # AzureML code snapshot exclusions
└── README.md                          # This file
```

## 🏋️ Training Approaches

| Approach               | Directory | Framework                            | Status  |
|------------------------|-----------|--------------------------------------|---------|
| Reinforcement Learning | `rl/`     | SKRL (primary), RSL-RL (alternative) | Active  |
| Imitation Learning     | `il/`     | LeRobot (ACT, Diffusion policies)    | Active  |
| Vision-Language-Action | `vla/`    | Multi-modal transformer policies     | Planned |

## 🚀 Submission

Training jobs submit via AzureML or OSMO. Each approach has dedicated submission scripts and workflow definitions.

| Approach | AzureML Script                                  | OSMO Script                                  |
|----------|-------------------------------------------------|----------------------------------------------|
| RL       | `rl/scripts/submit-azureml-training.sh`         | `rl/scripts/submit-osmo-training.sh`         |
| IL       | `il/scripts/submit-azureml-lerobot-training.sh` | `il/scripts/submit-osmo-lerobot-training.sh` |

## 📦 Packaging

Trained policies export to ONNX and TensorRT formats via `packaging/scripts/export_policy.py`. See [Packaging Specification](specifications/packaging.specification.md) for details.

## 📋 Specifications

| Document                                                     | Description                                       |
|--------------------------------------------------------------|---------------------------------------------------|
| [RL Training](specifications/rl-training.specification.md)   | SKRL, RSL-RL, Isaac Lab runtime configuration     |
| [IL Training](specifications/il-training.specification.md)   | LeRobot ACT/Diffusion policies, dataset injection |
| [VLA Training](specifications/vla-training.specification.md) | Multi-modal transformer training (planned)        |
| [Packaging](specifications/packaging.specification.md)       | ONNX/TensorRT model export                        |

> [!NOTE]
> Evaluation capabilities are managed separately and will be extracted to a dedicated evaluation domain.
