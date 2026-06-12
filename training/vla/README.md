# VLA Training

Vision-Language-Action (VLA) training for `pi0`, `pi0_fast`, and `pi05` policies via `lerobot[pi]`. Jobs submit to Azure ML as a single `CommandJob`, reusing the IL LeRobot entry script with a VLA dependency lockfile and policy whitelist.

## 📁 Directory Structure

```text
vla/
├── lerobot/                                          # VLA dependency pins
│   ├── pyproject.toml                                # lerobot[pi] + transformers + scipy overrides
│   └── requirements.txt                              # uv-compiled lockfile (installed --no-deps)
├── scripts/
│   └── submit-azureml-vla-pi0-training.sh            # AzureML submit (pi0/pi0_fast/pi05 whitelist)
├── workflows/
│   └── azureml/
│       └── vla-pi0-train.yaml                        # AzureML CommandJob template
└── README.md
```

## 🤖 Supported Policies

| Policy     | Description                                                     |
|------------|-----------------------------------------------------------------|
| `pi0`      | Physical Intelligence pi0 base (3B param flow-matching VLA)     |
| `pi0_fast` | pi0 variant with FAST action tokenization for higher throughput |
| `pi05`     | pi05 successor checkpoint (same API surface as `pi0`)           |

Any value outside `pi0|pi0_fast|pi05` is rejected by the submit script before any AzureML call.

## 🚀 Quick Start

### Train from a HuggingFace dataset

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-repo-id lerobot/aloha_sim_transfer_cube_human \
    --policy-type pi0 \
    --training-steps 30000
```

### Train from an AzureML data asset

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-asset "azureml:my-aloha-dataset:3" \
    --policy-type pi0_fast \
    --batch-size 8
```

### Fine-tune from a pretrained pi0 checkpoint

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-asset "azureml:my-aloha-dataset:3" \
    --policy-type pi0 \
    -- --policy.path=lerobot/pi0
```

The `--` boundary forwards `--policy.path` to `lerobot-train`, which short-circuits the `--policy.type` injection and loads weights and config from the HF Hub repo.

### Register the resulting checkpoint

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-asset "azureml:my-aloha-dataset:3" \
    --register-checkpoint pi0-aloha-transfer
```

The training script writes the registration manifest under `outputs/checkpoints/`; AzureML's job-completion hook publishes the model version.

## 📋 Specifications

See [VLA Training Specification](../specifications/vla-training.specification.md) for the broader VLA training approach.
