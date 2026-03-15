---
description: 'Required general instructions for entire codebase and project'
applyTo: '**'
---

# General Instructions

Conventions, domain knowledge, and non-obvious patterns for agents working in this repository. Items in HIGHEST PRIORITY sections override conflicting guidance.

## HIGHEST PRIORITY

**Breaking changes:** Do not add backward-compatibility layers or legacy support unless explicitly requested. Breaking changes are acceptable.

**Artifacts:** Do not create or modify tests, scripts, or one-off markdown docs unless explicitly requested.

**Comment policy:** Never include thought processes, step-by-step reasoning, or narrative comments in code.

* Keep comments brief and factual; describe **behavior/intent, invariants, edge cases**.
* Remove or update comments that contradict the current behavior. Do not restate obvious functionality.
* Do NOT add temporal or plan-phase markers (e.g. "Phase 1 cleanup", "... after migration", dates, or task references) to code files. When editing or updating any code files, always remove or replace these types of comments.

**Conventions and Styling:** Always follow conventions and styling in this codebase FIRST for all changes, edits, updates, and new files.

**Proactive fixes:** Always fix problems and errors you encounter, even if unrelated to the original request. Prefer root-cause, constructive fixes over symptom-only patches.

* Always correct all incorrect or problematic conventions, styling, and redundant and/or misleading comments.

**Edit tools:** Never use `insert_edit_into_file` tool when other edit and file modification tools are available.

## Repository Structure

| Directory | Purpose |
| --- | --- |
| `infrastructure/terraform/prerequisites/` | Azure subscription setup, provider registration |
| `infrastructure/terraform/` | Terraform infrastructure (AKS, networking, storage, identity) |
| `infrastructure/terraform/vpn/` | Point-to-site VPN for private cluster access |
| `infrastructure/setup/` | Post-deploy shell scripts (Helm charts, AzureML, OSMO) |
| `training/rl/` | RL training package (SKRL, RSL-RL, Isaac Lab) |
| `training/il/` | IL training package (LeRobot ACT/Diffusion) |
| `evaluation/sil/` | Software-in-the-loop evaluation scripts and workflows |
| `data-management/viewer/` | Dataset analysis tool (FastAPI backend + React frontend) |
| `data-pipeline/capture/` | Recording configuration and data capture |
| `shared/lib/` | Cross-domain shared shell libraries (canonical location) |
| `external/IsaacLab/` | NVIDIA IsaacLab (cloned for IntelliSense only, not built locally) |
| `docs/contributing/` | Architecture, roadmap, style guides, contribution workflow |

* Version: managed by release-please across `pyproject.toml` and `package.json`
* Python: >=3.11, managed by `uv` (not pip); `hatchling` builds `training/rl` into wheel
* Linting: `npm run lint:md` (markdownlint-cli2), `npm run spell-check` (cspell), `npm run lint:yaml` (yaml-lint)

## Terraform Conventions

* Boolean variable prefix: `should_` exclusively (NOT `enable_` or `is_`)
* `resource_group` variable type: `object({ id, name, location })` — never a string
* `variables.core.tf`: every module contains the SAME five core variables (`environment`, `resource_prefix`, `instance`, `resource_group`, optionally `location`)
* Resource naming: `{abbreviation}-{resource_prefix}-{environment}-{instance}` (e.g., `aks-nvidia-dev-001`)
* Standalone deployments (`vpn/`, `automation/`, `dns/`): use `data` sources to discover existing resources — no remote state references
* State management: local `.tfstate` files only (no remote backend)
* Resource conditionals: `should_*` boolean flags with `count` meta-argument
* Module file order: `main.tf`, `variables.tf`, `variables.core.tf`, `outputs.tf`, `versions.tf`
* Comment style: `/** */` file-level, `/* */` variable groups, `//` inline, `// ===` section separators
* Provider tracking: Microsoft partner ID `acce1e78-0375-4637-a593-86aa36dcfeac` in `versions.tf`

## Shell Script Conventions

Detailed template and structure in `.github/instructions/shell-scripts.instructions.md`.

* Two Terraform output libraries exist (do NOT mix them):
  * `shared/lib/common.sh`: dot-path accessors (`tf_get`, `tf_require`) for deploy and submission scripts
  * `shared/lib/terraform-outputs.sh`: jq-path accessor (`get_output`) for submission scripts (symlinked at `scripts/lib/terraform-outputs.sh`)
* `.env.local` load order: `common.sh` loads `.env.local` BEFORE `defaults.conf`; override defaults via `${VAR:-default}` pattern
* Idempotent K8s operations: `kubectl create --dry-run=client -o yaml | kubectl apply -f -`
* Every script supports `--config-preview` (print configuration and exit without changes)
* Every script ends with `section "Deployment Summary"` + `print_kv` calls
* `defaults.conf` is the central version and namespace configuration file for all deploy scripts

## Documentation Conventions

Detailed rules in `.github/instructions/docs-style-and-conventions.instructions.md`.

| Term | Use | Avoid |
| --- | --- | --- |
| Deploy | Provision infrastructure or install components | |
| Setup | Post-deploy configuration | |
| Cleanup | Remove components, keep infrastructure | |
| Destroy | Delete Azure infrastructure | Teardown |

## Deployment Pipeline

Four ordered deployment steps:

| Step | Directory | Description |
| --- | --- | --- |
| 1 | `infrastructure/terraform/prerequisites/` | Azure subscription init, provider registration |
| 2 | `infrastructure/terraform/` | Terraform infrastructure (AKS, networking, storage, identity) |
| 3 | `infrastructure/terraform/vpn/` | Point-to-site VPN (required for private clusters before any kubectl) |
| 4 | `infrastructure/setup/` | Helm charts, AzureML extension, OSMO control plane and backend |

* Default is private AKS — VPN step (3) is REQUIRED before any kubectl or Helm commands unless `should_enable_public_access = true`
* Three network modes: Full Private (default), Hybrid, Full Public
* Always run `source infrastructure/terraform/prerequisites/az-sub-init.sh` before any `terraform` or deploy script commands
  * Exports `ARM_SUBSCRIPTION_ID` and validates Azure CLI authentication
  * If the user has not done `az login`, the script requires interactive input
* Deploy scripts (`infrastructure/setup/`) must run in numeric order (01 → 02 → 03 → 04)
* Each deploy script is idempotent and safe to re-run

## OSMO Platform

OSMO is an external orchestration platform for multi-cluster Kubernetes workloads. Documentation and CLI source live in the adjacent `../OSMO/` repository.

* CLI pattern: `osmo <module> <command> [args]` — installed via native binary (curl/bash), NOT pip
* Dev login: `osmo login <url> --method dev --username guest`
* Workflow YAML uses Jinja templates (`{{ }}`) — NOT Helm Go templates
* Two payload strategies:
  * Base64-encoded archive: ~1MB limit, embedded in workflow YAML
  * Dataset folder injection: unlimited size, versioned, folder name in workflow env vars
* Config types: SERVICE, WORKFLOW, DATASET, BACKEND, POOL, POD_TEMPLATE, RESOURCE_VALIDATION, BACKEND_TEST, ROLE
* Apply config: `osmo config update <TYPE> [name] --file <path>`
* Namespace layout:
  * `osmo-control-plane` — service components
  * `osmo-operator` — backend operator
  * `osmo-workflows` — job execution pods
* KAI Scheduler with coscheduling (gang-scheduling for multi-GPU jobs)
* `oauth2Proxy.enabled: false` REQUIRED in Helm values when no OIDC provider is configured
* Prerelease mode: `OSMO_USE_PRERELEASE=true` switches both chart and image versions
* Service URL exposed via AzureML ingress controller internal load balancer

## AzureML Integration

AzureML runs on Arc-connected AKS clusters via the AzureML Kubernetes extension.

* Extension installed via `az k8s-extension create --extension-type Microsoft.AzureML.Kubernetes` (script-based, NOT Terraform managed)
* InstanceType CRDs define compute profiles: `defaultinstancetype`, `gpuspot`, `gpu`
* Job YAML schema: `$schema: .../commandJob.schema.json`
  * No empty strings in YAML values — use sentinel values (`auto`, `none`, `placeholder`)
  * Submit with runtime overrides: `az ml job create --file <yaml> --set "display_name=..." --set "environment_variables.KEY=value"`
* Code snapshot: each domain's workflow directory uploaded to AzureML via `code: .` relative path
* Identity chain: Terraform-created managed identity → federated credentials → K8s service accounts (`azureml:default`, `azureml:training`)
* Model validation mode: `mode: download` (NOT `ro_mount`) — workaround for workload identity auth failures in `data-capability` sidecar
* Multi-node: Volcano scheduler installed by AzureML extension when `installVolcano: true`
* Training submission scripts in `scripts/` use `scripts/lib/terraform-outputs.sh` to resolve infrastructure values

## Training Pipeline

Training runs in NVIDIA IsaacLab containers on GPU nodes via AzureML or OSMO.

* Container: `nvcr.io/nvidia/isaac-lab:2.3.2`
  * Python path: `/isaac-sim/kit/python/bin/python3` (NOT system Python)
  * `PYTHON` env var: set to `/workspace/isaaclab/isaaclab.sh -p` (wrapper activating correct conda env)
* EULA acceptance: all jobs MUST set `ACCEPT_EULA: "Y"` and `PRIVACY_CONSENT: "Y"`
* numpy: forcibly pinned to `>=1.26.0,<2.0.0` in `train.sh` for ABI compatibility with Isaac Sim
* Shutdown bug: Isaac Sim 4.x hangs after `env.close()` on vGPU nodes; fixed via `simulation_shutdown.py` with timeline stop + SIGKILL watchdog
* Vulkan: `NVIDIA_DRIVER_CAPABILITIES=all` required (Isaac Sim needs Vulkan for rendering)
* RL frameworks: SKRL (primary), RSL-RL (alternative)
* Behavioral cloning: LeRobot (ACT/Diffusion policies), runtime-installed via `uv pip` in AzureML container
* MLflow: monkey-patches `agent._update` for metric interception
  * Logging intervals: `step`, `balanced` (default, every 10 steps), `rollout`, or custom integer
* Checkpoint flow: training writes to local FS → `TRAINING_CHECKPOINT_OUTPUT` env var → AzureML uploads as `uri_folder`

## GPU Configuration

| GPU | Driver Source | MIG Strategy | Special Requirements |
| --- | --- | --- | --- |
| H100 | GPU Operator datacenter driver | Disabled | Standard |
| RTX PRO 6000 | Microsoft GRID DaemonSet (`580.105.08-grid-azure`) | `mig.strategy: single` (REQUIRED) | `nvidia.com/gpu.deploy.driver=false` node label |

* MIG strategy `single` is required for RTX PRO 6000: Azure vGPU host enables MIG, and `strategy: none` causes `CUDA_ERROR_NO_DEVICE` because `NVIDIA_VISIBLE_DEVICES` receives bare GPU UUIDs instead of MIG device UUIDs
* NVIDIA GPU Operator: driver deployment MUST be disabled on nodes with pre-installed Azure GRID drivers
* `NVIDIA_DRIVER_CAPABILITIES=all` required for all GPU workloads (Vulkan, compute, video)

## Validation

Run `npm install` (or `npm ci`) before any `npm run` lint commands. `shellcheck` must be installed separately (`brew install shellcheck` on macOS).

### Quick Reference

| File Type | Validation Commands |
| --- | --- |
| `*.md` | `npm run lint:md`, `npm run spell-check`, `npm run format:tables` |
| `*.tf`, `*.tfvars` | `terraform fmt -check`, `terraform validate`, `terraform plan` |
| `*.sh` | `shellcheck <file>` |
| `*.ps1` | `npm run lint:ps` |
| `*.yml` (GitHub Actions) | `npm run lint:yaml` |
| `data-management/viewer/frontend/**` | `cd data-management/viewer/frontend && npm run validate` (type-check + lint + test) |
| `data-management/viewer/backend/**` | `cd data-management/viewer/backend && pytest` and `ruff check src/` |
| Any file | `npm run spell-check` |

### Linting

* `npm run lint:all` runs `lint:md` + `lint:ps` + `lint:links` + `lint:yaml` in sequence
* `npm run spell-check` and `npm run format:tables` are NOT included in `lint:all` — run them separately
* `npm run lint:md:fix` and `npm run format:tables` auto-fix markdown issues
* `.copilot-tracking/` is excluded from markdown linting via `.markdownlint-cli2.jsonc`

### Terraform

Terraform validation is per-directory — each deployment directory has its own provider configuration and state:

* `terraform fmt -check -recursive infrastructure/terraform/` — formatting compliance (recursive across all directories)
* `terraform validate` — run inside each deployment directory individually:
  * `infrastructure/terraform/`
  * `infrastructure/terraform/vpn/`
  * `infrastructure/terraform/dns/`
  * `infrastructure/terraform/automation/`
* `terraform plan -var-file=terraform.tfvars` — validates configuration against provider APIs (requires `source infrastructure/terraform/prerequisites/az-sub-init.sh` first)

### Shell Scripts

* `shellcheck infrastructure/setup/*.sh training/**/*.sh evaluation/**/*.sh` — static analysis for deploy and submission scripts
* Deploy scripts (`infrastructure/setup/`) support `--config-preview` — prints configuration and exits without making changes; use for dry-run validation after modifying any deploy script

### Pester Tests

* `npm run test:ps` — runs Pester tests in `shared/ci/tests/` covering linting helpers and security checks

## Contributing References

| Document | Content |
| --- | --- |
| `docs/contributing/architecture.md` | Current and future architecture (hub-spoke, multi-node, 8 lifecycle domains) |
| `docs/contributing/ROADMAP.md` | Migration phases from monolithic to multi-node (Q2-Q3 2026) |
| `docs/contributing/infrastructure-style.md` | Terraform naming, modules, commenting (NOTE: boolean prefix guidance is outdated; use `should_` per this file) |
| `docs/contributing/contribution-workflow.md` | Branch naming, PR process, review checklist |
| `docs/contributing/prerequisites.md` | Required tools and versions |
| `docs/contributing/deployment-validation.md` | Post-deployment verification steps |
| `docs/contributing/cost-considerations.md` | Azure resource cost guidance |
| `docs/contributing/security-review.md` | Security review checklist |
| `docs/gpu-configuration.md` | Detailed GPU driver and operator configuration |
| `docs/mlflow-integration.md` | MLflow tracking and experiment management |
