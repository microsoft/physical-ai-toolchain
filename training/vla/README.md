# VLA Training

Vision-Language-Action (VLA) training for `pi0`, `pi0_fast`, and `pi05` policies via `lerobot[dataset,pi,smolvla]`. Jobs submit to Azure ML as a single `CommandJob`, reusing the IL LeRobot entry script with a VLA dependency lockfile and policy whitelist.
SmolVLA fine-tuning runs through OSMO with the IL LeRobot submission script and this VLA lockfile, and fits one workstation GPU on the [local OSMO profile](../../infrastructure/local/README.md).
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
│   ├── pyproject.toml                           # lerobot[dataset,pi,smolvla] dependencies and overrides
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
backbone, so it needs a Hugging Face token that can read that model:

1. Sign in to Hugging Face, open the model page, and accept its access conditions.
2. Create a fine-grained access token in your [token settings](https://huggingface.co/settings/tokens)
   with read access to `google/paligemma-3b-pt-224`.
3. Add the token to the untracked repository-root `.env.local`, creating it from
   `.env.local.example` if needed. Edit the file instead of echoing the token in a shell
   command, which would save it in your shell history:

   ```bash
   HF_TOKEN=hf_...
   ```

Git ignores `.env.local`, and Azure ML code snapshots exclude it. The test, the Dependabot
verification runner, and the submission scripts use the value in `.env.local` whenever the
file sets `HF_TOKEN`, and an exported value only when it doesn't; a submission script's
`--hf-token` option overrides both. An empty `HF_TOKEN=` line overrides an exported token, so
leave the line commented out if you export the token instead. Keep the line a plain value as
shown: the checks reject shell syntax such as `$OTHER` rather than guess what it expands to.

Every submission script loads `.env.local`, and the LeRobot evaluation script passes the
token in effect to every evaluation job, not only pi0. Those jobs and the pi0 training job
carry it as an environment variable that anyone who can read the jobs can see, so keep the
token limited to reading that one model.

To confirm access before submitting a job, request a small file from the model. The command
reads the token from `.env.local` without putting it on a command line, and prints `200` when
the token works; `401` or `403` means the token or the access conditions need attention:

```bash
(set -a; . ./.env.local; set +a
 printf 'Authorization: Bearer %s\n' "$HF_TOKEN" |
   curl -s -L -o /dev/null -w '%{http_code}\n' -H @- \
     https://huggingface.co/google/paligemma-3b-pt-224/resolve/main/config.json)
```

Then run the test:

```bash
uv run pytest -o addopts='' -vv -s -m e2e tests/e2e/test_e2e_aml_vla_pi0_training.py
```

Pytest fails during client-side setup, before resolving Azure fixtures or submitting a
job, when `HF_TOKEN` is set in neither place or `.env.local` sets it empty. Other E2E tests
require this variable only when they carry the `requires_hf_token` marker.

Dependabot verification runs this test as the `aml-vla-pi0` check in the `vla` category. See
[Verifying Dependabot Updates](../../docs/contributing/dependabot-verification.md).

## 🚀 SmolVLA Fine-Tuning

[SmolVLA](https://huggingface.co/lerobot/smolvla_base) is a compact VLA of about 450 million parameters that fine-tunes on one workstation GPU. Submit it through the IL LeRobot OSMO script with `-p smolvla`; the job installs LeRobot from [lerobot/uv.lock](lerobot/uv.lock) and fine-tunes `lerobot/smolvla_base` at the commit pinned in `scripts/lib/common.sh`:

```bash
training/il/scripts/submit-osmo-lerobot-training.sh \
  --profile local \
  --local-dataset my-learner-dataset \
  -p smolvla \
  --rename-map '{"observation.images.front": "observation.images.camera1"}' \
  --batch-size 8 \
  -r my-smolvla-policy
```

The base model names its cameras `observation.images.camera1` through `camera3`; `--rename-map` maps the dataset's camera keys onto them. Pass `--policy-path` and `--policy-path-revision` to fine-tune another SmolVLA checkpoint. See [SmolVLA Fine-Tuning](../../docs/training/lerobot-training.md#smolvla-fine-tuning-osmo) for the options.

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

## 🚀 GR00T N1.7 smoke run and Azure ML publication

Use the [GR00T launcher](scripts/groot/osmo-train-entry.sh) as a reference when training on an external scheduler.
Set `MAX_STEPS=2`, `SAVE_STEPS=2` and `LOGGING_STEPS=1`. Set `RUN_ID_OVERRIDE` to the scheduler's run ID when using
the launcher. The [mirror](../utils/aml_mirror.py) consumes `${OUTPUT_ROOT}/${RUN_ID}/checkpoint-2` and scalar
metrics from `runs/` (TensorBoard) or `trainer_state.json` (fallback). The
[dataset helper](scripts/groot/download_blob.py) and [example modality config](configs/groot/examples/modality_config.py)
are separate from the trainer.

GR00T N1.7 trains in Python 3.10. Set `AML_TOOLS_PYTHON` to an executable **inside the worker image**, for example
`/opt/aml-tools/bin/python`, running Python 3.11 or later. The launcher selects it before activating the training
environment and keeps it for dataset downloads, verification, runhooks and publishing. If unset, it looks for a
compatible `python3`, `python3.11` or `python` on the initial `PATH`. When runtime bootstrap is disabled, provision
`azure-identity`, `azure-storage-blob`, `azure-ai-ml`, `mlflow`, `azureml-mlflow` and `tbparse` in that tools environment.

Choose **one** publisher: use `AZURE_UPLOAD=true` and `AZURE_UPLOAD_STRICT=true` for inline publication, or leave
`AZURE_UPLOAD=false` and invoke the mirror from a separate post-training job. For a separate job, configure the
workspace, model and workload identity as described in the mirror header, then use:

```bash
export OUTPUT_DIR="${OUTPUT_ROOT}/${RUN_ID}"
export TRAINING_FRAMEWORK=groot
export AML_SOURCE=external-training
"${AML_TOOLS_PYTHON}" training/utils/aml_mirror.py
```

`PROJECT_ID` sets the MLflow `project_id` tag; `EXTERNAL_JOB_ID` sets `external.job_id`. To emit a scheduler-specific
alias too, append `scheduler.job_id=${EXTERNAL_JOB_ID}` to `AML_EXTRA_TAGS` (comma-separated `key=value` pairs).
Set these in the publishing process. The [OSMO workflow](workflows/osmo/groot-train.yaml) also accepts these values.

For a direct Blob copy, set `CHECKPOINT_BLOB_URL` to a new container prefix **per attempt**, with no SAS query.
The mirror creates `checksums.sha256` without overwriting, uploads and reads back the payload, and publishes
`ready.json` only if the remote SHA-256 values match the manifest. A failed transfer can leave unsealed objects;
retry at a fresh prefix, not by overwriting the old one. Disabling read-back or enabling overwrite is not supported
for direct publication. The marker attests to Blob integrity, not model registration: retain the final JSON receipt
with `checkpoint_blob.readback_verified`, `metrics_logged`, `checksums_sha256`, the MLflow run ID and model version.

## 📋 Specifications

See [VLA Training Specification](../specifications/vla-training.specification.md) for additional VLA approaches and components.
