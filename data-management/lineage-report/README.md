---
title: Azure ML Lineage Report
description: Generate a runtime Markdown report joining Viewer releases, Azure ML models, and MLflow runs
author: Microsoft
ms.date: 2026-09-25
ms.topic: how-to
---

Generate a bounded, deterministic Markdown report from Azure ML data assets,
registered models, and referenced MLflow runs. The report joins immutable Viewer
release evidence without committing deployed environment identifiers.

## 📋 Prerequisites

| Requirement                         | Details                                                              |
|-------------------------------------|----------------------------------------------------------------------|
| Python                              | Python 3.12 or later with `uv`                                       |
| Azure access                        | `DefaultAzureCredential` access to the target Azure ML workspace     |
| Terraform state or explicit context | `infrastructure/terraform` outputs, or all three workspace arguments |
| Registered releases                 | Data assets with Viewer release evidence properties                  |
| Registered models                   | Models tagged with release ID and manifest digest                    |

Authenticate before generating a runtime report:

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
```

## 🚀 Quick Start

Restore the frozen environment and generate the default report:

```bash
cd data-management/lineage-report
uv sync --frozen
uv run lineage-report
```

The default output is `data-management/lineage-report/output/report.md`. The
`output/` directory is gitignored because reports contain deployed workspace,
asset, model, and run identifiers.

The CLI resolves missing workspace arguments from
`terraform output -json azureml_workspace` in `infrastructure/terraform`.

## ⚙️ Configuration

| Argument            | Environment fallback     | Default                    | Purpose                                       |
|---------------------|--------------------------|----------------------------|-----------------------------------------------|
| `--subscription-id` | `AZURE_SUBSCRIPTION_ID`  | Terraform output           | Azure subscription                            |
| `--resource-group`  | `AZURE_RESOURCE_GROUP`   | Terraform output           | Azure ML resource group                       |
| `--workspace-name`  | `AZUREML_WORKSPACE_NAME` | Terraform output           | Azure ML workspace                            |
| `--terraform-dir`   | None                     | `infrastructure/terraform` | Terraform output directory                    |
| `--output`          | None                     | `output/report.md`         | Generated Markdown path                       |
| `--max-records`     | None                     | `1000`                     | Global bound for asset and model versions     |
| `--fixture`         | None                     | None                       | Offline JSON fixture instead of Azure clients |
| `--generated-at`    | None                     | Current UTC time           | Deterministic timestamp override              |

Provide explicit runtime context when Terraform state is unavailable:

```bash
uv run lineage-report \
  --subscription-id <subscription-id> \
  --resource-group <resource-group> \
  --workspace-name <workspace-name>
```

Do not place credentials, tokens, signed URLs, or environment-specific reports
in source control. An output path outside the ignored project `output/`
directory emits a warning.

## 🔗 Join Contract

The collector joins every asset and model on both release ID and manifest
SHA-256. Matching release IDs with different manifest digests are reported as a
digest mismatch instead of being joined.

| Source                | Required fields                                                                                                   |
|-----------------------|-------------------------------------------------------------------------------------------------------------------|
| Data asset properties | `dataset_id`, `release_id`, `manifest_sha256`, `statistics_sha256`, `target_format`, `statistics_profile_version` |
| Model tags            | `dataset_release_id`, `dataset_manifest_digest`, `dataset_trust`, Azure ML and MLflow run identifiers             |
| MLflow run            | Referenced run and experiment identity when reachable                                                             |

Azure ML tags support discovery. Checksum-protected release bytes remain authoritative.

## 📊 Report Statuses

| Status            | Meaning                                                             | Action                                                 |
|-------------------|---------------------------------------------------------------------|--------------------------------------------------------|
| `complete`        | Asset, model, manifest digest, and optional MLflow enrichment match | No action                                              |
| `asset-orphan`    | A release asset has no model with matching immutable evidence       | Train or register the expected model                   |
| `model-orphan`    | A release-tagged model has no matching asset                        | Restore or register the release asset                  |
| `digest-mismatch` | Release IDs match but manifest digests differ                       | Treat as an identity conflict and inspect both records |
| `mlflow-missing`  | Asset and model match, but the referenced MLflow run is unavailable | Restore access or verify the model's run tags          |

One release can produce multiple rows when several model versions reference the
same immutable evidence.

## 🧪 Offline Fixture Mode

Use fixture mode for deterministic local validation without Azure access:

```bash
uv run lineage-report \
  --fixture tests/fixtures/lineage.json \
  --output output/fixture-report.md \
  --generated-at 2026-09-25T12:00:00Z
```

The fixture JSON contains `context`, `assets`, `models`, and optional `runs`
objects matching the runtime entity fields.

## 🔍 Troubleshooting

| Symptom                            | Action                                                                                                |
|------------------------------------|-------------------------------------------------------------------------------------------------------|
| Terraform context resolution fails | Pass all three workspace arguments or run Terraform from the configured state directory               |
| Azure authentication fails         | Run the prerequisite authentication script and verify workspace Reader access                         |
| Report omits a release asset       | Confirm the asset version contains all required Viewer evidence properties                            |
| Report shows `model-orphan`        | Compare the model release ID and manifest digest tags with the data asset properties                  |
| Report shows `mlflow-missing`      | Verify the run still exists and the current identity can read MLflow tracking data                    |
| Output warning appears             | Write under `data-management/lineage-report/output/` or protect the external path from source control |

## 📚 Related Documentation

* [Dataset Release Workflow](../../docs/data-pipeline/dataset-release-workflow.md)
* [LeRobot Training](../../docs/training/lerobot-training.md)
* [Dataset Analysis Tool](../viewer/README.md)
