---
title: VLM Judge Validation on an Azure A10 VM
description: Validate the pinned Qwen runtime and durable judge lifecycle on an operator-managed Azure A10 VM.
author: Microsoft Robotics-AI Team
ms.date: 2026-10-08
ms.topic: how-to
---

Use this guide to validate Qwen inference on an existing Azure A10 VM after transferring the branch. No Qwen execution has been performed in the development container. Model-free tests do not establish GPU compatibility, model quality, browser accessibility, or live Azure Blob ownership.

## Implementation Status

This is an incremental validation handoff, not a release acceptance record.

* Available: durable single judging, sample evaluation and explicit approval APIs, conditional result application, and judge CLI submit/status/list/cancel/retry/results/approve/approvals/worker operations.
* In progress: migration of the task-label CLI to the shared lifecycle. Do not treat its existing direct execution path as approval-gated.
* Pending: dataset catalog, withdrawal workflow, dataset workspace and unified Episode Analysis. Do not claim these workflows passed from lifecycle unit tests.
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

## Evidence and Agent Handoff

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
Do not implement pending features or claim the dataset workspace, withdrawal
or Episode Analysis acceptance passed until those interfaces are delivered.
Return actionable failures and sanitized evidence; do not load secrets into chat.
```

Browser keyboard/focus, screen-reader announcements, mobile layout and rendered-media timing require separate runtime checks. Local storage tests and in-memory Blob contracts do not establish live Blob lease or reset behavior. Keep those results separate from GPU validation.

This guide was prepared with AI assistance and requires operator review before execution.
