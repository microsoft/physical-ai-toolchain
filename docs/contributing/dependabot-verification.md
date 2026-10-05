---
sidebar_position: 12
title: Verifying Dependabot Updates
description: Verification categories, CPU and GPU tiers, and the commands that prove a Dependabot pull request safe without exposing a deployment environment
author: Microsoft Robotics-AI Team
ms.date: 2026-10-04
ms.topic: how-to
keywords:
  - dependabot
  - dependency-updates
  - verification
  - azure-ml
---

Every Dependabot pull request maps to one or more verification categories. Each category lists the checks that prove the update safe, split into two tiers. CPU checks run on any workstation with Docker, and CI runs most of the same checks. Environment checks submit Azure ML jobs to a deployed environment, most of them on GPU nodes, and never use OSMO workflows. The suite lives in [tests/dependabot](pathname://../../tests/dependabot/README.md), and one command runs it.

## How Verification Works

| Tier        | Where it runs                                              | What it proves                                                                                | Command                                                                       |
|-------------|------------------------------------------------------------|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| CPU         | Any workstation with Docker; CI runs most of these checks  | Locks match manifests, packages install and import, builds and unit tests pass, browser tests | `npm run verify:dependabot`                                                   |
| Environment | A deployed environment with Azure ML GPU compute, over VPN | Runtimes work on real GPU nodes with Azure identity, MLflow, storage, and the model registry  | `npm run verify:dependabot -- --tier environment --environment <environment>` |

The runner selects categories from the files a branch changed relative to `origin/main`, and always adds the `baseline` category. Pass `--category <name>` to pick categories yourself, and `--dry-run` to see exactly what would run.

## Categories

The [category manifest](pathname://../../tests/dependabot/categories.json) maps all 29 entries in `.github/dependabot.yml` to these categories:

| Category         | Dependabot entries                                                                  | CPU checks                                                               | Environment checks                               |
|------------------|-------------------------------------------------------------------------------------|--------------------------------------------------------------------------|--------------------------------------------------|
| `baseline`       | Every update                                                                        | uv and npm lock consistency, public feeds, dependency pinning            | None                                             |
| `tooling`        | npm `/`, uv `/`, github-actions `/`                                                 | Markdown, spelling, YAML, workflow contract, Python lint, root tests     | None                                             |
| `dataviewer`     | npm `/` workspace, uv and docker under `data-management/viewer`                     | Frontend lint, type-check, format, tests, coverage, build; backend tests | None                                             |
| `docs`           | npm `/docs/docusaurus`                                                              | Docs validation; production browser tests in a container (optional)      | None                                             |
| `data-pipeline`  | uv `/data-pipeline`                                                                 | Capture tests in the project's own lock                                  | None                                             |
| `evaluation`     | uv `/evaluation`, `/evaluation/vlm_judge`                                           | Evaluation tests, CPU import smokes                                      | None                                             |
| `rl`             | uv `/training/rl`                                                                   | Training tests, import smoke                                             | Azure ML RL lifecycle                            |
| `il`             | uv `/training/il/lerobot`                                                           | Training and inference tests, import smoke                               | Azure ML IL lifecycle and IL pipeline            |
| `vla`            | uv `/training/vla/lerobot`                                                          | Training tests, import smoke                                             | Azure ML pi0 lifecycle                           |
| `gpu-smoke`      | uv `/training/smoke`                                                                | Training tests, import smoke                                             | Azure ML GPU smoke                               |
| `workflows`      | uv `/workflows/azureml/scripts`, `/workflows/azureml/osmo-proxy`, `/workflows/osmo` | Import smokes, e2e helper tests                                          | Azure ML IL pipeline with the register component |
| `gpu-offload`    | uv and docker under `gpu-offload`                                                   | Controller and runtime tests, wheel build                                | None on Azure ML                                 |
| `infrastructure` | terraform root, dns, vpn, automation; gomod `infrastructure/terraform/e2e`          | Terraform validate, test, TFLint, Go contract tests                      | Read-only plan comparison against deployed state |

Run `npm run verify:dependabot -- --list` for every check, its command, and its notes.

## Which Categories Need a GPU

Four categories need GPU verification, because their locks load in GPU training or evaluation jobs: `rl`, `il`, `vla`, and `gpu-smoke`. CI has no GPU nodes, so these run as Azure ML jobs in a deployed environment. Each job installs the lock from your checkout, trains briefly, and checks the result. The RL and IL lifecycles also register a model and run the matching evaluation.

`workflows` needs the environment but not a GPU. Its register component runs as the last step of an Azure ML pipeline and writes to the model registry, which CI can't reach. Both IL pipeline checks run on the compute's default instance type, which is CPU-only on Kubernetes computes.

The other categories verify on CPU. The `evaluation` lock has no runtime consumer of its own, because GPU evaluation jobs install the training locks, which the `rl` and `il` lifecycles already cover. The `infrastructure` environment check reads deployed Terraform state but runs no job and needs no GPU.

`gpu-offload` does use a GPU, but on a local NVIDIA host rather than Azure ML. Run its `c-cluster-31-gpu-check`, `d-offload-61-check-gpu-allocation`, and `d-offload-62-check-gpu-model` mise tasks there.

## Run the CPU Tier

```bash
# Categories touched by this branch, plus the baseline
npm run verify:dependabot

# One category, after previewing it
npm run verify:dependabot -- --category dataviewer --dry-run
npm run verify:dependabot -- --category dataviewer
```

Linux-only locks install inside a `linux/amd64` container when you run on macOS, so keep Docker running. Heavy checks that CI already runs on path-gated pull requests, such as runtime-image smokes, the dataviewer compose smoke, and the docs browser tests, are optional; add `--include-optional` to run them.

The docs browser tests run in a Playwright container on a clean snapshot, and one snapshot takes about an hour on Apple Silicon because system Chrome needs amd64 emulation. If they fail, the runner reruns the base ref and reports only new failures, because local contrast measurements can differ from CI.

## Run the Environment Tier

Environment checks read the environment from its non-secret bundle, never from tracked files:

1. Get the bundle. Run `infrastructure/setup/download-environment-bundle.sh --environment <environment> --resource-group <resource-group>`, which stores it in `~/.config/physical-ai-toolchain/environments/<environment>`, or use the gitignored `infrastructure/setup/generated/<environment>` directory written by the deployment workflow. Never commit a bundle.
2. Connect to the environment's VPN and run `az login`. Optionally run `infrastructure/setup/connect-environment.sh --environment <environment>` to configure kubectl and the OSMO CLI.
3. Commit your changes. Azure ML jobs upload your working tree, including uncommitted edits, and the run summary records whether the tree was clean.
4. Preview the jobs, then submit them:

   ```bash
   npm run verify:dependabot -- --tier environment --environment <environment> --category rl --dry-run
   npm run verify:dependabot -- --tier environment --environment <environment> --category rl
   ```

Before submitting, the runner confirms that the Azure CLI is signed in to the bundle's subscription. If the AKS cluster behind the compute target is stopped, for example after a nightly shutdown, the Azure ML checks report `not-run` instead of waiting for nodes; start the cluster and rerun.

Jobs run one at a time with unique names, and their test models are archived afterward. Each job holds a GPU node for several minutes or more, so run only the categories a pull request touches. Don't run local builds while a job uploads its snapshot.

Each GPU check uses its submission script's default GPU instance type unless you export `E2E_AML_INSTANCE_TYPE`, or set it before the command; `.env.local` doesn't set it. For the pi0 check, the value applies to both the training and evaluation jobs. Training updates only pi0's action expert, but the gated backbone still has to fit, so if the default instance type can land on a GPU that's too small, choose a larger one.

On managed AmlCompute clusters, where the cluster's VM size decides, export `E2E_AML_INSTANCE_TYPE` as an empty value so the pi0 jobs omit the instance type. The GPU smoke always needs a named instance type.

The pi0 check needs a Hugging Face token that can read the gated base model. Add `HF_TOKEN=<token>` to the untracked repository-root `.env.local`. The runner, the e2e tests, and the submission scripts read it from there, and a value in `.env.local` takes precedence over an exported one. The [VLA training README](pathname://../../training/vla/README.md) explains how to create the token.

Every submission script loads `.env.local`, and the LeRobot evaluation script passes the token to every evaluation job, not only pi0, as an environment variable that anyone who can read those jobs can see. Use a fine-grained token that can only read that model.

Each environment check has a time limit, set by `timeout_minutes` in the manifest, so a hung Azure call can't stall the run. When a check runs past its limit, the runner interrupts it and gives the test up to 15 minutes to cancel its jobs and archive its models before stopping it. The check then reports `failed` with a `timed out` reason.

The read-only Terraform comparison plans the root stack at the base and head refs against your local state and reports only resource addresses, actions, and changed attribute names. It never applies. The vpn, automation, and dns stacks are opt-in.

## Read the Results

Each run writes `summary.json`, `summary.md`, and one log per check to `logs/dependabot/<run-id>/`, which is gitignored. Environment-tier logs include Azure ML Studio links and resource names, and `summary.json` records the environment name. To share results in a pull request or issue, copy the status table from `summary.md` and remove any resource names from its reasons.

| Status    | Meaning                                                                         |
|-----------|---------------------------------------------------------------------------------|
| `passed`  | The check ran and succeeded                                                     |
| `failed`  | The check ran and failed, timed out, or its category setup failed               |
| `not-run` | A tool, variable, or reachable environment was missing, or the test was skipped |
| `skipped` | An optional check that wasn't requested                                         |

The command exits 0 when every selected required check passed, 1 when any check failed, 2 when nothing failed but a required check didn't run, and 64 for a usage error. A skipped live test never counts as a pass.

## Known Gaps

| Area                                   | Coverage today                                                                                                      |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| OSMO replay mirror (`/workflows/osmo`) | CPU and runtime-image import smokes; it runs only inside OSMO, so there's no Azure ML check                         |
| RL `rsl_rl` backend                    | Install and import only; the `rsl_rl` backend has no Azure ML path today                                            |
| Azure ML-to-OSMO proxy                 | Opt-in check; it drives OSMO and fails where the OSMO control template requests more storage than its workflow does |
| GitHub Actions bumps                   | Syntax and contract checks locally; runtime behavior is proven only by CI on a pushed branch                        |
| GPU offload                            | Local NVIDIA host through mise tasks; the SO-101 example needs the robot hardware                                   |
| Dependency pinning                     | The scan runs as CI runs it, which currently doesn't enforce the compliance threshold                               |
| IL pipeline checks                     | Can fail before they start when Azure ML pipeline creation times out; see the note below                            |

The IL pipeline checks, `aml-il-pipeline-register` and `aml-il-pipeline-diffusion`, can fail before they start. LeRobot pipeline creation can time out at the Azure ML gateway, which leaves the pipeline `NotStarted` with no child jobs until the check's 15-minute start timeout. A `GatewayTimeout` on the pipeline's `jobs/write` in the workspace Activity Log confirms it.

When this happens, no pipeline step runs, so the failure says nothing about the Dependabot update: `aml-il-lifecycle` and the `il` CPU checks still cover the LeRobot lock, and `azureml-register-import-smoke` covers the register lock. A fix is tracked as follow-up work.

CI doesn't yet run the suite's own tests, the root `tests/` unit tests, or the `gpu-smoke`, `azureml-register`, and `osmo-proxy` import smokes. Run them locally until CI adopts them.

## Add a Category or Check

1. Edit [categories.json](pathname://../../tests/dependabot/categories.json). Add a `cpu` command, or an `environment` pytest node from a `tests/e2e/test_e2e_aml_*` module. Give every environment check a positive `timeout_minutes`, which the consistency tests require. Size it above the test's own start, completion, and cleanup deadlines; no test checks the sizing.
2. Add any new environment check to the allowlist in [test_categories.py](pathname://../../tests/dependabot/test_categories.py).
3. When Dependabot gains a directory, add it to a category's `dependabot` list and `paths`.
4. Run `uv run --frozen pytest -o addopts="" tests/dependabot`. The consistency tests fail until every Dependabot entry is mapped and every referenced script, npm script, pytest node, and CI lane exists.

## Related Documentation

* [Updating External Components](component-updates.md) - Dependabot configuration, the advisory reviewer, and manual updates
* [Deployment Validation](deployment-validation.md) - Validation levels for infrastructure and workflow changes
* [Cost Considerations](cost-considerations.md) - GPU and environment costs
* [shared/ci smoke scripts](pathname://../../shared/ci/README.md) - Import smoke domains and depths

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
