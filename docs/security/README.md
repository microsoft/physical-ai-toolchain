---
sidebar_position: 1
title: Security Documentation
description: Index of security documentation including threat model and deployment security guide
author: Microsoft Robotics-AI Team
ms.date: 2026-07-01
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

| Script                                                                                                                                                    | Workflow                           | Purpose                                                                                                                                                                                   |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`scripts/security/Test-BinaryFreshness.ps1`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/security/Test-BinaryFreshness.ps1)     | `check-binary-integrity.yml`       | Verify pinned binary SHA-256 hashes and detect Helm chart version drift (SARIF output)                                                                                                    |
| [`scripts/security/Test-DependencyPinning.ps1`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/security/Test-DependencyPinning.ps1) | `dependency-pinning-scan.yml`      | Validate that GitHub Actions, package manifests, inline pip/uv installs, and workflow container images (`@sha256` digests) pin exact versions (Dockerfile base images: OpenSSF Scorecard) |
| [`scripts/security/Test-SHAStaleness.ps1`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/security/Test-SHAStaleness.ps1)           | `sha-staleness-check.yml`          | Detect SHA pins that have drifted behind upstream release tags                                                                                                                            |
| [`scripts/update-chart-hashes.sh`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-chart-hashes.sh)                           | Run manually after chart bumps     | Refresh pinned Helm chart versions and SHA-256 hashes in `infrastructure/setup/defaults.conf`                                                                                             |
| [`scripts/update-image-digests.sh`](https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-image-digests.sh)                         | Run manually after image tag bumps | Re-resolve and refresh `@sha256` container image digest pins (auto-discovered; Dockerfiles, compose, and `.github/` excluded)                                                             |

Script parameters vary by check: `Test-BinaryFreshness.ps1` uses `-SarifFile` and `-ConfigPreview`, `Test-DependencyPinning.ps1` uses `-Format sarif -OutputPath <path>`, and `Test-SHAStaleness.ps1` uses `-OutputFormat` and `-OutputPath`. Run `scripts/update-chart-hashes.sh` locally whenever a pinned Helm chart version is updated so `defaults.conf` stays in sync. Likewise, run `scripts/update-image-digests.sh` after bumping a container image tag so the `@sha256` digest pins stay in sync.

`Test-DependencyPinning.ps1 -Apply` rewrites tag-pinned GitHub Actions references with their resolved commit SHAs in place; run it manually to remediate pinning findings.

`Test-DependencyPinning.ps1` also flags unpinned inline `pip install` / `uv pip install` commands embedded in workflow YAML and shell scripts, scanned under the `shell-inline-pip` type. A compliant install uses an exact `==` pin, a lockfile (`-r`/`--requirement`, or a `uv export | uv pip install` pipe), or an editable local project (`-e .`). To exempt an intentional non-pin, add a `# pinning-ignore` comment on the install line:

```bash
uv pip install "numpy>=1.26,<2.0"  # pinning-ignore
```

Under the `docker` type, the scanner also flags workflow-YAML `image:` references that are not pinned by an immutable `@sha256` digest. Submission-time templated (`{{ image }}`) and shell-variable references are skipped, as are AzureML `environment:` asset references (versioned assets, not OCI images). Refresh digests with `scripts/update-image-digests.sh`; to exempt an intentional non-pin, add a `# pinning-ignore` comment on the `image:` line.

Under the `workflow-npm-commands` type, the scanner flags `npm install`, `npm i`, `npm update`, and `npm install-test` (and the `npm.cmd` shim) in workflow and composite-action `run:` steps, requiring `npm ci` for reproducible installs from the lockfile. Indentation-aware parsing confines detection to `run:` block content, so npm in step names, keys, or comments is not flagged. Add a `# pinning-ignore` comment on or directly above the command line to exempt an intentional non-`ci` install.

## 🔗 Related Resources

- [Contributing security review](../contributing/security-review.md): Contributor security checklist for pull requests
- [Azure security documentation](https://learn.microsoft.com/azure/security/): Authoritative security guidance for Azure services
- [AKS baseline architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks): Production-ready AKS security patterns

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
