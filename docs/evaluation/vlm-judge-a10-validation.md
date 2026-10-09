---
title: VLM Judge Validation on an Azure A10 VM
description: Validate the pinned Qwen runtime and durable judge lifecycle on an operator-managed Azure A10 VM.
author: Microsoft Robotics-AI Team
ms.date: 2026-10-09
ms.topic: how-to
---

Use this guide to validate Qwen inference on an existing Azure A10 VM after transferring the branch. No Qwen execution has been performed in the development container. Model-free tests do not establish GPU compatibility, model quality, browser accessibility, or live Azure Blob ownership.

## Implementation Status

This is an incremental validation handoff, not a release acceptance record.

* Available: durable single judging, sample evaluation and explicit approval APIs, conditional result application, and judge CLI submit/status/list/cancel/retry/results/approve/approvals/worker operations.
* Available: the task-label CLI uses the same durable lifecycle and conditional contribution application. `--resume --job-id <id>` retries failed targets; JSONL exports are not resume authority. Its task findings remain distinct from overall judge outcomes.
* Available: bounded dataset catalog search, group/sort/page controls, explicit refresh and deliberate dataset selection. Read-only Azure catalog requests passed locally; browser accessibility and rendered-layout acceptance remain outstanding.
* Implemented, awaiting final acceptance: revision-bound withdrawal API/CLI/UI, dataset sample/target workspace, approval and durable job controls, and unified Episode Analysis with canonical run history. Model-free checks do not establish browser or GPU acceptance.
* Deferred: all Qwen execution and target-GPU compatibility checks. Final browser workflow and accessibility acceptance remain outstanding.

## Prerequisites

Use an operator-managed VM with an NVIDIA A10 and a driver compatible with the CUDA build selected by the checked-in lockfile. Do not provision infrastructure or change an existing service to run this checklist.

1. The user commits and pushes the intended changes, then retrieves that exact commit on the VM. Record `git rev-parse HEAD` and `git status --short`; uncommitted local implementation will not transfer through a branch push.
2. Use a disposable copy of a permitted dataset with actual episode IDs, supported video files and saved human annotations. Do not mutate production datasets.
3. Select an immutable Qwen model commit. Keep model access tokens in the approved credential mechanism, never command arguments, reports or source files.
4. Choose dedicated job, cache and output directories. All competing workers must share the same durable job root, capacity scope and capacity setting.
5. Obtain operator approval before restoring packages, downloading weights, starting workers, or changing services on the VM. Do not install anything as an automatic response to a failed check.

The evaluator's [manifest](../../evaluation/vlm_judge/pyproject.toml) and [lockfile](../../evaluation/vlm_judge/uv.lock) are authoritative. Pydantic is a core dependency for saved inputs and curation. Restore the existing API and Qwen extras to exercise both HTTP and CLI surfaces without updating dependencies:

```bash
uv sync --project evaluation/vlm_judge --frozen --extra api --extra qwen3-vl
```

Use canonical public feeds and the checked-in explicit PyTorch index. Stop on unavailable public versions or incompatible wheels; do not regenerate the lockfile through a private mirror or hand-edit it.

## Delivery Profiles

Build the backend from the repository root, not the backend directory. The
[Dockerfile](../../data-management/viewer/backend/Dockerfile) installs both packages
without editable links and excludes checkout source from the runtime image. Its upload allowlist
excludes datasets, local environments and credentials. Building requires approved image
downloads and public package access; the development container has not completed an image build.

| Profile                     | `BACKEND_EXTRAS`             | `JUDGE_EXTRAS` | Runtime selection                                                                |
|-----------------------------|------------------------------|----------------|----------------------------------------------------------------------------------|
| Disabled or model-free echo | `azure,analysis,export,auth` | Empty          | Disabled by default; enable with `VLM_JUDGE_BACKEND=echo` for disposable tests   |
| Remote inference            | `azure,analysis,export,auth` | `openai`       | Enable with `VLM_JUDGE_BACKEND=openai` and operator-managed endpoint credentials |
| Local Qwen validation       | `azure,analysis,export,auth` | `qwen3-vl`     | Enable with `VLM_JUDGE_BACKEND=qwen3-vl` on the approved GPU runtime             |

Do not include the backend's `vlm-judge` extra: its Transformers pin differs from the
evaluator profile. Add `yolo` only when detection is needed. These are explicit build
profiles, not claims of target compatibility; require the image's dependency check and
the GPU checks below before accepting a Qwen image. Local and Azure dataset storage are
independent of the inference profile. Remote inference sends selected frames and saved
instructions to the configured endpoint; approve that transfer before enabling it.

After approval, build the echo profile and check installed imports outside the checkout:

```bash
docker build -f data-management/viewer/backend/Dockerfile \
  --build-arg BACKEND_EXTRAS=azure,analysis,export,auth \
  --build-arg JUDGE_EXTRAS= -t dataviewer-validation .
docker run --rm --workdir /tmp -e VLM_JUDGE_ENABLED=false \
  -e VLM_JUDGE_BACKEND=echo dataviewer-validation python -c \
  'import src.api.main, evaluation.vlm_judge.api, evaluation.vlm_judge.jobs; print("Installed imports passed")'
```

Then run the single-episode checklist with `--backend echo` against an approved disposable
dataset mounted into that image and durable job/output volumes. Import success alone is
not the required installed-image echo execution check. No GPU or model download is needed
for echo; its results are test fixtures, not model-quality evidence.

The [Compose configuration](../../data-management/viewer/docker-compose.yml) separates
source data at `/data`, durable local jobs at `/state/judge`, and disposable cache/model
scratch at `/scratch`. Confirm volume ownership for the configured UID/GID. Do not use
scratch as job authority. Azure storage selects the shared Blob job adapter in the
annotation container; replicas must share storage, capacity scope and capacity settings.

The local [launcher](../../data-management/viewer/start.sh) uses frozen backend and
evaluator profiles in the backend environment. An enabled Qwen or remote profile may
need an approved restore; echo does not require either inference extra. Never approve
an automatic lockfile update or replacement of the existing environment configuration.

Terraform bootstraps a disabled echo judge and scratch path. Its Container App template
is ignored after creation. The deployment script's image rollout does not automatically
reconcile all judge settings: the operator must explicitly review and roll out
`VLM_JUDGE_ENABLED`, backend, immutable model revision, capacity settings, cache location
and, for remote inference, base URL and an API-key secret reference. Use the platform's
secret mechanism, not plaintext Terraform values, tracked files or chat. No deployment
or environment mutation is part of this validation handoff.

## Runtime Check

Run from the repository root after the approved restore:

```bash
nvidia-smi
uv run --project evaluation/vlm_judge --frozen --no-sync --extra api --extra qwen3-vl python -c \
  'import sys, av, numpy, torch, torchvision, transformers; print("Python", sys.version); print("NumPy", numpy.__version__); print("Torch", torch.__version__); print("CUDA", torch.version.cuda); print("cuDNN", torch.backends.cudnn.version()); print("torchvision", torchvision.__version__); print("transformers", transformers.__version__); print("PyAV", av.__version__); assert torch.cuda.is_available(); print("GPU", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0)); print("BF16", torch.cuda.is_bf16_supported()); print((torch.ones((16,16), device="cuda") @ torch.ones((16,16), device="cuda")).sum().item())'
```

Record driver, Python, NumPy, Torch, CUDA, cuDNN, torchvision, transformers and PyAV versions. Verify optional native extensions against this exact runtime if enabled. A successful import or matrix operation is not proof that model loading, video decoding or generation works.

## Single Episode Smoke Test

Set these values locally; do not commit environment-specific paths or identifiers:

```bash
export VALIDATION_DATASET='<disposable-dataset-directory>'
export DATASET_ID='<canonical-dataset-id>'
export EPISODE_ID='<actual-episode-index>'
export VALIDATION_OUTPUT='<dedicated-output-directory>'
export MODEL_REVISION='<immutable-model-commit-sha>'
```

Define a shell function so status, retry and execution use identical settings:

```bash
judge_cli() {
  uv run --project evaluation/vlm_judge --frozen --no-sync --extra api --extra qwen3-vl \
    python -m evaluation.vlm_judge.run \
    --dataset "$VALIDATION_DATASET" --dataset-id "$DATASET_ID" \
    --backend qwen3-vl --model-id Qwen/Qwen3-VL-4B-Instruct \
    --model-revision "$MODEL_REVISION" --n-frames 4 --frame-size 448 \
    --job-dir "$VALIDATION_OUTPUT/jobs" --cache-dir "$VALIDATION_OUTPUT/cache" \
    --output "$VALIDATION_OUTPUT/results.jsonl" --capacity 1 \
    --capacity-scope a10-validation "$@"
}
judge_cli --single --indices "$EPISODE_ID" --detach --request-id a10-single-001
```

The response must contain a durable job ID and queued state without model execution. Set `JOB_ID` to the returned ID, then inspect and resume that same request:

```bash
judge_cli --operation status --job-id "$JOB_ID"
judge_cli --single --indices "$EPISODE_ID" --request-id a10-single-001
judge_cli --operation results --episode-index "$EPISODE_ID"
```

Confirm the same job ID, terminal status, canonical episode ID, saved instruction, selected camera windows, model identity and result reference. A predicted task failure is a valid completed invocation. Execution errors and partial results must return nonzero; cancellation returns 130. A detached submission succeeds only as an acknowledgment, not as proof of inference success.

Repeat with explicitly selected cameras using `--views`. Use a new request ID when changing the submitted payload. Test another actual episode, including a sparse ID when available. Never infer episode IDs from a count.

## Lifecycle and Approval Checks

Use disposable data and record expected versus observed outcomes:

* Submit the same request ID and identical payload twice; both responses identify the same job. A changed payload with that ID must fail.
* Submit a new request with identical saved inputs; verify saved evidence reuse. Use `--force` with another request ID to recompute while preserving execution identity.
* Cancel during real inference from another terminal. Status must remain responsive, late results must not publish, and the occupied device slot must not become available before the inference finishes.
* Stop a worker, preserve its job directory, then restart with `--operation worker` and the same configuration. After lease expiration, pending work must recover without duplicate contribution application. Repeated physical inference after a crash is possible.
* Run two workers against the same job root and capacity scope with capacity one. Observe bounded concurrent inference across workers, not one slot per process.
* Change a saved instruction or source on the disposable dataset. Old evidence becomes stale; queued work bound to the old snapshot must fail rather than silently use new inputs.
* Evaluate nonempty sample IDs with their own explicit saved human author, annotation revision and snapshot ID. Submit a JSON reference file using `--mode sample --sample-references <file>`. Unknown/legacy ownership and accepted machine output are not human ground truth.
* Approve the completed sample job with `--operation approve --job-id <sample-job-id>`. Errors or incomplete evaluation block approval; disagreement or inconclusive evidence requires deliberate `--acknowledge-exceptions`.
* Submit targets separately with `--approval-id <approval-id>`. Configuration/source/sample changes must reject stale approval; every target retains its own saved instruction. Agreement is calibration evidence, not training or demonstrated generalization.
* Judge-only work must not change labels. On the disposable dataset, test explicit application or approved `judge-and-label`, retry after interruption, immutable run/result linkage, independent human edits and separate judged/applied counts.

Use the [common API](../../evaluation/vlm_judge/api.py) for authenticated or viewer-managed runs. The viewer mounts lifecycle routes under `/api/judge`; standalone routes are mounted by the operator. Preserve existing principal and CSRF requirements. Do not substitute an unauthenticated local actor for failed authentication.

## Model-Free Browser Checks

The isolated runner uses temporary synthetic data and dedicated ports 4173 and
18000 through 18002. It never reuses existing servers or resolves Python dependencies.
The installed backend development environment and Chrome are prerequisites; obtain
approval for missing prerequisites instead of installing them automatically.

```bash
cd data-management/viewer/frontend
npm run test:a11y -- --grep 'P06 real backend'
npm run test:a11y -- --grep 'P06 fresh browser'
npm run test:a11y
```

The first request-only case runs without Chrome and verifies real HTTP label persistence,
stale-write rejection and explicit clearing. The second uses separate browser contexts to
verify label persistence without shared browser drafts. The complete suite also covers
keyboard, adaptive layout and API documentation; collection is not execution evidence.
These cases do not replace complete edit-descriptor recovery, unmounted dirty-selection
readiness, real-process recovery or assistive-technology checks.

Save persists supported episode resources and stays on the episode. Next is a separate
navigation action, not a save. Browser drafts are recovery copies, not saved judge input.
Review saved inputs before judging; merely viewing Episode Analysis must not run inference
or apply labels. Explicit application is a separate conditional operation. Saved edit
descriptors are not rendered videos: unsupported transformed judge inputs must fail, and
export behavior must be verified for each supported operation rather than assumed.

## Evidence and Agent Handoff

### Withdrawal and Workspace Checks

On disposable data only, inspect and confirm a bound withdrawal preview:

```bash
judge_cli --operation reset-preview
export PREVIEW_ID='<returned-preview-id>'
judge_cli --operation reset-confirm --preview-id "$PREVIEW_ID"
judge_cli --operation reset-status
judge_cli --operation reset-retry
```

Use retry only after partial completion. A conflict requires a fresh preview after reviewing the changed saved fields. Verify removable fields, accepted-but-unchanged removals, preserved human revisions, legacy unknowns and conflicts. Start a reset while a disposable job is running; its late completion must not republish withdrawn output. Repeat confirmation of the same preview and verify idempotency. Preserve independent motion analysis and human validation evidence.

Open the viewer through the existing approved `start.sh` configuration. Verify catalog selection, dataset disclosure, separate sample/target IDs, explicit saved-author references, camera/method configuration, sample disagreement acknowledgment, approval staleness, durable progress and reconnect after reload.

Confirm that closing the disclosure preserves the selected episode, filters, camera state, frame and unsaved drafts without automatic playback. Test keyboard focus, hidden controls, live announcements and narrow layouts independently of model accuracy.

In Episode Analysis, verify judge-only results without label changes, explicit conditional application, distinct motion/task findings and marked withdrawn history. A transient refresh failure must retain prior evidence with retry; access loss or source/principal changes must not expose unrelated cached evidence. Refreshing results must not clear dirty drafts or reapply old machine contributions.

### Report Format

Keep results in an operator-approved location outside tracked source. Record commit, runtime versions, model revision, synthetic or permitted dataset description, selected IDs/views, run/result references, exit codes, timings, peak device memory and sanitized error categories. Do not publish credentials, service endpoints, raw model responses, private annotations or environment paths.

Give a VM agent this bounded instruction:

```text
Validate the checked-out commit using docs/evaluation/vlm-judge-a10-validation.md.
First report commit, dirty state, GPU/runtime versions and prerequisite gaps.
Do not provision infrastructure, change services, install packages, download
weights, mutate existing datasets, commit or push without operator approval.
Use only approved disposable data and dedicated job/cache/output directories.
Run implemented Qwen/lifecycle checks and preserve exact exit codes and evidence.
Do not bypass assertions, coverage, pinned dependencies or public-feed policy.
Report Passed, Failed, Deferred or Unavailable for each check. Distinguish
model quality, GPU compatibility, storage contracts and browser accessibility.
Do not implement new features. Validate the delivered dataset workspace,
withdrawal and Episode Analysis separately from model inference, and record
unavailable browser or runtime checks without claiming acceptance.
Return actionable failures and sanitized evidence; do not load secrets into chat.
```

Browser keyboard/focus, screen-reader announcements, mobile layout and rendered-media timing require separate runtime checks. A live disposable Blob probe has verified persisted jobs, competing claims, cancellation fencing, retry and evidence retrieval, with exact cleanup. It does not establish lease-renewal/expiry, real-process Azure recovery or dataset reset behavior. Keep those results separate from GPU validation.

This guide was prepared with AI assistance and requires operator review before execution.
