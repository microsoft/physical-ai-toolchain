---
title: CI Smoke Scripts
description: GPU-free import smoke scripts for training and evaluation domains, runnable locally and in CI.
author: Microsoft Robotics-AI Team
ms.date: 2026-09-23
---

GPU-free import smoke checks that catch syntax, import, dependency-resolution, and interpreter/ABI regressions before they reach a GPU job. The same scripts run in CI (`.github/workflows/smoke-cpu.yml`) and locally.

## 📋 Prerequisites

| Tool   | Required for                             | Install                               |
|--------|------------------------------------------|---------------------------------------|
| Docker | `smoke-image.sh` (any local host)        | <https://docs.docker.com/get-docker/> |
| uv     | `smoke-import.sh` direct on linux/x86_64 | `mise install uv`                     |
| bash   | All modes                                | Preinstalled on macOS and Linux       |

Run every command from the repository root.

## 🚀 Usage

The locks target linux/x86_64, so on macOS or any non-linux host run the smoke through Docker with `smoke-image.sh`. On a linux/x86_64 host (and in CI) the inner `smoke-import.sh` runs directly.

```bash
# Any host with Docker (macOS included)
shared/ci/smoke-image.sh rl --mode cpu           # CPU import smoke, lightweight container
shared/ci/smoke-image.sh il --mode cpu
shared/ci/smoke-image.sh vla --mode cpu
shared/ci/smoke-image.sh evaluation --mode cpu
shared/ci/smoke-image.sh vlm-judge --mode cpu
shared/ci/smoke-image.sh osmo-replay --mode cpu
shared/ci/smoke-image.sh rl                       # runtime-image smoke (Isaac Lab)
shared/ci/smoke-image.sh il                       # runtime-image smoke (PyTorch)
shared/ci/smoke-image.sh vla                      # runtime-image smoke (PyTorch)
shared/ci/smoke-image.sh evaluation               # runtime-image smoke (PyTorch)
shared/ci/smoke-image.sh osmo-replay               # runtime-image smoke (Python slim)

# linux/x86_64 host or CI — run the inner probe directly, no Docker
shared/ci/smoke-import.sh rl --mode cpu
```

`smoke-image.sh` mounts the repository at `/workspace` and runs `smoke-import.sh <domain> --mode <mode>` inside a linux/amd64 container: a lightweight uv image for `--mode cpu`, the domain's production image for `--mode image`. CI runs the CPU smoke directly on its linux runners and calls `smoke-image.sh` for the runtime-image depth after a free-disk-space step.

> [!NOTE]
> The runtime images are multi-gigabyte. The first `--mode image` run pulls the image; expect several minutes and ensure free disk.

## 📦 Scripts

| Script            | Purpose                                                                          |
|-------------------|----------------------------------------------------------------------------------|
| `smoke-import.sh` | Inner probe: install a domain's locked deps and import it; runs on linux/x86_64  |
| `smoke-image.sh`  | Run `smoke-import.sh` in a linux/amd64 container (`--mode cpu\|image`), any host |

## 🧪 Domains

| Domain        | Python | Runtime image                             | CPU smoke | Runtime-image smoke |
|---------------|--------|-------------------------------------------|-----------|---------------------|
| `rl`          | 3.12   | Isaac Lab 3.0 (`DEFAULT_ISAAC_LAB_IMAGE`) | yes       | yes                 |
| `il`          | 3.12   | PyTorch (`lerobot-train.yaml` default)    | yes       | yes                 |
| `vla`         | 3.12   | PyTorch (`DEFAULT_LEROBOT_TRAIN_IMAGE`)   | yes       | yes                 |
| `evaluation`  | 3.12   | PyTorch (`evaluate.yaml`)                 | yes       | yes                 |
| `vlm-judge`   | 3.12   | none                                      | yes       | no                  |
| `osmo-replay` | 3.11   | Python (`replay-azureml.yaml`)            | yes       | yes                 |

Image references come from their source of truth: `scripts/lib/common.sh` for `rl` and `vla`, `training/il/workflows/osmo/lerobot-train.yaml` for `il`, `evaluation/sil/workflows/azureml/components/evaluate.yaml` for `evaluation`, and `workflows/osmo/replay-azureml.yaml` for `osmo-replay`.

## 🔍 What each depth catches

CPU import smoke installs exported locked versions with CPU torch wheels and omits GPU-only packages. It catches import, installation, and interpreter-syntax errors, not the production CUDA wheel set.

The runtime-image smoke installs the committed lock exactly as production does and imports the domain on the real interpreter. It catches the interpreter and ABI-at-import class. It does not prove CUDA, Vulkan, MIG, or a real training loop.

## 🔧 CI integration

`.github/workflows/smoke-cpu.yml` runs the CPU import smoke for every domain on each pull request (unconditional baseline) and the runtime-image smoke path-gated to the changed training domain. The job feeds the single required `pr-validation-summary` check.

## 🏗️ Design

The rationale behind the gate's shape. Change these invariants only with equivalent reasoning.

### Two depths are complementary, not redundant

For one domain the runtime-image smoke is higher fidelity, yet it does not make the CPU import smoke redundant: the two install different wheel sets. The CPU smoke selects CPU torch wheels (`--torch-backend cpu`) from exported pinned versions; the runtime-image smoke installs the production CUDA lock on the real interpreter. A break can exist in one wheel set and not the other.

The CPU depth is also the cheap baseline that runs on every PR, while the runtime-image depth is multi-gigabyte and minutes long, so it is reserved for the changed domain.

### The CPU baseline is unconditional

> [!IMPORTANT]
> The `import-smoke` matrix runs for every domain on every PR with no path filter. The required summary rejects skipped selected mandatory jobs, including the always-selected `smoke` caller. Keep the CPU matrix unconditional so expensive runtime-image selection cannot remove the import baseline.
>
> Only the expensive runtime-image depth is path-gated, and those filters must fail open: when in doubt, run. Do not add an `if:` or path filter to `import-smoke`.

### Install preserves the committed resolution

The domain locks encode pyproject `override-dependencies` and package sources. The IL, VLA, and evaluation runtime-image smokes use frozen `uv sync`, preserving the explicit PyTorch CUDA index. Evaluation uses the IL runtime lock in `training/il/lerobot`; its CPU smoke uses the `evaluation` lock. The RL and OSMO replay runtime-image smokes export their locks and install with `--no-deps`. Both paths install the committed resolution rather than resolving dependencies again.

The import step is required: dependency or ABI skew can install cleanly and fail only when imported. For the CPU depth, the export removes the CUDA local-version suffix from torch and torchvision before `--torch-backend cpu` selects CPU wheels, and strips standalone `nvidia-*`, `cuda-*`, and `torchcodec` packages. Installation uses `--no-deps` to retain the exported versions.

When a runtime image lacks uv, `smoke-import.sh` downloads the pinned archive with at most three attempts.
Each transfer has a 35-second limit, with two-second pauses, so transfer recovery takes less than 120 seconds.
Only connection and timeout errors or HTTP 408, 429, and 5xx responses are retried.
A 404, checksum mismatch, invalid archive, install failure, or failed import stops the smoke immediately.
Run `bash shared/ci/tests/smoke-import-bootstrap.sh` to exercise the controlled transfer and archive fixtures locally.

### Per-domain runtime images and interpreters

Each image-enabled domain runs in its production runtime; there is no single image. Image references are read from their source of truth: `DEFAULT_ISAAC_LAB_IMAGE` and `DEFAULT_LEROBOT_TRAIN_IMAGE` in `scripts/lib/common.sh` for `rl` and `vla`, respectively, the `lerobot-train.yaml` default for `il`, the Azure ML `evaluate.yaml` component for `evaluation`, and the `replay-azureml.yaml` default for `osmo-replay`.

The LeRobot locks require Python 3.12, so the `il`, `vla`, and `evaluation` runtime-image smokes provision 3.12 in a venv rather than relying on the image's Python version; `rl` uses the Isaac Lab kit interpreter when available.

### Required-check wiring

The `smoke` job in `pr-validation.yml` calls the reusable `smoke-cpu.yml` workflow and is listed in `pr-validation-summary.needs`, the single required check. The canonical evaluator requires selected mandatory jobs to succeed; only jobs deselected by verified dependency selection may skip. The `smoke` caller is always selected, while its runtime-image jobs are gated by caller inputs. In `main.yml`, smoke runs unconditionally with all image inputs enabled, and `release-please` depends on its result.

### What the gate does not prove

- No GPU execution: CUDA, Vulkan, MIG, or a real training/inference loop.
- The RL probe loads the pip-package ABI surface (numpy, torch, skrl, and the Azure/MLflow stack via `training.utils`) but not Isaac's `omni`/`isaaclab` plugins, which are imported lazily in code and require the Isaac app. Isaac plugin-load regressions need GPU end-to-end coverage.
- VLA runtime-image smoke imports torch, TorchCodec, Transformers, the video decoder, and pi0 configuration without downloading gated model weights or running inference. It does not validate model access or GPU execution.
- `vlm-judge` has CPU import coverage only; it does not have a runtime-image smoke.

### Adding a domain

- Add the domain to the `import-smoke` matrix in `smoke-cpu.yml` and a `case` branch in `smoke-import.sh` (project directory, Python version, import probe).
- If it has a production runtime image, add an `image-smoke-<domain>` job gated on a caller input, wire that input from a `changes` path filter in `pr-validation.yml`, and select the image from its source of truth in `smoke-image.sh`.
- Do not add a path filter to the CPU baseline. Make any image-depth filter fail open.
