---
sidebar_position: 1
title: Security Documentation
description: Index of security documentation including threat model and deployment security guide
author: Microsoft Robotics-AI Team
ms.date: 2026-09-23
ms.topic: overview
keywords:
  - security
  - threat model
  - deployment
  - vulnerability
  - compliance
---

## 📋 Overview

Security documentation for the Physical AI Toolchain covering threat analysis, deployment hardening, and vulnerability reporting.

## 📄 Documents

| Document                                                                                | Description                                                      |
|-----------------------------------------------------------------------------------------|------------------------------------------------------------------|
| [Threat Model](threat-model.md)                                                         | STRIDE-based threat analysis and remediation roadmap             |
| [Deployment Security Guide](../operations/security-guide.md)                            | Security configuration inventory and deployment responsibilities |
| [Release Verification](release-verification.md)                                         | Verify release artifact provenance and SBOM attestations         |
| [Workflow Permissions](workflow-permissions.md)                                         | GitHub Actions permission scopes and OSSF Scorecard exceptions   |
| [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md) | Vulnerability disclosure and reporting process                   |

## 🔒 Security Posture

This reference architecture deploys AKS clusters with GPU node pools, Azure Machine Learning, and NVIDIA OSMO for robotics training and inference. All components are infrastructure-as-code artifacts; no hosted service or user-facing application exists.

The [threat model](threat-model.md) documents:

- 19 threats across STRIDE categories
- Security controls mapped to each threat
- Trust boundary analysis across IaC, cluster, and ML pipeline layers
- Prioritized remediation roadmap

The [security guide](../operations/security-guide.md) documents:

- Default security configurations shipped with the architecture
- Deployment team responsibilities before, during, and after provisioning
- Security considerations checklist with Azure documentation references

## 🛠️ Operational Scripts

Automated security and freshness checks that run on GitHub Actions schedules and publish findings to the Security tab.

| Script                                                                                                                                                    | Workflow                                                            | Purpose                                                                                                                                                                               |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`scripts/security/Modules/PinnedToolVersions.psm1`](../../scripts/security/Modules/PinnedToolVersions.psm1)                                              | `check-binary-freshness.yml`, `check-binary-integrity.yml`          | Provide literal tool version discovery across tracked shell, PowerShell, JSON, and JSONC files                                                                                        |
| [`scripts/security/Test-BinaryFreshness.ps1`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/security/Test-BinaryFreshness.ps1)     | `check-binary-integrity.yml`                                        | Verify pinned binary SHA-256 hashes and detect Helm chart version drift (SARIF output)                                                                                                |
| [`scripts/security/Test-DependencyPinning.ps1`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/security/Test-DependencyPinning.ps1) | `dependency-pinning-scan.yml`                                       | Validate exact pins for GitHub Actions, packages, inline pip/uv installs, workflow container images, and AzureML environment assets (Dockerfile base images: OpenSSF Scorecard)       |
| [`scripts/security/Test-SHAStaleness.ps1`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/security/Test-SHAStaleness.ps1)           | `sha-staleness-check.yml`                                           | Detect SHA pins that have drifted behind upstream release tags                                                                                                                        |
| [`scripts/update-chart-hashes.sh`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-chart-hashes.sh)                           | Run manually after chart bumps                                      | Refresh pinned Helm chart versions and SHA-256 hashes in `infrastructure/setup/defaults.conf`                                                                                         |
| [`scripts/update-image-digests.sh`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-image-digests.sh)                         | `check-image-digest-freshness.yml` (weekly); manual after tag bumps | Detect registry digest drift and refresh `@sha256` pins and derived AzureML environment versions across tracked non-Dockerfile surfaces; excludes gh-aw lock files and test artifacts |

Script parameters vary by check: `Test-BinaryFreshness.ps1` uses `-SarifFile` and `-ConfigPreview`, `Test-DependencyPinning.ps1` uses `-Format sarif -OutputPath <path>`, `Test-SHAStaleness.ps1` uses `-OutputFormat` and `-OutputPath`, and `update-image-digests.sh` uses `--check --sarif-output <path>`.

`check-binary-freshness.yml` uses `PinnedToolVersions.psm1` to discover configured tool pins without per-file workflow registration. Discovery excludes `scripts/tests/Fixtures/` and `*.Tests.ps1` while retaining executable test helpers.

`update-image-digests.sh --check` exits 0 when pins are current, 2 when image-digest or AzureML environment-version drift findings are written to SARIF, and 1 for resolution or report-generation failures. The scheduled workflow keeps drift non-gating while propagating failures.

The script resolves anonymous OCI registries, including hosts with ports. It acquires anonymous pull tokens only for Docker Hub and NGC; registries that require other authentication flows are not supported.

Run `scripts/update-chart-hashes.sh` locally whenever a pinned Helm chart version is updated so `defaults.conf` stays in sync. Likewise, run `scripts/update-image-digests.sh` after bumping a container image tag so the `@sha256` digest pins stay in sync.

`Test-DependencyPinning.ps1 -Apply` rewrites tag-pinned GitHub Actions references with their resolved commit SHAs in place; run it manually to remediate pinning findings.

`Test-DependencyPinning.ps1` also flags unpinned inline `pip install` / `uv pip install` commands embedded in workflow YAML and shell scripts, scanned under the `shell-inline-pip` type. A compliant install uses an exact `==` pin, a lockfile (`-r`/`--requirement`, a `uv export | uv pip install` pipe, or frozen `uv sync`), or an editable local project (`-e .`). To exempt an intentional non-pin, add a `# pinning-ignore` comment on the install line:

```bash
uv pip install "numpy>=1.26,<2.0"  # pinning-ignore
```

Under the `docker` type, the scanner flags workflow-YAML `image:` references that are not pinned by an immutable `@sha256` digest. Submission-time templated (`{{ image }}`) and shell-variable references are skipped.

Under the `azureml-environments` type, AzureML `environment:` asset references require an explicit version; labels and unversioned references are mutable and rejected. Repository policy also rejects the ambiguous explicit version name `latest`; use a digest-derived version. Refresh digest and environment pins with `scripts/update-image-digests.sh`. To exempt an intentional non-pin, add a `# pinning-ignore` comment on the `image:` or `environment:` line, or on the line directly above it.

Under the `workflow-npm-commands` type, the scanner flags `npm install`, `npm i`, `npm update`, and `npm install-test` (and the `npm.cmd` shim) in workflow and composite-action `run:` steps, requiring `npm ci` for reproducible installs from the lockfile. Indentation-aware parsing confines detection to `run:` block content, so npm in step names, keys, or comments is not flagged. Add a `# pinning-ignore` comment on or directly above the command line to exempt an intentional non-`ci` install.

## 🔍 Container Scan Categories

The [container scan workflow](../../.github/workflows/container-scan.yml) scans digest-pinned external `FROM` references from tracked `Dockerfile` and `Containerfile` sources. [The lane map](../../scripts/security/container-scan-lanes.json) binds each logical scan role to source slots. The `trivy-image-<lane-id>` category remains stable when its image tag or digest changes; the SARIF filename still uses a full-reference hash to distinguish concrete images.

Each map entry has an `id` and a nonempty `sources` array of `{ "path": "...", "from": 0 }` objects. `path` is a tracked repository-relative file path, and `from` is the zero-based ordinal of **every** case-insensitive `FROM` statement in that file, including internal stages and ARG-templated stages. The map does not copy tags or digests.

Run `bash scripts/security/discover-base-images.sh --matrix` after adding, moving, or reordering a base-image source. The helper rejects unmapped, stale, duplicate, or ambiguous bindings rather than omitting a scan. Retain the lane ID when a source moves without changing its role. If two sources assigned to one lane diverge, review the roles and give a distinct role a new ID; do not select one reference arbitrarily.

Changing from full-reference categories to lane categories creates a **one-time category-set transition**. GitHub does not rename or merge historical analyses when new results arrive. Retain old analyses by default; the historical category total can increase before the new set stabilizes. A stable category does not guarantee unchanged alert IDs because SARIF fingerprints also affect alert continuity.

After the change merges, inventory existing Trivy analyses and categories with paginated, read-only GitHub API requests. Record retrieval time, ref, workflow run and attempt, commit, SARIF ID, and the expected lane map. Inspect one default-branch scan and a second comparable default-branch scan.

For each expected lane, correlate its upload SARIF ID to a completed analysis with no errors, the matching default ref and commit, and exactly the expected category. Compare **sets** of returned categories for the two runs, not only counts: the second run must introduce zero categories for unchanged lanes. A green workflow alone does not prove SARIF processing completed. Do not mark runtime acceptance complete if a lane is missing, duplicated, or timed out.

Historical cleanup is optional and requires separate authorization. First verify new default-branch coverage and save an old-to-new category inventory. Review an exact allowlist of obsolete Trivy analyses by ref, tool, and category; GitHub deletes each category's analysis set newest-first, and deletion of its last analysis can remove historical alert evidence.

Obtain separate consent before any deletion, especially the final analysis in a set. Never delete all Trivy history to achieve a lower category count. Roll back by reviewing a code revert, not by deleting analyses; reverting can resume the old category churn.

## 🔗 Related Resources

- [Contributing security review](../contributing/security-review.md): Contributor security checklist for pull requests
- [Azure security documentation](https://learn.microsoft.com/azure/security/): Authoritative security guidance for Azure services
- [AKS baseline architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks): Production-ready AKS security patterns

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
