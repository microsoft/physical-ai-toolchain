# Azure ML Lineage

`aml_lineage.py` links an observability bundle to a published viewer release. It reads the release and the data asset the viewer registered, and writes MLflow runs, tags, and artifacts. It never creates or changes data assets.

## Identity Chain

Each record stores the digest of the stage before it, so any report traces back to the released bytes.

| Stage | Identity | Stored in |
| --- | --- | --- |
| Release | `release_id` and SHA-256 of `metadata/release-manifest.json` | Viewer release root |
| Data asset | `{name}:{release_id}` with `manifest_sha256` and `statistics_sha256` properties | Azure ML workspace, written by the viewer |
| Bundle | `inputs_digest` over the SHA-256 of every file read | Bundle `manifest.json` |
| Run | Run ID with release, asset, and bundle tags | Azure ML experiment |
| Report | SHA-256 of each attached artifact | Run record, updated by `attach` |

The asset name matches `azureml_data_asset_name` in the viewer backend: the lowercased dataset ID slug followed by a 12-character SHA-256 suffix. The asset version is the release ID.

## Run Tags

| Tag | Value |
| --- | --- |
| `run_kind` | `dataset-observability` |
| `dataset_id`, `dataset_release_id` | Release identity from the marker and manifest |
| `dataset_manifest_digest` | SHA-256 of the release manifest |
| `dataset_trust` | `verified`, set only after every release check passes |
| `dataset_target_format` | Target format and version, for example `lerobot:v3.0` |
| `observability_inputs_digest` | Bundle inputs digest |
| `observability_tool_sha256` | SHA-256 of the bundle tool |
| `azureml_data_asset`, `data_uri` | Registered asset and its storage URI, omitted with `--no-asset` |

Child runs carry `episode_index`, the source episode index from the manifest mapping, anomaly counts, and `clip_sha256` when media is supplied. Parent metrics use the episode index as the step, and child metrics use the frame index as the step.

## Verification

| Check | Scope |
| --- | --- |
| Data asset | The registered asset exists, is not archived, and still describes the release manifest |
| Run finished | The parent run reached `FINISHED` |
| Run lineage tags | Parent tags match the release record and bundle |
| Episode child runs | One finished child run per bundle episode |
| Artifact listed and digest | Each attached file is present and its SHA-256 matches the attach record |

## Failure Handling

| Condition | Behavior |
| --- | --- |
| HTTP 429 throttling | Waits, then resends only points that did not land |
| Etag conflict or 5xx | Waits briefly, rereads metric history, and resends missing points |
| Interrupted logging | The run stays `RUNNING`; rerun `log` with `--resume RUN_ID` |
| Terminal run passed to `--resume` | Refused, because Azure ML cannot reopen a finished, failed, or killed run |
| Existing run for the same release and bundle | Refused unless `--allow-duplicate` is set |
| More than 100 runs | Child run searches follow page tokens |
| Artifact listing | Uses the artifact repository directly, because `MlflowClient.list_artifacts` fails on Azure ML artifact stores |
