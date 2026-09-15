---
title: LeRobot SO-101 integration
description: Pinned LeRobot workflows, multi-architecture images, and optional Xavier offloading
ms.date: 2026-08-26
---

This example packages a pinned
[LeRobot](https://github.com/huggingface/lerobot) checkout for SO-101 episode
collection, fine-tuning, evaluation, and rollout. The image builds for both
`linux/amd64` and `linux/arm64` and includes the Xavier remoting runtime.

## 📋 Initialize

Initialize the pinned LeRobot submodule after cloning:

```bash
git submodule update --init --recursive \
  gpu-offload/examples/so101-real-hardware/upstream
```

The exact commit is stored by the Git submodule reference. `.lerobot-version` records
the requested upstream tag, branch, or commit used by image tags and scripts.

## 📦 Build the image

Build and load an image for the host architecture:

```bash
./scripts/build_container.sh
```

Publish one manifest containing both architectures:

```bash
./scripts/build_container.sh \
  --push \
  --platforms linux/amd64,linux/arm64 \
  --image ghcr.io/<org>/lerobot-so101
```

The amd64 image uses the CUDA-enabled PyTorch version from LeRobot's lock file.
The arm64 image replaces it with CUDA 13 wheels required by NVIDIA Thor. The
build reads the remoting package directly from `gpu-offload/runtime` through a
BuildKit named context, so the image always contains the runtime from the same
checkout.

## 🔄 Update LeRobot

Update only to an explicit reviewed tag, branch, or commit:

```bash
./scripts/update_upstream.sh v0.6.2
```

The script updates `upstream/` and `.lerobot-version`. Review the upstream
release notes, rebuild both architectures, run the workflows you use, and then
commit both changed paths.

## ⚙️ Configure SO-101

Edit `config/so101.env` or override values in `scripts/.env`, the repository
`.env`, or `.env.local`.

```dotenv
ROBOT_PORT=/dev/ttyACM0
TELEOP_PORT=/dev/ttyACM1
ROBOT_CAMERAS='{front: {type: opencv, index_or_path: /dev/video0, width: 640, height: 480, fps: 30}}'
```

## 🚀 Run workflows

Collect episodes:

```bash
./scripts/collect_episodes.sh \
  --repo-id <user>/so101_pickplace \
  --task "pick up the cube" \
  --num-episodes 50
```

Finetune ACT:

```bash
./scripts/finetune.sh \
  --dataset <user>/so101_pickplace \
  --policy act \
  --steps 100000 \
  --output outputs/train/act_so101
```

Evaluate in simulation:

```bash
./scripts/evaluate.sh \
  --policy outputs/train/act_so101/checkpoints/last/pretrained_model \
  --env-type pusht \
  --episodes 10
```

Run directly on the robot:

```bash
./scripts/rollout.sh \
  --policy /policies/act_so101 \
  --task "pick up the cube" \
  --duration 60
```

Each workflow accepts native LeRobot overrides after `--`.

## ☸️ Kubernetes rollout

The Helm Job is suspended by default so installation cannot move the robot.
Override policy, calibration, devices, cameras, image repository, and node
placement in a values file or with Helm arguments.

Load a locally built image into a containerd-based development cluster:

```bash
IMAGE=lerobot-so101:0.6.1 ./scripts/load_image_into_k8s.sh
```

Install the suspended Job:

```bash
./scripts/install_k8s_rollout.sh \
  --set hostPaths.policy=/path/to/policy \
  --set hostPaths.calibration=/path/to/calibration
```

Start it after checking the physical workspace:

```bash
./scripts/start_k8s_rollout.sh
```

## 🖥️ Offloaded inference

Enable transparent Xavier offloading during installation and startup:

```bash
./scripts/install_k8s_rollout.sh --offload \
  --set hostPaths.policy=/path/to/policy \
  --set hostPaths.calibration=/path/to/calibration

./scripts/start_k8s_rollout.sh --offload
```

Use `charts/lerobot-rollout/values.example.yaml` as a starting point. Adapt its image, policy,
calibration, serial-device, and camera values to the target host. The checked-in
file demonstrates a two-camera SO-101 configuration; it is not tied to a host
named Thor or to the devices and paths available on another system.

```bash
./scripts/install_k8s_rollout.sh \
  --values charts/lerobot-rollout/values.example.yaml

./scripts/start_k8s_rollout.sh \
  --values charts/lerobot-rollout/values.example.yaml
```

The chart adds the `xavier: "true"` opt-in label and an ACT `remote.yaml`
ConfigMap. Policy loading and `ACTPolicy.select_action` execute in the generated
server deployment. The application container retains robot and camera I/O.

With `offload.rawObservation.enabled=false`, the example runs LeRobot's pinned
upstream synchronous inference code without modifying it. The GPU offload runtime
transparently intercepts the configured policy load and
`ACTPolicy.select_action` calls, demonstrating that an existing application can
use GPU offload without source changes.

Validate policy loading and one synthetic action without opening robot devices:

```bash
./scripts/install_k8s_rollout.sh --offload \
  --set job.suspend=false \
  --set validation.enabled=true
```

Enable aggregated control-loop timing for a rollout:

```bash
./scripts/install_k8s_rollout.sh --offload \
  --set rollout.timing.enabled=true \
  --set rollout.timing.reportEvery=100
```

Timing summaries report mean, p50, p95, and maximum latency for camera access,
serial reads and writes, observation preparation, remote policy calls, action
dispatch, and related control-loop stages. Timing is disabled by default.

Move image conversion and the policy processor pipeline to the offload server:

```bash
./scripts/install_k8s_rollout.sh --offload \
  --set offload.rawObservation.enabled=true
```

This mode sends compact `uint8` camera tensors instead of normalized `float32`
tensors. The example-layer implementation in
`docker/raw_observation_inference.py` moves image preparation and the policy
processor pipeline to the server without changing the pinned LeRobot submodule.
It demonstrates an optional optimization that requires only a small integration
wrapper when transparent method offload does not provide enough throughput.
This mode applies only to synchronous inference and is disabled by default.

## 📊 Results

See [raw observation offload results](results/README.md) for real-hardware
`float32` and `uint8` timing tables, measurement methodology, and analysis.
