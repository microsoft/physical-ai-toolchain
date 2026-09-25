# Record Episodes for a Verified Experiment

Record LeRobot episodes, review them in the Dataset Analysis Tool, publish an immutable release to Azure Blob Storage, and use that release for verified training and evaluation. Use this workflow when moving from T0 local experimentation to shared T1 data or T2 cloud training.

## When to Use This Guide

| Tier | Use |
|------|-----|
| T0 - Dev | Record, inspect, and train locally. Local folders remain unverified unless consumed through a verifier-aware entry point. |
| T1 - Lab | Store source datasets in Azure Blob Storage and publish immutable Viewer releases directly to the same container. |
| T2 - Pilot | Train and evaluate registered models from marker-complete Azure releases with `--dataset-trust verified`. |

A raw upload to Azure Blob Storage is not a verified release. Verified consumers require the Viewer publication marker, manifest, quality evidence, exact inventory, file sizes, and checksums.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| LeRobot dataset | Record episodes with the Viewer Operator workspace or provide an existing LeRobot dataset |
| Azure Storage | Storage account and dataset container for T1 and T2 |
| Dataset Analysis Tool | Backend, frontend, and release worker from `data-management/viewer` |
| T2 compute | Azure ML or OSMO configured for cloud training and evaluation |

The repository does not provide a generic ROS 2 bag-to-LeRobot converter. Native ROS 2 bag capture requires a separate conversion step before the dataset can enter this workflow.

## Record Episodes

For supported robots, use the [Operator Workspace](https://github.com/microsoft/physical-ai-toolchain/blob/main/data-management/viewer/docs/operator-workspace.md) to record episodes directly into a discoverable LeRobot dataset. Save each completed episode before starting review.

For native ROS 2 capture, record bags independently and convert them with a robot-specific pipeline outside this repository. Do not point the Viewer at an unconverted bag directory.

## Land the Source Dataset in Azure

T0 users can keep the source dataset on local disk. At T1 and T2, upload the complete source dataset to the Azure dataset container before review:

```bash
az storage blob upload-batch \
  --account-name <storage-account> \
  --destination datasets \
  --destination-path source/<dataset-id> \
  --source <local-lerobot-dataset> \
  --auth-mode login
```

This upload creates a mutable source dataset. It does not create a release and must not be consumed with `--dataset-trust verified`.

## Start the Viewer in Azure Mode

Configure the Viewer to read the source container and publish releases directly to the backend-owned export prefix:

```bash
export STORAGE_BACKEND=azure
export AZURE_STORAGE_ACCOUNT_NAME=<storage-account>
export AZURE_STORAGE_DATASET_CONTAINER=datasets
export AZURE_STORAGE_DATASET_EXPORT_PREFIX=exports

cd data-management/viewer
./start.sh
```

Use Azure CLI, managed identity, or workload identity authentication. Do not create a local release and copy it with a generic recursive upload. Direct Azure publication verifies the destination and creates `.published.json` last.

## Review and Publish a Release

1. Open the Azure-backed dataset in the annotation workspace.
2. Select **Save** after annotations, labels, instructions, or edits change.
3. Select **Run quality** and resolve every failed required check.
4. Select **Accept episode** with the applicable reason codes.
5. Repeat review for each candidate episode.
6. Select **Create Release**, choose the `azure` destination, and enter a release ID and reason.
7. Review the accepted, rejected, and excluded episode sets.
8. Confirm the release and wait for the `succeeded` state.

The immutable release is published beneath:

```text
exports/releases/<dataset-id>/<release-id>/
```

Confirm that the final publication marker exists:

```bash
az storage blob exists \
  --account-name <storage-account> \
  --container-name datasets \
  --name "exports/releases/<dataset-id>/<release-id>/.published.json" \
  --auth-mode login \
  --query exists \
  --output tsv
```

Continue only when the command returns `true`.

## Train from the Verified Release

At T2, submit training with the exact release root and verified trust. The runtime rejects incomplete, changed, or non-release inputs.

OSMO:

```bash
training/il/scripts/submit-osmo-lerobot-training.sh \
  --blob-url "https://<storage-account>.blob.core.windows.net/datasets/exports/releases/<dataset-id>/<release-id>" \
  --dataset-trust verified \
  --register-checkpoint <model-name>
```

Azure ML:

```bash
training/il/scripts/submit-azureml-lerobot-training.sh \
  --blob-url "https://<storage-account>.blob.core.windows.net/datasets/exports/releases/<dataset-id>/<release-id>" \
  --dataset-trust verified \
  --register-checkpoint <model-name> \
  --stream
```

## Evaluate the Registered Model

Resolve the registered model version, then evaluate it against the same immutable release.

OSMO:

```bash
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --from-aml-model \
  --model-name <model-name> \
  --model-version <model-version> \
  --from-blob-dataset \
  --storage-account <storage-account> \
  --storage-container datasets \
  --blob-prefix "exports/releases/<dataset-id>/<release-id>" \
  --dataset-trust verified \
  --mlflow-enable
```

Azure ML:

```bash
evaluation/sil/scripts/submit-azureml-lerobot-eval.sh \
  --from-aml-model \
  --model-name <model-name> \
  --model-version <model-version> \
  --from-blob \
  --storage-account <storage-account> \
  --storage-container datasets \
  --blob-prefix "exports/releases/<dataset-id>/<release-id>" \
  --dataset-trust verified \
  --mlflow-enable
```

## Verify Lineage

The workflow is complete when:

- The Viewer release contains `.published.json`, `metadata/release-manifest.json`, and `checksums.sha256`.
- Training records `dataset.trust=verified`, the release ID, and `lineage/dataset-lineage.json` in MLflow.
- The registered model contains `azureml_lineage.json` with the same release identity.
- Evaluation results retain the release ID and source episode mapping.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| Blob folder exists but verified training fails | Confirm the URL points to the release root, not the mutable source dataset, and check `.published.json` exists |
| Release destination does not offer Azure | Restart the Viewer with `STORAGE_BACKEND=azure` and complete Azure storage configuration |
| Episode is excluded | Save changes, rerun quality, and accept the current source identity before creating a new release |
| Evaluation rejects the dataset | Use `--from-blob-dataset` for OSMO or `--from-blob` for Azure ML together with the exact release prefix |

## Related Documentation

- [Dataset Release Workflow](../../data-pipeline/dataset-release-workflow.md)
- [Preparing Datasets for Training](preparing-datasets-for-training.md)
- [LeRobot Training](../../training/lerobot-training.md)
- [Evaluation Guide](../../evaluation/README.md)
