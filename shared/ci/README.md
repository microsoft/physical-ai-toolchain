---
title: CI Smoke Scripts
description: GPU-free import smoke scripts for training and evaluation domains, runnable locally and in CI.
author: Microsoft Robotics-AI Team
ms.date: 2026-06-25
---

GPU-free import smoke checks that catch syntax, import, dependency-resolution, and interpreter/ABI regressions before they reach a GPU job. The same scripts run in CI (`.github/workflows/smoke-cpu.yml`) and locally.

## 📋 Prerequisites

| Tool   | Required for                             | Install                                            |
|--------|------------------------------------------|----------------------------------------------------|
| Docker | `smoke-image.sh` (any local host)        | <https://docs.docker.com/get-docker/>              |
| uv     | `smoke-import.sh` direct on linux/x86_64 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| bash   | All modes                                | Preinstalled on macOS and Linux                    |

Run every command from the repository root.

## 🚀 Usage

The locks target linux/x86_64, so on macOS or any non-linux host run the smoke through Docker with `smoke-image.sh`. On a linux/x86_64 host (and in CI) the inner `smoke-import.sh` runs directly.

```bash
# Any host with Docker (macOS included)
shared/ci/smoke-image.sh rl --mode cpu           # CPU import smoke, lightweight container
shared/ci/smoke-image.sh il --mode cpu
shared/ci/smoke-image.sh evaluation --mode cpu
shared/ci/smoke-image.sh rl                       # runtime-image smoke (Isaac Lab)
shared/ci/smoke-image.sh il                       # runtime-image smoke (PyTorch)

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

| Domain       | Python | Runtime image                           | CPU smoke | Runtime-image smoke |
|--------------|--------|-----------------------------------------|-----------|---------------------|
| `rl`         | 3.11   | Isaac Lab (`DEFAULT_ISAAC_LAB_IMAGE`)   | yes       | yes                 |
| `il`         | 3.12   | PyTorch (`lerobot-train.yaml` default)  | yes       | yes                 |
| `evaluation` | 3.12   | none (shares the Isaac Lab SiL runtime) | yes       | no                  |

Image references come from their source of truth: `scripts/lib/common.sh` for `rl`/`evaluation`, and `training/il/workflows/osmo/lerobot-train.yaml` for `il`.

## 🔍 What each depth catches

CPU import smoke installs CPU torch wheels, so it validates a different dependency graph than the production CUDA one. It catches import, resolution, and interpreter-syntax errors — not the production CUDA resolution.

The runtime-image smoke installs the committed lock exactly as production does and imports the domain on the real interpreter. It catches the interpreter and ABI-at-import class. It does not prove CUDA, Vulkan, MIG, or a real training loop.

## 🔧 CI integration

`.github/workflows/smoke-cpu.yml` runs the CPU import smoke for every domain on each pull request (unconditional baseline) and the runtime-image smoke path-gated to the changed training domain. The job feeds the single required `pr-validation-summary` check.

## 🏗️ Design

The rationale behind the gate's shape. Change these invariants only with equivalent reasoning.

### Two depths are complementary, not redundant

For one domain the runtime-image smoke is higher fidelity, yet it does not make the CPU import smoke redundant: the two install different dependency graphs. The CPU smoke resolves CPU torch wheels (`--torch-backend cpu`); the runtime-image smoke installs the production CUDA lock on the real interpreter. A break can exist in one graph and not the other.

The CPU depth is also the cheap baseline that runs on every PR, while the runtime-image depth is multi-gigabyte and minutes long, so it is reserved for the changed domain.

### The CPU baseline is unconditional — never path-gate it

> [!IMPORTANT]
> The `import-smoke` matrix runs for every domain on every PR with no path filter, by design. A path filter that returns the wrong answer skips the job, and a skipped required job counts as a pass — the gate goes green while testing nothing. That exact failure has shipped before (a broken path-filter regex; a folder restructure that stopped jobs from running).
>
> Only the expensive runtime-image depth is path-gated, and those filters must fail open: when in doubt, run. Do not add an `if:` or path filter to `import-smoke`.

### Install is `--no-deps`; the import is what catches drift

The domain locks encode pyproject `override-dependencies`. Re-resolving at install time discards those overrides and fails (for example an `azure-storage-blob` pin conflict), so the smoke installs the exact committed lock with `--no-deps`. The consequence is deliberate: dependency resolution does not run at install, so a dependency or ABI skew installs cleanly and is caught only by the subsequent import.

The import step is therefore load-bearing, not a formality — dropping it would leave the gate green-while-broken for the ABI class. For the CPU depth, `--torch-backend cpu` redirects the lock's CUDA-pinned torch to CPU wheels, and the standalone `nvidia-*`/`cuda-*` wheels are stripped (CPU torch needs none, and they are multi-gigabyte).

### Per-domain runtime images and interpreters

Each domain runs in its own production runtime; there is no single image. Image references are read from their source of truth, never hard-coded: `DEFAULT_ISAAC_LAB_IMAGE` in `scripts/lib/common.sh` for `rl`/`evaluation`, and the `lerobot-train.yaml` default for `il`. The LeRobot lock requires Python 3.12 while its PyTorch image ships 3.11, so the `il` runtime-image smoke provisions 3.12 in a venv, mirroring the production entry script; `rl`/`evaluation` use the Isaac Lab kit interpreter.

### Required-check wiring

The reusable `smoke` workflow is a caller in `pr-validation.yml` and is listed in `pr-validation-summary.needs`, the single required check. Skipped needed jobs count as a pass, so only the path-gated runtime-image jobs may ever skip — the CPU baseline always runs and always reports. `main.yml` has no aggregator or path-filter job, so the same caller is added there unconditionally.

### What the gate does not prove

- No GPU execution: CUDA, Vulkan, MIG, or a real training/inference loop.
- The RL probe loads the pip-package ABI surface (numpy, torch, skrl, and the Azure/MLflow stack via `training.utils`) but not Isaac's `omni`/`isaaclab` plugins, which are imported lazily in code and require the Isaac app. Isaac plugin-load regressions need GPU end-to-end coverage.
- Evaluation has no dedicated runtime image, so it gets the CPU import smoke only.

### Adding a domain

- Add the domain to the `import-smoke` matrix in `smoke-cpu.yml` and a `case` branch in `smoke-import.sh` (project directory, Python version, import probe).
- If it has a production runtime image, add an `image-smoke-<domain>` job gated on a caller input, wire that input from a `changes` path filter in `pr-validation.yml`, and select the image from its source of truth in `smoke-image.sh`.
- Do not add a path filter to the CPU baseline. Make any image-depth filter fail open.
