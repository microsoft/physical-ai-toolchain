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
| `scripts/` | CI/CD scripts, shared libraries, linting, security, and Pester tests |
| `scripts/lib/` | Cross-domain shared shell and PowerShell libraries |
| `external/IsaacLab/` | NVIDIA IsaacLab (cloned for IntelliSense only, not built locally) |
| `docs/contributing/` | Architecture, roadmap, style guides, contribution workflow |

* Do not modify files in `external/`
* Version: managed by release-please across `pyproject.toml` and `package.json`
* Python: >=3.12, managed by `uv` (not pip); `hatchling` builds `training/rl` into wheel
* Linting: `npm run lint:md` (markdownlint-cli2), `npm run spell-check` (cspell), `npm run lint:yaml` (yaml-lint)

## Terraform Conventions

* Boolean variable prefix: `should_` exclusively (NOT `enable_` or `is_`)
* `resource_group` variable type: `object({ id, name, location })` — never a string
* `variables.core.tf`: every module contains the SAME five core variables (`environment`, `resource_prefix`, `instance`, `resource_group`, optionally `location`)
* `variables.deps.tf`: typed object dependencies from other modules (used in `modules/sil/`, `modules/dataviewer/`)
* Root deployments do NOT have `variables.core.tf`; core variables live in `variables.tf`
* Resource naming: `{abbreviation}-{resource_prefix}-{environment}-{instance}` (e.g., `aks-nvidia-dev-001`)
* No-hyphen naming for Key Vault (`kv`), Storage (`st`), ACR (`acr`): `kv{prefix}{env}{instance}`
* Standalone deployments (`vpn/`, `automation/`, `dns/`): use `data` sources to discover existing resources — no remote state references
* State management: local `.tfstate` files only (no remote backend)
* Resource conditionals: `should_*` boolean flags with `count` meta-argument
* Module file order: `main.tf`, `variables.tf`, `variables.core.tf`, `outputs.tf`, `versions.tf`
* Comment style: `/** */` file-level, `/* */` variable groups, `//` inline, `// ===` section separators
* Provider: Microsoft partner ID `acce1e78-0375-4637-a593-86aa36dcfeac` in `versions.tf`; `required_version = ">= 1.9.8, < 2.0"`

```hcl
# Resource naming
locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"
}

# Boolean variable convention
variable "should_deploy_postgresql" { type = bool; default = true }
```

## Shell Script Conventions

Detailed template and structure in `.github/instructions/shell-scripts.instructions.md`.

* Two Terraform output libraries exist (do NOT mix them):
  * `scripts/lib/common.sh`: dot-path accessors (`tf_get`, `tf_require`) for deploy and submission scripts
  * `scripts/lib/terraform-outputs.sh`: jq-path accessor (`get_output`) for submission scripts
* `.env.local` load order: `common.sh` loads `.env.local` BEFORE `defaults.conf`; override defaults via `${VAR:-default}` pattern
* Idempotent K8s operations: `kubectl create --dry-run=client -o yaml | kubectl apply -f -`
* Every script supports `--config-preview` (print configuration and exit without changes)
* Every script ends with `section "Deployment Summary"` + `print_kv` calls
* `defaults.conf` is the central version and namespace configuration file for all deploy scripts

### Library Functions (`scripts/lib/common.sh`)

| Function | Purpose |
| --- | --- |
| `info`, `warn`, `error`, `fatal` | Colored logging (fatal exits) |
| `section "Title"` | Print section header |
| `print_kv "Key" "$val"` | Print key-value pair |
| `require_tools tool1 tool2` | Validate CLI tools exist |
| `tf_get "$json" "path" "default"` | Extract optional Terraform output |
| `tf_require "$json" "path" "desc"` | Extract required Terraform output |
| `connect_aks "$rg" "$cluster"` | Get AKS credentials |
| `ensure_namespace "$ns"` | Create namespace idempotently |

## Python Conventions

* Package management: `uv` (not pip); `hatchling` builds; Python >=3.12
* Child configs extend root ruff config: `extend = "../../pyproject.toml"`
* `from __future__ import annotations` required as the first import in every module

### Import Ordering

```python
from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from training.rl.scripts.skrl_mlflow_agent import create_mlflow_logging_wrapper

if TYPE_CHECKING:
    from azure.storage.blob import BlobServiceClient
```

stdlib → third-party → first-party (blank-line separated). `collections.abc` over `typing` for `Iterator`, `Sequence`, `Callable`. `TYPE_CHECKING` guard for heavy optional imports.

### Naming

| Pattern | Convention | Examples |
| --- | --- | --- |
| Classes | PascalCase | `AzureMLContext`, `StorageError` |
| Enums | PascalCase StrEnum | `TaskCompletenessRating(StrEnum)` |
| Public functions | snake_case | `load_metadata()`, `prepare_for_shutdown()` |
| Private functions | _snake_case | `_parse_mlflow_log_interval()` |
| Module constants (private) | _UPPER_SNAKE | `_LOGGER`, `_DEFAULT_MLFLOW_INTERVAL` |
| Module constants (public) | UPPER_SNAKE | `NUM_JOINTS`, `CONTROL_HZ` |

### Type Annotations

All functions (public and private) have full parameter + return annotations. Local variables are NOT annotated.

```python
# Built-in generics (not typing.List, typing.Dict)
list[str], dict[str, int], tuple[int, int]

# Union with pipe (not Optional)
str | None, Path | None

# Constrained types
Annotated[float, Field(gt=0)]
Literal["local", "azure"]
```

### Logging

| Domain | Variable | Logger Name |
| --- | --- | --- |
| Training/RL/Eval | `_LOGGER` | Custom domain (`"isaaclab.skrl"`) |
| Backend/Dataviewer | `logger` | `__name__` |

Always %-style formatting: `_LOGGER.warning("Invalid %s, using default (%d)", arg, default)`. Never f-strings in log calls.

### Error Handling

* Domain-specific exceptions: `AzureConfigError(RuntimeError)`, `StorageError(Exception)`
* API errors: `raise HTTPException(status_code=404, detail="Dataset not found")`
* Required env vars: `require_env("AZURE_SUBSCRIPTION_ID")` (raises `RuntimeError`)
* Optional deps: `try: import pyarrow ... except ImportError: PARQUET_AVAILABLE = False`

### FastAPI Architecture

* Layer flow: `routers/` → `services/` → `storage/` (ABC adapter pattern)
* Singletons: module-level variable + factory (`_dataset_service` + `get_dataset_service()`)
* Input validation: `Depends()` factories (`path_string_param()`, `query_string_param()`)
* Auth: global `Depends(require_auth)` on router-level; CSRF via `Depends(require_csrf_token)` on mutations
* Input sanitization: CR/LF stripping, null byte rejection, path traversal prevention

### Ruff Configuration

```toml
target-version = "py312"
line-length = 120
select = ["E", "W", "F", "I", "UP", "B", "SIM", "RUF"]
quote-style = "double"
```

## React/TypeScript Conventions

Detailed rules in `.github/instructions/dataviewer.instructions.md`.

* Stack: Vite 8, React 19, TypeScript ~6.0, Tailwind CSS v4 + shadcn/ui, Zustand v5, TanStack Query v5

### File Naming

| Category | Convention | Examples |
| --- | --- | --- |
| Components | PascalCase `.tsx` | `TrajectoryPlot.tsx`, `CameraSelector.tsx` |
| Stores | kebab-case `.ts` | `annotation-store.ts`, `edit-store.ts` |
| Hooks | kebab-case `.ts` | `use-datasets.ts`, `use-annotations.ts` |
| Types | kebab-case `.ts` | `annotations.ts`, `api.ts` |
| UI primitives | kebab-case `.tsx` | `button.tsx`, `dialog.tsx` |
| Barrels | `index.ts` | Every feature folder |

### Component Patterns

* Named exports only (no `export default`)
* `memo` for expensive renders
* `ref` as a prop for shadcn/ui primitives (React 19 ref-as-prop pattern)
* Props interfaces defined in-file above the component

### TypeScript

* `interface` for object shapes (props, state, API types)
* `type` for unions, aliases, literals, store intersections
* `@/` path alias → `./src/*`; strict mode enabled
* `export type` in barrel files for type-only re-exports

### State Management

* Zustand for client state (devtools middleware, separate selectors files)
* TanStack Query for server state (query key factory pattern)
* Hybrid sync: query hooks fetch → `useEffect` syncs to Zustand stores

### Styling

* Tailwind CSS v4 utility-first + `cn()` utility (`clsx` + `tailwind-merge`)
* CVA (class-variance-authority) for component variants
* No CSS modules, no styled-components

### API Client

* Raw `fetch` in `src/lib/api-client.ts` (no axios)
* CSRF token caching + `X-CSRF-Token` header on mutations
* MSAL auth via `getAuthHeaders()`
* Automatic `snakeToCamel` key transformation on responses

### ESLint/Prettier

* ESLint flat config: `simple-import-sort` (error), `jsx-a11y`, `@tanstack/query`
* Prettier: no semicolons, single quotes, trailing commas, 100 char width, Tailwind plugin

## Testing Patterns

Tests always test behaviors. Mocks reserved for external dependencies (Azure SDK, MLflow, YOLO).

### Python Tests

* File naming: `test_*.py`
* Class-based grouping by feature (`class TestDatasetDiscovery:`)
* Patching: `monkeypatch` (not `unittest.mock.patch` decorator)
* Async: `asyncio_mode = "auto"` (no per-test decorator)
* Fixtures: session-scoped for expensive setup; function-scoped for isolation
* Heavy deps: `sys.modules` injection for Azure/MLflow stubs
* Singletons: reset module-level `_service = None` between tests
* Coverage: `--cov=training --cov-report=term-missing --cov-report=xml`

### TypeScript Tests

* Framework: Vitest + `@testing-library/react` + `jest-dom`
* File naming: `*.test.ts` (logic), `*.test.tsx` (React)
* Module mocking: `vi.mock('@/path')` with `vi.hoisted()`
* Store tests: direct `getState()` calls, `reset()` in `beforeEach`
* Component tests: `render()` + `screen` queries + `userEvent`
* Hook tests: `renderHook()` from `@testing-library/react`
* Cleanup: `vi.restoreAllMocks()` in `afterEach`

### PowerShell Tests

* Framework: Pester 5; file naming: `*.Tests.ps1`
* Structure: `Describe`/`Context`/`It` with tags (`Unit`, `Integration`)
* Mocking: `Mock -ModuleName` + `-ParameterFilter`

## Documentation Conventions

Detailed rules in `.github/instructions/docs-style-and-conventions.instructions.md`.

| Term | Use | Avoid |
| --- | --- | --- |
| Deploy | Provision infrastructure or install components | |
| Setup | Post-deploy configuration | |
| Cleanup | Remove components, keep infrastructure | |
| Destroy | Delete Azure infrastructure | Teardown |

* Voice: direct, technical, imperative. No hedging, no conversational filler.
* H2 in README.md files: prefix with emoji (`## 📋 Prerequisites`, `## 🚀 Quick Start`)
* Alerts: GitHub-flavored `> [!NOTE]`, `> [!WARNING]` — NOT legacy `> **Note**:`
* Structured data: use tables, not bold-prefix list items
* Avoid H4+ headings; restructure instead
* Numbered lists only for sequential content
* Code blocks: always specify language

## Coding Agent Environment

GitHub Copilot Coding Agent runs in a cloud GitHub Actions environment, separate from the local devcontainer. The `.github/workflows/copilot-setup-steps.yml` workflow pre-installs tools so the cloud agent can author code, run linters, and execute tests with the same capabilities a local contributor has in `.devcontainer/devcontainer.json`.

The cloud-agent workflow does NOT install: `actionlint` (devcontainer-only, used for `npm run lint:yaml`), `golangci-lint`, `terraform-docs`, `osmo`, `ngc`, Azure CLI, kubectl, helm, k9s. These are Azure-deployment or local-validation tools the agent does not need to author or test code.

### Environment Synchronization

Treat `.github/workflows/copilot-setup-steps.yml` and `.devcontainer/devcontainer.json` as paired environments. When changing toolchain versions in either file, evaluate whether the other needs the same change:

* Language runtimes (Python, Node, Go, Terraform) MUST stay aligned — drift causes "works locally, fails in agent" bugs.
* Test runners (Pester, pytest, vitest) MUST stay aligned for the same reason.
* Azure-deployment tools (`az`, `kubectl`, `helm`, OSMO, NGC) live in the devcontainer only.
* Lint-only tools may live in either or both depending on whether the agent invokes the linter.

The weekly `copilot-setup-steps.yml` cron and `Test-BinaryFreshness.ps1` weekly run together surface upstream drift across both surfaces.

## Git Workflow

Full specification in `.github/instructions/commit-message.instructions.md`.

* Conventional commits: `type(scope): description` (<100 bytes subject line)
* Types: `feat`, `fix`, `refactor`, `perf`, `style`, `test`, `docs`, `build`, `ops`, `chore`, `security`
* Scopes: `(infrastructure)`, `(pipeline)`, `(data)`, `(sdg)`, `(training)`, `(evaluation)`, `(deployment)`, `(intelligence)`, `(scripts)`, `(docs)`, `(agents)`, `(prompts)`, `(instructions)`, `(skills)`, `(templates)`, `(adrs)`, `(settings)`, `(build)`
* Body: 0-5 bulleted items, <300 bytes total
* Footer: always ends with emoji + `- Generated by Copilot`

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
* Training submission scripts use `scripts/lib/terraform-outputs.sh` to resolve infrastructure values

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
| `*.tf`, `*.tfvars` | `npm run lint:tf`, `npm run lint:tf:validate`, `terraform plan` |
| `*.tftest.hcl` | `npm run test:tf`, `cd infrastructure/terraform/modules/<name> && terraform test` or `cd infrastructure/terraform && terraform test` |
| `*.go` | `npm run lint:go` (golangci-lint), `npm run test:go` (`go test`) |
| `*.sh` | `shellcheck <file>` |
| `*.ps1` | `npm run lint:ps` |
| `*.yml` (GitHub Actions) | `npm run lint:yaml` |
| `data-management/viewer/frontend/**` | `cd data-management/viewer/frontend && npm run validate` (type-check + lint + test) |
| `data-management/viewer/backend/**` | `cd data-management/viewer/backend && pytest` and `ruff check src/` |
| `training/**/*.py` | `cd training && ruff check . && pytest` |
| `evaluation/**/*.py` | `cd evaluation && ruff check . && pytest` |
| `data-pipeline/**/*.py` | `cd data-pipeline && ruff check .` |
| Any file | `npm run spell-check` |

### Linting

* `npm run lint:all` runs `lint:md` + `lint:ps` + `lint:links` + `lint:yaml` + `lint:tf` + `lint:go` in sequence
* `npm run spell-check` and `npm run format:tables` are NOT included in `lint:all` — run them separately
* `npm run lint:md:fix` and `npm run format:tables` auto-fix markdown issues
* `.copilot-tracking/` is excluded from markdown linting via `.markdownlint-cli2.jsonc`

### Terraform

Terraform validation is per-directory — each deployment directory has its own provider configuration and state:

* `npm run lint:tf` — TFLint recursive linting across all directories
* `npm run lint:tf:validate` — `terraform fmt -check -recursive` + `terraform init -backend=false && terraform validate` per deployment directory (`.`, `vpn/`, `dns/`, `automation/`)
* `terraform plan -var-file=terraform.tfvars` — validates configuration against provider APIs (requires `source infrastructure/terraform/prerequisites/az-sub-init.sh` first)
* CI: `.github/workflows/terraform-validation.yml` reusable workflow runs `lint:tf:validate` with `soft-fail: true`
* `npm run test:tf` — `terraform test` across all modules with `tests/` directories; uses `mock_provider` and `command = plan` — no Azure credentials required
* Per-module: `cd infrastructure/terraform/modules/<name> && terraform init -backend=false && terraform test`
* CI: `.github/workflows/terraform-tests.yml` reusable workflow runs `terraform test` independently from validation

### Shell Scripts

* `shellcheck infrastructure/setup/*.sh training/**/*.sh evaluation/**/*.sh` — static analysis for deploy and submission scripts
* Deploy scripts (`infrastructure/setup/`) support `--config-preview` — prints configuration and exits without making changes; use for dry-run validation after modifying any deploy script

### Pester Tests

* `npm run test:ps` — runs Pester tests in `scripts/tests/` covering linting helpers and security checks

## CI/CD Pipeline

* Two orchestrators: `main.yml` (push to main), `pr-validation.yml` (PRs) using reusable `workflow_call` workflows
* PR validation sequence: spell check → markdown lint → table format → frontmatter → PSScriptAnalyzer → YAML lint → link check → Python lint → Python tests → frontend tests → Pester → dependency review → dependency pinning → CodeQL
* Security: all actions SHA-pinned (not tag-referenced), `persist-credentials: false` on all checkouts
* Security workflows: CodeQL (weekly + PR), Gitleaks (push + PR), OpenSSF Scorecard (weekly), dependency review (PR), SHA pinning scan (PR + main)
* Pre-commit: Husky v9 + lint-staged on frontend files only (ESLint + Prettier auto-fix)
* Codecov: `pytest` and `pester` flags, 80-100% range, carryforward enabled

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
| `.github/instructions/dataviewer.instructions.md` | Frontend coding patterns, component design, testing philosophy |
| `.github/instructions/shell-scripts.instructions.md` | Shell script template, section order, library function reference |
| `.github/instructions/commit-message.instructions.md` | Conventional commit format, types, scopes, footer requirements |
