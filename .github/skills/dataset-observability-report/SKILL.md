---
name: dataset-observability-report
description: 'Build a self-contained HTML observability report for a published LeRobot v3 viewer release and link it to the release data asset in Azure ML with per-episode runs, lineage, and verification - Brought to you by microsoft/physical-ai-toolchain'
---

# Dataset Observability Report

Compute per-episode metrics from a published viewer release, render a self-contained HTML report, and record the analysis in Azure ML next to the data asset the viewer registered for that release.

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| Python 3.12+ and `uv` | Runs the scripts with the pinned dependencies in `pyproject.toml` |
| `ffmpeg` and `ffprobe` | Cuts preview clips and contact sheets in `media.py` |
| A published viewer release copy | Local copy of the release root, including `.published.json`, `checksums.sha256`, and `metadata/release-manifest.json` |
| Viewer registration enabled | The viewer registers the release as an Azure ML data asset when `DATAVIEWER_AZUREML_REGISTRATION_ENABLED=true` |
| Azure CLI sign-in | `DefaultAzureCredential` reads the workspace and data asset, and writes MLflow runs |

The scripts never register or upload data assets. The viewer release workflow owns registration, so a report links only to a release that the viewer has already published and registered.

No `uv.lock` is committed. Resolve dependencies through your approved package index, for example `uv sync --project .github/skills/dataset-observability-report`.

## Workflow

Run the steps in order from the repository root. Each step writes a JSON record that the next step reads.

| Step | Command | Output |
|------|---------|--------|
| 1. Metrics | `observe.py RELEASE_ROOT -o BUNDLE` | Bundle with input digests, dataset metrics, and per-episode metrics |
| 2. Media | `media.py RELEASE_ROOT BUNDLE -o MEDIA` | Preview clips and contact sheets per episode |
| 3. Release check | `aml_lineage.py release RELEASE_ROOT --bundle BUNDLE -o release.json` | Release record that ties the bundle to the manifest digest and the registered data asset |
| 4. Runs | `aml_lineage.py log BUNDLE --release release.json --media MEDIA -o run.json` | One parent run with dataset metrics and one child run per episode |
| 5. Lineage | `aml_lineage.py lineage --release release.json --run run.json -o lineage.json` | Lineage graph from the release to the run |
| 6. Report | `render.py BUNDLE --media MEDIA --lineage lineage.json -o report.html` | Self-contained HTML report |
| 7. Attach | `aml_lineage.py attach run.json --file report.html=report --file lineage.json=lineage` | Report and lineage logged as run artifacts |
| 8. Verify | `aml_lineage.py verify --release release.json --run run.json -o verify.json` | Checks for the asset, runs, tags, and artifact digests |

Scripts live in `.github/skills/dataset-observability-report/scripts/`. Run them with `uv run --project .github/skills/dataset-observability-report python <script>`.

Set `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZUREML_WORKSPACE_NAME`, or pass `--subscription`, `--resource-group`, and `--workspace` to the `release` step. Records contain workspace identifiers, so keep them out of source control.

## Release Check

The `release` step fails when any check fails and writes the record with `verified: false`.

| Check | Failure meaning |
|-------|-----------------|
| Marker matches manifest | The release copy is incomplete or mixes releases |
| Checksums cover the manifest inventory | The release copy is missing or has extra files |
| Bundle inputs match release digests | The bundle was computed from different bytes than the release |
| Bundle read every release file | The bundle skipped part of the release |
| Data asset registered for the release | The viewer did not register this release; enable registration and republish |
| Asset manifest digest | The registered asset describes a different manifest |

Pass `--no-asset` to link only the release manifest when the workspace has no registered asset. Pass `--data-asset NAME` when the asset name differs from the viewer's default.

## Logging Options

| Option | Behavior |
|--------|----------|
| `--experiment` | Experiment name; defaults to `dataset-observability` |
| `--frame-stride` | Logs every Nth frame as step metrics in child runs |
| `--tag KEY=VALUE` | Adds tags to the parent run |
| `--resume RUN_ID` | Continues an interrupted run and sends only missing values |
| `--allow-duplicate` | Logs a second run for the same release and bundle |
| `--dry-run` | Prints the parent and first child payloads without writing |

See [references/azure-ml-lineage.md](references/azure-ml-lineage.md) for tags, verification, and failure handling. See [references/data-analysis.md](references/data-analysis.md) for the metrics and anomaly rules.
