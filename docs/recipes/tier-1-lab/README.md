# T1 — Lab: Add Your First Cloud Resource (Storage)

The small-lab and integrator tier. You keep the local training lifecycle loop from
[T0 — Dev](../tier-0-dev/README.md) and add exactly **one** cloud resource: a single Azure Blob
storage account. Nothing else moves to the cloud: no Kubernetes, no Arc, no Flux. This is the
honest first cloud step for one site, a few robots, and a shared GPU box.

> [!NOTE]
> Start from the [T0 — Dev recipe](../tier-0-dev/README.md). T1 changes only *where data and run
> history live*, not the training, validation, or deployment mechanics. For canonical tier
> definitions, see the [tier model](../../design/tier-model.md) and the
> [architecture tier detail](../../contributing/architecture.md#t1--lab).

## 🧱 Minimum Infrastructure

| Concern     | What you need                                                                              |
|-------------|--------------------------------------------------------------------------------------------|
| Hardware    | One site, a few robots, optionally a shared GPU box. Local GPU still optional.             |
| Edge infra  | A shared disk (NFS/SMB), or each robot `rsync`s up. No Kubernetes, no Arc, no Flux.        |
| Cloud infra | **One Azure Blob storage account** with a dataset container. AzureML and MLflow optional.  |
| Tooling     | T0 tooling plus the Azure CLI (`az login`) and `azcopy`.                                   |
| Tracking    | Local file-backed tracking carried from T0, optionally promoted to a shared MLflow server. |

The delta from T0 is one storage account. That is the whole tier.

## 🚀 Steps

### Step 1: Capture to shared disk (or rsync up)

Record on each robot as in T0, then land the data on a shared NFS/SMB disk so the lab can reach it,
or have each robot `rsync` its datasets to the GPU box. The recording mechanics are unchanged from
[Configuring Edge Data Recording](../data-collection/configuring-edge-data-recording.md).

### Step 2: Move data to one Blob container

Upload datasets to your single Blob container with `azcopy` or the Azure CLI:

```bash
az login
az storage blob upload-batch \
  --account-name <storage-account> \
  --destination datasets \
  --source ~/datasets/insertion-task
```

See [Blob storage structure](../../cloud/blob-storage-structure.md) for the expected container
layout, and [Preparing Datasets for Training](../data-collection/preparing-datasets-for-training.md)
for downloading, inspecting, and validating Blob datasets.

### Step 3: Curate against the Blob container

Run the dataviewer against the Blob container (managed identity or SAS) instead of a local directory.
The curation workflow is the same as T0; only the data source changes from local disk to cloud
storage.

### Step 4: Train locally, or reach to AzureML when needed

Keep training on the shared local GPU box exactly as in T0. When that box saturates, this is the
first point where reaching to AzureML becomes optional, but it is **not** required at T1. If you do
submit a cloud job, see [AzureML training](../../training/azureml-training.md) and
[LeRobot training](../../training/lerobot-training.md). Datasets now live in Blob, so train directly
from Blob URLs.

### Step 5: Track locally, or promote to managed MLflow

Keep training outputs on local disk as at [T0 — Dev](../tier-0-dev/README.md#step-5-keep-your-run-outputs-locally).
When a team needs shared run history, AzureML provides managed MLflow tracking. See
[Experiment tracking](../../training/experiment-tracking.md). Adopting that hosted tracking server plus
a *model registry* as the default is the [T2 — Pilot](../tier-2-pilot/README.md) concern.

### Step 6: Validate and run on robot

Validation (`run-local-lerobot-eval.py`) and deployment (a plain container per robot, hand-updated
with `docker pull` across 2–3 robots) are unchanged from T0.

## 🎓 Graduate When

- Training scale or team size **outgrows one GPU box**: cloud training becomes the default at
  [T2 — Pilot](../tier-2-pilot/README.md).
- Dataset governance, versioning, and shared catalogs become necessary.

## 🔗 Related Documentation

- [Tier model (canonical reference)](../../design/tier-model.md)
- [Architecture: T1 — Lab](../../contributing/architecture.md#t1--lab)
- [Blob storage structure](../../cloud/blob-storage-structure.md)
- [T0 — Dev](../tier-0-dev/README.md) · [T2 — Pilot](../tier-2-pilot/README.md)
