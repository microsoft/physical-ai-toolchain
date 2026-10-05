---
title: Dependabot Verification Suite
description: Category manifest, runner, and tests that verify Dependabot dependency updates on CPU and against a deployed Azure ML environment.
author: Microsoft Robotics-AI Team
ms.date: 2026-10-04
---

Every Dependabot update maps to a verification category. A category lists the checks that prove the update safe in two tiers: CPU checks that run on any workstation (and in CI), and environment checks that submit Azure ML jobs to a deployed environment, most of them on GPU nodes. One command selects the categories a branch touches and runs them.

For when to run which tier, costs, and known coverage gaps, see the [Dependabot verification guide](../../docs/contributing/dependabot-verification.md).

## 📋 Prerequisites

| Tool                | Needed for                                                                   |
|---------------------|------------------------------------------------------------------------------|
| uv and npm          | The runner and most CPU checks                                               |
| Docker              | Import smokes, Linux-only test environments, and the docs browser tests      |
| PowerShell 7        | Lint wrappers behind `npm run lint:*` and the Terraform checks               |
| Azure CLI (`az ml`) | Environment checks, after `az login` and connecting to the environment's VPN |

A check whose tool is missing reports `not-run` with the missing tool instead of failing.

## 🚀 Quick Start

Run from the repository root:

```bash
# See every category and check
npm run verify:dependabot -- --list

# Run the CPU checks for whatever this branch changed relative to origin/main
npm run verify:dependabot

# Preview, then run, one category
npm run verify:dependabot -- --category docs --dry-run
npm run verify:dependabot -- --category docs

# Preview, then run, the Azure ML checks for a category against a named environment
npm run verify:dependabot -- --tier environment --environment <environment> --category rl --dry-run
npm run verify:dependabot -- --tier environment --environment <environment> --category rl
```

Add `--include-optional` for heavy checks that CI already runs on path-gated pull requests, such as runtime-image smokes and the docs browser tests.

## 🧪 Categories

The `baseline` category runs with every selection. The others are selected by the Dependabot directory a change touches.

| Category         | Dependabot entries                                                                  | Environment checks                                          |
|------------------|-------------------------------------------------------------------------------------|-------------------------------------------------------------|
| `baseline`       | Every update                                                                        | None                                                        |
| `tooling`        | npm `/`, uv `/`, github-actions `/`                                                 | None                                                        |
| `dataviewer`     | npm `/` workspace, uv and docker under `data-management/viewer`                     | None                                                        |
| `docs`           | npm `/docs/docusaurus`                                                              | None                                                        |
| `data-pipeline`  | uv `/data-pipeline`                                                                 | None                                                        |
| `evaluation`     | uv `/evaluation`, `/evaluation/vlm_judge`                                           | None                                                        |
| `rl`             | uv `/training/rl`                                                                   | `aml-rl-lifecycle`                                          |
| `il`             | uv `/training/il/lerobot`                                                           | `aml-il-lifecycle`, `aml-il-pipeline-diffusion` (CPU steps) |
| `vla`            | uv `/training/vla/lerobot`                                                          | `aml-vla-pi0` (needs `HF_TOKEN`)                            |
| `gpu-smoke`      | uv `/training/smoke`                                                                | `aml-gpu-smoke`                                             |
| `workflows`      | uv `/workflows/azureml/scripts`, `/workflows/azureml/osmo-proxy`, `/workflows/osmo` | `aml-il-pipeline-register` (CPU steps)                      |
| `gpu-offload`    | uv and docker under `gpu-offload`                                                   | None on Azure ML (uses a local NVIDIA host)                 |
| `infrastructure` | terraform root, dns, vpn, automation; gomod `infrastructure/terraform/e2e`          | `terraform-plan-compare` (read-only, no GPU)                |

Environment checks run on GPU nodes unless marked otherwise. The IL pipeline steps use the compute's default instance type, which is CPU-only on Kubernetes computes.

[categories.json](categories.json) is the source of truth for every check, its command, and its prerequisites.

## ⚙️ Environment Selection

Environment checks never read environment details from tracked files. With `--environment <name>`, the runner sets `E2E_ENVIRONMENT`, and the e2e tests read the environment's non-secret `deployment.json` bundle from the first of these locations:

1. `E2E_ENVIRONMENT_BUNDLE_DIR`, or `--bundle-dir`
2. `~/.config/physical-ai-toolchain/environments/<name>`, where `download-environment-bundle.sh` stores it
3. `infrastructure/setup/generated/<name>`, which is gitignored and written by the environment-deployment workflow

The bundle fills `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZUREML_WORKSPACE_NAME`, `AZURE_STORAGE_ACCOUNT_NAME`, `AKS_CLUSTER_NAME`, and a derived `AZUREML_COMPUTE`. Variables you export yourself take precedence. With a named environment, the tests never consult local Terraform state, and a missing value fails the run. Set `E2E_AML_INSTANCE_TYPE` to target a specific GPU instance type for the GPU smoke and VLA checks.

Secrets never go in a bundle. Put `HF_TOKEN` for the VLA check in the untracked repository-root `.env.local`, starting from `.env.local.example`. The runner, the e2e tests, and the submission scripts read it from there or from your environment, and the runner passes it only to the check that declares it.

Before submitting anything, the runner checks that `az` is signed in to the environment's subscription. If the AKS cluster behind the compute target is stopped, the Azure ML checks report `not-run`; start the cluster and rerun. Environment checks run one at a time, and they upload your working tree to Azure ML, including uncommitted changes, so commit first and don't run builds at the same time.

Each environment check stops at its `timeout_minutes` limit. The runner interrupts it, waits up to 15 minutes for the test to cancel its jobs and archive its models, and reports it `failed`.

## 📊 Results

Each run writes `summary.json` and `summary.md`, plus one log per check, to `logs/dependabot/<run-id>/` (gitignored). The summary records the commit and whether the tree had uncommitted changes. Environment-tier logs contain Azure ML Studio links and resource names, so share only the `summary.md` status table, with resource names removed from its reasons.

| Status    | Meaning                                                                     |
|-----------|-----------------------------------------------------------------------------|
| `passed`  | The check ran and succeeded                                                 |
| `failed`  | The check ran and failed, timed out, or its category setup failed           |
| `not-run` | A tool, variable, or reachable environment was missing, or the test skipped |
| `skipped` | An optional check that wasn't requested                                     |

The runner exits 0 when every selected required check passed, 1 when any check failed, 2 when nothing failed but a required check didn't run, and 64 for a usage error.

## 🛠️ Adding a Check

1. Add the check to its category in [categories.json](categories.json): a `cpu` command, or an `environment` pytest node under `tests/e2e/test_e2e_aml_*`.
2. Add new environment checks to the allowlist in [test_categories.py](test_categories.py).
3. Run the suite's tests. They fail until every Dependabot entry is mapped and every referenced script, npm script, pytest node, and CI lane exists:

   ```bash
   uv run --frozen pytest -o addopts="" tests/dependabot
   ```

When Dependabot gains a directory, add it to a category's `dependabot` list and `paths` so routing selects it.
