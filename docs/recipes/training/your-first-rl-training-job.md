# Your First RL Training Job

Submit an Isaac Lab RL training job to OSMO and verify that training metrics appear in MLflow. By the end of this recipe, you will have a trained checkpoint for the Anymal-C velocity locomotion task.

> [!NOTE]
> This recipe requires deployed infrastructure with OSMO running. Complete the [Quickstart](../../getting-started/quickstart.md) first.

## 📋 Prerequisites

| Requirement | Details |
|-------------|---------|
| Infrastructure | Azure resources deployed via Terraform |
| OSMO | Control plane and backend running (`kubectl get pods -n osmo-control-plane`) |
| VPN | Connected to private cluster (if using private AKS) |
| Azure CLI | Authenticated (`az login`) |
| kubectl | Connected to AKS cluster |

## 🚀 Steps

### Step 1: Preview the training configuration

The submission script auto-detects Azure context from Terraform outputs. Preview what will be submitted before running a real job:

```bash
cd training/rl/scripts
./submit-osmo-training.sh --config-preview
```

Review the output. The preview shows the workflow template, task name, container image, GPU/CPU/memory allocation, and Azure ML context. No job is submitted.

### Step 2: Run a smoke test (optional)

Verify Azure connectivity and credential resolution before committing GPU time:

```bash
./submit-osmo-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --num-envs 16 \
  --max-iterations 10 \
  --run-smoke-test
```

The smoke test validates Azure ML workspace access, MLflow tracking URI resolution, and storage connectivity. It runs a minimal training loop (10 iterations, 16 environments) to catch configuration issues early.

### Step 3: Submit a training job

Submit a full training run with the SKRL backend (default):

```bash
./submit-osmo-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --num-envs 2048 \
  --max-iterations 1500
```

The script packages `training/rl/` as a base64 payload, injects it into the OSMO workflow template, and submits the job. OSMO schedules the job on a GPU node via KAI Scheduler.

Override the backend to use RSL-RL instead of SKRL:

```bash
./submit-osmo-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --backend rsl_rl \
  --num-envs 2048 \
  --max-iterations 1500
```

### Step 4: Monitor training progress

Watch pod status:

```bash
kubectl get pods -n osmo-control-plane --watch
```

View training logs:

```bash
kubectl logs -n osmo-control-plane -l app=osmo-training --tail=100 -f
```

Access the OSMO UI dashboard for real-time metrics. See [Cluster Operations](../../infrastructure/cluster-setup-advanced.md) for OSMO UI access instructions.

### Step 5: Verify results in MLflow

Open the Azure ML Studio portal and navigate to the MLflow experiment. Training metrics (reward, episode length, loss) appear as the job progresses.

List registered models from the CLI:

```bash
az ml model list \
  --resource-group <your-resource-group> \
  --workspace-name <your-workspace>
```

## ✅ Verify

The recipe succeeded when:

- OSMO pod reached `Completed` status
- MLflow experiment shows training metrics across iterations
- A checkpoint artifact exists in the Azure ML model registry (if `--register-checkpoint` was used)

## ⚙️ Configuration Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--task` | `Isaac-Velocity-Rough-Anymal-C-v0` | Isaac Lab task environment |
| `--num-envs` | `2048` | Parallel simulation environments |
| `--max-iterations` | (unset) | Training iterations; omit for task default |
| `--backend` | `skrl` | Training backend (`skrl` or `rsl_rl`) |
| `--gpu` | `1` | GPU count |
| `--checkpoint-mode` | `from-scratch` | `from-scratch`, `warm-start`, or `resume` |
| `--register-checkpoint` | (none) | Model name for Azure ML registration |

See [Scripts Reference](../../reference/scripts.md) for the full parameter table.

## 🔗 Related Recipes

- [Your First LeRobot Training Job](your-first-lerobot-training-job.md) — behavioral cloning alternative
- [End-to-End LeRobot Pipeline](end-to-end-lerobot-pipeline.md) — automated train → evaluate → register
- [Isaac Lab Training Guide](../../training/isaac-lab-training.md) — detailed RL reference documentation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
