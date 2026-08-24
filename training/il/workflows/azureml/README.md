# LeRobot AzureML Pipelines

End-to-end AzureML Pipeline jobs for LeRobot imitation learning, chaining `preprocess → train → evaluate` (with optional `register`) as a single DAG. This complements the existing standalone CommandJob path (`lerobot-train.yaml`) by providing automatic step orchestration, per-step compute targeting, and unified MLflow lineage.

Adapted from an upstream `lerobot-act-train-end-to-end` AzureML Pipeline. Hydra configuration composition, gate signals, Fabric lineage, candidate-tag patching, and eval-gates-register enforcement are intentionally out of scope.

## 📁 Directory Structure

```text
training/il/workflows/azureml/
├── components/
│   ├── preprocess.yaml                  # Dataset validation + layout prep
│   └── train.yaml                       # LeRobot training (env-var bridge)
├── lerobot-pipeline.yaml                # 3-step DAG (default)
├── lerobot-pipeline-with-register.yaml  # 4-step DAG (opt-in register)
├── lerobot-train.yaml                   # Standalone CommandJob (existing)
└── README.md                            # This file
```

Related components live outside this directory:

- `evaluation/sil/workflows/azureml/components/evaluate.yaml`
- `workflows/azureml/components/register.yaml`

## 🏗️ Pipeline Architecture

```text
dataset (uri_folder)
       │
       ▼
preprocess_step ──▶ prepared_dataset (uri_folder, layout: {repo_id}/meta/, {repo_id}/data/, …)
       │       └──▶ effective_preprocessing_config (audit artifact)
       │
       ▼
train_step ──▶ checkpoints (uri_folder, MLflow-tracked)
       │
       ▼
evaluate_step ──▶ eval_metrics (uri_folder)
       │                       ├── metrics.json          (VLA schema v1)
       │                       ├── failure_cases.jsonl   (VLA schema v1)
       │                       ├── eval_results.json     (toolchain legacy)
       │                       └── plots/                (trajectory plots)
       ▼
register_step (opt-in) ──▶ AML Model Registry
```

`settings.continue_on_step_failure: false` is standard AzureML DAG behavior — a failing step stops downstream execution. This is **not** an eval-gates-register governance contract.

## 🚀 Quick Start

### 3-step pipeline (default)

```bash
training/il/scripts/submit-azureml-lerobot-pipeline.sh \
  --dataset-repo-id user/koch-pick-place \
  --dataset-asset azureml:koch-pick-place:3
```

### 4-step pipeline with model registration

```bash
training/il/scripts/submit-azureml-lerobot-pipeline.sh \
  --dataset-repo-id user/koch-pick-place \
  --dataset-asset azureml:koch-pick-place:3 \
  --with-register \
  --register-model-name koch-pick-place-act
```

### Per-step compute overrides

```bash
training/il/scripts/submit-azureml-lerobot-pipeline.sh \
  --dataset-repo-id user/dataset \
  --dataset-asset azureml:dataset:1 \
  --compute-preprocess azureml:cpu-cluster \
  --compute-train azureml:gpu-h100-cluster \
  --compute-evaluate azureml:gpu-cluster
```

### Direct submission (without the wrapper script)

> [!IMPORTANT]
> The `train_step` and `evaluate_step` consume their `dataset_repo_id` via the
> step-level `environment_variables` block (`DATASET_REPO_ID`, `POLICY_TYPE`,
> `JOB_NAME`) defined in the pipeline YAML, not
> through component inputs. Direct `az ml job create` invocations rewrite the
> top-level `inputs.*`, but those step-level env vars are NOT auto-derived
> from pipeline inputs. Prefer `training/il/scripts/submit-azureml-lerobot-pipeline.sh`,
> which renders a temporary YAML with all step-level env vars patched.
> When invoking `az ml job create` directly, also patch the relevant
> `jobs.<step>.environment_variables` entries to match your inputs.

```bash
az ml job create \
  --file training/il/workflows/azureml/lerobot-pipeline.yaml \
  --set inputs.dataset.path=azureml:my-dataset:1 \
  --set inputs.dataset_repo_id=user/my-dataset \
  --set inputs.compute_train=azureml:gpu-cluster \
  --set jobs.train_step.environment_variables.DATASET_REPO_ID=user/my-dataset \
  --set jobs.evaluate_step.environment_variables.DATASET_REPO_ID=user/my-dataset
```

## 🧩 Pipeline Inputs

| Input                 | Type         | Required | Description                                                                                        |
|-----------------------|--------------|----------|----------------------------------------------------------------------------------------------------|
| `dataset`             | `uri_folder` | Yes      | Raw LeRobot dataset asset (must contain `meta/`, `data/`)                                          |
| `dataset_repo_id`     | `string`     | Yes      | HuggingFace-style repo id; must match dataset folder name                                          |
| `policy_type`         | `string`     | No       | `act` (default), `diffusion`, or `pi0`                                                             |
| `job_name`            | `string`     | No       | Display label for MLflow run naming                                                                |
| `compute_preprocess`  | `string`     | No       | AML compute target for `preprocess_step`                                                           |
| `compute_train`       | `string`     | No       | AML compute target for `train_step`                                                                |
| `compute_evaluate`    | `string`     | No       | AML compute target for `evaluate_step`                                                             |
| `compute_register`    | `string`     | No       | AML compute target for `register_step` (4-step variant only)                                       |
| `register_model_name` | `string`     | No       | AML Model Registry name (4-step variant only; required by submission script — fails fast on empty) |

> [!NOTE]
> To reuse a locked config from a previous run, pass
> `--preprocessing-config <uri>` to `submit-azureml-lerobot-pipeline.sh`. It is
> a step-level input on `preprocess_step`, not a top-level pipeline input.

## 📤 Pipeline Outputs

| Output         | Type         | Description                                                                                 |
|----------------|--------------|---------------------------------------------------------------------------------------------|
| `checkpoints`  | `uri_folder` | Trained checkpoint folder uploaded by the MLflow training wrapper                           |
| `eval_metrics` | `uri_folder` | `metrics.json` + `failure_cases.jsonl` (VLA schema v1) + legacy `eval_results.json` + plots |

## 🔄 Comparison with Standalone CommandJob

| Aspect            | `lerobot-train.yaml` (CommandJob)              | `lerobot-pipeline.yaml` (Pipeline)               |
|-------------------|------------------------------------------------|--------------------------------------------------|
| Job type          | Single `command` job                           | Multi-step `pipeline` job                        |
| Preprocess        | Skipped (caller must prepare dataset)          | Built-in `preprocess_step`                       |
| Evaluation        | Submitted separately                           | Built-in `evaluate_step` with DAG dependency     |
| Registration      | Manual or inline                               | Opt-in via `lerobot-pipeline-with-register.yaml` |
| Compute targeting | Single target for the entire job               | Per-step targets via pipeline inputs             |
| Multi-asset merge | Supported (1-64 assets via `--data-asset-uri`) | Single dataset input only                        |
| Submission script | `submit-azureml-lerobot-training.sh`           | `submit-azureml-lerobot-pipeline.sh`             |

Use the standalone CommandJob path when merging multiple dataset assets or when running training only. Use the pipeline when you want orchestrated preprocess + train + evaluate (+ optional register) in one submission.

## 📐 Evaluation Schema (VLA v1)

The `evaluate_step` emits the VLA `evaluation_schema_version=1` artifacts alongside the toolchain's existing `eval_results.json`:

- **`metrics.json`** — Aggregate metrics (per-policy loss, L1, L2, latency) for downstream gating and dashboards.
- **`failure_cases.jsonl`** — Per-episode failure records for triage.

This schema follows the VLA evaluation schema v1 contract. Candidate-tag patching from the upstream source is not ported.

## 🔧 Component Design

The `train` and `evaluate` Components wrap the existing toolchain entry scripts via inline bash bridges:

- AzureML auto-creates `AZURE_ML_INPUT_<name>` / `AZURE_ML_OUTPUT_<name>` env vars for Component inputs and outputs.
- The bash bridge translates those AzureML env vars to the entry script's private env-var ABI (`DATASET_ROOT`, `OUTPUT_DIR`, etc.).
- This pattern keeps the entry scripts unchanged and reusable across CommandJob and Pipeline submission paths.

The `preprocess` Component is a newly authored script (`training/il/scripts/lerobot/preprocess.py`) adapted from the upstream source with Hydra/gates/lineage stripped.

The `register` Component wraps `workflows/azureml/scripts/register_model.py`.

## 📚 Related

- [LeRobot training entry](../../scripts/lerobot/train.py)
- [Preprocess script](../../scripts/lerobot/preprocess.py)
- [Evaluation runner](../../../../evaluation/sil/scripts/run_evaluation.py)
- [Register script](../../../../workflows/azureml/scripts/register_model.py)
- [IL Training Specification](../../../specifications/il-training.specification.md)
