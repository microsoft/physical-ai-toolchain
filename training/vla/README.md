# VLA Training

Vision-Language-Action (VLA) training for `pi0`, `pi0_fast`, and `pi05` policies via `lerobot[dataset,pi]`. Jobs submit to Azure ML as a single `CommandJob`, reusing the IL LeRobot entry script with a VLA dependency lockfile and policy whitelist.
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
│   ├── pyproject.toml                           # lerobot[dataset,pi] dependencies and overrides
│   └── uv.lock                                  # Reproducible Linux x86_64 and ARM64 dependency lock
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

### Train directly from a pinned Hugging Face policy

Download an immutable policy snapshot inside the Azure ML training pod when
workspace model-asset transfer is not required:

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
  --dataset-asset "azureml:ur10e-gear-pick-place-train:1" \
  --policy-type pi05 \
  --init-from-policy-hf-repo lerobot/pi05_base \
  --init-from-policy-hf-revision b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba \
  --policy-dtype bfloat16 \
  --gradient-checkpointing \
  --rename-map '{"observation.images.d435":"observation.images.base_0_rgb","observation.images.d405":"observation.images.left_wrist_0_rgb"}'
```

The submit script requires a full Git commit and forwards the repository and
revision as job environment values. The checked-in entrypoint downloads the
snapshot after installing the locked runtime, validates `config.json` and the
`model.safetensors` tensor index, and then passes the local directory to
LeRobot. PI policy processors load their tokenizer from the gated
`google/paligemma-3b-pt-224` repository, so the job requires `HF_TOKEN` even
when the policy repository is public. Missing PI 0.5 camera slots are masked
and padded by LeRobot.

Use `--policy-dtype bfloat16` to instantiate PI policy storage in BF16 instead
of relying only on runtime mixed precision. Add `--gradient-checkpointing` when
activation memory is the limiting factor; it reduces memory usage by
recomputing activations during backward.

Use [Azure ML Arc VLA Setup and Operations](../../docs/training/vla-azureml-arc-setup.md)
to prepare Arc-connected K3s compute, submit PI 0.5 training, and monitor the
run. See [VLA Full-Run Troubleshooting](../../docs/training/vla-full-run-troubleshooting.md)
for the failure chronology, diagnostic signatures, unsuccessful mitigations,
and validated recovery configuration.

### Register the resulting checkpoint

```bash
./training/vla/scripts/submit-azureml-vla-pi0-training.sh \
    --dataset-asset "azureml:my-aloha-dataset:3" \
    --register-checkpoint pi0-aloha-transfer
```

The training script writes the registration manifest under `outputs/checkpoints/`; AzureML's job-completion hook publishes the model version.

## 🧪 End-to-End Test

The Azure ML pi0 E2E test initializes pi0 from the gated
[`google/paligemma-3b-pt-224`](https://huggingface.co/google/paligemma-3b-pt-224)
backbone. Accept the model access conditions on Hugging Face, then export a read token
authorized for the model before running the test:

```bash
export HF_TOKEN="$(cat /secure/path/to/hf-token)"
uv run pytest -o addopts='' -vv -s -m e2e tests/e2e/test_e2e_aml_vla_pi0_training.py
```

Pytest fails during client-side setup before resolving Azure fixtures or submitting a
job when `HF_TOKEN` is unset or empty. Other E2E tests require this variable only when
they carry the `requires_hf_token` marker.

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
