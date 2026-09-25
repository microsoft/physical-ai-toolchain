---
sidebar_position: 7
title: VLA Full-Run Troubleshooting
description: Failure chronology and recovery guidance for scaling PI 0.5 from an Azure ML smoke test to a full training run
author: Microsoft Robotics-AI Team
ms.date: 2026-09-17
ms.topic: troubleshooting
keywords:
  - vla
  - pi05
  - lerobot
  - azureml
  - troubleshooting
  - cuda
  - mlflow
---

Use this guide when a PI 0.5 smoke test succeeds but a full Azure ML training run fails during model delivery, policy initialization, feature mapping, GPU execution, or metric reporting. It records the observed failure sequence, unsuccessful mitigations, permanent fixes, and the conservative configuration validated on a single RTX PRO 6000 GPU.

For host preparation, Arc and Azure ML compute attachment, job submission, and monitoring, see [Azure ML Arc VLA Setup and Operations](vla-azureml-arc-setup.md).

## Scope

The full-run investigation used:

| Setting          | Value                                                        |
|------------------|--------------------------------------------------------------|
| Policy           | LeRobot PI 0.5                                               |
| Policy revision  | `lerobot/pi05_base@b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba` |
| Dataset source   | Versioned Azure ML data asset                                |
| Compute          | Azure ML on Arc-connected Kubernetes                         |
| GPU              | Single RTX PRO 6000 with approximately 95 GiB usable memory  |
| Precision target | BF16                                                         |
| Fine-tuning mode | Expert-only                                                  |
| Training target  | 40,000 optimizer steps                                       |

Do not copy deployed resource names, workspace identifiers, private endpoints, or credentials into job definitions. Resolve Azure context from Terraform outputs or local ignored configuration.

## Failure and Fix Summary

| Stage                   | Failure                                             | Root cause                                                                                               | Resolution                                                                                                                                                   |
|-------------------------|-----------------------------------------------------|----------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Model import            | Large model output upload reset or stalled          | A single 14.5 GB Azure ML output transfer was unreliable over the available network path                 | Added chunked transport with SHA-256 verification, then removed model import from the critical path by downloading a pinned snapshot inside the training pod |
| Single-GPU launch       | Accelerate rejected `--multi_gpu --num_processes=1` | `--multi_gpu` requires more than one process                                                             | Add `--multi_gpu` only when more than one GPU is visible                                                                                                     |
| Code upload             | Azure ML returned `AuthorizationFailure`            | The workstation could not route to the workspace storage private endpoint                                | Connect the workstation to the Azure point-to-site VPN; do not weaken storage firewall, shared-key, or public-network settings                               |
| Dependency setup        | Pinning `click<8.4` failed resolution               | Hugging Face Hub required a newer Click version                                                          | Treat the `spin` Click message as a nonfatal resolver warning and remove the incompatible pin                                                                |
| Policy features         | PI 0.5 rejected the dataset image schema            | The policy expected three canonical camera names while the dataset supplied two device-specific names    | Map the two physical cameras and allow LeRobot to mask and pad the absent third camera                                                                       |
| Rename-map transport    | All image features were missing                     | Azure CLI `--set` stripped quotes from raw JSON across nested shell and job serialization layers         | Validate the JSON, encode it as base64 during submission, and decode it in the checked-in entrypoint                                                         |
| Policy loading          | LeRobot could continue without pretrained weights   | A load error could return an initialized model instead of failing the job                                | Detect the pretrained-load failure message and terminate the training process group                                                                          |
| Public policy bootstrap | PaliGemma tokenizer returned HTTP 401               | The public PI 0.5 policy processor still references a gated tokenizer repository                         | Require an authorized Hugging Face token for PI policy initialization                                                                                        |
| Batch 128               | First forward pass raised CUDA OOM                  | The process used approximately 91.5 GiB and needed another 7.56 GiB                                      | Reduce memory demand; allocator tuning alone was not sufficient                                                                                              |
| Batch 64                | First forward pass raised CUDA OOM                  | The process used approximately 92 GiB and needed another 3.78 GiB                                        | Instantiate policy storage as BF16, enable gradient checkpointing, and use batch 16 for the unattended run                                                   |
| MLflow chart            | Metrics collapsed onto 1,000-step boundaries        | LeRobot formatted steps as `1K`, `2K`, and `3K`; the wrapper reused those rounded values as MLflow steps | Maintain an exact internal step counter using the configured log frequency                                                                                   |

## Model Delivery Failures

### Large Azure ML model output was unreliable

The initial design imported the Hugging Face checkpoint through an Azure ML component and passed the resulting workspace model asset to training. The model contains a `model.safetensors` file of approximately 14.5 GB. Upload sessions for that output reset or failed after the download completed.

The first mitigation split `model.safetensors` into 1 GiB parts before Azure ML output upload. The training entrypoint reconstructed the file and verified its SHA-256 digest before loading it. This improved transport integrity but retained an intermediary upload as a critical dependency.

The final training path downloads the policy snapshot directly inside the training pod:

1. Require a full 40-character lowercase Git revision.
2. Install the frozen LeRobot environment.
3. Call Hugging Face `snapshot_download()` with the pinned revision.
4. Verify that `config.json` exists.
5. Verify that `model.safetensors` exists and contains tensor keys.
6. Pass the local snapshot directory to LeRobot as `policy.path`.

This path avoids an Azure ML model round trip while preserving immutable model selection.

### Failed pods repeated the full download

Pod-local storage is ephemeral. Every failed attempt downloaded the 14.5 GB snapshot again, adding approximately ten minutes before the next diagnostic signal.

This remains an optimization opportunity. A durable cache must preserve revision identity and file-integrity validation. Do not reuse an unverified mutable directory.

## Submission and Connectivity Failures

### Single-GPU Accelerate launch was invalid

The original launch command combined:

```text
accelerate launch --multi_gpu --num_processes=1
```

Accelerate rejects multi-GPU mode with one process. The launcher now always adds `--num_processes=<count>` and adds `--multi_gpu` only when the detected count is greater than one.

The valid single-GPU BF16 form is:

```text
accelerate launch --num_processes=1 --mixed_precision=bf16
```

### Workspace Blob upload returned AuthorizationFailure

The signed-in user already had the required Blob data role. The failure occurred because workspace storage disabled public network access and the local WSL environment could not reach its private endpoint.

Connect the Windows host using the existing Azure VPN Client point-to-site profile. After connection:

- WSL DNS must resolve the Blob hostname to the private address.
- TLS must reach the private endpoint.
- Entra-authenticated Blob list, write, and delete probes must succeed.
- Azure ML code upload must complete without changing storage security settings.

Do not enable public network access, shared-key authentication, or broad firewall exceptions to resolve this condition.

### Reusing an immutable code asset caused runtime drift

While local code upload was blocked, emergency runs reused an older immutable code asset and applied corrections with inline `sed` commands. This allowed fast experiments but created several nested quoting layers:

1. Local Bash
2. Submission script argument parsing
3. Azure CLI `--set`
4. Azure ML command serialization
5. Remote Bash
6. Runtime source editing
7. Training argument parsing

The permanent path uploads the committed source and invokes:

```bash
bash il/scripts/lerobot/azureml-train-entry.sh
```

Do not use runtime source edits for repeatable training.

## Dependency Resolution Failure

The runtime emitted a resolver warning because `spin` constrained Click below 8.4 while the selected Hugging Face Hub package required a newer version. The warning did not prevent snapshot download.

Pinning `click<8.4` converted the warning into a real dependency-resolution failure. Remove that pin and preserve the frozen VLA dependency lock. Diagnose whether a resolver message is fatal from the process exit and subsequent imports, not from the word `ERROR` alone.

## Camera Feature Mapping Failures

The PI 0.5 checkpoint expects:

```text
observation.images.base_0_rgb
observation.images.left_wrist_0_rgb
observation.images.right_wrist_0_rgb
```

The UR10e dataset supplied:

```text
observation.images.d435
observation.images.d405
```

The proof-of-concept mapping is:

```json
{
  "observation.images.d435": "observation.images.base_0_rgb",
  "observation.images.d405": "observation.images.left_wrist_0_rgb"
}
```

LeRobot masks and pads the absent `right_wrist_0_rgb` input. Confirm the physical camera placement before treating this mapping as production metadata.

### Raw JSON did not survive Azure CLI overrides

Passing the JSON directly through Azure CLI `--set` removed embedded quotes. LeRobot then saw no valid image mapping and failed with:

```text
ValueError: All image features are missing from the batch
```

The submission script now:

1. Validates a string-to-string JSON object.
2. Encodes the object as base64.
3. Sends the encoded value through the Azure ML environment.
4. Decodes it in the entrypoint.
5. Constructs the final `--rename_map=<json>` argument as one array element.

Use `--config-preview` to inspect the decoded mapping before submission.

## Policy Authentication and Loading Failures

### A public policy still required authentication

The `lerobot/pi05_base` snapshot is public, but its `policy_preprocessor.json` references:

```text
google/paligemma-3b-pt-224
```

The tokenizer repository is gated. An anonymous run downloaded and validated the PI 0.5 policy, then failed during processor construction with HTTP 401.

Accept the PaliGemma access conditions and supply a read token authorized for the repository. Keep the token in an ignored local environment file or a secret store. The current Azure ML submission path forwards it as `HF_TOKEN`; principals with job-read permissions may be able to inspect job environment metadata. Use a short-lived token and rotate it after the experiment.

### Pretrained-weight fallback required a fail-fast guard

LeRobot can emit a message indicating that it is returning a model without loaded pretrained weights. Continuing from that state would create a successful-looking but invalid experiment.

The MLflow wrapper now terminates the complete Accelerate process group when it detects:

```text
Returning model without loading pretrained weights
```

A valid initialization must include:

```text
Loaded state dict from model.safetensors
Remapped 812 state dict keys
All keys loaded successfully!
```

## GPU Memory Failures

### Batch 128 exceeded capacity

The first batch-128 forward pass failed before optimizer step 1:

```text
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 7.56 GiB.
```

At failure, the process used approximately 91.5 GiB of a 95 GiB GPU. Camera mapping, checkpoint loading, dataset creation, and optimizer creation had already succeeded.

### Expandable segments did not make batch 64 fit

`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` reduced allocator fragmentation but could not resolve true capacity pressure. Batch 64 still failed before step 1:

```text
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 3.78 GiB.
```

At failure, the process used approximately 92 GiB and had less than 3 GiB free. Do not treat allocator tuning as a substitute for reducing model, optimizer, or activation memory.

### Accelerate BF16 did not change policy storage dtype

The job launched with Accelerate BF16, but the PI 0.5 configuration still reported:

```text
dtype: float32
gradient_checkpointing: false
```

Accelerate mixed precision controls runtime operations. PI 0.5 separately controls model storage through `policy.dtype`.

The submission path now exposes:

| Option                     | Effect                                                       |
|----------------------------|--------------------------------------------------------------|
| `--mixed-precision bf16`   | Enables Accelerate BF16 operations                           |
| `--policy-dtype bfloat16`  | Instantiates PI policy storage in BF16                       |
| `--gradient-checkpointing` | Recomputes activations during backward to reduce memory      |
| `--train-expert-only`      | Freezes the VLM and trains the action expert and projections |

### Conservative configuration validated

The stable unattended run used batch 16, BF16 policy storage and operations, gradient checkpointing, expert-only training, expandable CUDA segments, 1,000-step checkpoints, and per-update metric logging. Use the complete command in [Azure ML Arc VLA Setup and Operations](vla-azureml-arc-setup.md#submit-pi-05-training) to reproduce this configuration.

Observed steady-state behavior:

| Metric             | Observation                                                         |
|--------------------|---------------------------------------------------------------------|
| GPU memory         | Approximately 16.34 GiB                                             |
| Update time        | Approximately 1.33 seconds                                          |
| Throughput         | Approximately 12 samples per second                                 |
| Stability evidence | More than 6,000 optimizer steps without OOM or non-finite gradients |
| Projected duration | Approximately 15 hours for 40,000 steps after training starts       |

Treat these values as an observed baseline, not a performance guarantee. Dataset size, camera count, GPU model, driver, runtime packages, and contention affect results.

## MLflow Step-Axis Failure

The run used `--log-freq 1`, but the Azure ML chart appeared to log only at steps 1,000, 2,000, and 3,000 after the first thousand updates.

LeRobot formats large counters for humans:

```text
step:999
step:1K
step:2K
step:3K
```

The wrapper parsed the formatted token and passed the rounded value to MLflow. Hundreds of metric records therefore shared the same x-axis step, producing vertical lines at thousand boundaries. Training and raw metric collection continued.

The wrapper now advances an exact internal counter by `LOG_FREQ` after formatted `K` tokens begin. This fix applies to newly submitted jobs because active Azure ML jobs execute an immutable uploaded code snapshot.

For an affected active run with `--log-freq 1`, use the number of `train/loss` metric records as the precise optimizer-step count. Reconstruct or backfill a separate exact-step metric after the run if the original chart must be retained.

## Diagnostic Sequence

Use this order to avoid repeating expensive downloads:

1. Run the submission script with `--config-preview`.
2. Confirm the workstation can reach the workspace Blob private endpoint.
3. Confirm the code asset uploads from committed source.
4. Confirm the policy repository and revision are immutable.
5. Confirm Hugging Face authentication can access the gated tokenizer.
6. Confirm the rename map decodes to the expected camera keys.
7. Confirm the snapshot integrity message appears.
8. Confirm all pretrained keys load successfully.
9. Confirm policy storage reports `dtype: bfloat16`.
10. Confirm gradient checkpointing is enabled when requested.
11. Confirm optimizer step 1 completes.
12. Observe at least five steps for stable memory, finite gradients, and consistent update time.
13. Confirm a checkpoint is uploaded at the configured save interval.

## Implementation Checkpoints

| Commit     | Change                                                                    |
|------------|---------------------------------------------------------------------------|
| `97e7223b` | Added datastore-backed pinned Hugging Face model import                   |
| `3879cf74` | Hardened model transport and enabled single-GPU BF16 launch               |
| `ebd62cbd` | Corrected single-GPU Accelerate behavior and added camera feature mapping |
| `4098ebd8` | Added direct pinned Hugging Face policy bootstrap and load validation     |
| `a990c20e` | Required authentication for gated PI policy processors                    |
| `c57cb620` | Added explicit PI policy dtype and gradient-checkpointing controls        |
| `8a0f366c` | Preserved exact MLflow optimizer-step numbers                             |

## Remaining Work

- Replace plain job-environment token forwarding with a secret-backed runtime mechanism.
- Add a reusable one-step preflight mode that validates model loading, camera mapping, one forward pass, backward, and optimizer update.
- Add revision-scoped durable model caching to avoid downloading 14.5 GB for every failed pod.
- Record exact peak GPU memory from the container or MLflow system metrics instead of relying only on LeRobot's reported metric.
- Confirm the D435 and D405 physical mounting roles before standardizing the camera map.
- Backfill exact-step metrics for affected historical runs when chart continuity is required.
