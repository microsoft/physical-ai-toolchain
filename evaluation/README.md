# Evaluation

Software-in-the-loop (SiL) and hardware-in-the-loop (HiL) evaluation for trained robot policies.

## 📂 Directory Structure

| Directory         | Purpose                                             |
|-------------------|-----------------------------------------------------|
| `sil/`            | SiL evaluation scripts, workflows, Docker artifacts |
| `metrics/`        | Plotting, artifact upload, MLflow bootstrapping     |
| `tests/`          | Evaluation tests                                    |
| `hil/`            | Hardware-in-the-loop evaluation (placeholder)       |
| `setup/`          | Evaluation setup scripts (placeholder)              |
| `specifications/` | Domain specifications                               |
| `examples/`       | Example configurations                              |

## 🚀 Quick Start

Submit an Isaac Lab policy evaluation:

```sh
evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh
```

Submit a LeRobot evaluation:

```sh
evaluation/sil/scripts/submit-azureml-lerobot-eval.sh
```
