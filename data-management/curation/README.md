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
Azure SDK or web framework. These are library foundations, not a complete release
workflow. Release-specific encoding, publishers, durable release jobs, CLI/API
commands and viewer controls are not yet available.

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
same-account Blob-to-Blob are planned routes, not evidence that publishers exist.
Capacity estimates remain advisory until durable admission and runtime limits
are implemented. Call `load_media_limits` after application environment loading;
its overrides are listed in the [backend environment example](../viewer/backend/.env.example).

`execute_exact_copy` writes an independent, create-only local object through
descriptor-bound reads and writes, checking the planned byte length and digest.
Source and destination roots must be disjoint; symlinks and unsafe paths are
rejected. A successful object copy is not a release commit, durable capacity
reservation or proof of native LeRobot readability. Use `iter_subset_frames` for
streamed frame mappings; the materialized timeline helper admits its estimated
memory cost before allocation.

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
