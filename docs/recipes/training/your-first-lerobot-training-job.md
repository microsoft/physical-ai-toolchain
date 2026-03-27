# Your First LeRobot Training Job

Submit a LeRobot behavioral cloning training job to OSMO using a HuggingFace dataset and verify that the trained policy appears in Azure ML. By the end of this recipe, you will have a trained ACT policy for the ALOHA sim insertion task.

> [!NOTE]
> This recipe requires deployed infrastructure with OSMO running. Complete the [Quickstart](../../getting-started/quickstart.md) first.

## 📋 Prerequisites

| Requirement    | Details                                             |
|----------------|-----------------------------------------------------|
| Infrastructure | Azure resources deployed via Terraform              |
| OSMO           | Control plane and backend running                   |
| VPN            | Connected to private cluster (if using private AKS) |
| Azure CLI      | Authenticated (`az login`)                          |

## 🚀 Steps

### Step 1: Preview the training configuration

Preview what will be submitted:

```bash
cd training/il/scripts
./submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human \
  --config-preview
```

Review the dataset source, policy type, training hyperparameters, and Azure ML context. No job is submitted.

### Step 2: Submit a training job

Submit a training run with the default ACT policy:

```bash
./submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

The script submits an OSMO workflow that pulls the dataset from HuggingFace, trains an ACT policy, and logs metrics to MLflow.

Customize training hyperparameters:

```bash
./submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-type act \
  --training-steps 50000 \
  --batch-size 32 \
  --save-freq 5000
```

### Step 3: Train with data from Azure Blob Storage

Use `--from-blob` when your dataset is in Azure Storage instead of HuggingFace:

```bash
./submit-osmo-lerobot-training.sh \
  -d my-org/my-dataset \
  --from-blob \
  --storage-account <your-storage-account> \
  --storage-container datasets \
  --blob-prefix my-dataset/v1
```

The script downloads the dataset from Blob Storage using managed identity credentials before training starts.

### Step 4: Monitor training progress

**OSMO UI**: Open the OSMO dashboard to view workflow status, pod logs, and real-time metrics. See [Accessing OSMO](../../training/osmo-training.md#-accessing-osmo) for connection instructions (VPN or port-forward).

**Azure ML Studio**: Navigate to your workspace at [ml.azure.com](https://ml.azure.com/), open the Jobs section, and select the MLflow experiment. Training metrics (loss, gradient norm, learning rate) stream in real time as the job progresses.

To view results in detail:

1. Open [ml.azure.com](https://ml.azure.com/) and select your workspace
2. Navigate to **Jobs** in the left sidebar
3. Find your experiment (named after the task, e.g., `lerobot-act-training`) and select it
4. Click the latest run to open the run detail page
5. Select the **Metrics** tab to view training curves (loss, gradient norm, learning rate) — use the chart controls to overlay multiple metrics or smooth noisy curves
6. Select the **Outputs + logs** tab to view stdout/stderr logs from the training container
7. If `--register-checkpoint` was used, navigate to **Models** in the left sidebar to confirm the registered model and its version

**CLI** (optional): Check workflow status via the OSMO CLI:

```bash
osmo workflow list
```

### Step 5: Register the trained model (optional)

Register the trained checkpoint to Azure ML for versioned model management:

```bash
./submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human \
  --register-checkpoint my-act-policy
```

The `--register-checkpoint` flag triggers automatic model registration after training completes.

## ✅ Verify

The recipe succeeded when:

- OSMO training pod reached `Completed` status
- MLflow experiment shows training loss decreasing over steps
- Model artifacts exist in Azure ML (if `--register-checkpoint` was used)

## ⚙️ Configuration Reference

| Parameter               | Default    | Description                                |
|-------------------------|------------|--------------------------------------------|
| `-d, --dataset-repo-id` | (required) | HuggingFace dataset repository             |
| `--policy-type`         | `act`      | Policy architecture (`act` or `diffusion`) |
| `--training-steps`      | `100000`   | Total training iterations                  |
| `--batch-size`          | `32`       | Training batch size                        |
| `--learning-rate`       | `1e-4`     | Optimizer learning rate                    |
| `--save-freq`           | `5000`     | Checkpoint save frequency                  |
| `--val-split`           | `0.1`      | Validation split ratio                     |
| `--from-blob`           | (disabled) | Use Azure Blob Storage as data source      |
| `--register-checkpoint` | (none)     | Model name for Azure ML registration       |

See [Scripts Reference](../../reference/scripts.md) for the full parameter table.

## 🔗 Related Recipes

- [End-to-End LeRobot Pipeline](end-to-end-lerobot-pipeline.md) — automated train → evaluate → register
- [Preparing Datasets for Training](../data-collection/preparing-datasets-for-training.md) — dataset download and validation
- [Your First RL Training Job](your-first-rl-training-job.md) — reinforcement learning alternative
- [LeRobot Training Guide](../../training/lerobot-training.md) — detailed IL reference documentation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
