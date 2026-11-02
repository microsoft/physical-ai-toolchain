---
title: Curation
description: Saved-input curation, durable judge jobs and portable release verification.
ms.date: 2026-10-30
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

## 📦 Release Library Foundations

`physical_ai_curation.release` provides versioned contracts, saved-input freezing,
object planning and independent package verification without a decoder, evaluator,
Azure SDK or web framework. Optional table, statistics and encoding helpers add
generation without a publication workflow. Guarded publication and durable job
primitives are available. The local coordinator composes native release generation;
public CLI/API submission and viewer controls remain open.

Use `verify_candidate` from `physical_ai_curation.release.verification` to check
canonical control files, inventoried bytes, frozen source/output references,
preview output paths and bounded frame-lineage records. It does not publish or
discover a package. `verify_published` additionally requires matching receipt,
report and completion-marker bindings. Both accept an independently obtained
`expected_manifest_sha256`; without it, verification establishes internal
consistency rather than protection against replacement of the whole package.
Hashes do not authenticate a producer or prove recording dates, quality or the
truth of provenance claims.

Source freezing requires explicit saved selections. Local admission requires a
finalized, quiescent source; observed changes invalidate its revision binding.
It is not a universal point-in-time filesystem snapshot. Viewer local no-auth
mode supplies `localuser` automatically; authenticated scope and CSRF remain
authoritative without a separate release-role or grant policy.

`plan_release_objects` classifies exact copies, generated metadata and necessary
encoding for LeRobot v3 targets. It opens no media or storage. Local-to-local and
same-account Blob-to-Blob are planned routes, not live target publication evidence.
Capacity estimates remain advisory; the job store separately admits durable
reservations and supervised execution applies hard runtime bounds.
Call `load_media_limits` after application environment loading;
its overrides are listed in the [backend environment example](../viewer/backend/.env.example).

`execute_exact_copy` writes an independent, create-only local object through
descriptor-bound reads and writes, checking the planned byte length and digest.
Source and destination roots must be disjoint; symlinks and unsafe paths are
rejected. A successful object copy is not a release commit, durable capacity
reservation or proof of native LeRobot readability. Use `iter_subset_frames` for
streamed frame mappings; the materialized timeline helper admits its estimated
memory cost before allocation.

`release.tables` retains selected recorded columns, rebuilds native output
coordinates, writes a named task index and produces episode/video references.
Persistent language changes are retimed through shared dataset-I/O logic;
events on removed frames are dropped. Table and statistics operations require
NumPy, PyArrow and Pandas in the composing runtime, not the base installation.
Missing mandatory media summaries are explicit errors. Count-weighted moments
retain sample counts; aggregated quantiles are labelled approximate. Valid source
summaries can be preserved for a full selection only when counts and independently
generated moments agree. These checks do not prove that supplied source summaries
are truthful or that saved transforms were applied.

`release.composition.prepare_saved_generation` reads selected frozen saved values,
validates their dataset/episode identity and composes frame removal plus saved
`invert` and `grayscale` color filters. Grayscale additionally requires Pillow.
Crop, resize, color adjustment, other filters, insertion, trajectory changes,
subtask replacement and combined global/camera transforms are refused during
preview. Unselected edits never enter the plan, and annotations do not replace
recorded task identity. Encoder options and copied-camera summaries participate
in the existing object planner's digest. Missing copied-camera summaries block
preview without decoding video. Shared-object encoding follows frozen output
episode order, not canonical source-ID order.

`release.tables.generate_native_selection` combines this plan, condition-bound
source tables/tasks and actual encoded-media results into native data/episode
tables and statistics. It checks confirmation, output frame windows, video rates,
geometry and codec descriptors. It neither opens storage nor publishes files.
The caller must revalidate frozen source/saved identities, regenerate the plan
and check confirmation before media effects; checking again during assembly does
not replace that pre-dispatch check. The caller also retains frozen evidence and
streamed frame mappings for later package assembly.

`release.encoding.encode_windows` additionally requires PyAV. It reads explicit
frame windows from a seekable binary stream and writes H.264 video with contiguous
timestamps. Frame removal, per-window color filters and explicit low-level
horizontal/vertical flips are supported. Flips are not current saved-edit fields.
Short media,
timestamp gaps, incompatible rates and insufficient estimated memory are errors.
Video summaries describe bounded samples of transformed RGB frames before lossy
encoding; their counts are sampled frames, not pixels or all episode frames.
The caller supplies admitted source streams and a private output sink. The helper
does not check storage revisions, own effects or publish a release.

`release.publication.LocalPublisher` writes independent, create-only objects under
the chosen dataset name beneath an explicitly supplied destination. The name
defaults to the package ID; `PublicationAttempt.dataset_name` selects another
name. Configure a parent subfolder to group releases, or use the normal `data_dir`
or Azure dataset container. Attempt identity does not add another dataset level.
An external `_releases/<dataset_name>.json` companion binds the named root to its
package/attempt before payload writes; existing unowned roots are never adopted.
Directory entries and completed object names are synchronized before completion.
`prepare` independently verifies destination content and writes derived receipt
and report artifacts. `publish` writes the canonical marker without a rename or
promotion copy, then verifies the published package. Generic viewer catalogs
exclude operational metadata, not completed release datasets. Publisher and viewer
discovery admit recorded names only after independent marker/content verification;
direct reads apply the same check. Legacy datasets without an ownership record
remain ordinary datasets and are not labelled verified releases. Blob admission
uses the existing async storage client through a bounded streaming bridge, with
no second credential client or whole-payload buffer. Configure its total deadline
and byte budget after environment loading; the defaults are 30 seconds and 4 GiB.
Larger releases may need higher configured bounds. A missing marker, changed
payload, unavailable ownership probe or exhausted budget refuses admission rather
than falling back to legacy discovery. Dataset video uploads do not mutate a
recorded release.

`release.azure_publication.BlobPublisher` uses injected synchronous Azure clients,
create-only destination writes, source ETag conditions and independent streaming
verification. Source URLs must be query-free and same-account. Source bearer
authorization requests synchronous service copy; asynchronous reconciliation is
separate and requires a service-authorized source. Pending or failed copies block
verification. Abort cleanup binds the owned operation and copy ID and uses
conditional deletion. SDK imports are lazy.

Both publishers require `PublicationControl` to durably record and fence effects
before dispatch, retain uncertain intents and serialize publication with
cancellation. Publication requires an irrevocable commit decision and a closed
ledger with stopped writers. The adapters are not standalone job runners or an
unsupervised publication API. Provider tests do not establish live Azure
authorization, copy limits or cancellation behavior.

`validate_processing_confirmation` requires the current preview digest whenever
processing needs confirmation. Missing statistics and capacity blockers cannot
be overridden. The composer must first revalidate frozen inputs and regenerate
the plan; this check is not authentication, fencing or durable job admission.

### Storage-Native Job State

`release.jobs.ReleaseJobStore` persists release acceptance, scoped idempotency,
fenced leases, reservations, effect intents and lifecycle events as versioned
per-job JSON records. Local records live under `jobs/<job_id>.json` beneath the
explicitly configured state folder. Choose a folder beneath `data_dir`, a separate
folder or a subfolder of a selected directory. No database server or SQLite file
is required. Each transition replaces one complete record atomically and syncs
the file and directory while holding a filesystem lock.

`release.record_storage.BlobRecordStorage` uses the selected container/prefix for
the same job documents. It coordinates acceptance with a renewable Blob lease,
conditional record writes and a pending-write checkpoint. A replacement owner
finishes that checkpoint before admitting new work; a lost response does not
permit a duplicate job. An Azure-backed job does not depend on a local database
or local state cache. Indexes, idempotency bindings and reservations are rebuilt
from persisted records when the service reopens the store. Initial deployment
trades indexed lookup performance for self-contained, inspectable storage.

`release.record_storage.BlobLocation` binds a remote payload destination to its
canonical HTTPS account origin, container and prefix. Job acceptance does not
resolve that destination as a local path or measure worker disk as Azure capacity.
Blob payload reservations share an account-wide application ceiling, including
retained output, across containers, prefixes and execution hosts using the same
job authority. `CURATION_RELEASE_BLOB_RESERVATION_LIMIT_BYTES` defaults to 1 TiB;
this is an admission policy limit, not measured Azure quota or free space. Use the
same configured ceiling for workers sharing that authority. Local scratch still
requires its own filesystem reservation and can fail independently of remote
payload admission.

Mutable operational records remain separate from immutable published package
bytes, within configured storage. Local runtime proofs and scratch may use a
configured worker folder. Missing proof from an unavailable execution host is an
unresolved obligation, not evidence that the old writer stopped. There is no
automatic migration from the earlier uncommitted database implementation.

Pass settings from `ReleaseRuntimeSettings.from_environment(os.environ)` after
environment loading. The [backend environment example](../viewer/backend/.env.example)
documents lease, reserve, native memory, termination, liveness, contention and
recovery overrides, plus bounded record sizes and scan counts. State storage must
remain available across worker restarts. A supplied Blob adapter accepts the
same validated limits explicitly; optional Azure imports remain lazy.

### Supervised Execution

Acceptance precedes `running`; `sealed` closes payload effects and journals exact
receipt/report identities. `committing` records the irrevocable manifest-bound
decision before the final marker, and `committed` records verified publication.
The final local or Blob marker uses a separate commit-bound intent and registered guardian
child; closing the payload ledger never grants a general late-write capability.
Recovery checks the persisted guardian proof even when the controller lost its
stop acknowledgment. It removes only registered inode-matching marker partials,
verifies an existing marker before acknowledging it, and retains foreign residue.
Pending marker effects or writers cannot advance the job to `committed`.
Cancellation moves an uncommitted job to `cancelling`; stopped effects, cleanup
and retained capacity are separate status fields. No clean cancellation is reported
while an effect or private scratch charge is unresolved. A new worker cannot
dispatch another path while an earlier intent remains unresolved.

`release.coordinator.generation_request_bytes` supplies the exact durable request
binding. `run_release_generation` revalidates frozen source/saved/context identities,
checks that binding and the confirmed plan, then executes guarded local/local or
same-account Blob/Blob output. Mixed routes, cross-account copies and overlapping
source/output roots are refused. Blob source reads bind fresh raw ETag/version
identity to the frozen revision, use conditional requests and verify streamed
content. Unsupported version-specific copy URLs remain refused; query strings
and credential-bearing source addresses are not accepted.
Native metadata and table decoding/assembly run in capped guardian children;
affected video spools run sequentially under the same boundary. Unchanged video
copies never invoke an encoder or decoder. Local payload copies, server-side Blob
copies, generated files and streamed lineage run in registered guardian children.
Azure credentials are acquired within the child, not serialized in its protocol.
Native scratch and output sinks reject
growth before exceeding admitted bounds. Shared filesystems use one reservation;
separate scratch filesystems receive independent durable charges.

Private workspaces journal their creator's kernel identity and directory inode
before receiving bytes. Normal cleanup requires current creator authority and
stopped children; restart recovery additionally requires independent creator-exit
evidence. Replaced directories, symlink residue, exhausted cleanup budgets and
uncertain creation outcomes retain their charge. State and scratch roots are
service-private directories, not shared writable output areas. Unknown destination
residue is not deleted or reported as completed cleanup.

Scoped status separates reserved scratch, observed current scratch and measured
peak scratch. Idempotent completed-I/O observations record original-source and
independent destination verification reads, unchanged-copy bytes and generated
local output. These are not estimates or proof of unobserved partial effects.
Blob composition additionally records completed storage-copy bytes, verified
affected-media downloads and independently verified generated uploads. Local
copies are not mislabeled as remote traffic. Uncertain transfers do not count as
acknowledged observations.

`SupervisedBlobPublisher.reconcile_copy(cancel=True)` requires durable cancellation
and a journaled copy identity before starting a separate registered cleanup child.
It rechecks ownership, aborts a pending copy and conditionally deletes only its
unpublished residue, including completion during abort. Lost delete responses
remain unresolved until independent absence and stopped-writer proof are observed.
Ordinary writer registration remains refused after cancellation. This copy-level
reconciliation does not complete whole-release cleanup, remove the ownership
companion or release retained capacity.

`SupervisedBlobPublisher.cleanup_cancelled()` checks the entire unpublished
root against durable payload/control intents before deleting anything. Markers,
unjournaled objects and foreign ownership prevent cleanup. Each registered child
rechecks the companion, settles pending copies and deletes with current ETag
conditions. A separate cleanup-only intent authorizes removal of the exact
companion after the payload root is empty. Capacity is released only after stopped
writer proof and independent absence checks for both root and companion.
Lost payload or companion delete responses survive a job-store restart and require
explicit retry. Cleanup uses the configured total budget for streaming preflight
checks and supervised children; a blocked child is stopped and reaped. Failure
exposes `cleanup_state=failed` and retains capacity, rather than reporting clean
cancellation. Provider tests with real children do not establish live Azure behavior.

`physical_ai_curation.release.runner.execute_job` restores an accepted request
from durable JSON using a trusted `ConfiguredExecution` source, saved reader,
selection, context and destination. Native metadata and table identities are
reconstructed by a bounded guardian child; accepting-process Arrow buffers are
not required. Sealed/committing jobs recover only the exact manifest, and uncertain
effects require reconciliation rather than blind redispatch. `run_queue` performs
one bounded pass using a service-owned resolver; it is not an HTTP background task.

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

Status includes bounded category/status/duration diagnostics and conservative
current/peak journaled destination bytes. These include uncertain intents, not
measured Blob allocation; independent absence or completed owned cleanup reduces
current usage, never the historical peak. These counters do not replace capacity
reservations or actual verification reads. Public submission/API clients remain a
separate adapter layer.

The Linux supervisor requires pidfds and procfs. It persists process start/boot
identity before releasing the child start gate, drains descendants after controller
loss and emits stop proof only after reaping. Missing proof, a killed guardian or
an unsupported host remains unresolved; a reused PID is not evidence of shutdown.
`recover_runtimes` processes at most 128 registered runtimes per invocation within
one configured total wait budget. Recheck status before replacement effects.

Job pages contain at most 100 entries and expose only the requesting principal's
scope. Lifecycle transitions and their event outbox share a transaction; JSONL
export repairs only a partial final record and verifies existing cursors against
the events embedded in job records. The export is reconstructible local
application telemetry, not OTLP or job authority. There is no automatic job/event
TTL or reservation expiry. Retain job records and relevant operational evidence
until all active effects and output-retention obligations are reconciled;
reads never prune them.

`BlobPublisher.reconcile_copy` observes destination ownership, copy ID and actual
content through a durable reconciliation guard. It records copy identity before
abort/delete and handles completed copies or already-absent cleanup residue.
Provider-fault tests are not live Azure proof. Local and same-account Blob
generation composition is implemented. Public cleanup/recovery entry points,
public submission adapters and hosted/target acceptance remain separate work.

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
