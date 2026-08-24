# VLA Training

Vision-Language-Action (VLA) training for `pi0`, `pi0_fast`, and `pi05` policies via `lerobot[pi]`. Jobs submit to Azure ML as a single `CommandJob`, reusing the IL LeRobot entry script with a VLA dependency lockfile and policy whitelist.
NVIDIA GR00T fine-tuning runs through OSMO using the same lifecycle domain.

## 📁 Directory Structure

```text
vla/
├── configs/
│   └── groot/
│       └── examples/
│           ├── data_config.py                  # GR00T N1.5 example data config
│           ├── modality_config.py              # GR00T N1.7+ example modality config
│           └── README.md                       # How to adapt for a custom embodiment
├── lerobot/
│   ├── pyproject.toml                           # lerobot[pi] dependencies and overrides
│   └── uv.lock                                  # Reproducible Linux x86_64 dependency lock
├── scripts/
│   ├── groot/
│   │   ├── osmo-train-entry.sh                  # Container entry: env setup + fine-tune
│   │   └── download_blob.py                     # Azure Blob dataset downloader
│   ├── submit-azureml-vla-pi0-training.sh       # pi0 family submission to Azure ML
│   └── submit-osmo-lerobot-vla-fine-tuning.sh   # GR00T submission to OSMO
├── workflows/
│   ├── azureml/
│   │   └── vla-pi0-train.yaml                   # Azure ML pi0 CommandJob template
│   └── osmo/
│       └── groot-train.yaml                     # OSMO GR00T fine-tuning workflow
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

### Fine-tune from a registered pi0 checkpoint

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-asset "azureml:my-aloha-dataset:3" \
    --init-from-policy-model "azureml:pi0-base:1"
```

The model input uses download mode and forwards its local path to LeRobot as `policy.path`.

### Register the resulting checkpoint

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-asset "azureml:my-aloha-dataset:3" \
    --register-checkpoint pi0-aloha-transfer
```

The training script writes the registration manifest under `outputs/checkpoints/`; AzureML's job-completion hook publishes the model version.

## 🚀 GR00T-N1.5 Fine-Tuning

GR00T-N1.5-3B is NVIDIA's vision-language-action foundation model for robot manipulation. Fine-tuning is submitted via the VLA submission script:

```bash
training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --base-model nvidia/GR00T-N1.5-3B \
  --data-config example \
  --data-config-file training/vla/configs/groot/examples/data_config.py \
  --blob-url https://<account>.blob.core.windows.net/<container>/<path> \
  --azure-upload
```

See [configs/groot/examples/README.md](configs/groot/examples/README.md) for how to adapt the bundled example for a custom embodiment.

## 📋 Specifications

See [VLA Training Specification](../specifications/vla-training.specification.md) for additional VLA approaches and components.
