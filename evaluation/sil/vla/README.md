# VLA Simulation Evaluation

Run a vision-language-action (VLA) checkpoint in a closed Isaac Sim loop without ROS. The harness separates checkpoint serving, observation/action conversion, simulation, and task scoring. Rho and OpenPI backends share the same runner; the first native task adapter is UR10e gear-to-bin.

This is not dataset-replay evaluation: current camera frames and joint measurements go to the policy, and returned targets change the next simulated observation. Historical experiment scripts and results remain unchanged and are not runtime dependencies.

## 📋 Prerequisites

| Process                   | Requirements                                                                                                                                         |
|---------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| Host inspection and tests | Python 3.11 or 3.12, `uv`, this directory's committed `uv.lock`; `audit` adds OpenUSD                                                                |
| Policy server             | A separately installed compatible Rho or OpenPI environment, its native checkpoint loader, local checkpoint, and cached auxiliary assets             |
| Native simulator          | Linux, NVIDIA GPU, Python 3.11, Isaac Sim 5.1.0.0, Isaac Lab 0.54.4, and this harness's NumPy/msgpack/WebSocket/PyAV dependencies                    |
| UR10e task owner          | Reviewed `physical-ai-toolchain-skills` checkout containing `workflows/isaac-sim/ur10e-gear-to-bin/scripts/capture/` and a full expert configuration |
| Scene                     | Complete reviewed UR10e USD package, both camera prims, gear, and bin; local references must resolve within the selected asset root                  |

Run commands from the `physical-ai-toolchain` repository root. Keep model and simulator environments separate: this project intentionally does not install Torch, Rho, OpenPI, JAX, or Isaac. Its lightweight lock supports host validation, not a replacement lock for either native environment. Record each selected framework checkout and use its own dependency lock.

`uv sync --project evaluation/sil/vla --frozen --group dev` prepares the host tests. Install only the harness dependencies in the existing model/native environments using their supported environment-management procedure; do not replace Isaac's bundled NumPy, Torch, or USD to satisfy the host audit group. The native task checks Python/Isaac versions before launch and reads USD dependencies after its application starts.

## 🚀 Prepare a Checkpoint Contract

Choose a template:

| Template                                                                    | Backend                                                            |
|-----------------------------------------------------------------------------|--------------------------------------------------------------------|
| [`rho-ur10e.example.json`](../../examples/vla/rho-ur10e.example.json)       | Rho local checkpoint bundle with checkpoint-owned transforms       |
| [`openpi-ur10e.example.json`](../../examples/vla/openpi-ur10e.example.json) | OpenPI local checkpoint plus its registered training configuration |

Save an edited copy under the ignored `outputs/` root or another operator-controlled location. Replace all placeholder paths and the checksum. Relative paths resolve against the configuration file, not the terminal's current directory. Both processes must use the same configuration bytes; in containers, preserve those configured mount paths.

Fingerprint the exact checkpoint directory without loading model weights:

```bash
uv run --project evaluation/sil/vla --frozen python -m evaluation.sil.vla fingerprint \
  --checkpoint /absolute/path/to/checkpoint
```

Set `policy.checkpoint_sha256` to the returned `checkpoint.sha256`. The digest covers relative filenames, file sizes, and file SHA-256 values, including weights, normalization assets, and configuration. It is not a digest embedded in the checkpoint's own manifest. Materialize checkpoint symlinks before fingerprinting. Only reviewed local checkpoints are accepted; the harness does not pick the newest checkpoint or download one.

Configure `task.producer_root` as the packaged UR10e `scripts/capture` directory, not the companion repository's archived collector or a historical evaluation folder. Select a full expert profile, stage, and reference root. The task reuses its owner for articulation setup, seeded square XY placement, physics stepping, and cameras; it does not replay demonstrations or call the expert's grasp/trajectory routines during policy execution.

The selected producer directory contains executable Python, including modules imported by inspection. Review and pin that checkout before selecting it. File hashes detect changes; they do not authenticate a producer or sandbox its code.

Validate the selected checkpoint and task on the host:

```bash
uv run --project evaluation/sil/vla --frozen --group audit python -m evaluation.sil.vla inspect \
  --config /absolute/path/to/evaluation.json
```

This hashes inputs, checks channel/camera/cadence compatibility, and inventories local USD dependencies. It does not load the model, open Isaac, or establish task success. Explicit runtime MDL identifiers remain unpinned and appear separately in provenance. A missing dependency or mismatched checkpoint hash is a blocker, not permission to substitute assets or statistics.

## ▶️ Serve and Evaluate

Start the policy server in the selected model environment. Replace the interpreter and configuration paths; do not run this with the host-only test environment.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /path/to/model-environment/bin/python \
  -m evaluation.sil.vla serve \
  --config /absolute/path/to/evaluation.json \
  --output "$PWD/outputs/vla-policy-server/run-001" \
  --device cuda:0 --port 8000
```

The server fingerprints the checkpoint before and after loading, writes `server.json` with actual framework and source identities, and serves one active evaluation client. It uses the selected framework's input/output transforms. Required auxiliary assets must already be local or cached. Environment flags and local-cache checks are not an OS network sandbox; use a network-disabled container when that isolation is required.

Run the simulator separately, only after accepting NVIDIA's applicable terms/privacy choices and selecting the concrete GPU, scene, seed budget, and output directory:

```bash
/path/to/isaac-environment/bin/python -m evaluation.sil.vla run \
  --config /absolute/path/to/evaluation.json \
  --output "$PWD/outputs/vla-evaluation/run-001" \
  --device cuda:0 --port 8000 \
  --accept-nvidia-terms --accept-nvidia-privacy
```

Both flags are required and supply scoped `ACCEPT_EULA=Y` and `PRIVACY_CONSENT=Y` only during native execution. They do not authorize hardware motion, cloud operations, unbounded evaluation, or checkpoint changes. An empty `.env` is not consent. Nothing in inspection or tests starts NVIDIA software.

The two processes may share a GPU if memory permits or use separately selected devices. OpenPI JAX loading requires exactly one visible device; set CUDA visibility and `JAX_PLATFORMS` in its environment before loading. The simulator remains single-environment and serializes against the UR10e producer's lock; unrelated Isaac processes may not honor that lock.

The default transport is loopback-only. For a network-disabled simulator container, supply the same `--socket /mounted/path/policy.sock` to both processes instead of a TCP connection. Mount sources, checkpoint assets, and scene read-only; expose only fresh evidence/cache directories and the shared ownership lock as writable. The server creates a fresh socket with owner-only permissions. Use a fresh socket path after interrupted runs; it is not deleted automatically.

This protocol is not wire-compatible with an unmodified OpenPI or Rho server. Use this harness's `serve` command, which wraps the native backend behind the shared request/response contract. Model-load, inference, contract, transport, and timeout errors stop the operation; the harness does not reconnect and replay requests.

## ⚙️ Observation and Action Contracts

| Field                     | Meaning                                                                                                                                    |
|---------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `channels`                | Ordered measured joint positions and final target channels; simulator units are radians                                                    |
| `image_shapes`            | Exact named uint8 HWC RGB camera frames; undeclared cameras are rejected                                                                   |
| `policy.image_keys`       | Mapping from camera IDs to the selected checkpoint's input keys                                                                            |
| `state_key`, `prompt_key` | Backend input names; Rho uses `observation.state` and `task`; OpenPI depends on its training configuration                                 |
| `state_mapping`           | Select/reorder measured channels, then apply `value * scale + offset` before native preprocessing                                          |
| `action_mapping`          | Select/reorder output channels, then apply `value * scale + offset` after native output transforms                                         |
| `delta_indices`           | Final simulator channels still representing deltas after the native output pipeline; anchored once to the measured state at inference time |
| `action_horizon`          | Exact predicted chunk length; no assumed universal VLA horizon                                                                             |
| `execution_horizon`       | Prefix consumed before requesting another chunk; must not exceed prediction length                                                         |
| `control_hz`              | Simulation action cadence, which must match the task configuration                                                                         |
| `joint_limit_behavior`    | `reject` stops before applying an out-of-range target; `clip` records both raw and clipped targets and intervention counts                 |

The UR10e adapter requires six ordered arm positions plus `finger_joint`, two cameras, and the expert profile's cadence, normally 30 Hz control and 120 Hz physics. Gripper values are continuous radians, not a percentage or a binary open/closed flag. The example uses identity mappings, not a claim that every checkpoint uses these units.

For an OpenPI checkpoint trained with different camera names, joint ordering, or gripper scaling, declare the actual mappings. Native OpenPI normalization and its data/model output transforms run first; do not normalize twice or apply delta inversion twice. If native output remains state-relative, list only those remaining delta channels. A Cartesian/velocity-output checkpoint is not a joint-position policy; an affine mapping cannot substitute for a calibrated IK or velocity controller.

Rho supports one current observation with either absolute POSITION actions or one pre-normalization state-relative POSITION `DeltaActions` transform. Its checkpoint's absolute-index mask is preserved. Delta inversion uses the measured model state before normalization/clipping, not reconstructed clipped state.

Scalar/broadcast STATE statistics, unsupported transforms, extra features, and multi-observation histories are rejected. Historical checkpoints that relied on the old scalar-normalization defect require a separately reviewed backend; this harness does not silently reproduce or repair that training defect.

Inference is synchronous: simulation pauses while a chunk is computed, then consumes the declared prefix. Measured inference latency is reported but is not modeled as real-time actuation delay. There is no RTC, speculative action smoothing, IK fallback, or online training. Rho records its per-episode NumPy/Torch seeding; OpenPI reports task-reset-only seeding because its native model RNG is not reset by this adapter. Identical seeds do not promise pixel- or physics-identical rollouts.

## 📊 Episodes, Evidence, and Scoring

`evaluation.seeds` contains explicit, non-overlapping `train`, `validation`, `test`, or `unverified` groups. Each seed runs once in the declared order. Group membership is caller-declared, not independently inferred from training data. Keep the external split manifest with the experiment and do not call previously inspected seeds blind holdouts.

`max_steps` bounds each episode. `minimum_free_gib` is a minimum free-space reserve, not an estimate of the whole run. Reserve enough additional space for every inference input, raw/decoded output, numeric trajectory, and full camera video. Output directories must be fresh and disjoint from inputs. No automatic resume, failed-seed replacement, canonical promotion, or deletion is performed.

| Artifact                                   | Evidence                                                                                                      |
|--------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| `config.json`, `provenance.json`           | Full selected contract, backend identity, task source/dependency hashes, and runtime limitations              |
| `episode_<index>_seed_<seed>/initial.json` | Measured reset state, initial command, joint limits, and task-private baseline                                |
| `requests/*.input.npz`                     | Exact raw robot state and RGB inputs; no privileged goal, contact, phase, or pose features                    |
| `requests/*.output.npz`, `requests/*.json` | Native backend output, decoded absolute chunks, request/session IDs, and inference timing                     |
| `trajectory.npz`                           | Pre-action state, post-action state/evidence, raw/executed targets, control indices/times, and latency        |
| `<camera>.mp4`                             | Every post-action camera frame, encoded independently per camera                                              |
| `result.json`                              | Episode execution status and task outcome separately, clipping counts, video checks, and file hashes          |
| `evaluation.json`                          | All declared episodes completed and final source/shutdown checks passed; not a claim that all tasks succeeded |
| `failure.json`, `shutdown-errors.json`     | Retained operational or cleanup failures; no completed aggregate manifest                                     |

All persisted arrays are numeric and readable with `allow_pickle=False`. Video checks decode every frame to verify cadence, dimensions, and count. Source RGB hashes and encoded-file hashes are distinct; these checks do not establish pixel fidelity or human visual review.

The versioned UR10e scorer evaluates sustained bilateral contact, lift after contact begins, retention before release in the end-effector frame, contact clearing, final live-bin containment dwell, and full-stream target derivatives including the initial command. The bin's moving rigid frame is used, not a cached world-space target. It retains the configured conservative box envelope and reports the gate details separately from execution completion.

The scorer is intentionally not labeled the historical Rho scorer: lift ordering and explicit bin reset semantics are stricter. Body-level filtered contact is not opposing-pad contact proof; conservative boxes are not mesh collision tests; contact clearing alone does not establish intentional release. Review both camera videos and evaluate task-specific metrics before drawing policy-performance or hardware-transfer conclusions.

## 🧩 Extend the Harness

| Boundary        | Extension point                                                                                                                                                                    |
|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Model family    | Implement `Backend.infer` in [`backends.py`](backends.py), add a finite loader dispatch entry, and record native framework/transform provenance                                    |
| Robot/task      | Implement `Simulation` from [`interfaces.py`](interfaces.py), providing reset, one-step target application, measured observations, limits, task evidence, score, hold, and cleanup |
| Task acceptance | Implement and version the task scorer independently from the policy; keep privileged data out of inference                                                                         |
| Deployment      | Run the server in the model's locked environment and the task in its validated simulator environment; retain the shared configuration and session checks                           |

The runner handles arbitrary declared joint widths and camera sets within its explicit bounds. UR10e is the only registered native task today. Unknown adapters are rejected; configuration files cannot import arbitrary Python entry points. Add a focused task/backend test before registering another implementation.

## 🧪 Validation

```bash
uv lock --check --project evaluation/sil/vla
uv run --project evaluation/sil/vla --frozen --group dev pytest \
  -c evaluation/sil/vla/pyproject.toml --cov-config=evaluation/sil/vla/pyproject.toml \
  evaluation/sil/vla/tests
uv run --project evaluation/sil/vla --frozen --group dev ruff check \
  evaluation/sil/vla
uv run --project evaluation/sil/vla --frozen --group dev ruff format --check \
  evaluation/sil/vla
```

The lightweight project owns its colocated CPU tests and coverage gate separately from the parent evaluation suite's ACT/Torch fixtures. It exercises strict configuration, exact action conversion, real loopback WebSockets, streamed MP4 encode/decode, failure preservation, and rigid-frame scoring. Framework loading and native simulator boundaries use controlled fakes, not robot or model acceptance evidence.

No fresh Rho/OpenPI checkpoint inference or Isaac rollout has been validated for this new package. Confirm the selected framework/API revision, run one bounded native episode, verify observation/action mappings and both cameras, then scale the declared seed set. Existing historical evaluation successes do not qualify this migration automatically.

## 🔗 Related Evaluation Paths

* [`policy_evaluation.py`](../policy_evaluation.py): generic SKRL/RSL-RL Isaac Lab evaluation, not a VLA checkpoint loader.
* [`run_evaluation.py`](../scripts/run_evaluation.py): LeRobot recorded-dataset inference comparison, not closed-loop simulation.
* [Evaluation overview](../../README.md): software- and hardware-in-the-loop entry points.
