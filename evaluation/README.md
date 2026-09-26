# Evaluation

Software-in-the-loop (SiL) and hardware-in-the-loop (HiL) evaluation for trained robot policies.

## 📂 Directory Structure

| Directory         | Purpose                                               |
|-------------------|-------------------------------------------------------|
| `sil/`            | SiL evaluation scripts, workflows, Docker artifacts   |
| `metrics/`        | Plotting, artifact upload, MLflow bootstrapping       |
| `tests/`          | Evaluation tests                                      |
| `hil/`            | CPU smoke and independently no-command HiL evaluation |
| `setup/`          | Evaluation setup scripts (placeholder)                |
| `specifications/` | Domain specifications                                 |
| `examples/`       | Example configurations                                |

## 🚀 Quick Start

Submit an Isaac Lab policy evaluation:

```sh
evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh
```

Submit a LeRobot evaluation:

```sh
evaluation/sil/scripts/submit-azureml-lerobot-eval.sh
```

Submit a pi0-family VLA evaluation:

```sh
evaluation/sil/scripts/submit-azureml-vla-pi0-eval.sh \
  --from-aml-model \
  --model-name <model-name> \
  --model-version <version> \
  --dataset-repo-id <owner/dataset> \
  --dataset-revision <commit-sha>
```

Run the progressive HiL validation after connecting the local OSMO backend:

```sh
data-pipeline/setup/hil/03-run-cpu-smoke.sh --connection-file <connection-receipt>
data-pipeline/setup/hil/04-run-no-command-check.sh --connection-file <connection-receipt>
```

## 📐 LeRobot Evaluation Output

ACT, diffusion, pi0, and pi0_fast replay evaluations emit the same files:

| File                  | Contract | Purpose                                                        |
|-----------------------|----------|----------------------------------------------------------------|
| `eval_results.json`   | Legacy   | Toolchain aggregate and per-episode replay metrics             |
| `metrics.json`        | VLA v1   | Policy-independent aggregate metrics and verdict fields        |
| `failure_cases.jsonl` | VLA v1   | One record per episode that failed during rollout              |
| `ep*_predictions.npz` | Internal | Predicted actions, ground-truth actions, and inference timings |

`metrics.json` uses `evaluation_schema_version: 1`. Metric names are stable across policy families: `action_accuracy_l2`, `action_accuracy_l1`, `inference_latency_mean_ms`, and `throughput_hz`. The evaluator does not apply deployment gates, so absolute verdicts pass and regression verdicts are skipped.

pi0 and pi0_fast require task descriptions in the LeRobot dataset metadata. Store task text in `meta/tasks.jsonl` and associate episodes through `task_index` or the `tasks` field in `meta/episodes.jsonl`.
