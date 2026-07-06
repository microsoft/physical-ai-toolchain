# T2 — Pilot: Cloud Training, Registry, and Shared Catalogs (Recommended)

The **recommended production** path. This is the tier where cloud training genuinely becomes the
*default* rather than an option: one site, several robots, real training scale, and a team
collaborating. You add an AzureML workspace, a model registry, and shared MLflow on top of the T1
storage account, and still run **no Kubernetes, no Arc, and no fleet control plane**.

> [!NOTE]
> T2 is the recommended starting point for teams who have outgrown a single GPU box. The full training lifecycle is still
> achievable here with **manual deployment**. Robots are hand-updated with `docker pull`. For
> canonical tier definitions, see the [tier model](../../design/tier-model.md) and the
> [architecture tier detail](../../contributing/architecture.md#t2--pilot).

## 🧱 Minimum Infrastructure

| Concern     | What you need                                                                       |
|-------------|-------------------------------------------------------------------------------------|
| Hardware    | One site, several robots, a collaborating team.                                     |
| Edge infra  | None beyond Docker. **No** Kubernetes, **no** Arc, **no** Flux, **no** fleet plane. |
| Cloud infra | AzureML workspace + Blob storage + **model registry** + **MLflow**. ACSA optional.  |
| Tooling     | Azure CLI (`az login`), deployed infrastructure (Terraform), optionally OSMO.       |
| Tracking    | Managed MLflow on AzureML (hosted), with model versioning load-bearing.             |

The delta from T1 is that cloud GPU training, a model registry, and a hosted catalog become the
**default**, not an occasional reach.

## 🚀 Steps

### Step 1: Deploy the cloud infrastructure

Provision the AzureML workspace, storage, and registry. Follow the
[Getting Started](../../getting-started/README.md) hub and the
[Quickstart](../../getting-started/quickstart.md#quick-start) for the clone-to-first-job path. This
recipe assumes that infrastructure is deployed rather than duplicating its steps; for the
infrastructure reference see [Infrastructure](../../infrastructure/README.md).

### Step 2: Prepare the dataset in cloud storage

Land and validate datasets in Blob as at [T1 — Lab](../tier-1-lab/README.md), following
[Preparing Datasets for Training](../data-collection/preparing-datasets-for-training.md) and the
[Blob storage structure](../../cloud/blob-storage-structure.md).

### Step 3: Train on cloud GPU (default)

Submit a LeRobot behavioral-cloning job to AzureML or OSMO: multi-GPU, queued jobs, multiple people,
VLA scale. Use the existing recipes rather than re-deriving the commands here:

- [Your First LeRobot Training Job](../training/your-first-lerobot-training-job.md): submit a single
  cloud training job.
- [End-to-End LeRobot Pipeline](../training/end-to-end-lerobot-pipeline.md): run
  train → evaluate → register in one command.
- [Your First RL Training Job](../training/your-first-rl-training-job.md): Isaac Lab RL on OSMO.

Reference docs: [AzureML training](../../training/azureml-training.md),
[OSMO training](../../training/osmo-training.md), [LeRobot training](../../training/lerobot-training.md).

### Step 4: Track and register

Managed MLflow on AzureML is the default tracking backend, and the **model registry becomes
load-bearing**. Trained checkpoints are registered and versioned automatically at job completion.
See [Experiment tracking](../../training/experiment-tracking.md) and
[MLflow integration](../../training/mlflow-integration.md).

### Step 5: Curate with the hosted dataviewer

The dataviewer is deployed as a **shared web app** rather than localhost, so the whole team browses
and annotates the same catalogs.

### Step 6: Validate

Validate registered models directly from the registry. The local entry point
(`run-local-lerobot-eval.py`) accepts `--model-name` / `--model-version` to pull a registered model,
and [Evaluation](../../evaluation/README.md) covers the cloud and batch evaluation paths.

### Step 7: Run on robot (manual `docker pull`)

Deployment is still manual: hand-update a handful of reachable robots with `docker pull`. This stays
tractable at one site with several robots. Declarative GitOps deployment is the
[T3 — Production](../tier-3-production/README.md) concern.

## 🎓 Graduate When

- The number of robots or the **update cadence** makes hand-updating each robot error-prone, and
  **version skew** across robots becomes a real problem, while all robots are still at one reachable
  site: [T3 — Production](../tier-3-production/README.md).

## 🔗 Related Documentation

- [Tier model (canonical reference)](../../design/tier-model.md)
- [Architecture: T2 — Pilot](../../contributing/architecture.md#t2--pilot)
- [Getting Started](../../getting-started/README.md) · [Training docs](../../training/README.md)
- [T1 — Lab](../tier-1-lab/README.md) · [T3 — Production](../tier-3-production/README.md)
