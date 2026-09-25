---
title: Dataset Release Workflow
description: Review, package, publish, and verify immutable dataset releases from the Dataset Analysis Tool
author: Microsoft
ms.date: 2026-09-25
ms.topic: how-to
---

Use the Dataset Analysis Tool release workflow to publish reviewed episodes as an immutable LeRobot 3.0 package. A release preserves the source dataset, binds every included episode to an accepted decision and quality report, and publishes only after semantic read-back and SHA-256 verification succeed.

## Prerequisites

| Requirement           | Details                                                                                                      |
|-----------------------|--------------------------------------------------------------------------------------------------------------|
| Dataset Analysis Tool | Start the backend and frontend from `data-management/viewer`                                                 |
| Reviewed episode      | Create immutable annotation and edit revisions, run quality checks, and record an explicit accepted decision |
| Release destination   | Configure a local release root or Azure Blob export prefix on the backend                                    |
| Release worker        | Restore the frozen `data-management/viewer/release-worker/uv.lock` environment                               |

## Configure Destinations

The backend derives all storage locations. The browser selects only `local` or `azure`; it never sends a filesystem path, Blob prefix, storage credential, or source media value.

| Variable                              | Default           | Purpose                                                                                         |
|---------------------------------------|-------------------|-------------------------------------------------------------------------------------------------|
| `DATAVIEWER_RELEASE_ROOT`             | `./data-exports`  | Local root for review records, durable jobs, staging, and local published releases              |
| `AZURE_STORAGE_DATASET_EXPORT_PREFIX` | `exports`         | Backend-owned Blob prefix for mutable sidecars, review records, claims, and published releases  |
| `STORAGE_BACKEND`                     | `local`           | Selects the configured local or Azure storage implementation                                    |
| `AZURE_STORAGE_ACCOUNT_NAME`          | None              | Azure Storage account used when `STORAGE_BACKEND=azure`                                         |
| `AZURE_STORAGE_DATASET_CONTAINER`     | None              | Dataset and default release container in Azure mode                                             |
| `AZURE_STORAGE_ANNOTATION_CONTAINER`  | Dataset container | Optional container for mutable annotation and label sidecars beneath `<export-prefix>/mutable/` |

Keep the local release root separate from `DATA_DIR`. The backend rejects source overlap, absolute caller paths, traversal, invalid identifiers, and symbolic-link escapes. In Azure mode, omit `AZURE_STORAGE_SAS_TOKEN` to use `DefaultAzureCredential` where managed identity, workload identity, or Azure CLI authentication is available.

Every persisted workflow path includes the source dataset ID. For a source dataset named `my-dataset`, local workflow artifacts use this structure:

```text
<DATAVIEWER_RELEASE_ROOT>/
├── reviews/
│   └── my-dataset/
│       └── episodes/
│           └── episode-000000/
│               ├── annotations/<annotation-revision-id>.json
│               ├── edits/<edit-revision-id>.json
│               ├── quality/<quality-run-id>.json
│               └── decisions/<decision-id>.json
├── release-jobs/
│   └── my-dataset/
│       └── <job-id>/
│           ├── job.json
│           ├── events.jsonl
│           └── workflow.json
├── .staging/
│   └── my-dataset/
│       └── <job-id>/
└── releases/
    └── my-dataset/
        ├── .claims/<release-id>.json
        └── <release-id>/
```

Azure review and publication paths mirror the dataset scope beneath the configured export prefix:

```text
<AZURE_STORAGE_DATASET_EXPORT_PREFIX>/
├── reviews/my-dataset/episodes/episode-000000/<review-kind>/<record-id>.json
├── claims/my-dataset/<release-id>.json
└── releases/my-dataset/<release-id>/
```

Durable jobs and worker staging remain on the backend filesystem in Azure mode. Their paths still include `my-dataset`, while immutable review records and published releases use the Azure paths shown above.

Azure browsing uses an optimized media cache, but quality and release operations do not. Each operation materializes the complete source dataset, including Parquet, HDF5, metadata, and video files, into an operation-owned temporary workspace. The backend deletes that workspace after the operation completes. Source containers remain read-only; mutable annotation and label sidecars remain beneath the backend-owned export prefix.

> [!IMPORTANT]
> Publish cloud releases directly from a Viewer configured with `STORAGE_BACKEND=azure`. A generic recursive upload of a local release is not a supported promotion path because it does not guarantee destination verification or creation of `.published.json` after every other object. Follow [Record Episodes for a Verified Experiment](../recipes/data-collection/record-to-verified-experiment.md) for the T1 and T2 workflow.

## Create a Release

1. Open a dataset and episode in the annotation workspace.
1. Select **Save** to persist annotations, labels, language instructions, and edits. **Save** does not navigate.
1. Select **Run quality**. This captures the current saved source identity and checks timestamps, stream completeness, frame continuity, feature shapes and dtypes, metadata, labels, and calibration structure. Resolve every failed required check.
1. Record an explicit **Accept episode** decision with reason codes. The decision references the immutable annotation revision, edit revision, quality run, and source identity shown in the workspace.
1. Select **Create Release**. Do not select **Export Copy**; Export Copy creates a mutable artifact and is not an immutable release package.
1. Enter a release ID and reason, select the configured destination kind, and confirm the fixed LeRobot 3.0 target.
1. Review the eligibility result. Excluded episodes remain visible with machine-readable reasons, such as a failed required quality check or changed source identity.
1. Select **Create Release**. Reusing the same idempotency key and inputs returns the existing job. Reusing a release ID or idempotency key with different inputs returns HTTP `409`.
1. Monitor the durable job through `queued`, `running`, `verifying`, and `publishing`. Use **Cancel Release** before publication when cancellation is required.
1. Confirm the terminal `succeeded` state and inspect the manifest and checksum references.

**Run quality**, **Accept episode**, and **Create Release** are disabled when the current episode has unsaved changes. **Next** remains independent and asks for confirmation before discarding unsaved changes.

An accepted decision applies to exact source bytes. Saving any reviewed source file after acceptance invalidates release eligibility. When the UI reports that saved episode data changed after acceptance, run quality again and accept the current saved version before release.

The UI uses the actor from the accepted decision. Before a decision is cached, it falls back to the current annotation actor for display, but submission remains disabled until an accepted decision exists.

## Understand Quality and Format Support

The source quality profile verifies source identity, timestamps, stream completeness, frame continuity, feature shapes and dtypes, metadata, labels, and calibration structure. Any failed required check blocks release eligibility.

Structural calibration validation proves that required calibration fields and shapes are present. It does not prove physical calibration accuracy, sensor alignment, or drift. Validate those properties with robot-specific procedures before training or deployment.

The release worker owns native LeRobot 3.0 writing, semantic read-back, and LeRobot 2.1 conversion through the pinned LeRobot runtime. Quality and release source adapters support LeRobot and HDF5 datasets. Every immutable release target is LeRobot 3.0. The workspace's separate HDF5 Export action does not create release metadata, accepted or rejected ledgers, checksums, durable jobs, or immutable publication markers.

## Inspect Release Artifacts

A verified package contains the following application-owned artifacts alongside the LeRobot dataset files:

```text
<release-id>/
├── metadata/
│   ├── accepted.json
│   ├── rejected.json
│   ├── excluded.json
│   ├── package-quality.json
│   ├── release-statistics.json
│   ├── quality/
│   │   └── <quality-run-id>.json
│   └── release-manifest.json
├── checksums.sha256
├── .published.json
└── <LeRobot 3.0 dataset files>
```

| Artifact                                 | Verification purpose                                                                                                    |
|------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| `metadata/accepted.json`                 | Canonical ledger of accepted decisions included in the package                                                          |
| `metadata/rejected.json`                 | Canonical ledger of excluded rejected decisions recorded for the release                                                |
| `metadata/excluded.json`                 | Canonical ledger of other candidates excluded without a rejection decision                                              |
| `metadata/quality/<quality-run-id>.json` | Source-quality evidence bound to each accepted episode                                                                  |
| `metadata/package-quality.json`          | Semantic read-back, feature, frame-count, visual sample, inventory, and checksum evidence for packaged bytes            |
| `metadata/release-statistics.json`       | Deterministic descriptive trajectory metrics, numerical library versions, statistics profile, and feature-schema digest |
| `metadata/release-manifest.json`         | Source provenance, decision IDs, episode mapping, formats, versions, feature schema, counts, and file inventory         |
| `checksums.sha256`                       | SHA-256 digest for every inventoried package file                                                                       |
| `.published.json`                        | Final marker proving publication completed after verification                                                           |

The publisher verifies staged bytes, copies or uploads the complete candidate, verifies the destination, and creates `.published.json` last. Readers list only marker-complete releases. Existing release destinations are never overwritten.

Release packages contain regular files and directories only. Finalization and verification reject symbolic links because object storage and upload clients can materialize links as additional files, changing the exact manifest inventory.

Release statistics describe the exact staged LeRobot package. They include
per-episode duration, smoothness, efficiency, jitter, hesitation, corrections,
score, and flags. Statistics never affect quality outcomes, eligibility, or
release trust. The inventory and `checksums.sha256` bind the statistics file to
the immutable package without changing manifest schema `2.0.0`.

For local storage, published packages are beneath `<DATAVIEWER_RELEASE_ROOT>/releases/<dataset-id>/<release-id>/`. Azure packages are beneath `<AZURE_STORAGE_DATASET_EXPORT_PREFIX>/releases/<dataset-id>/<release-id>/`.

## Register Releases in Azure ML

Configure Azure Blob storage, install the optional dependency extra, and enable
registration in `backend/.env`:

```bash
cd data-management/viewer
uv sync --frozen --project backend --extra azureml
```

```env
STORAGE_BACKEND=azure
DATAVIEWER_AZUREML_REGISTRATION_ENABLED=true
AZURE_SUBSCRIPTION_ID=<subscription-id>
AZURE_RESOURCE_GROUP=<resource-group>
AZUREML_WORKSPACE_NAME=<workspace-name>
```

Azure ML registration uses this identity contract:

| Field         | Value                                                                                                     |
|---------------|-----------------------------------------------------------------------------------------------------------|
| Asset name    | Lowercase dataset slug plus a collision-resistant SHA-256 suffix                                          |
| Asset version | Exact Viewer release ID                                                                                   |
| Asset type    | `uri_folder`                                                                                              |
| Asset path    | Canonical marker-complete Azure Blob release folder                                                       |
| Properties    | Dataset ID, release ID, manifest digest, statistics digest, target format, and statistics profile version |
| Tags          | Bounded searchable dataset, release, format, and profile values                                           |

Use release IDs of 1 to 30 characters matching
`[A-Za-z0-9][A-Za-z0-9._-]*` when Azure ML registration is enabled. A published
release with an incompatible ID remains valid but cannot be registered.

Registration runs after publication and does not change release state. Startup
reconciliation retries every marker-complete Azure release through the same
evidence checks. One corrupt, conflicting, or unavailable registration does not
block unrelated releases or backend startup.

Consume the exact asset version with `ro_mount` through the training submitter.
See [LeRobot Training](../training/lerobot-training.md#azureml-data-asset-native-mount-azureml-only)
for commands. Generate release-to-model observability with the
[Azure ML Lineage Report](../../data-management/lineage-report/README.md).

## Consume a Verified Release

Set `--dataset-trust verified` when a training or evaluation Blob source points to a marker-complete Viewer release. The runtime verifies the publication marker, manifest, quality evidence, exact inventory, file sizes, and SHA-256 values before use. Generic local folders, Hub datasets, and other raw sources remain `unverified` and cannot emit release identity.

A single verified release remains immutable. Combining multiple verified releases creates a separate `derived` workspace with ordered parent summaries and its own digest. The derived workspace never inherits a parent release digest.

Training runs, evaluation results, and registered models retain release identity and source episode mapping. The demonstrated simulation endpoint is `evaluation/sil`. Hardware-in-the-loop or physical robot execution is optional and outside this workflow's critical path.

Use [Record Episodes for a Verified Experiment](../recipes/data-collection/record-to-verified-experiment.md) for complete Azure publication, verified training, evaluation, and lineage commands.

## Recover and Cancel Jobs

Jobs, transition events, and workflow responses persist together beneath `<DATAVIEWER_RELEASE_ROOT>/release-jobs/<dataset-id>/<job-id>/`. The UI stores the active job ID locally and resumes polling after a component remount or browser refresh.

At backend startup, reconciliation handles non-terminal jobs as follows:

| Evidence                                     | Recovery result          |
|----------------------------------------------|--------------------------|
| Publication marker exists                    | Mark the job `succeeded` |
| Job is still `queued`                        | Leave it queued          |
| Staging directory exists                     | Requeue the job          |
| Staging and publication evidence are missing | Mark the job `failed`    |

After reconciliation, the API release processor schedules every queued job and advances it through assembly, verification, and publication. Failed staging content remains available for diagnostics.

Cancellation is cooperative. A request sets a durable cancellation flag, and the worker stops at the next checkpoint before publication. A job already publishing cannot be cancelled because the publisher must finish verification and resolve the immutable destination claim. Partial staging content never counts as published.

## Operate the Release Processor and Worker

The API owns the asynchronous release processor in its application lifespan. Submission persists a queued job and notifies that processor; backend startup reconciles interrupted jobs before accepting new work. The LeRobot writer runs as a structured subprocess from a separate Python project because its media dependencies are incompatible with the API:

| Runtime          | Dependency ownership                                                |
|------------------|---------------------------------------------------------------------|
| `backend`        | Viewer API and media paths with PyAV 18.1; does not install LeRobot |
| `release-worker` | LeRobot 0.6.1 and its required PyAV 15.0 boundary                   |

Do not install LeRobot into the backend environment or downgrade backend PyAV. The backend writes a temporary `request.json` plus one `allow_pickle=False` NumPy value file for each frame feature. It invokes the worker through `uv run --frozen`, and the worker writes `response.json` containing either structured results or a structured error. Standard output is not part of the protocol.

`./start.sh` restores the worker before starting the backend:

```bash
cd data-management/viewer
./start.sh
```

Restore the worker manually when operating the backend without `start.sh`:

```bash
uv sync --frozen --project data-management/viewer/release-worker
```

Run the worker lifecycle from `data-management/viewer`:

| Command                           | Purpose                                                                      |
|-----------------------------------|------------------------------------------------------------------------------|
| `npm run build:release-worker`    | Restore the production environment from the frozen lock                      |
| `npm run test:release-worker`     | Run native worker tests from the frozen development environment              |
| `npm run lint:release-worker`     | Run Ruff against worker source and tests                                     |
| `npm run validate:release-worker` | Run worker lint and tests                                                    |
| `npm run clean`                   | Remove frontend, backend, and worker generated environments and build output |

The worker owns its `pyproject.toml` and `uv.lock`. Dependabot tracks `/data-management/viewer/release-worker` independently and holds PyAV below 16 while LeRobot 0.6.1 requires the PyAV 15 boundary. Generate lock updates only from public package sources and validate them with the repository uv-lock and public-feed checks.

## Troubleshoot Failures

| Symptom                                        | Action                                                                                                                            |
|------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| `uv is required to restore the release worker` | Install `uv`, then rerun `./start.sh` or the manual frozen sync command                                                           |
| Worker manifest or lock is missing             | Restore `release-worker/pyproject.toml` and `release-worker/uv.lock`; do not bypass frozen restore                                |
| Worker returns no valid response               | Run `npm run validate:release-worker`, verify the worker environment exists, and inspect the durable job failure reason           |
| Structured worker error                        | Correct the reported source, feature, conversion, or read-back issue; do not install LeRobot into the backend                     |
| Eligibility excludes an episode                | Reopen the episode, rerun quality after any source change, and create a new accepted decision referencing the current evidence    |
| HTTP `409`                                     | Choose a new release ID, or retry the original idempotent request with identical inputs                                           |
| Job fails after restart                        | Inspect staging and publication evidence; a missing pair is intentionally terminal to prevent ambiguous publication               |
| Checksums or read-back fail                    | Treat the package as unpublished, preserve diagnostics, and correct the source or packaging failure before using a new release ID |

Release diagnostics contain IDs, state, and verification status only. They do not include raw sensor values, credentials, filesystem roots, or Blob prefixes.
