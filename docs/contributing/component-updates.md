---
sidebar_position: 12
title: Updating External Components
description: Process for identifying, updating, and vetting reused externally-maintained components
author: Microsoft Robotics-AI Team
ms.date: 2026-07-01
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

| Component                 | Source    | Version Location                                              | Current Version | Update Method    |
|---------------------------|-----------|---------------------------------------------------------------|-----------------|------------------|
| NVIDIA GPU Operator       | Helm      | `infrastructure/setup/defaults.conf` → `GPU_OPERATOR_VERSION` | v26.3.2         | Manual           |
| KAI Scheduler             | Helm      | `infrastructure/setup/defaults.conf` → `KAI_SCHEDULER_VERSION`| v0.20.1         | Manual           |
| OSMO Chart                | Helm      | `infrastructure/setup/defaults.conf` → `OSMO_CHART_VERSION`   | 1.3.0           | Manual           |
| OSMO Image                | Container | `infrastructure/setup/defaults.conf` → `OSMO_IMAGE_VERSION`   | 6.3.0           | Manual           |
| AzureML K8s Extension     | Azure CLI | `02-deploy-azureml-extension.sh` → `--release-train stable`   | Latest stable   | Automatic        |
| Isaac Lab                 | Container | Hardcoded in 7+ files                                         | 2.3.2           | Manual grep      |
| ORAS                      | Binary    | `scripts/security/tool-checksums.json`                        | 1.2.0           | Manual           |
| Azure Terraform Providers | Terraform | `versions.tf` across 8 directories                            | Floor-pinned    | Dependabot (2/4) |
| Python Packages           | uv        | `pyproject.toml`, `uv.lock`                                   | Mixed           | Dependabot       |
| GitHub Actions            | GitHub    | Workflow YAML (18 files)                                      | SHA-pinned      | Dependabot       |

> [!IMPORTANT]
> Isaac Lab version `2.3.2` is hardcoded across workflow YAMLs, deploy scripts, and `pyproject.toml` files. No centralized variable exists. Use `grep -r "2.3.2" --include="*.yaml" --include="*.yml" --include="*.toml" --include="*.sh"` to locate all references before updating.

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

Dependabot opens PRs weekly on Monday for covered ecosystems. Configuration lives in `.github/dependabot.yml`.

| Ecosystem      | Directory                       | Grouping                | Schedule       |
|----------------|---------------------------------|-------------------------|----------------|
| pip            | `/`                             | `python-dependencies`   | Weekly, Monday |
| pip            | `/training/`                    | `training-dependencies` | Weekly, Monday |
| terraform      | `/infrastructure/terraform`     | None                    | Weekly, Monday |
| terraform      | `/infrastructure/terraform/dns` | None                    | Weekly, Monday |
| github-actions | `/`                             | `github-actions`        | Weekly, Monday |

PR flow: Dependabot opens PR → CI runs (dependency-review, pinning-scan, CodeQL, linters) → advisory reviewer agent posts a GHSA/OSV-enriched risk summary → maintainer reviews changelog and test results → merge.

> [!NOTE]
> Dependabot does not cover Helm charts, container images, or 2 additional Terraform directories (`vpn/`, `automation/`). These require manual updates.

### Advisory Reviewer Agent

An agentic workflow at [.github/workflows/aw-dependabot-pr-review.md](pathname://../../.github/workflows/aw-dependabot-pr-review.md) triggers on every Dependabot PR and posts a single review with the verdict `APPROVE` or `COMMENT`. It never emits `REQUEST_CHANGES` and never blocks a merge.

The reviewer enriches each update with:

* GHSA, OSV, and NVD advisory lookups for referenced CVE/GHSA IDs
* Release-notes highlights pulled from the ecosystem registry (npm, PyPI, Go proxy, Terraform registry, Docker Hub)
* Surface-specific risk flags (Isaac Sim numpy ABI pin, `azurerm` major bumps, CUDA-adjacent Docker base images, unpinned Action tags)

The review body prepends a `⚠️ Maintainer review recommended` banner when any high-risk signal fires. Up to five inline comments are anchored to the changed manifest or lockfile lines. The workflow skips drafts and any PR that touches `.github/workflows/**`. The persona is defined in [.github/agents/dependabot-pr-reviewer.agent.md](pathname://../../.github/agents/dependabot-pr-reviewer.agent.md).

Maintainers remain the source of truth — the reviewer is advisory context, not automated policy.

### Python Lockfiles

Every Python subproject carries a committed `uv.lock` beside its `pyproject.toml`. The lock is the single resolution source of truth — runtime-flat `requirements.txt` files are not committed.

* **Regenerate** a lock with `uv lock` (or `uv lock --upgrade`) after editing `pyproject.toml`. Never hand-edit `uv.lock` and never run `uv pip compile` to produce a committed flat file.
* **Derive** runtime dependencies at build or submit time via `uv export --frozen --no-hashes --no-emit-project` piped into `uv pip install --no-deps`. `--frozen` reads the lock without regenerating it. The OSMO replay mirror ([training/utils/replay-azureml.sh](pathname://../../training/utils/replay-azureml.sh)) derives its requirements this way from `workflows/osmo/uv.lock`.
* **Constrain** the universal lock to supported platforms with `[tool.uv] environments` (for example linux x86_64 for GPU and Isaac subprojects). Preserve these markers when regenerating.
* Dependabot regenerates affected locks natively on dependency PRs. The read-only `uv lock --check` gate (see [CI Validation for Dependency PRs](#ci-validation-for-dependency-prs)) fails any PR whose lock drifts from its manifest, so no manual `uv lock` step is required on Dependabot PRs.

## Tool Checksums

The `scripts/security/tool-checksums.json` file is the repository's single source of truth for explicit tool versions and their SHA-256 digests. This file currently manages:

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
   grep -r "2.3.2" --include="*.yaml" --include="*.yml" --include="*.toml" --include="*.sh"
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

For directories not covered by Dependabot (`vpn/`, `automation/`):

1. Run `terraform init -upgrade` in the target directory
2. Run `terraform plan -var-file=terraform.tfvars` to verify no breaking changes
3. Submit PR with provider changelog references

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
