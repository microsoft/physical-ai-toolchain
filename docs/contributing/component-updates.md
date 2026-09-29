---
sidebar_position: 12
title: Updating External Components
description: Process for identifying, updating, and vetting reused externally-maintained components
author: Microsoft Robotics-AI Team
ms.date: 2026-09-29
ms.topic: how-to
keywords:
  - component-updates
  - dependencies
  - openssf
  - dependabot
---

This guide covers identification, updating, vetting, and breaking change handling for all reused externally-maintained components. It satisfies the OpenSSF Best Practices Silver `documentation_reuse_component_update` criterion.

For quick dependency commands, see the [Component Updates](pull-request-process.md#component-updates) section of the Pull Request Process guide. For CVE-driven security updates, see [Security Review](security-review.md).

## Component Inventory

| Component                 | Source    | Version Location                                               | Current Version   | Update Method        |
|---------------------------|-----------|----------------------------------------------------------------|-------------------|----------------------|
| NVIDIA GPU Operator       | Helm      | `infrastructure/setup/defaults.conf` → `GPU_OPERATOR_VERSION`  | v26.3.2           | Manual               |
| KAI Scheduler             | Helm      | `infrastructure/setup/defaults.conf` → `KAI_SCHEDULER_VERSION` | v0.20.1           | Manual               |
| OSMO Chart                | Helm      | `infrastructure/setup/defaults.conf` → `OSMO_CHART_VERSION`    | 1.3.0             | Manual               |
| OSMO Image                | Container | `infrastructure/setup/defaults.conf` → `OSMO_IMAGE_VERSION`    | 6.3.0             | Manual               |
| AzureML K8s Extension     | Azure CLI | `02-deploy-azureml-extension.sh` → `--release-train stable`    | Latest stable     | Automatic            |
| Isaac Lab                 | Container | Shared default plus direct-workflow fallbacks                  | 3.0.0-beta2-post1 | Image digest updater |
| ORAS                      | Binary    | `scripts/security/tool-checksums.json`                         | 1.2.0             | Manual               |
| Azure Terraform Providers | Terraform | `versions.tf` across 4 deployment directories                  | Floor-pinned      | Dependabot           |
| Python Packages           | uv        | `pyproject.toml`, `uv.lock`                                    | Mixed             | Dependabot           |
| GitHub Actions            | GitHub    | Workflow YAML (18 files)                                       | SHA-pinned        | Dependabot           |

> [!IMPORTANT]
> Isaac Lab defaults to `DEFAULT_ISAAC_LAB_IMAGE` in `scripts/lib/common.sh`. Direct OSMO workflow fallbacks repeat the digest-pinned reference and must stay synchronized.

## Identifying Available Updates

| Ecosystem        | Tool or Method                                                | Command or Location                                   |
|------------------|---------------------------------------------------------------|-------------------------------------------------------|
| Python           | Dependabot PRs, `uv lock --upgrade`                           | `.github/dependabot.yml`, `pyproject.toml`            |
| Shell Downloads  | Manual check, `scripts/security/tool-checksums.json`          | `tool-checksums.json`                                 |
| Terraform        | Dependabot PRs, `terraform init -upgrade`                     | `.github/dependabot.yml`, `infrastructure/terraform/` |
| Helm Charts      | `helm repo update && helm search repo <chart> --versions`     | NVIDIA NGC Helm repositories                          |
| Container Images | NVIDIA NGC catalog, GitHub release pages                      | `nvcr.io/nvidia/` namespace                           |
| GitHub Actions   | Dependabot PRs, `gh api repos/{owner}/{repo}/releases/latest` | `.github/dependabot.yml`                              |

## Automated Updates (Dependabot)

Dependabot checks version updates weekly on Monday. Configuration lives in [.github/dependabot.yml](pathname://../../.github/dependabot.yml). The 24 update entries retain separate Python and Docker runtime environments while sharing npm and Terraform configurations across compatible directories.

| Ecosystem      | Coverage                                                                                      | Grouping                                                                             |
|----------------|-----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| npm            | Root workspace, including the Dataviewer frontend, and `/docs/docusaurus`                     | Shared development tools, Vitest, Docusaurus, authentication, and web runtime groups |
| uv             | 14 project roots covering development, training, evaluation, data, workflows, and GPU offload | Separate patch/minor group per project                                               |
| terraform      | `/infrastructure/terraform`, plus `dns`, `vpn`, and `automation`                              | One coordinated patch/minor provider group                                           |
| github-actions | Workflow files, excluding generated `*.lock.yml` files                                        | One patch/minor group; compiler-managed actions remain excluded                      |
| gomod          | `/infrastructure/terraform/e2e`                                                               | One patch/minor group                                                                |
| docker         | Six configured Dataviewer and GPU-offload Dockerfile directories                              | Separate patch/minor group per directory                                             |

The shared npm configuration uses these version-update groups:

| Group                | Included updates                                                                   | Review boundary                                                                                                  |
|----------------------|------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| `npm-vitest`         | `vitest` and `@vitest/*`, including major versions                                 | Keep the test runner and coverage provider together; require frontend tests and coverage validation              |
| `npm-docusaurus`     | Patch/minor `@docusaurus/*` updates across development and production dependencies | Keep framework packages aligned; require documentation typecheck, build, and tests                               |
| `npm-authentication` | Patch/minor production `@azure/msal-*` updates                                     | Review authentication separately from tooling and other runtime changes                                          |
| `npm-development`    | Other patch/minor development dependencies across both npm roots                   | Keep tooling separate from production dependencies; validate affected lint, test, build, or test-server behavior |
| `npm-runtime`        | Other patch/minor production dependencies across both npm roots                    | Review web runtime behavior separately from tooling and authentication                                           |

Groups are evaluated in order. Explicit exclusions keep the coupled families out of the generic groups. Apart from the dedicated Vitest group, major updates remain outside groups and require separate compatibility review. Existing ESLint 10 exclusions apply to both npm roots until the accessibility and React plugins support that major version.

Version updates use a seven-day cooldown, with a fourteen-day major-version cooldown where configured. The open-PR limit applies to each update entry, not the entire repository. Security updates remain ungrouped and are not delayed by the version-update schedule or cooldown.

Grouping reduces PR overhead; it does not waive CI or workload-specific validation. Development dependencies can affect generated assets and test servers. Review every changed manifest and lockfile, not only the PR title. Existing open PRs may be superseded as Dependabot reevaluates the configuration after it reaches the default branch; this change does not merge or approve them.

PR flow: Dependabot opens PR → CI runs → maintainer optionally requests an advisory review → maintainer reviews changelog and test results → merge.

> [!NOTE]
> Dependabot covers only the configured Dockerfile directories, not every container reference. Helm charts and image references in workflow YAML or shell defaults still require manual updates.

### Advisory Reviewer Agent

An agentic workflow at [.github/workflows/aw-dependabot-pr-review.md](pathname://../../.github/workflows/aw-dependabot-pr-review.md) runs when a maintainer comments `/aw-dependabot-review` on a Dependabot PR. It posts a single `COMMENT` review, never `APPROVE` or `REQUEST_CHANGES`. Human approval remains the merge gate.

The reviewer enriches each update with:

* GHSA, OSV, and NVD advisory lookups for referenced CVE/GHSA IDs
* Release-notes highlights pulled from the ecosystem registry (npm, PyPI, Go proxy, Terraform registry, Docker Hub)
* Surface-specific risk flags (Isaac Sim numpy ABI pin, `azurerm` major bumps, CUDA-adjacent Docker base images, unpinned Action tags)

The review body prepends a `⚠️ Maintainer review recommended` banner when any high-risk signal fires. Up to five inline comments are anchored to the changed manifest or lockfile lines. The workflow skips drafts and any PR that touches `.github/workflows/**`. The persona is defined in [.github/agents/dependabot-pr-reviewer.agent.md](pathname://../../.github/agents/dependabot-pr-reviewer.agent.md).

Maintainers remain the source of truth — the reviewer is advisory context, not automated policy.

### Python Lockfiles

Every Python subproject carries a committed `uv.lock` beside its `pyproject.toml`. The lock is the single resolution source of truth — runtime-flat `requirements.txt` files are not committed.

* **Regenerate** a lock with `uv lock` (or `uv lock --upgrade`) after editing `pyproject.toml`. Never hand-edit `uv.lock` and never run `uv pip compile` to produce a committed flat file.
* **Install** source-aware LeRobot runtimes with `uv sync --active --frozen --no-config --no-install-project` so `[tool.uv.sources]` package indexes remain part of the runtime contract.
* **Derive** other runtime dependencies with `uv export --frozen --no-hashes --no-emit-project` piped into `uv pip install --no-deps`. Both forms read the lock without regenerating it. The OSMO replay mirror ([training/utils/replay-azureml.sh](pathname://../../training/utils/replay-azureml.sh)) uses the export form with `workflows/osmo/uv.lock`.
* **Constrain** the universal lock to supported platforms with `[tool.uv] environments` (for example linux x86_64 for GPU and Isaac subprojects). Preserve these markers when regenerating.
* Dependabot regenerates affected locks natively on dependency PRs. The read-only `uv lock --check` gate (see [CI Validation for Dependency PRs](#ci-validation-for-dependency-prs)) fails any PR whose lock drifts from its manifest, so no manual `uv lock` step is required on Dependabot PRs.

## Tool Checksums

The `scripts/security/tool-checksums.json` file is the source of truth for tools installed from this manifest. This file currently manages:

* **ORAS**: Fetched inside the GR00T training container to push checkpoints to ACR.
* **Actionlint**: Used in the devcontainer for GitHub Actions workflow linting.
* **Gitleaks**: Used in the devcontainer for secret scanning.

### Updating Tool Checksums

When you need to update a tool managed by this manifest (e.g. bumping ORAS to a new release):

1. **Find the target version and release asset**: Visit the tool's upstream release page (e.g. `https://github.com/oras-project/oras/releases`).
2. **Retrieve the SHA-256 checksum**: Download the target asset (`oras_..._linux_amd64.tar.gz`) or its checksum file, and run `shasum -a 256 <file>`.
3. **Update the manifest**: Edit `scripts/security/tool-checksums.json`, updating the `version` and `sha256` fields for the appropriate entry.
4. **Commit the change**: All downstream consumers dynamically read from this file at runtime; no secondary edits are required.

> [!WARNING]
> Do not attempt to update these tools directly in shell scripts. The CI pinning scanner will flag mismatches if download URLs point to one version while checking against another, but the canonical version and hash live in `tool-checksums.json`.

Other developer-tool versions are pinned at their bootstrap assignment sites. The binary freshness workflow discovers supported literal assignments in tracked shell, PowerShell, JSON, and JSONC files and requires replicated pins to remain consistent. See [`scripts/README.md`](pathname://../../scripts/README.md#-where-pins-live).

## Manual Update Process

### Helm Charts

Helm chart versions are centralized in `infrastructure/setup/defaults.conf`.

1. Check for a new chart version:

   ```bash
   helm repo update
   helm search repo <chart-name> --versions
   ```

2. Update the version variable in `infrastructure/setup/defaults.conf`
3. Run `--config-preview` on affected deploy scripts to verify configuration
4. Deploy to a test cluster and validate
5. Submit PR with changelog summary from the upstream release

### Container Images (Isaac Lab)

Runtime GPU images are digest-pinned: the human-readable tag stays for legibility while an
immutable `@sha256:<digest>` makes the pull tamper-evident. Dependabot's `docker` ecosystem only
tracks Dockerfiles, so these tag-plus-digest references are bumped manually.

1. Check NVIDIA NGC for a new Isaac Lab release
2. Search for all current version references:

   ```bash
   grep -r "3.0.0-beta2-post1" --include="*.yaml" --include="*.yml" --include="*.toml" --include="*.sh"
   ```

3. Resolve the digest the new tag points to:

   ```bash
   docker buildx imagetools inspect nvcr.io/nvidia/isaac-lab:<version> --format '{{.Manifest.Digest}}'
   ```

4. Update every reference to `<version>@sha256:<digest>`:
   * `scripts/lib/common.sh` — `DEFAULT_ISAAC_LAB_IMAGE` (embed `<version>@sha256:<digest>`; `DEFAULT_ISAAC_LAB_IMAGE_VERSION` is derived from it automatically)
   * the OSMO workflow fallback `image:` lines (kept in sync with `DEFAULT_ISAAC_LAB_IMAGE`)
   * `setup-dev.sh` — `ISAACLAB_COMMIT`, the matching IsaacLab git commit cloned for intellisense
   * `pyproject.toml` and any remaining tag-only references
5. The GR00T base image (`pytorch/pytorch`) in `training/vla/workflows/osmo/groot-train.yaml` is
   digest-pinned the same way; refresh its digest when bumping that tag.
6. Test a training workflow with the new image
7. Submit PR with migration notes from the NVIDIA release changelog

### Terraform Providers

Dependabot coordinates patch/minor provider updates across all four deployment directories. For grouped or manual provider updates:

1. Review provider release notes and identify each affected deployment directory
2. Run `terraform init -upgrade` in each affected directory
3. Review `terraform plan -var-file=terraform.tfvars` in each configured environment for unexpected changes
4. Include provider changelog references and plan results in the PR

## Vetting Criteria

Apply this checklist before merging any component update.

| Criterion           | Check                                               | Required For               |
|---------------------|-----------------------------------------------------|----------------------------|
| Changelog review    | Read release notes for breaking changes             | All updates                |
| API compatibility   | Verify no breaking API changes affect current usage | Major and minor updates    |
| License check       | Confirm license unchanged or still OSI-approved     | All updates                |
| Security advisories | Check GitHub Security Advisories, NVD               | All updates                |
| CI passage          | All CI checks pass on the update PR                 | All updates                |
| Deployment test     | `--config-preview` then deploy to test cluster      | Helm and container updates |

## Breaking Change Handling

1. Identify breaking changes from the upstream changelog and migration guides
2. Assess impact on deployment scripts, training workflows, and CI
3. Create migration steps in the PR description
4. Update affected documentation (README files, deployment guides, workflow templates)
5. Add `breaking-change` label to the PR
6. Request review from infrastructure owners (`@microsoft/edge-ai-core-dev`)

## CI Validation for Dependency PRs

These workflows validate dependency update PRs automatically.

| Workflow                      | Purpose                              | Scope              |
|-------------------------------|--------------------------------------|--------------------|
| `dependency-review.yml`       | Block moderate+ vulnerabilities      | All dependency PRs |
| `dependency-pinning-scan.yml` | Enforce 95% SHA pinning compliance   | GitHub Actions     |
| `codeql-analysis.yml`         | Static analysis for code + workflows | Repository-wide    |
| `scorecard.yml`               | OpenSSF Scorecard assessment         | Repository-wide    |
| `uv-lock-consistency.yml`     | Fail on `uv.lock`/manifest drift     | Python lockfiles   |

## Security-Critical Updates

For CVE-driven updates requiring expedited handling:

1. Maintainer identifies a CVE affecting a project dependency
2. Open a priority PR referencing the security advisory
3. Target 24-48 hour review turnaround
4. Update `SECURITY.md` if disclosure is warranted

See [Security Review](security-review.md) for the full security update process.

## Related Documentation

* [Pull Request Process](pull-request-process.md) - PR workflow, reviewer assignment, approval criteria
* [Security Review](security-review.md) - Security checklist, credential handling, vulnerability reporting
* [Documentation Maintenance](documentation-maintenance.md) - Update triggers, ownership, freshness policy
* [Contributing Guide](README.md) - Prerequisites, workflow, commit messages

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
