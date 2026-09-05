---
title: SO-101 Operator View
description: Configure and use the dataviewer for SO-101 teleoperation, recording, policy rollout, trajectory review, and VLM evaluation
author: Physical AI Toolchain contributors
ms.date: 2026-09-04
ms.topic: how-to
---

Use the dataviewer **Operate** workspace to run one local SO-101 leader/follower session, record LeRobot datasets, or execute a bounded GR00T policy rollout. Completed recordings remain available to the **Analyze** workspace for multi-camera playback, end-effector trajectory review, annotation, and VLM judging.

## Scope

| Adapter     | Purpose                                          | Hardware access | Preflight                  |
|-------------|--------------------------------------------------|-----------------|----------------------------|
| `disabled`  | Default for hosted and analysis-only deployments | None            | Disabled                   |
| `simulated` | Exercise browser and backend session lifecycles  | None            | Disabled                   |
| `lerobot`   | Operate the configured SO-101 arms and cameras   | Local devices   | Required before each start |

| Session mode | Runtime                                 | Inputs                                               | Motion bounds                                                   |
|--------------|-----------------------------------------|------------------------------------------------------|-----------------------------------------------------------------|
| Teleoperate  | Leader drives follower                  | Leader arm, follower arm, wrist camera, front camera | Optional per-step follower clamp                                |
| Record       | Teleoperation plus LeRobot v3 recording | Same as teleoperation                                | Optional per-step follower clamp                                |
| Policy       | GR00T action chunks drive follower      | Follower arm, wrist camera, front camera, checkpoint | Required clamp of at most 5 degrees and a 1–300 second duration |

The operator implementation runs only with local dataset storage and one backend worker. Azure storage mode and multi-process backend deployments reject operator startup.

## Prerequisites

| Requirement  | Detail                                                                             |
|--------------|------------------------------------------------------------------------------------|
| Host         | Linux x86-64 with Python 3.12, Node.js, npm, and `uv`                              |
| Arms         | SO-101 leader and follower matching the selected profile                           |
| Serial links | Stable leader and follower udev links; raw `/dev/ttyACM*` ordering is not accepted |
| Calibration  | LeRobot calibration JSON for all six joints on each arm                            |
| Cameras      | One stable V4L wrist camera path and one Intel RealSense D405 serial               |
| Storage      | Writable local `DATA_DIR` with at least 10 GiB free for the shipped profile        |
| Safety       | Physical power cutoff or emergency stop reachable by the operator                  |

> [!WARNING]
> **Stop Session** is a graceful software stop, not a certified emergency stop. Use the physical power cutoff when motion is unsafe or software control is unavailable.

## Build the worker

Build the isolated worker from `data-management/viewer`. The worker has its own lock and LeRobot dependency set because hardware packages do not run inside the FastAPI environment.

```bash
cd data-management/viewer
npm run build:worker
```

The generated executable is:

```text
<repo>/data-management/viewer/operator-worker/.venv/bin/physical-ai-operator-worker
```

Run the full simulated validation before connecting hardware:

```bash
cd data-management/viewer
npm run validate
```

## Configure simulation

Set the adapter in `backend/.env` or the shell, then start the application:

```env
STORAGE_BACKEND=local
DATA_DIR=/absolute/path/to/datasets
OPERATOR_ADAPTER_MODE=simulated
```

```bash
cd data-management/viewer
npm start
```

Open `http://localhost:5173`, select **Operate**, and start either simulated session. Simulation validates API state, idempotent commands, event streaming, and the browser control surface. It does not emit physical camera frames or joint telemetry.

## Configure real hardware

For an unauthenticated local workstation, bind the backend to loopback and explicitly authorize loopback-only hardware access:

```env
STORAGE_BACKEND=local
DATA_DIR=/absolute/path/to/datasets
BACKEND_HOST=127.0.0.1
DATAVIEWER_AUTH_DISABLED=true
OPERATOR_ADAPTER_MODE=lerobot
OPERATOR_ALLOW_UNAUTHENTICATED_LOOPBACK=true
OPERATOR_HOST_LEASE_PATH=/tmp/physical-ai-operator.lock
OPERATOR_WORKER_EXECUTABLE=<repo>/data-management/viewer/operator-worker/.venv/bin/physical-ai-operator-worker
```

The lease parent directory must exist. The lease path must be a regular file or a path that can be created with mode `0600`; symlinks are rejected.

The shipped profile is `data-management/viewer/backend/src/api/operator/profile_data/so101.toml`. It pins the expected embodiment, six actuators, USB identities, calibration files, camera geometry, and recording defaults. The loader accepts only these environment overrides:

| Variable                             | Profile field                           |
|--------------------------------------|-----------------------------------------|
| `OPERATOR_SO101_LEADER_PORT`         | Stable leader serial-device link        |
| `OPERATOR_SO101_FOLLOWER_PORT`       | Stable follower serial-device link      |
| `OPERATOR_SO101_WRIST_CAMERA_PATH`   | Stable wrist-camera V4L path            |
| `OPERATOR_SO101_FRONT_CAMERA_SERIAL` | RealSense SDK serial used by the worker |

Unknown `OPERATOR_SO101_*` variables fail startup. Update the profile deliberately when actuator names, USB identities, logical IDs, calibration locations, or the RealSense descriptor identity differ from the shipped hardware snapshot.

Start the application after the profile matches the attached devices:

```bash
cd data-management/viewer
npm start
```

## Run preflight

Select **Operate**, choose Teleoperate, Record, or Policy, and run **SO-101 Preflight**. Preflight reads sysfs, procfs, calibration files, credentials, and storage metadata without opening a device node or enabling torque.

| Check               | Blocking conditions                                                                     |
|---------------------|-----------------------------------------------------------------------------------------|
| Leader and follower | Missing stable link, inaccessible character device, USB mismatch, or duplicate identity |
| Calibration         | Missing, malformed, or non-six-joint calibration JSON                                   |
| Wrist camera        | Missing stable path, inaccessible device, or USB mismatch                               |
| Front camera        | Expected D405 USB descriptor identity not found exactly once                            |
| Dataset storage     | Missing directory, insufficient permissions, or less than the profile free-space floor  |
| Upload credentials  | Hub upload requested without `HF_TOKEN` or a readable cached token                      |
| Policy runtime      | Missing executable or incomplete checkpoint                                             |
| Device ownership    | A visible process already holds a configured device                                     |

A process-visibility warning does not block start when no holder is visible. Any blocking result prevents start. Preflight evidence expires after 30 seconds, is bound to the selected mode and resource fingerprints, and is consumed by a successful start attempt.

## Operate a session

The backend owns the authoritative state and streams revisions over server-sent events. The browser exposes these terminal states:

| State       | Meaning                                             |
|-------------|-----------------------------------------------------|
| `idle`      | No worker exists                                    |
| `starting`  | Worker is validating and acquiring resources        |
| `running`   | Motion-capable session is active                    |
| `stopping`  | Cleanup and torque-off verification are in progress |
| `completed` | Session finished and cleanup was confirmed          |
| `cancelled` | User cancelled and cleanup was confirmed            |
| `failed`    | Worker, protocol, acquisition, or cleanup failed    |

Do not restart when `cleanup_unconfirmed` is shown. Verify that both arms are de-energized and all serial/camera handles are released, then restart the backend.

### Teleoperation

Run preflight for Teleoperate and select **Start Teleoperation**. The worker acquires cameras, leader, and follower transactionally, enables follower motion only after initialization, and reports control-rate metrics and joint telemetry. **Stop Session** requests cleanup and verifies torque-off before the backend reports a safe terminal state.

### Recording

Configure the task, dataset name, camera rates, episode count, duration, save destination, and optional clamp before running Record preflight. Every recorded frame includes the task string, follower observation, commanded action, and both camera views.

| Control           | Keyboard    | Result                                                                     |
|-------------------|-------------|----------------------------------------------------------------------------|
| Pause or resume   | Space       | Stop or resume frame writes while teleoperation remains active             |
| Save Episode      | Right Arrow | Commit the pending episode and start the next episode                      |
| Discard Episode   | Left Arrow  | Clear only the current pending episode buffer                              |
| Finish Recording  | Up Arrow    | Commit pending frames, finalize the dataset, and optionally upload it      |
| Discard Recording | Down Arrow  | Cancel the session and delete the complete dataset created by that session |

A name collision appends a UTC timestamp, then a numeric suffix when required. The worker writes below `DATA_DIR` and rejects paths that escape that root. Hub upload runs only after local finalization; upload failure preserves the local dataset.

### Policy rollout

Policy mode is hidden until all server-side runtime settings are present:

```env
OPERATOR_POLICY_PYTHON=/absolute/path/to/policy/python
OPERATOR_POLICY_CHECKPOINT=/absolute/path/to/checkpoint
OPERATOR_POLICY_CUDA_VISIBLE_DEVICES=0
```

The checkpoint must contain `config.json`, `model.safetensors`, `policy_preprocessor.json`, and `policy_postprocessor.json`. Policy preflight verifies those files. The worker acquires the follower and both cameras but not the leader, reads a fresh observation before each action chunk, rejects empty, wrong-shaped, or non-finite chunks, and clamps every target relative to the current joint state. The browser defaults the clamp to 2 degrees; the backend rejects values above 5 degrees.

## Inspect telemetry and trajectories

Operate mode displays:

| Surface                  | Source                                                                    |
|--------------------------|---------------------------------------------------------------------------|
| Wrist and front previews | Worker-owned JPEG samples; the backend never opens a second camera handle |
| Joint plot               | Leader, follower, and commanded six-joint values                          |
| Loop metrics             | Target and actual frequency, p95/max loop time, and overruns              |
| End-effector trace       | Approximate SO-101 forward kinematics from follower joint telemetry       |

The live 3D trace is a visualization, not calibrated metrology or collision detection. It uses the SO-101 link model in `frontend/src/components/episode-viewer/end-effector-trajectories.ts`.

Analyze mode exposes **End effector 3D** in the camera selector. Trajectory sources are selected in this order:

1. Recorded dual-arm Cartesian features (`observation.state.ee_quat_pos` or `observation.state.ee_6d_pos`).
2. Per-frame `end_effector_pose`, including HDF5 episodes.
3. SO-101 forward kinematics when the state schema contains the recognized six-joint names.

Unknown joint schemas do not produce a fabricated SO-101 trace. HDF5 camera arrays are materialized as H.264 MP4 files for VLM and browser use through system FFmpeg, required PyAV, or OpenCV in that fallback order.

## Analyze and judge recordings

After **Finish Recording**, switch to **Analyze**. Dataset discovery refreshes every 30 seconds and on browser focus; select the resolved dataset ID after it appears. The task written into each frame becomes the dataset task instruction used by annotation and VLM judging.

Enable the judge as described in [Dataset Analysis Tool](../data-management/viewer/README.md#vlm-as-judge-experimental). Then:

1. Select the recorded dataset and episode.
2. Confirm or refine **Task Instruction**.
3. Enable both camera views when the task needs wrist and scene context.
4. Select **Run judge** or **Force fresh**.
5. Review outcome votes, progress, VOC, milestones, and failure mode before applying a label.

> [!IMPORTANT]
> Treat VLM output as review evidence, not ground truth. Self-consistent votes can still contradict visible milestone evidence or a human label. Preserve human annotations and use **Force fresh** only when a deliberate re-evaluation is required.

Results are cached under `<dataset>/annotations/vlm_judge/`. Re-evaluating the same videos, instruction, model, prompt version, process method, and agent configuration returns the cached result.

## API reference

| Method   | Endpoint                                       | Purpose                                                          |
|----------|------------------------------------------------|------------------------------------------------------------------|
| `GET`    | `/api/operator/capabilities`                   | Adapter, mode, profile, robot, camera, and protocol capabilities |
| `GET`    | `/api/operator/status`                         | Current authoritative session state                              |
| `POST`   | `/api/operator/preflights`                     | Run read-only readiness checks                                   |
| `GET`    | `/api/operator/preflights/{preflight_id}`      | Read current preflight evidence                                  |
| `DELETE` | `/api/operator/preflights/{preflight_id}`      | Cancel a preflight resource                                      |
| `POST`   | `/api/operator/sessions`                       | Start one session with idempotent `command_id`                   |
| `POST`   | `/api/operator/sessions/{session_id}/commands` | Save, discard, pause, resume, or finish recording                |
| `DELETE` | `/api/operator/sessions/{session_id}`          | Cancel the named session                                         |
| `GET`    | `/api/operator/events`                         | Stream status revisions with replay and heartbeats               |
| `GET`    | `/api/operator/cameras/{camera}/frame`         | Read the latest worker-owned JPEG preview                        |

Hardware mutations require operator authorization, double-submit CSRF, and a current session ID. API-key deployments require `DATAVIEWER_OPERATOR_API_KEY` through `X-Operator-API-Key`; keep it distinct from `DATAVIEWER_API_KEY`. Role-based deployments require the `Operator` role. Easy Auth is rejected for hardware access unless `OPERATOR_TRUST_EASY_AUTH=true`.

## Failure containment

The backend and worker enforce these boundaries:

- One FastAPI process and one cooperative host lease own hardware.
- Protocol v2 validates session, service instance, nonce, sequence, profile fingerprint, resource fingerprint, worker version, Python version, and LeRobot version.
- Resource acquisition rolls back in reverse order on failure.
- Parent-death handling and an independent watchdog attempt verified torque-off after process failure.
- Missing cleanup evidence sets `cleanup_unconfirmed` and blocks another start.
- Worker logs are bounded and redact local path-shaped values before browser delivery.
- Camera payloads are capped at 300 KiB.
- Operator mode rejects Azure storage and remains disabled by default.

## Troubleshooting

| Symptom                         | Action                                                                                            |
|---------------------------------|---------------------------------------------------------------------------------------------------|
| Operator unavailable            | Set `OPERATOR_ADAPTER_MODE`; restart because environment changes are not hot-reloaded             |
| Stable arm link missing         | Restore the configured udev symlink; do not substitute an unverified raw serial path              |
| USB identity mismatch           | Confirm the attached arm role and serial before changing the profile                              |
| D405 not found                  | Compare the SDK and USB descriptor serials with the profile                                       |
| Device ownership warning        | Stop known camera, teleoperation, or serial processes; rerun preflight                            |
| Preflight expired               | Rerun preflight and start within 30 seconds                                                       |
| Cleanup unconfirmed             | Use the physical cutoff, verify released devices, and restart the backend                         |
| New recording absent in Analyze | Wait for the 30-second dataset refresh or refocus the browser                                     |
| VLM judge disabled              | Set `VLM_JUDGE_ENABLED=true` and restart the backend                                              |
| HDF5 judge reports no video     | Confirm the episode contains camera arrays and that its generated `meta/videos/` path is writable |

## Validation

Run validation from `data-management/viewer`:

```bash
npm run validate:backend
npm run validate:worker
npm run validate:frontend
npm run validate
```

Real-hardware smoke validation remains supervised and opt-in. Run it in this order:

1. Complete preflight with no blocking checks.
2. Start and stop a short teleoperation session.
3. Confirm follower torque is off and both serial devices can be reopened.
4. Record one short episode, save it, and finish.
5. Open the dataset in Analyze, enable **End effector 3D**, and play both camera views.
6. Run one cached and one forced-fresh VLM judgment.

## Related documentation

- [Dataset Analysis Tool](../data-management/viewer/README.md)
- [Operator View Design](design/operator-view-spec.md)
- [VLM-as-Judge](../evaluation/vlm_judge/README.md)
