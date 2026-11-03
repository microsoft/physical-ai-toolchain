---
title: Curation
description: Saved-input curation, durable judge jobs and portable release verification.
ms.date: 2026-11-03
ms.topic: reference
---

`physical-ai-curation` owns saved inputs, sample approval, durable jobs, result
application and withdrawal. Evaluation supplies optional inference through an
adapter; ordinary curation does not require a model, decoder or evaluator install.

## 📋 Prerequisites

| Component | Requirement                                                       |
|-----------|-------------------------------------------------------------------|
| Python    | 3.12 or later                                                     |
| Dataset   | Configured, confined LeRobot v2.1 or v3 root                      |
| Identity  | Explicit local principal, or the viewer's authenticated principal |
| State     | Durable job storage and conditional label/annotation storage      |

These local distributions are resolved through repository source mappings, not
assumed to be published to PyPI. Restore a consumer's committed lockfile when
using the viewer. The evaluator environment alone does not install curation.

| Install profile                 | Dependencies and behavior                                                                       |
|---------------------------------|-------------------------------------------------------------------------------------------------|
| Curation base                   | Pydantic and neutral dataset-I/O; JSONL metadata and management operations                      |
| `physical-ai-curation[parquet]` | Adds the dataset-I/O Parquet dependencies                                                       |
| `physical-ai-curation[api]`     | Adds FastAPI and Uvicorn for standalone HTTP management                                         |
| Viewer `vlm-judge` extra        | Adds evaluator core and local model runtime dependencies; validate GPU compatibility separately |

See [data-management/viewer/README.md](../viewer/README.md) for the viewer's frozen
restore and launch profiles. A curation-only application supplies its own storage
adapters; importing the base package does not install an evaluator.

Native release consumers additionally provide NumPy, PyArrow and Pandas for
tables/statistics, PyAV for necessary encoding, and Pillow for grayscale edits.
Blob consumers provide Azure Identity and Azure Storage Blob clients. These are
consumer runtime requirements, not additional curation extras. Restore the
consumer's locked profile; do not install the judge model runtime for release work.

## 🚀 Commands

Run from the repository root in an already-restored viewer backend environment.
`--no-sync` does not install missing dependencies. Replace the example dataset,
principal and job directory with the same configured scope used by the caller.

```bash
uv run --project data-management/viewer/backend --no-sync \
  python -m physical_ai_curation.run \
  --dataset /path/to/dataset --dataset-id example-dataset \
  --principal-scope-id local-operator --job-dir outputs/curation/jobs \
  --operation list
```

Management includes list, status, results, cancellation, application and reset
operations. Submission, a scoring worker and inference retry need evaluation.
Reads never trigger scoring or label application. `apply` is explicit and uses
saved evidence, current input checks and conditional writes, not a model rerun.

Dataset runs require reviewed sample evidence and an approval for the same
configuration. A single-episode smoke run is not a dataset approval. Force skips
inference reuse but does not bypass saved-input, scope or approval checks.
The two result families remain distinct: overall judge outcomes do not supply
task-specific grasp or place findings. Approval does not authorize release publication.

## 📦 Command Ownership

| Previous invocation                          | Canonical invocation                           |
|----------------------------------------------|------------------------------------------------|
| `evaluation.vlm_judge.run` lifecycle options | `python -m physical_ai_curation.run`           |
| Viewer `scripts/vlm_label_dataset.py`        | `python -m physical_ai_curation.label_dataset` |
| `evaluation.vlm_judge.api`                   | `physical_ai_curation.api:app`                 |
| Evaluator dataset and API shell recipes      | [data-management/curation/scripts](scripts)    |

There are no forwarding modules or old-command aliases. `evaluation.vlm_judge.run`
now provides direct metadata-based inference only. Policy rollouts and the OpenAI
shim remain in evaluation. See [evaluation/vlm_judge/README.md](../../evaluation/vlm_judge/README.md)
for inference and model configuration.

## 📦 Curated Releases

`physical_ai_curation.release` freezes selected saved inputs, plans copy/processing
actions, publishes independent LeRobot v3 datasets and verifies completed packages.
Use the [viewer workflow](../viewer/README.md#-curated-release) interactively or the
headless client below. Release consumes saved evidence; it does not run inference
or require judge approval.

Supported routes are local-to-local and same-account Blob-to-Blob from supported
v2.1/v3 sources. Finalize local sources before release. Destinations use
`DEST/<source-relative-path>/releases/<release-name>` and stay read-only after
publication. The [release specification](../specifications/curated-dataset-release.specification.md)
defines the complete route, selection, layout, lifecycle and verification contracts.

### Library Use

Import contracts and verification from `physical_ai_curation.release` without
installing the viewer, evaluator, decoder or Azure SDK. Native generation and Blob
adapters require the consumer dependencies listed above. A trusted composition
supplies source/saved-input readers, destination, job storage and identity; selected
judge evidence additionally requires an explicit `JudgeEvidenceAuthority`.

Use `verify_candidate` from `physical_ai_curation.release.verification` before
publication and `verify_published` for a completed package. Supply an independently
obtained `expected_manifest_sha256`. Without it, verification establishes internal
consistency, not that the package is the intended release. Verification does not
authenticate its producer or certify recording dates, provenance truth or quality.

### Headless Client

The standard-library client uses the same scoped API as the viewer. Set
`RELEASE_API_URL` and either `RELEASE_API_KEY` or `RELEASE_BEARER_TOKEN`; setting
both credentials is a usage error. Credentials are sent only as headers. With
credentials set, plaintext HTTP to non-loopback hosts is refused. Redirects are
not followed, and remote errors are reduced to fixed codes.

```text
python -m physical_ai_curation.release.client [--base-url URL] [--timeout-seconds N] [--format {json,text}] COMMAND
```

| Command                                                                                                             | Purpose                                                                |
|---------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| `preview --request FILE`                                                                                            | Read-only preview from a JSON request file                             |
| `submit --request FILE --preview-digest D --processing-digest D --idempotency-key K [--ack-local-disk] [--confirm]` | Submit a previewed request                                             |
| `status JOB_ID`, `wait JOB_ID [--timeout S] [--interval S]`                                                         | Read or wait for a job                                                 |
| `list [--cursor N] [--limit N] [--dataset-id ID]`                                                                   | List jobs                                                              |
| `accepted-request --idempotency-key K`                                                                              | Recover acceptance after a lost response                               |
| `cancel JOB_ID`, `recover JOB_ID`, `result JOB_ID`                                                                  | Job control and verified result                                        |
| `artifact JOB_ID ALIAS`                                                                                             | Download `manifest`, `inventory`, `receipt` or `report`                |
| `inventory DATASET_ID [--cursor N] [--limit N]`                                                                     | Source and saved-evidence inventory                                    |
| `source-releases DATASET_ID`, `source-release DATASET_ID HANDLE`                                                    | Retained releases for a source                                         |
| `source-episodes DATASET_ID HANDLE [--cursor N] [--limit N]`                                                        | Episodes in a retained release                                         |
| `source-media DATASET_ID HANDLE EPISODE_INDEX CAMERA --output NEW_FILE`                                             | Write episode media; existing files are refused                        |
| `verify PATH --expected-manifest SHA256`                                                                            | Verify local published output against an independent manifest identity |

Request files contain `destination`, `package_id`, `attempt_id`, `dataset_name`,
`dataset_id`, `description`, `episode_indices`, `resources` and `finalized`. Use
current saved revisions from inventory and the digests returned by preview.
See the [API contract](../specifications/curated-dataset-release.specification.md#api-contract)
for routes and response semantics.

Exit codes: `0` success, `2` usage, `3` refused, `4` remote error, `5` transport error,
`6` verification failed, `7` wait timed out, `8` job not committed.

After an uncertain submit, retain the original idempotency key and look up its
accepted request before retrying. For stale selection, fetch current inventory and
preview again. Cancellation is not immediate cleanup: inspect effects stopped,
cleanup and capacity status. Once committing has begun, recover the exact committed
result instead of trying to cancel it. Never delete job records to clear a reservation.

### Clean Start After Release Contract Changes

Old release layouts, incomplete former job records and previous execution
bindings are unsupported. There are no migration tools, old-command aliases,
fallback parsers or automatic missing-field upgrades. Users are responsible for
cleaning obsolete release structures before starting the updated workflow.

Stop release workers and settle or reconcile pending local processes and remote
copies first. Never delete job/effect records while uncertain work still depends
on them. Archive any old published packages or diagnostics needed for retention
outside the configured active roots. Then remove obsolete flat release directories,
their ownership/association sidecars, former job records and unused private
runtime/scratch state. Start with an empty release job root and the source-mirrored
collection layout. Do not delete source datasets or another active user's state.
This is an operator cleanup procedure, not automatic package adoption or migration.
Current supported source formats and truthful unknown-origin provenance remain
supported data, not release compatibility fallbacks.

### Standalone Worker

Standalone local execution uses an operator-owned configuration file:

```bash
python -m physical_ai_curation.release.runner --config worker.json --job JOB_ID
```

The bounded JSON configuration has `schema_version=curated-release-worker/v1`,
`job_root`, `source_root`, `destination_root`, `selection`, `context` and
`finalized=true`. Selection and context are the accepted `ReleaseSelection` and
`FreezeContext` values, not newly chosen actor overrides. Standalone local
composition requires `localuser`; hosted composition derives authority in the
service. Exit zero means settled; exit one returns a fixed `recovery_required`
result without exposing provider exceptions or paths. Use the native profile for
generation; importing the runner does not load Arrow, Azure or media packages.

For selections containing judge evidence, set `judge_state_root` to the explicitly
configured judge job directory containing `state.json`. The worker does not guess
this location or run inference. `ScopedJudgeEvidenceAuthority` in
`physical_ai_curation.adapters.local_saved_input` reads only the accepted principal's
current generation and validates the newest result against the current source
revision. Invalid or stale evidence is refused rather than replaced by an older
result. Without the binding, evidence selections are refused. Local saved labels,
episode contribution ledgers and evidence use the same revision and projection
contracts as the viewer reader; hosted execution still derives its own authority.

### Storage and Runtime Settings

Run supervised workers on Linux with pidfds and procfs. Keep job storage available
across restarts and runtime/scratch directories private to the service. Missing
process-stop proof or uncertain cleanup requires reconciliation, not forced removal.

| Setting                                         | Operator purpose                                                                                  |
|-------------------------------------------------|---------------------------------------------------------------------------------------------------|
| `RELEASE_JOB_ROOT`                              | Required local job/runtime root for the viewer release service                                    |
| `RELEASE_DESTINATIONS`                          | Configured aliases for absolute local destinations or Blob locations                              |
| `RELEASE_JOB_STORAGE`                           | Optional Blob location for shared durable job records                                             |
| `CURATION_RELEASE_BLOB_RESERVATION_LIMIT_BYTES` | Shared application ceiling for Blob payload reservations; defaults to 1 TiB, not Azure free space |
| `CURATION_RELEASE_READ_METADATA_BYTES`          | Retained metadata read limit; defaults to 64 MiB                                                  |
| `CURATION_RELEASE_READ_MEDIA_BYTES`             | Buffered retained-media response limit; defaults to 512 MiB                                       |

Load the application environment before calling
`ReleaseRuntimeSettings.from_environment(os.environ)` or `load_media_limits`.
The [backend environment example](../viewer/backend/.env.example) documents lease,
memory, reserve, termination, cleanup, recovery and record-size overrides. Workers
sharing job authority must use the same Blob reservation ceiling. Local scratch
has its own capacity requirement even for Blob destinations.

Status distinguishes estimates, reserved bytes, observed current usage and peak
usage. Verification reads, service copies, processing downloads and generated
uploads have different costs; see
[capacity and accounting](../specifications/curated-dataset-release.specification.md#capacity-and-accounting).
Retain job records and operational events until active effects and output-retention
obligations are reconciled. There is no automatic job/event expiry or read-time pruning.

## 🔒 Storage and Security

Keep the same dataset identity, principal scope, storage adapters and job directory
when reopening current-format jobs. Inference success and application success are
separate durable states. Application-only retries preserve contribution identity
and human-owned values; withdrawal removes only covered machine contributions.

Only caller-bound v1 evidence is supported. Pre-merge jobs without bindings or with
older input fingerprints have no compatibility reader or migration; recreate those
jobs explicitly. Reads do not rewrite stored state. Existing annotations with
unknown provenance remain protected rather than being guessed removable.

Viewer routes retain authentication, CSRF and source-qualified authorization.
Standalone HTTP requires explicit local opt-in, a dataset ID-to-root allowlist,
a local actor and durable job directory. It admits loopback peers and matching
Host/Origin only; proxy headers, shared-host exposure, tunnels and port forwarding
are unsupported. Dataset roots cannot be the filesystem root, and HTTP clients
cannot submit arbitrary media paths. Keep credentials outside arguments and Git.

Without evaluation, HTTP management remains available and scoring returns `503`.
A management-only worker leaves queued inference for an evaluator-backed worker.
The launch prerequisites and examples are in
[evaluation/vlm_judge/README.md](../../evaluation/vlm_judge/README.md).
