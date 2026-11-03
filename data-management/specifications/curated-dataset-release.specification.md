---
title: Curated Dataset Release
description: Contracts for saved-selection freezing, immutable LeRobot publication, verification and recoverable release jobs.
ms.date: 2026-11-03
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

Selection preserves explicit episode order and selected saved resource revisions
and values. Unsaved browser drafts and unselected edits are not release inputs.
Frozen source identities and source-object references remain distinct from output
dataset identities, paths and frame coordinates.

Local admission requires a finalized, quiescent source and a caller acknowledgment.
Revision checks detect observed changes; they are not a general point-in-time
filesystem snapshot. Source and destination roots must be disjoint, and unsafe
paths or symlink traversal are refused. Blob reads bind ETag/version identity to
the frozen revision and use conditional requests plus content verification.

Selected annotations, labels, contribution ledgers and judge evidence retain
their source attribution and revisions. Judge evidence requires an explicitly
bound authority for the accepted principal and current generation. Invalid or
stale newest evidence is refused, not replaced with an older result. Local no-auth
composition uses `localuser`; hosted composition derives identity from existing
authentication and saved-resource scope. Releases introduce no role or grant policy.

## Preview and Admission

Preview freezes the selected inputs and computes object actions, processing
requirements, digests and estimated bytes. It creates no job, reserves no capacity
and writes no destination objects. Missing statistics, unsupported operations or
capacity blockers cannot be overridden by acknowledging processing.

Submission requires the matching preview and processing digests, applicable
acknowledgments and a scoped idempotency key. The service revalidates inputs and
the plan before acceptance. A changed selected saved revision returns
`409 stale_preview`. Known insufficient capacity returns `507`; an ambiguous
transport/server failure does not establish whether acceptance occurred.

Accepted-request lookup and replay use the original idempotency key before
reopening source data. Retrying an uncertain submission must not create another
logical job. Configured destination aliases resolve server-side before acceptance;
the accepted effective destination is retained across worker restart.

## Native Generation and Saved Projections

Unchanged video objects are independently copied without an encoder or decoder.
Affected media uses the confirmed processing plan. Supported saved operations are
frame removal and `invert`/`grayscale` color filters. Crop, resize, other color
adjustments, insertion, trajectory changes, subtask replacement and combined
global/camera transforms are refused during preview.

Generation preserves selected recorded columns, rebuilds native episode/frame
coordinates and task indices, and produces consistent video references and
statistics. Persistent language changes are retimed; events on removed frames
are omitted. Shared-object encoding follows selected output episode order.
Missing required media summaries, short media, timestamp gaps, incompatible rates
and insufficient processing memory are errors.

Count-weighted statistics retain sample counts; aggregated quantiles are labelled
approximate. Transformed-video summaries describe bounded RGB frame samples before
lossy encoding, not every frame or pixel. Source summaries may be preserved for a
full selection only when counts and independently generated moments agree.

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
    evidence/preview.json         # Frozen plan
    release/manifest.json         # Package bindings
    release/inventory.json        # Payload/evidence lengths and hashes
    release/receipt.json          # Verification receipt
    release/report.md             # Derived readable report
    release/marker.json           # Final publication boundary
  _releases/<release-name>.json    # Private attempt ownership
  _release_associations/           # Private source/principal associations
```

### Schema and Closure

Package control models use semantic `schema_version` values with supported major
version 1; current model and verifier versions are `1.0.0`. Unknown fields and
unsupported major versions are rejected. API and private worker bindings have
their own versions and are not aliases for the package schema.

The inventory binds normalized relative payload/evidence paths, lengths and SHA-256
digests; it does not list itself or other control artifacts. The manifest binds
the inventory, frozen input, preview and provenance mappings, but never itself.
Receipt and report bind the manifest digest. The final marker binds manifest,
inventory, receipt and report digests.

Candidate verification checks canonical controls, inventoried bytes, references,
output paths and bounded lineage before publication. Public verification additionally
requires the matching receipt, report and marker. Extra, missing, changed or
incomplete-copy objects prevent successful verification and published access.

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
independent database or cache. Job state must remain available across restarts.

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

Preview estimates are advisory. Acceptance reserves capacity and execution enforces
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

* [Curation library, CLI and operations](../curation/README.md)
* [Viewer release workflow](../viewer/README.md#-curated-release)
* [Release contract models](../curation/physical_ai_curation/release/models.py)
* [Verification implementation](../curation/physical_ai_curation/release/verification.py)
* [Runtime configuration](../viewer/backend/.env.example)
