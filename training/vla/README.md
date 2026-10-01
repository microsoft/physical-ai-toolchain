# VLA Training

Vision-Language-Action (VLA) training for `pi0`, `pi0_fast`, and `pi05` policies via `lerobot[dataset,pi]`. Azure ML validates data, calibrates batch size, trains the policy, and finalizes an immutable candidate.
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
│   ├── azureml-component-entry.sh                # Shared Azure ML component launcher
│   ├── calibrate_vla.py                          # Isolated micro-batch calibration
│   └── submit-osmo-lerobot-vla-fine-tuning.sh   # GR00T submission to OSMO
├── workflows/
│   ├── azureml/
│   │   ├── vla-calibration-sweep.yaml             # Isolated batch-size selection
│   │   └── vla-training-pipeline.yaml             # VLA training pipeline
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

The LeRobot PI adapter rejects values outside `pi0|pi0_fast|pi05` before training starts.

## 🚀 Quick Start

Submit a versioned Azure ML dataset to the standalone calibration sweep, then
pass the selected outputs to the training pipeline. Use immutable dataset,
model, and code revisions.

Before the first calibration sweep on an Arc compute target, run the
[sweep failure-isolation smoke test](../../docs/training/vla-azureml-arc-setup.md#validate-sweep-failure-isolation).
Continue only when the sweep completes despite its deliberate failed trial and
publishes the successful trial output.

```bash
CODE_REVISION=$(git rev-parse HEAD)
COMPUTE="azureml:<compute-name>"

CALIBRATION_JOB=$(az ml job create \
  --file training/vla/workflows/azureml/vla-calibration-sweep.yaml \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --set compute="$COMPUTE" \
  --set inputs.compute_target="$COMPUTE" \
  --set inputs.dataset.path="azureml:ur10e-gear-pick-place-train:1" \
  --set inputs.dataset_asset_id="azureml:ur10e-gear-pick-place-train:1" \
  --set inputs.dataset_repo_id="<dataset-repository>" \
  --set inputs.policy_type=pi05 \
  --set inputs.init_from_policy_hf_repo_id=lerobot/pi05_base \
  --set inputs.init_from_policy_hf_revision=b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba \
  --set inputs.adapter_name=lerobot-pi \
  --set inputs.code_repository=https://github.com/microsoft/physical-ai-toolchain.git \
  --set inputs.code_revision="$CODE_REVISION" \
  --set inputs.policy_dtype=bfloat16 \
  --set inputs.gradient_checkpointing=true \
  --set inputs.hf_key_vault_url="<key-vault-url>" \
  --set inputs.hf_token_secret_name="<secret-name>" \
  --query name --output tsv)

az ml job show \
  --name "$CALIBRATION_JOB" \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --query status --output tsv

BEST_CALIBRATION_RUN=$(az ml job show \
  --name "$CALIBRATION_JOB" \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --query properties.best_child_run_id --output tsv)
CALIBRATION_OUTPUT_ROOT="azureml://datastores/workspaceblobstore/paths/azureml/$BEST_CALIBRATION_RUN"

az ml job create \
  --file training/vla/workflows/azureml/vla-training-pipeline.yaml \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --set inputs.dataset.path="azureml:ur10e-gear-pick-place-train:1" \
  --set inputs.dataset_asset_id="azureml:ur10e-gear-pick-place-train:1" \
  --set inputs.dataset_repo_id="<dataset-repository>" \
  --set inputs.workload_contract.path="$CALIBRATION_OUTPUT_ROOT/workload_contract/" \
  --set inputs.calibration_report.path="$CALIBRATION_OUTPUT_ROOT/calibration_report/" \
  --set inputs.pipeline_contract_fingerprint="<pipeline-contract-sha256>" \
  --set inputs.policy_type=pi05 \
  --set inputs.init_from_policy_hf_repo_id=lerobot/pi05_base \
  --set inputs.init_from_policy_hf_revision=b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba \
  --set inputs.adapter_name=lerobot-pi \
  --set inputs.code_repository=https://github.com/microsoft/physical-ai-toolchain.git \
  --set inputs.code_revision="$CODE_REVISION" \
  --set inputs.policy_dtype=bfloat16 \
  --set inputs.gradient_checkpointing=true \
  --set inputs.compute_preflight="azureml:<compute-name>" \
  --set inputs.compute_train="azureml:<compute-name>" \
  --set inputs.compute_finalize="azureml:<compute-name>" \
  --set inputs.hf_key_vault_url="<key-vault-url>" \
  --set inputs.hf_token_secret_name="<secret-name>"
```

Submit the training pipeline only after the calibration sweep reports
`Completed`. The sweep runs candidates `[1,2,4]` serially in separate
containers and publishes the largest safe candidate's report and workload
contract. Arc pipeline children consume the selected trial's concrete,
run-scoped datastore folders because cross-job `azureml://jobs/...` inputs do
not materialize on this compute target. Training regenerates the dataset
manifest and rejects calibration evidence that does not match the current
dataset, model, code, and runtime.

The pipeline requires a full Git commit. The checked-in entrypoint downloads the
snapshot after installing the locked runtime, validates `config.json` and the
`model.safetensors` tensor index, and then passes the local directory to
LeRobot. PI policy processors load their tokenizer from the gated
`google/paligemma-3b-pt-224` repository. Calibration and training retrieve the
Hugging Face token from Key Vault through managed identity. Missing PI 0.5 camera
slots are masked and padded by LeRobot.

Set `inputs.policy_dtype=bfloat16` to instantiate PI policy storage in BF16 instead
of relying only on runtime mixed precision. Set
`inputs.gradient_checkpointing=true` when activation memory is the limiting
factor; it reduces memory usage by recomputing activations during backward.

Use [Azure ML Arc VLA Setup and Operations](../../docs/training/vla-azureml-arc-setup.md)
to prepare Arc-connected K3s compute, submit PI 0.5 training, and monitor the
run. See [VLA Full-Run Troubleshooting](../../docs/training/vla-full-run-troubleshooting.md)
for the failure chronology, diagnostic signatures, unsuccessful mitigations,
and validated recovery configuration.

The pipeline ends after finalizing the candidate and its lineage manifest. Evaluation,
deployment gating, and model registration remain separate lifecycle concerns.

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
