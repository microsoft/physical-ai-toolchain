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
├── vla/                               # Vision-Language-Action training
│   ├── scripts/                       # Calibration and training entry points
│   └── workflows/                     # Azure ML and OSMO workflows
├── smoke/                             # Azure ML GPU smoke test
│   ├── scripts/                       # Smoke test, job entry point, and submission script
│   └── workflows/                     # AzureML job definition
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
| Vision-Language-Action | `vla/`    | Multi-modal transformer policies     | Active  |

## 🚀 Submission

Training jobs submit via Azure ML or OSMO. Each approach has dedicated submission scripts and workflow definitions.

> [!IMPORTANT]
> Azure ML VLA training is a two-job workflow. Run the standalone calibration
> sweep and wait for it to complete, then submit the training pipeline with the
> selected child's outputs. Follow the [VLA Quick Start](vla/README.md#-quick-start).

| Approach | Azure ML entry point                                                  | OSMO entry point                              |
|----------|-----------------------------------------------------------------------|-----------------------------------------------|
| RL       | `rl/scripts/submit-azureml-training.sh`                               | `rl/scripts/submit-osmo-training.sh`          |
| IL       | `il/scripts/submit-azureml-lerobot-training.sh`                       | `il/scripts/submit-osmo-lerobot-training.sh`  |
| VLA      | [Calibration sweep, then training pipeline](vla/README.md#-quick-start) | `vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh` |

Before a first training run on a new GPU target, run `smoke/scripts/submit-azureml-gpu-smoke.sh --instance-type <name> --stream`. It proves the GPU and the services training depends on. See [Smoke-Test a GPU Target](../docs/training/azureml-training.md#-smoke-test-a-gpu-target).

> [!TIP]
> Mirror completed OSMO runs to Azure ML for model versioning. See [Azure ML Mirror](../infrastructure/setup/README.md#️-azure-ml-mirror-optional) in the cluster setup guide.

## 📦 Packaging

Trained policies export to ONNX and TensorRT formats via `packaging/scripts/export_policy.py`. See [Packaging Specification](specifications/packaging.specification.md) for details.

## 📋 Specifications

| Document                                                     | Description                                   |
|--------------------------------------------------------------|-----------------------------------------------|
| [RL Training](specifications/rl-training.specification.md)   | SKRL, RSL-RL, Isaac Lab runtime configuration |
| [IL Training](specifications/il-training.specification.md)   | LeRobot ACT/Diffusion policies, blob datasets |
| [VLA Training](specifications/vla-training.specification.md) | Multi-modal transformer training              |
| [Packaging](specifications/packaging.specification.md)       | ONNX/TensorRT model export                    |

> [!NOTE]
> Evaluation capabilities are managed separately and will be extracted to a dedicated evaluation domain.
