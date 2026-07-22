# Hardware-in-the-Loop Evaluation

Tier 0 runs policy inference on deployment-representative hardware against Isaac simulation on a separate workstation. Tier 3 retains the OSMO/K3s CPU and independently no-command gates. Neither mode commands a physical robot.

## 🚀 Tier 0 Quick Start

Build the shared x86_64 CPU policy-host image:

```bash
docker build --platform linux/amd64 \
  --file evaluation/hil/docker/Dockerfile.policy-host \
  --tag physical-ai-hil-policy-host:local \
  .
```

Run exactly one policy-host command and one simulator-host command for the selected framework. Use the same `--run-id`, `--steps`, `ROS_DOMAIN_ID`, and directly reachable network on both hosts.

### LeRobot ACT

Target policy host:

```bash
docker run --rm --network host \
  --env ROS_DOMAIN_ID=42 \
  --env ROS_LOCALHOST_ONLY=0 \
  --env RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  --volume <local-act-policy>:/policy:ro \
  physical-ai-hil-policy-host:local \
  python evaluation/hil/scripts/run-local-lerobot-policy.py \
    --policy-path /policy \
    --device cpu \
    --steps 100 \
    --run-id ur10e-001
```

Simulator host:

```bash
docker run --rm --gpus all --network host \
  --env ACCEPT_EULA=Y \
  --env PRIVACY_CONSENT=Y \
  --env NVIDIA_DRIVER_CAPABILITIES=all \
  --env PYTHONPATH=/workspace/physical-ai-toolchain \
  --env ROS_DOMAIN_ID=42 \
  --env ROS_LOCALHOST_ONLY=0 \
  --env RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  --volume "$PWD":/workspace/physical-ai-toolchain \
  --volume <local-ur10e-scene-dir>:/workspace/scenes:ro \
  --workdir /workspace/physical-ai-toolchain \
  nvcr.io/nvidia/isaac-lab:2.3.2@sha256:388dbc806f48359a964cb9f807feb226da95d0a107f470fdcad9780ea10fe6f2 \
  /workspace/isaaclab/isaaclab.sh -p \
    evaluation/hil/scripts/run-local-lerobot-sim.py \
    --config /workspace/scenes/hil-config.json \
    --policy-id <policy-name-or-version> \
    --output-dir outputs/hil/ur10e-001 \
    --run-id ur10e-001 \
    --headless --enable_cameras
```

Copy `evaluation/hil/config/local-lerobot-ur10e.json` into the mounted scene directory as `hil-config.json` and replace the USD and prim paths before the run. The ACT policy must be trained from data matching that scene's six-joint, camera, action, and 30 Hz contract.

### Isaac Lab JIT

Target policy host:

```bash
docker run --rm --network host \
  --env ROS_DOMAIN_ID=42 \
  --env ROS_LOCALHOST_ONLY=0 \
  --env RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  --volume <local-jit-artifact-dir>:/policy:ro \
  physical-ai-hil-policy-host:local \
  python evaluation/hil/scripts/run-local-jit-policy.py \
    --policy-path /policy/policy.pt \
    --io-descriptor /policy/policy_io.json \
    --device cpu \
    --steps 1000 \
    --run-id anymal-c-001
```

Simulator host:

```bash
docker run --rm --gpus all --network host \
  --env ACCEPT_EULA=Y \
  --env PRIVACY_CONSENT=Y \
  --env NVIDIA_DRIVER_CAPABILITIES=all \
  --env PYTHONPATH=/workspace/physical-ai-toolchain \
  --env ROS_DOMAIN_ID=42 \
  --env ROS_LOCALHOST_ONLY=0 \
  --env RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  --volume "$PWD":/workspace/physical-ai-toolchain \
  --volume <local-jit-artifact-dir>:/policy:ro \
  --workdir /workspace/physical-ai-toolchain \
  nvcr.io/nvidia/isaac-lab:2.3.2@sha256:388dbc806f48359a964cb9f807feb226da95d0a107f470fdcad9780ea10fe6f2 \
  /workspace/isaaclab/isaaclab.sh -p \
    evaluation/hil/scripts/run-local-isaaclab-sim.py \
    --policy-path /policy/policy.pt \
    --io-descriptor /policy/policy_io.json \
    --output-dir outputs/hil/anymal-c-001 \
    --run-id anymal-c-001 \
    --steps 1000 \
    --response-timeout-s 5 \
    --seed 42 \
    --headless
```

`policy_io.json` must describe the exact `Isaac-Velocity-Rough-Anymal-C-v0` task and policy artifact. Generate it from the live task and JIT policy with `scripts/generate-policy-io.py`; do not infer term order from model dimensions.

## 📦 Components

| Path | Purpose |
|------|---------|
| `scripts/run-local-lerobot-policy.py` | Target-host ACT process |
| `scripts/run-local-lerobot-sim.py` | Isaac Sim UR10E evaluator |
| `scripts/run-local-jit-policy.py` | Target-host JIT process |
| `scripts/run-local-isaaclab-sim.py` | Isaac Lab Anymal-C evaluator |
| `scripts/generate-policy-io.py` | Live-task RL sidecar generator |
| `config/local-lerobot-ur10e.json` | Explicit user-supplied scene contract |
| `docker/Dockerfile.policy-host` | Shared x86_64 ROS 2 Jazzy CPU image |
| `no_command_runner.py` | T3 deterministic no-command runtime |
| `workflows/osmo/` | T3 OSMO scheduling and no-command gates |

## 🧱 Boundaries

* T0 accepts local policy and scene artifacts only. It does not call Azure, Kubernetes, OSMO, Arc, FluxCD, or physical robot command code.
* The public ALOHA dataset remains an offline example. It is not compatible with the UR10E scene contract.
* Jetson GPU policy hosting is not implemented. It requires an exact model and JetPack/L4T image.
* A successful simulator command writes `summary.json`. A failed run removes stale success output and preserves the originating failure.

## 🏭 Tier 3 OSMO Gates

After `02-connect-osmo-backend.sh` reports a connection receipt, run:

```bash
data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt>

data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt>
```

The no-command adapter rejects every proposed action with `NO_COMMAND_TRANSPORT`. T3 adds orchestration; it does not replace or prove the T0 simulator loop.

## 📚 Documentation

* [HiL Evaluation](../../docs/evaluation/hil-evaluation.md)
* [T0 Dev Recipe](../../docs/recipes/tier-0-dev/README.md)
* [Ubuntu Validation Handoff](../../docs/recipes/tier-0-dev/ubuntu-hil-validation.md)
* [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md)
