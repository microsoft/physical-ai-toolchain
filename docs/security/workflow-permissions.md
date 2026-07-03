---
sidebar_position: 4
title: Workflow Permissions
description: GitHub Actions permission scopes and OSSF Scorecard Token-Permissions exception rationale
author: Microsoft Robotics-AI Team
ms.date: 2026-07-03
ms.topic: reference
keywords:
  - security
  - github-actions
  - permissions
  - ossf-scorecard
  - token-permissions
---

## 📋 Overview

All GitHub Actions workflows in this repository follow the [OpenSSF Scorecard Token-Permissions](https://github.com/ossf/scorecard/blob/main/docs/checks.md#token-permissions) principle:

- Top-level `permissions:` is `contents: read` (read-only by default).
- Write-scoped permissions are declared at the **job level** only when a specific step requires them.
- No workflow grants `permissions: write-all` or omits an explicit top-level `permissions:` block.

This document enumerates every job-scoped `security-events`, `contents`, and `attestations` write grant across `.github/workflows/` and records the justification so security auditors and Scorecard reviewers can verify each exception.

## 🔒 Job-Scoped Write Permissions

The 21 write permissions below are required by the action or CLI invoked in the corresponding job. Each grant is the minimum scope needed.

| Workflow                      | Job                         | Permission               | Rationale                                                                                                                    |
|-------------------------------|-----------------------------|--------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `check-binary-integrity.yml`  | `check-hashes`              | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish binary integrity findings to the Security tab.                    |
| `codeql-analysis.yml`         | `analyze`                   | `security-events: write` | Required by `github/codeql-action/analyze` to upload CodeQL SARIF results to the Security tab.                               |
| `container-scan.yml`          | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish Trivy base-image CVE findings to the Security tab.                |
| `dast-zap-scan.yml`           | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish ZAP DAST findings to the Security tab.                            |
| `dependency-pinning-scan.yml` | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish SHA-pinning findings to the Security tab.                         |
| `gitleaks-scan.yml`           | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish secret-scanning findings to the Security tab.                     |
| `main.yml`                    | `dependency-pinning`        | `security-events: write` | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                              |
| `main.yml`                    | `codeql-analysis`           | `security-events: write` | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                      |
| `main.yml`                    | `generate-dependency-sbom`  | `contents: write`        | Required by `gh release upload "${TAG}" dependencies.spdx.json --clobber` to attach the dependency SBOM to the release.      |
| `main.yml`                    | `attest-release`            | `attestations: write`    | Required by `actions/attest-build-provenance` and `actions/attest` to create Sigstore provenance attestations.               |
| `main.yml`                    | `attest-release`            | `contents: write`        | Required by `gh release upload` to attach `*.sigstore.json` and `*.intoto.jsonl` attestation artifacts to the release.       |
| `main.yml`                    | `sbom-diff`                 | `contents: write`        | Required by `gh release upload "${TAG}" dependency-diff.md --clobber` to attach the dependency-change report to the release. |
| `main.yml`                    | `append-verification-notes` | `contents: write`        | Required by `gh release edit` to append artifact-verification instructions to the release body.                              |
| `pr-validation.yml`           | `dependency-pinning`        | `security-events: write` | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                              |
| `pr-validation.yml`           | `codeql-analysis`           | `security-events: write` | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                      |
| `pr-validation.yml`           | `container-scan`            | `security-events: write` | Inherited by reusable `container-scan.yml`; required for SARIF upload.                                                       |
| `pr-validation.yml`           | `osv-scanner`               | `security-events: write` | Required by `google/osv-scanner-action` to publish OSV dependency findings to the Security tab.                              |
| `pr-validation.yml`           | `terraform-security`        | `security-events: write` | Inherited by reusable `terraform-security.yml`; required for SARIF upload.                                                   |
| `scorecard.yml`               | `scorecard`                 | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish OpenSSF Scorecard findings to the Security tab.                   |
| `terraform-security.yml`      | `checkov`                   | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish Checkov findings to the Security tab.                             |
| `weekly-validation.yml`       | `container-rescan`          | `security-events: write` | Inherited by reusable `container-scan.yml`; required for SARIF upload of the soft-fail base-image rescan.                    |

## 🔑 GitHub App Token Write Scopes

The workflow-scoped `GITHUB_TOKEN` remains read-only. Workflows that need repository mutations mint GitHub App tokens for the minimum write scopes required by the corresponding step.

| Workflow                        | Step                        | Token scope            | Rationale                                                                                                 |
|---------------------------------|-----------------------------|------------------------|-----------------------------------------------------------------------------------------------------------|
| `container-cve-remediation.yml` | `Generate GitHub App token` | `contents: write`      | Required to push digest-bump branches and update files when a clean replacement base-image digest exists. |
| `container-cve-remediation.yml` | `Generate GitHub App token` | `issues: write`        | Required to open or refresh tracking issues when no clean replacement base-image digest is available.     |
| `container-cve-remediation.yml` | `Generate GitHub App token` | `pull-requests: write` | Required to open digest-bump pull requests for human review.                                              |

> [!NOTE]
> The repository/organization **"Allow GitHub Actions to create and approve pull requests"** setting is intentionally *not* required for `container-cve-remediation.yml`. That toggle governs the default `GITHUB_TOKEN`; this workflow instead mints a scoped GitHub App token, which the toggle does not apply to.
> Using an App token is also deliberate for gate coverage: pull requests opened with an App token trigger `pr-validation.yml` (including the `container-scan` gate), whereas pull requests opened with the default `GITHUB_TOKEN` would not.

## 🛡️ Defense in Depth

The release-publishing path uses additional hardening beyond minimum permissions:

- All actions are SHA-pinned (no floating tags).
- `persist-credentials: false` on every `actions/checkout` invocation.
- `id-token: write` is granted only to jobs that mint Sigstore OIDC tokens; the token is never exposed to user-controlled steps.
- Release-gated jobs (`generate-dependency-sbom`, `attest-release`, `sbom-diff`, `append-verification-notes`) run only when `release-please` produces a release (`needs.release-please.outputs.release_created == 'true'`).

## 🔗 Related Resources

- [OpenSSF Scorecard Token-Permissions check](https://github.com/ossf/scorecard/blob/main/docs/checks.md#token-permissions)
- [GitHub Actions: Assigning permissions to jobs](https://docs.github.com/en/actions/using-jobs/assigning-permissions-to-jobs)
- [Release Verification](release-verification.md)
- [Threat Model](threat-model.md)

<!-- markdownlint-configure-file { "MD024": false } -->

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
