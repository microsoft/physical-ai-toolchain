---
sidebar_position: 4
title: Workflow Permissions
description: GitHub Actions permission scopes and OSSF Scorecard Token-Permissions exception rationale
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
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

This document enumerates every job-scoped write permission across `.github/workflows/` and records the justification so security auditors and Scorecard reviewers can verify each exception.

## 🔒 Job-Scoped Write Permissions

The 15 write permissions below are required by the action or CLI invoked in the corresponding job. Each grant is the minimum scope needed.

| Workflow                          | Job                            | Permission                | Rationale                                                                                                                              |
|-----------------------------------|--------------------------------|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `check-binary-integrity.yml`      | `check-binary-integrity`       | `security-events: write`  | Required by `github/codeql-action/upload-sarif` to publish binary integrity findings to the Security tab.                              |
| `codeql-analysis.yml`             | `analyze`                      | `security-events: write`  | Required by `github/codeql-action/analyze` to upload CodeQL SARIF results to the Security tab.                                         |
| `dast-zap-scan.yml`               | `dast-zap-scan`                | `security-events: write`  | Required by `github/codeql-action/upload-sarif` to publish ZAP DAST findings to the Security tab.                                      |
| `dependency-pinning-scan.yml`     | `dependency-pinning-scan`      | `security-events: write`  | Required by `github/codeql-action/upload-sarif` to publish SHA-pinning findings to the Security tab.                                   |
| `gitleaks-scan.yml`               | `scan`                         | `security-events: write`  | Required by `github/codeql-action/upload-sarif` to publish secret-scanning findings to the Security tab.                               |
| `main.yml`                        | `dependency-pinning`           | `security-events: write`  | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                                        |
| `main.yml`                        | `codeql-analysis`              | `security-events: write`  | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                                |
| `main.yml`                        | `generate-dependency-sbom`     | `contents: write`         | Required by `gh release upload "${TAG}" dependencies.spdx.json --clobber` to attach the dependency SBOM to the release.                |
| `main.yml`                        | `attest-release`               | `attestations: write`     | Required by `actions/attest-build-provenance` and `actions/attest` to create Sigstore provenance attestations.                         |
| `main.yml`                        | `attest-release`               | `contents: write`         | Required by `gh release upload` to attach `*.sigstore.json` and `*.intoto.jsonl` attestation artifacts to the release.                 |
| `main.yml`                        | `sbom-diff`                    | `contents: write`         | Required by `gh release upload "${TAG}" dependency-diff.md --clobber` to attach the dependency-change report to the release.           |
| `main.yml`                        | `append-verification-notes`    | `contents: write`         | Required by `gh release edit` to append artifact-verification instructions to the release body.                                        |
| `pr-validation.yml`               | `dependency-pinning`           | `security-events: write`  | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                                        |
| `pr-validation.yml`               | `codeql-analysis`              | `security-events: write`  | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                                |
| `scorecard.yml`                   | `analysis`                     | `security-events: write`  | Required by `github/codeql-action/upload-sarif` to publish OpenSSF Scorecard findings to the Security tab.                             |

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
