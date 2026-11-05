---
title: Curated Dataset Release
description: Contracts for saved-selection capture, advisory preview, immutable LeRobot publication, verification and recoverable release jobs.
ms.date: 2026-11-05
ms.topic: reference
---

A curated release is an independent, immutable LeRobot v3 dataset produced from
an ordered source selection and explicitly selected saved curation. Publication
binds its payload, provenance and verification artifacts to one manifest identity.
Sources are not modified, and consuming a published package does not require them.

## Scope and Supported Routes

| Source and destination                     | Output                                     | Contract                                                                           |
|--------------------------------------------|--------------------------------------------|------------------------------------------------------------------------------------|
| Local to local                             | LeRobot v3 from supported v2.1 or v3 input | Independent copies, generated metadata and necessary media encoding                |
| Same-account Blob to Blob                  | LeRobot v3 from supported v2.1 or v3 input | Condition-bound service copies, affected-media downloads and generated uploads     |
| Local to Blob or Blob to local             | Unsupported                                | Mixed storage routes are refused                                                   |
| Cross-account Blob                         | Unsupported                                | Copy source URLs must be same-account, query-free and without embedded credentials |
| v2.1 output or arbitrary format conversion | Unsupported                                | Supported input formats do not imply matching output formats                       |

The [general curation operations](dataset-curation.specification.md) are separate
contracts. Release does not imply support for arbitrary splitting, merging or
conversion. It does not run inference or require judge approval. Azure ML
registration, training/model integration, signing, WORM storage and legal-retention
policy are outside this contract.

## Selection and Source Identity

Selection preserves explicit episode order. The worker captures the current revisions
and values of explicitly selected saved resources during execution, not preview.
Unsaved browser drafts and unselected edits are not release inputs.
Frozen source identities and source-object references remain distinct from output
dataset identities, paths and frame coordinates.

Local admission requires the caller's attestation that the source is finalized and
quiescent. Preview must not attempt to prove that attestation through source scans,
freshness polling or repeated revision checks.
Revision checks detect observed changes; they are not a general point-in-time
filesystem snapshot. Source and destination roots must be disjoint, and unsafe
paths or symlink traversal are refused. Blob reads bind ETag/version identity to
the revision captured by the worker and use conditional requests. A storage revision identifies
the captured object state; it is not a content hash.

The Azure policy is independent of where the application runs: a workstation,
dev container and Azure host follow the same rules. Unchanged payloads are copied
within Azure Storage without downloading them to the host for verification.
Necessary metadata and selected frame-table ranges may be read into bounded memory,
but source episodes and tables are not staged on disk. Video download and staging
require a confirmed encoding action. Generated output scratch is accounted for
separately and does not authorize staging unchanged source payloads.
These restrictions apply during preview, generation, recovery and verification.
An unavailable trusted content hash must not trigger a payload download fallback,
a fabricated digest or an ETag represented as a content hash. Revision-bound copy
evidence records the available assurance instead.

Selected annotations, labels, contribution ledgers and judge evidence retain
their source attribution and revisions. Judge evidence requires an explicitly
bound authority for the accepted principal and current generation. Invalid or
stale newest evidence is refused, not replaced with an older result. Local no-auth
composition uses `localuser`; hosted composition derives identity from existing
authentication and saved-resource scope. Releases introduce no role or grant policy.

## Preview and Admission

Preview is advisory and quick. It describes the submitted selection and configured
destination without reading source metadata or saved-resource contents. It does not freeze or verify
source or saved revisions, hash payloads, enumerate the full inventory or repeat
selected-object checks. Unknown estimates and completeness are reported as unknown,
not inferred. Preview creates no job, reserves no capacity and writes no destination
objects. Unsupported operations and known structural or capacity blockers are
refusals. Missing or partial feature statistics are warnings and never block release.

Submission requires the selected intent, applicable acknowledgments and a scoped
idempotency key. Acknowledgments include local source finalization, local disk use
and video processing consent when processing is required. Required processing without
consent fails before any video is processed. Acceptance durably records the selected
intent before expensive source inspection. The submitted selection must match the
intent confirmed in the dialog; a mismatched request is refused. A source revision
changing after preview is not a stale-preview rejection. The worker captures the
source and its actual revisions during execution. An admission-time capacity refusal
returns `507`; insufficient capacity discovered during execution fails the accepted
job before output effects. An ambiguous transport/server failure does not establish
whether acceptance occurred.

Accepted-request lookup and replay use the original idempotency key before
reopening source data. Retrying an uncertain submission must not create another
logical job. Configured destination aliases resolve server-side before acceptance;
the accepted effective destination is retained across worker restart.

Job creation time is the durable acceptance event's timestamp, exposed as UTC ISO
8601 text and displayed in the browser's locale and timezone. Capture, retry and
reconnection must not replace it with a new creation time.

Acceptance durably records the immutable execution intent before native generation
starts in the independent backend worker. Closing the dialog stops browser work,
not an accepted job. Reload and reopen use the saved request key or active-job
pointer to rediscover that job; cross-tab pointer updates do not submit new jobs.
Only explicit job cancellation requests server-side cancellation.

## Native Generation and Saved Projections

Unchanged video objects are independently copied without an encoder or decoder.
Affected media uses the confirmed processing plan. Supported saved operations are
frame removal and `invert`/`grayscale` color filters. Crop, resize, other color
adjustments, insertion, trajectory changes, subtask replacement and combined
global/camera transforms are refused during worker capture, before output effects.

Azure frame-table processing uses conditional ranges pinned to the captured
revision. Parquet footer size, row-group statistics, compressed transfer and decoded
memory bounds are checked before reading selected row groups. Only selected episode
rows are retained. Missing usable Parquet row-group statistics, changed revisions or exceeded limits
are refusals, not permission to download or stage the entire source table. Metadata
row and expanded-byte limits are checked before decoding; the native worker memory
limit remains the process-level backstop.

Generation preserves selected recorded columns, rebuilds native episode/frame
coordinates and task indices, and produces consistent video references and
statistics. Persistent language changes are retimed; events on removed frames
are omitted. Shared-object encoding follows selected output episode order.
Missing generated media summaries, short media, timestamp gaps, incompatible rates
and insufficient processing memory are errors.

Count-weighted statistics retain sample counts; aggregated quantiles are labelled
approximate. Transformed-video summaries describe bounded RGB frame samples before
lossy encoding, not every frame or pixel. Source summaries may be preserved for a
full selection only when counts and independently generated moments agree.

Feature statistics are image and numeric normalization statistics. Missing or
partial camera summaries and absent statistics metadata publish with a warning that
names the omitted coverage. Present valid values are preserved, and absent values are
not fabricated. Completeness is reported separately from package integrity and
training readiness. Motion-quality analysis is a separate curation signal and is not a
release requirement. Curation enrichment that would supply missing statistics is future
backlog work, not part of this contract.

Selected annotations are projected into output coordinates under
`annotations/episodes/episode_<output-index>.json`, using six-digit indices.
`meta/episode_labels.json` uses release identity and output episode keys. Removed
frames do not retain active annotation spans. Source-only claims and analysis
invalidated by processing remain historical frozen evidence. Saved edit instructions
are not replayed against the generated release. All projections are inventoried.

## Package and Verification Contract

The collection is `DEST/<full-source-relative-path>/releases`. Each package uses
one stable named root; no additional attempt directory or promotion copy is needed.
The illustrative layout below omits native shard and optional lineage filenames;
the manifest and inventory define the actual closed object set.

```text
DEST/<source-relative-path>/releases/
  <release-name>/
    meta/, data/, videos/          # Native v3 dataset objects
    annotations/                  # Selected output-coordinate projections
    evidence/frozen-input.json    # Frozen selection and saved values
    evidence/preview.json         # Worker-captured execution plan
    release/manifest.json         # Package bindings
    release/inventory.json        # Payload lengths and declared integrity evidence
    release/receipt.json          # Verification receipt
    release/report.md             # Derived readable report
    release/marker.json           # Final publication boundary
  _releases/<release-name>.json    # Private attempt ownership
  _release_associations/           # Private source/principal associations
```

### Schema and Closure

Package control models use semantic `schema_version` values with supported major
version 2; current model and verifier versions are `2.0.0`. Unknown fields and
unsupported major versions are rejected. API and private worker bindings have
their own versions and are not aliases for the package schema.

Release and job schemas are go-forward contracts, without migration or compatibility
layers for earlier release/job state. This does not remove support for LeRobot v2.1
and v3 source datasets.

The inventory binds normalized relative payload/evidence paths and lengths. Each
entry declares its `assurance`:

- `content_sha256`: the entry binds a SHA-256 digest checked by streaming its bytes.
  Local outputs use this form.
- `storage_revision`: the entry binds a storage revision and a copy-operation
  SHA-256 instead of a content digest. Azure-side unchanged copies use this form.
  Revision values are storage-state identifiers, not content hashes. Azure-to-local
  unchanged-payload materialization is unsupported and fails before admission, with
  no download fallback.
- `uploaded_content`: the entry binds a SHA-256 computed during generation/upload,
  the destination revision and upload-operation evidence. Verification checks
  storage properties without downloading the generated payload again. This is
  not independent destination readback. Frozen input, the execution plan and lineage
  require genuine content hashes and bounded byte verification even when uploaded.

Frozen Azure sources declare revision-bound identities without content hashes.
A separate fingerprint binds the credential-free source container and object path;
copy effect identity is never stored in a content-hash field. Local sources retain
their byte hashes. Later retained metadata or media reads
are conditioned on the destination revision admitted by the inventory and reject
same-length replacement before returning bytes.

The inventory does not list itself or other control artifacts. The manifest binds
the inventory, frozen input, preview and provenance mappings, but never itself.
Receipt and report bind the manifest digest. The final marker binds manifest,
inventory, receipt and report digests.

Candidate verification checks canonical controls, each entry's declared assurance,
references, output paths and bounded lineage before publication. Public verification
additionally requires the matching receipt, report and marker. Extra, missing,
revision-changed or incomplete-copy objects prevent successful verification and
published access. Revision-bound evidence trusts Azure's revision and copy semantics;
it does not detect byte corruption that leaves those storage properties unchanged.
Receipts and readable reports show separate content-digest, storage-revision and
uploaded-content counts.

Pass an independently obtained expected manifest SHA-256 to verify the intended
package. Without it, verification establishes internal consistency, not protection
against replacement of the whole package. Hashes do not authenticate a producer,
certify training quality or prove the truth of recording dates or provenance.
Preserve partial/unknown origin explicitly. Release producer/time describes release
creation, not an inferred recorder identity or recording date.

### Evidence and Operational Records

Compact control JSON and streamed domain-provenance JSONL serve different purposes.
Manifest-declared lineage streams are inventoried, versioned and digest/count/order
bound. They preserve source-to-output mapping without materializing the full stream
inside a compact control document.

Mutable job records, runtime proofs, scratch and live operational JSONL remain
outside the immutable package. Operational events derive from durable job state;
events after publication must not be appended to sealed evidence. Application JSONL
is not OTLP or proof of distributed tracing. Diagnostic categories/status and
seconds/bytes measurements must not expose raw authors, tokens, private paths or
provider exceptions. OpenTelemetry export is a separate integration.

## Publication, Jobs and Recovery

Payload and control writes are create-only and durably recorded before dispatch.
Existing unowned output is not adopted. Copies bind source revisions and expected
bytes; configured bearer-authorized Blob copies request synchronous service copy.
Pending or failed copies block verification. Successful individual copies do not
constitute publication.

Local job storage uses atomically replaced, synchronized `jobs/<job_id>.json`
records and filesystem locking. Blob job storage uses conditional records, a
renewable coordination lease and recovery of pending record writes. Idempotency,
reservations and indexes are reconstructed from authoritative records, not an
independent database or cache. Before output effects, the worker persists the captured source binding, execution plan
and reservation under its fence. Recovery reuses that binding and never recaptures a
different source for work already started. Job state must remain available across restarts.

| State or transition         | Required behavior                                                                  |
|-----------------------------|------------------------------------------------------------------------------------|
| Accepted to running         | Restore accepted bindings and claim a live fenced lease before effects             |
| Running                     | Register writers and effect intents; enforce admitted source/output/scratch bounds |
| Sealed                      | Close payload effects and retain exact receipt/report identities                   |
| Committing                  | Record the irrevocable manifest-bound decision; serialize it against cancellation  |
| Committed                   | Write the marker last and independently verify publication with stopped writers    |
| Cancelling                  | Stop and reconcile effects, then remove only owned unpublished residue             |
| Cancelled                   | Confirm stopped effects, completed cleanup and released capacity separately        |
| Failed or uncertain cleanup | Retain capacity and unresolved obligations; require reconciliation                 |

Cancellation is too late after the irrevocable commit decision. Recovery must
complete or verify that exact publication, not choose a different manifest.
Before that decision, cancellation requires independent stopped-writer and absence
checks. Foreign ownership, unjournaled residue or a published marker prevent
unpublished-output cleanup. Lost copy/delete responses require reconciliation;
uncertain work must not be blindly dispatched again.

Azure copies, generated uploads and cleanup use bounded supervised batches.
`CURATION_RELEASE_BLOB_BATCH_SIZE` defaults to 16 objects and accepts 1 through 64.
Each object retains its own journaled identity and idempotent byte accounting.
Recovery reconciles partial batches before dispatching new work; matching completed
objects are reused. The publication marker remains independently arbitrated.
Cleanup uses conditional batch deletion and checks every response. It removes the
ownership companion only after the payload root is empty, then removes the pending
source association and registered runtime proofs. Successful cancellation removes
the private job record, not shared coordinator state or unrelated files.

Failed cancellation exposes Retry cleanup. Explicit recovery clears retry backoff
without replacing a live lease. Automatic retries respect backoff after writers
have stopped. A definitive job-not-found response clears only the matching scoped
browser pointer and history entry; it does not prove cleanup succeeded or submit a
replacement job. A removed job's idempotency key is no longer retained.

The Linux supervisor uses pidfds/procfs and process start/boot identity. A reused
PID is not stopped-writer proof. Private scratch cleanup requires matching ownership
and stopped children; recovery also requires creator-exit evidence. Missing proofs,
replaced directories or exhausted cleanup budgets retain their charges. Workers
must not revive an expired fence or start replacement effects while an earlier
intent remains unresolved.

Job status separates lifecycle state, effects stopped, cleanup state and capacity
state. There is no automatic job/event TTL or reservation expiry. Reads do not
prune records. Keep active obligations and retention requirements reconciled before
operator cleanup; incompatible old execution bindings are not automatically migrated.

## Retained Browsing and Authorization

Private source associations bind existing source/principal scope, destination,
attempt and final manifest. The association is retained independently of job rows
and original source bytes. It is not a permission grant or publication marker.
Conflicting/finalized associations are not replaced or silently adopted.

The viewer lists releases beneath their source, not in the ordinary editable
dataset catalog. Retained metadata, episodes and media are read-only and bind each
read to the accepted inventory membership, length and hash. Removing the marker
or changing inventoried bytes refuses access. Retained output survives source/job
removal; job-scoped controls still require retained job records.

Mutations preserve authentication, CSRF and existing source/principal scope.
Clients receive configured aliases and opaque handles, not unchecked operator paths.
Direct source-write routes must also reject recorded release roots and descendants;
catalog exclusion alone is insufficient protection.

## Capacity and Accounting

Preview estimates are advisory and may be unknown. Acceptance reserves a conservative
bound, and execution enforces
hard bounds. Local destination/scratch filesystems share reservations only when
they are the same filesystem. Affected-media scratch accounts for the required
source spool, accepted encoder output budget and protocol files, not a second
complete dataset copy.

Blob payload admission uses an account-wide application ceiling across workers
sharing the same authority, including retained output. It does not measure Azure
free space or quota. Local scratch remains independently constrained.

| Category                              | Meaning                                                                     |
|---------------------------------------|-----------------------------------------------------------------------------|
| Local exact-copy bytes                | Independent local output, not remote network transfer                       |
| Storage-copy bytes                    | Acknowledged service-side copies, distinct from generated uploads           |
| Source/destination verification reads | Separate reads, including independent destination readback                  |
| Affected-media downloads              | Source bytes needed for processing                                          |
| Generated uploads                     | Acknowledged generated output sent to Blob                                  |
| Scratch/destination usage             | Reserved bounds, observed current usage and historical peak remain distinct |

Uncertain/partial transfers are not acknowledged-byte measurements. Uncertain
effects retain capacity. Cleanup can reduce current usage, not the historical
peak. Costs depend on storage transactions, retention, network path and processing;
copy-first does not eliminate verification traffic or establish a fixed saving.

## API Contract

Requests reject unknown fields. Submission returns `202` with `Location` and
`Retry-After: 5`; clients reconnect through scoped job and accepted-request reads.
The route contracts below complement generated OpenAPI and canonical models,
which remain authoritative for field schemas.

| Method and path                                                     | Purpose                                                                |
|---------------------------------------------------------------------|------------------------------------------------------------------------|
| `GET /api/releases/v1/capabilities`                                 | Schema and supported features                                          |
| `GET /api/releases/v1/destinations`                                 | Configured destination aliases                                         |
| `POST /api/releases/v1/preview`                                     | Read-only body preview                                                 |
| `GET /api/releases/v1/preview`                                      | Read-only query preview                                                |
| `POST /api/releases/v1/jobs`                                        | Submit with `Idempotency-Key`                                          |
| `GET /api/releases/v1/requests`                                     | Scoped accepted-request lookup                                         |
| `GET /api/releases/v1/jobs`, `GET /api/releases/v1/jobs/{job_id}`   | List and read job status                                               |
| `POST /api/releases/v1/jobs/{job_id}/cancel`, `.../recover`         | Request cancellation or recovery (`202`)                               |
| `GET /api/releases/v1/jobs/{job_id}/result`                         | Verified committed result; otherwise `409 release_result_not_verified` |
| `GET /api/releases/v1/jobs/{job_id}/artifacts/{artifact}`           | Aliases `manifest`, `inventory`, `receipt`, `report`                   |
| `GET /api/releases/v1/inventory/{dataset_id}`                       | Source inventory and saved-evidence freshness                          |
| `GET /api/releases/v1/sources/{dataset_id}/releases`                | Source-associated verified releases                                    |
| `GET .../sources/{dataset_id}/releases/{handle}`                    | One retained release                                                   |
| `GET .../releases/{handle}/episodes`                                | Paged retained episodes                                                |
| `GET .../releases/{handle}/episodes/{episode_index}/media/{camera}` | Inventoried episode video; `Cache-Control: no-store`                   |

## Integration and Related Documentation

Dataset-I/O owns native interpretation; curation owns freezing, planning,
verification and job/publication policy; viewer adapters own authenticated
composition and transport. Optional consumers use a verified manifest identity,
verifier result, committed result/artifact API or retained read-only handle.
Azure ML registration and training/model lineage belong to the separate
[integration work](https://github.com/microsoft/physical-ai-toolchain/issues/2123),
not a core library dependency.

- [Curation library, CLI and operations](../curation/README.md)
- [Viewer release workflow](../viewer/README.md#-curated-release)
- [Release contract models](../curation/physical_ai_curation/release/models.py)
- [Verification implementation](../curation/physical_ai_curation/release/verification.py)
- [Runtime configuration](../viewer/backend/.env.example)
