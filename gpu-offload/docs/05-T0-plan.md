---
title: GPU Offload T0 Plan
description: Close the gap between the GPU-offload domain and the local T0 robot lifecycle
ms.date: 2026-08-28
ms.topic: roadmap
---

<!-- cspell:ignore Containerfile rmtconfigkube taskconfig unvalidated -->

Define the work required for `gpu-offload/` to support the repository's T0 target:
one laptop, one robot, zero cloud, and no required Kubernetes. The canonical T0
lifecycle remains in the [Tier 0 recipe](../../docs/recipes/tier-0-dev/README.md);
this plan covers plain-process and optional local-Kubernetes GPU-offload profiles for
the final run-on-robot stage.

## Current Answer

No GPU-offload guide currently delivers the complete T0 robot target.

| Existing guide or example                                                 | What it proves                                                                      | Remaining T0 gap                                                         |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [GPU Offload Local Guides](./README.md)                                   | Admission, server creation, discovery, transport, and remote execution              | Valid optional local-Kubernetes substrate, but no robot lifecycle        |
| [First Local Offload](./02-first-local-offload.md)                        | CPU, WSL2 GPU, and bare-metal GPU execution in generated server-stage pods          | Does not consume a trained robot policy or connect to ROS 2 hardware     |
| [ur10e-single example](../examples/ur10e-single/README.md)                | A real policy offloaded to a GPU stage, driving hardware through a closed loop      | Needs a UR10e, a 7 GB checkpoint on the node, and a Kubernetes cluster   |
| [SO-101 real-hardware example](../examples/so101-real-hardware/README.md) | Pinned LeRobot workflows for collection, fine-tuning, evaluation, and rollout       | Has not passed end-to-end validation on hardware with offloading enabled |
| [Tier 0 recipe](../../docs/recipes/tier-0-dev/README.md)                  | Capture, curate, train, validate, and manual inference without cloud infrastructure | Does not connect the final inference process to the GPU-offload runtime  |

T0 permits two local profiles:

| Profile          | Purpose                                                                                       | Requirement       |
|------------------|-----------------------------------------------------------------------------------------------|-------------------|
| Plain local      | Lowest-dependency default using processes or containers                                       | Required baseline |
| Local Kubernetes | Reuse the controller, generated server stages, and GPU resource allocation on the same laptop | Optional          |

Remote Kubernetes, GitOps, Arc, and multi-node deployment remain T3-T4 concerns.

## T0 Target

Run robot control and GPU inference on the same laptop. Use plain processes or
containers by default, or place them in a single-node local Kubernetes cluster.

```text
Physical robot
    │
    │ USB + ROS 2 topics
    ▼
Hardware bridge process
    │
    │ observations / commands
    ▼
Control process or container
    │
    │ remoter call over Unix socket or 127.0.0.1
    ▼
GPU inference process or container
    │
    └── local checkpoint + CUDA, CPU, or MPS
```

| Concern        | T0 requirement                                                                             |
|----------------|--------------------------------------------------------------------------------------------|
| Infrastructure | No cloud or remote cluster; local Kubernetes and Helm are optional                         |
| Robot          | One locally attached robot with a ROS 2 hardware bridge                                    |
| Model          | A local LeRobot checkpoint produced by the T0 training recipe                              |
| Control        | A plain process or container with no GPU requirement                                       |
| Inference      | A plain process or container with optional local GPU access                                |
| Discovery      | Static endpoint configuration or local pod discovery                                       |
| Transport      | Unix socket by default, loopback TCP as the portable fallback                              |
| Safety         | Operator-supervised run, command deadline, stale-observation rejection, and emergency stop |
| Fallback       | Direct in-process inference remains available when offload is disabled                     |

## Gap Analysis

### Lifecycle Coverage

`gpu-offload/` should integrate with the repository lifecycle rather than duplicate
capture, curation, training, or evaluation tooling.

| T0 lifecycle stage | Current owner                                      | GPU-offload gap                                               |
|--------------------|----------------------------------------------------|---------------------------------------------------------------|
| Capture            | `data-pipeline/capture/` and ROS 2                 | None; link to the canonical T0 instructions                   |
| Move data          | Operator file copy                                 | None                                                          |
| Curate             | `data-management/viewer/`                          | None                                                          |
| Train              | `training/il/` and `lerobot-train`                 | Accept the local checkpoint layout as an input                |
| Track              | Local training output                              | None                                                          |
| Validate           | `evaluation/sil/scripts/run-local-lerobot-eval.py` | Require successful local evaluation before hardware execution |
| Run on robot       | `gpu-offload/examples/so101-real-hardware/`        | Add validated plain-local and local-Kubernetes launch paths   |

### Runtime and Packaging

| Gap                                                     | Current evidence                                                                                        | Required change                                                                  |
|---------------------------------------------------------|---------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| Kubernetes is mandatory even for plain local mode       | `runtime/pyproject.toml` installs `kubernetes`; `autoremote.py` and `remoter.py` import `rmtconfigkube` | Move Kubernetes discovery behind an optional dependency and lazy provider import |
| Local endpoint configuration is not productized         | The core runtime can read a location file, but all committed examples generate it from pod discovery    | Define and validate a static local endpoint configuration                        |
| Kubernetes config rewriting owns `remoteloc` resolution | `rmtconfigkube.rewrite_taskconfig()` converts stage names into server labels                            | Extract provider-neutral task-config compilation for local and Kubernetes modes  |
| No local server lifecycle                               | Kubernetes creates and probes generated server Deployments                                              | Add local start, readiness, shutdown, PID, and log handling                      |
| No single T0 command                                    | The porting notes list a Podman-native local launcher as pending                                        | Add a launcher that starts server, verifies readiness, then starts the client    |
| Local execution is not tested                           | Existing validation runs inside kind                                                                    | Add process-level and container-level local smoke coverage                       |

### SO-101 and VLA Integration

| Gap                                                 | Current evidence                                                                   | Required change                                                                              |
|-----------------------------------------------------|------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| VLA payload types are partly unsupported            | `class2dict` encodes torch tensors; the registry still has no NumPy adapter        | Add a bounded, explicit ndarray adapter with dtype and shape validation                      |
| Payload limits are not sized from real observations | The codec defaults to an 8 MiB encoded-message limit                               | Measure two-camera observations and configure tested limits without unbounded messages       |
| Model placement is ambiguous                        | The rollout path loads the policy in the control process unless a stage remotes it | Define server-owned model loading so weights are not loaded in the control process           |
| Checkpoint contract is unspecified                  | The example takes an operator-supplied checkpoint path                             | Accept the checkpoint produced by the T0 LeRobot training command and validate it at startup |
| ROS 2 camera topics are placeholders                | The example requires operator topic edits                                          | Add a checked configuration file and a preflight topic/shape check                           |
| Hardware path is unvalidated                        | The example states that it has not passed end-to-end validation                    | Add staged acceptance from fake topics to torque-disabled hardware to supervised motion      |

### Control-Loop Safety and Operations

| Gap                        | Required behavior                                                                                                 |
|----------------------------|-------------------------------------------------------------------------------------------------------------------|
| No inference deadline      | Reject late actions and stop command publication after the configured deadline                                    |
| No stale-observation guard | Attach sequence and monotonic timestamp metadata and reject mismatched results                                    |
| No server-loss behavior    | Transition to a stopped state; never continue with the last action                                                |
| No operator stop contract  | Provide keyboard and ROS 2 stop inputs that disable command publication                                           |
| No startup preflight       | Verify robot calibration, camera topics, checkpoint, device, endpoint, and action limits before enabling commands |
| No run evidence            | Write local timing, timeout, dropped-action, and shutdown records without requiring hosted tracking               |

## Planned Repository Changes

### New Artifacts

| Path                                                            | Purpose                                                                              |
|-----------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `gpu-offload/runtime/remoter/local_config.py`                   | Compile and validate static local endpoints without Kubernetes                       |
| `gpu-offload/runtime/remoter/codec_numpy.py`                    | Encode bounded NumPy arrays with explicit dtype, shape, and byte limits              |
| `gpu-offload/runtime/remoter/codec_torch.py`                    | Encode CPU tensor payloads and restore the requested inference device explicitly     |
| `gpu-offload/scripts/run-t0-local.sh`                           | Start the local server and client, wait for readiness, and clean up exact child PIDs |
| `gpu-offload/examples/first-run/local/`                         | Minimal process-level proof with no Kubernetes                                       |
| `gpu-offload/examples/so101-real-hardware/config/t0-local.yaml` | Local checkpoint, topics, endpoint, deadlines, and safety settings                   |
| `gpu-offload/examples/so101-real-hardware/Containerfile.t0`     | Optional local control and inference image build                                     |
| `gpu-offload/docs/06-T0-local-run.md`                           | Runnable operator guide for plain and local-Kubernetes profiles                      |

### Changes to Existing Artifacts

| Path                                              | Change                                                                                |
|---------------------------------------------------|---------------------------------------------------------------------------------------|
| `runtime/pyproject.toml`                          | Keep the core transport local-only and move Kubernetes packages into an extra         |
| `runtime/remoter/autoremote.py`                   | Select `local` or `kubernetes` discovery explicitly and fail on invalid configuration |
| `runtime/remoter/rmtconfigkube.py`                | Retain pod discovery only; reuse provider-neutral config compilation                  |
| `runtime/remoter/remoter.py`                      | Register bounded application adapters and expose deterministic readiness and shutdown |
| `examples/so101-real-hardware/scripts/rollout.sh` | Add direct/offloaded mode selection, inference deadlines, and stale-result guards     |
| `examples/so101-real-hardware/README.md`          | Separate T0 local instructions from T3-T4 Kubernetes deployment                       |
| `gpu-offload/README.md`                           | Link the validated T0 guide and distinguish local from remote Kubernetes              |

## Delivery Sequence

### Phase 1: Local Runtime Proof

1. Split the Kubernetes dependency from the core runtime.
2. Extract provider-neutral stage and task configuration compilation.
3. Add a static location file for Unix socket and loopback TCP endpoints.
4. Add deterministic server readiness and bounded shutdown.
5. Run the first-run square-function example as two plain processes.

Exit criteria:

- No `kubectl`, kind, Helm, or Kubernetes Python package is installed.
- One command starts the server and client and returns a remote result.
- The result identifies the server process, not the client process.
- Server startup failure exits nonzero with a useful error.
- Interrupting the launcher stops only the child processes it created.

### Phase 2: Local Container and Kubernetes Proof

1. Build the same client and server image with the runtime included.
2. Run the server with local GPU access when available.
3. Connect the client through a mounted Unix socket or loopback port.
4. Verify CPU fallback with the same configuration shape.
5. Run the same application in the existing single-node kind profile.

Exit criteria:

- Docker completes the flow required by the root T0 contract.
- Podman may be supported, but Docker remains the documented T0 baseline.
- The inference container receives the GPU; the control container does not.
- No privileged container, host PID namespace, or container-engine socket is required.
- The kind profile remains on the same laptop and has no cloud dependency.

### Phase 3: VLA Data Contract

1. Add NumPy and PyTorch codec adapters.
2. Define supported dtypes, dimensions, devices, and maximum encoded sizes.
3. Benchmark representative SO-101 joint and two-camera observations.
4. Load the SmolVLA policy only in the inference server.
5. Return action tensors with sequence and timing metadata.

Exit criteria:

- Representative observations and actions round-trip without pickle.
- Unsupported dtype, device, shape, and oversized payloads fail closed.
- The control process does not allocate model weights or require CUDA.
- Latency percentiles are recorded for the target laptop.

### Phase 4: Safe SO-101 Dry Run

1. Add T0 configuration and preflight validation.
2. Run against recorded or synthetic ROS 2 topics with command publication disabled.
3. Add inference deadline, stale-result rejection, server-loss stop, and operator stop.
4. Verify action clipping and coordinate conversion against recorded episodes.

Exit criteria:

- No command reaches a motor bus during the dry run.
- Late, missing, duplicated, and out-of-order responses produce no action.
- Disconnecting the inference server stops the control loop.
- Local logs identify every rejected action and the reason.

### Phase 5: Supervised Hardware Acceptance

1. Start with torque disabled and verify observations, cameras, and proposed actions.
2. Enable motion at reduced limits with an operator at the emergency stop.
3. Run a bounded episode using a checkpoint that passed local evaluation.
4. Record timing, safety events, task result, and clean shutdown.
5. Replace the unvalidated warning only after the complete procedure passes.

Exit criteria:

- One laptop and one SO-101 complete capture-to-run with no cloud dependency.
- The offloaded run uses the local inference process or container.
- A server failure, deadline miss, or stale result never publishes a command.
- The operator guide contains complete commands from checkpoint path to cleanup.

## Definition of Done

The GPU-offload T0 path is complete when a new user can follow one guide and:

1. Use a checkpoint produced by the canonical T0 training recipe.
2. Validate the checkpoint locally before hardware execution.
3. Start the ROS 2 hardware bridge, control loop, and local inference server.
4. Confirm that model inference executes outside the control process.
5. Complete a bounded supervised SO-101 episode.
6. Stop and clean up the selected local profile without cloud resources or hidden manual steps.

Until all six conditions pass, describe the SO-101 path as reference material rather
than a T0 quick start.
