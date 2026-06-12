---
sidebar_position: 4
title: Workflow Permissions
description: GitHub Actions permission scopes and OSSF Scorecard Token-Permissions exception rationale
author: Microsoft Robotics-AI Team
ms.date: 2026-06-03
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

The 47 write permissions below are required by the action or CLI invoked in the corresponding job. Each grant is the minimum scope needed.

| Workflow                      | Job                          | Permission               | Rationale                                                                                                                          |
|-------------------------------|------------------------------|--------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| `check-binary-integrity.yml`  | `check-hashes`               | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish binary integrity findings to the Security tab.                          |
| `codeql-analysis.yml`         | `analyze`                    | `security-events: write` | Required by `github/codeql-action/analyze` to upload CodeQL SARIF results to the Security tab.                                     |
| `dast-zap-scan.yml`           | `scan`                       | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish ZAP DAST findings to the Security tab.                                  |
| `dast-zap-scan.yml`           | `scan`                       | `issues: write`          | Required by `zaproxy/action-baseline` with `allow_issue_writing: true` to create and update issue-based scan reports.              |
| `dependency-pinning-scan.yml` | `scan`                       | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish SHA-pinning findings to the Security tab.                               |
| `dependency-review.yml`       | `dependency-review`          | `pull-requests: write`   | Required by `actions/dependency-review-action` with `comment-summary-in-pr: always` to post vulnerability summaries on PRs.        |
| `gitleaks-scan.yml`           | `scan`                       | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish secret-scanning findings to the Security tab.                           |
| `scorecard.yml`               | `scorecard`                  | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish OpenSSF Scorecard findings to the Security tab.                         |
| `scorecard.yml`               | `scorecard`                  | `id-token: write`        | Required by `ossf/scorecard-action` with `publish_results: true` to authenticate via OIDC when publishing results to the OpenSSF API. |
| `terraform-security.yml`      | `checkov`                    | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish Checkov Terraform security findings to the Security tab.                |
| `main.yml`                    | `dependency-pinning`         | `security-events: write` | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                                    |
| `main.yml`                    | `codeql-analysis`            | `security-events: write` | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                            |
| `main.yml`                    | `pester-tests`               | `id-token: write`        | Inherited by reusable `pester-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.                 |
| `main.yml`                    | `pytest-training`            | `id-token: write`        | Inherited by reusable `pytest-training.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.              |
| `main.yml`                    | `pytest-dm-tools`            | `id-token: write`        | Inherited by reusable `pytest-dm-tools.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.              |
| `main.yml`                    | `pytest-data-pipeline`       | `id-token: write`        | Inherited by reusable `pytest-data-pipeline.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.         |
| `main.yml`                    | `pytest-inference`           | `id-token: write`        | Inherited by reusable `pytest-inference.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.             |
| `main.yml`                    | `dataviewer-frontend-tests`  | `id-token: write`        | Inherited by reusable `dataviewer-frontend-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.    |
| `main.yml`                    | `dataviewer-backend-pytests` | `id-token: write`        | Inherited by reusable `dataviewer-backend-pytests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.   |
| `main.yml`                    | `evaluation-pytests`         | `id-token: write`        | Inherited by reusable `evaluation-pytests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.           |
| `main.yml`                    | `fuzz-regression-tests`      | `id-token: write`        | Inherited by reusable `fuzz-regression-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.        |
| `main.yml`                    | `terraform-tests`            | `id-token: write`        | Inherited by reusable `terraform-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.              |
| `main.yml`                    | `go-tests`                   | `id-token: write`        | Inherited by reusable `go-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.                     |
| `main.yml`                    | `release-please`             | `id-token: write`        | Required by `gitsign` to mint a Sigstore OIDC token for signing the release git tag.                                               |
| `main.yml`                    | `generate-dependency-sbom`   | `id-token: write`        | Required by `anchore/sbom-action` with `dependency-snapshot: true` to post to the GitHub Dependency Graph API via OIDC.            |
| `main.yml`                    | `generate-dependency-sbom`   | `contents: write`        | Required by `gh release upload "${TAG}" dependencies.spdx.json --clobber` to attach the dependency SBOM to the release.            |
| `main.yml`                    | `attest-release`             | `id-token: write`        | Required by `actions/attest` to mint OIDC tokens for Sigstore provenance and SBOM attestations.                                    |
| `main.yml`                    | `attest-release`             | `attestations: write`    | Required by `actions/attest` to create Sigstore provenance attestations.                                                           |
| `main.yml`                    | `attest-release`             | `contents: write`        | Required by `gh release upload` to attach `*.sigstore.json` and `*.intoto.jsonl` attestation artifacts to the release.             |
| `main.yml`                    | `sbom-diff`                  | `contents: write`        | Required by `gh release upload "${TAG}" dependency-diff.md --clobber` to attach the dependency-change report to the release.       |
| `main.yml`                    | `append-verification-notes`  | `contents: write`        | Required by `gh release edit` to append artifact-verification instructions to the release body.                                    |
| `main.yml`                    | `publish-release`            | `issues: write`          | Required by `gh api` to close the GitHub milestone corresponding to the release tag.                                               |
| `pr-validation.yml`           | `dependency-review`          | `pull-requests: write`   | Inherited by reusable `dependency-review.yml`; required to post vulnerability summaries on pull requests.                          |
| `pr-validation.yml`           | `dependency-pinning`         | `security-events: write` | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                                    |
| `pr-validation.yml`           | `codeql-analysis`            | `security-events: write` | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                            |
| `pr-validation.yml`           | `pester-tests`               | `id-token: write`        | Inherited by reusable `pester-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.                 |
| `pr-validation.yml`           | `dataviewer-frontend-tests`  | `id-token: write`        | Inherited by reusable `dataviewer-frontend-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.    |
| `pr-validation.yml`           | `pytest-training`            | `id-token: write`        | Inherited by reusable `pytest-training.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.              |
| `pr-validation.yml`           | `pytest-dm-tools`            | `id-token: write`        | Inherited by reusable `pytest-dm-tools.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.              |
| `pr-validation.yml`           | `pytest-data-pipeline`       | `id-token: write`        | Inherited by reusable `pytest-data-pipeline.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.         |
| `pr-validation.yml`           | `pytest-inference`           | `id-token: write`        | Inherited by reusable `pytest-inference.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.             |
| `pr-validation.yml`           | `dataviewer-backend-pytests` | `id-token: write`        | Inherited by reusable `dataviewer-backend-pytests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.   |
| `pr-validation.yml`           | `evaluation-pytests`         | `id-token: write`        | Inherited by reusable `evaluation-pytests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.           |
| `pr-validation.yml`           | `fuzz-regression-tests`      | `id-token: write`        | Inherited by reusable `fuzz-regression-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.        |
| `pr-validation.yml`           | `terraform-security`         | `security-events: write` | Inherited by reusable `terraform-security.yml`; required for SARIF upload.                                                         |
| `pr-validation.yml`           | `terraform-tests`            | `id-token: write`        | Inherited by reusable `terraform-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.              |
| `pr-validation.yml`           | `go-tests`                   | `id-token: write`        | Inherited by reusable `go-tests.yml`; required by `codecov/codecov-action` for OIDC tokenless coverage upload.                     |

## 🛡️ Defense in Depth

The release-publishing path uses additional hardening beyond minimum permissions:

- All actions are SHA-pinned (no floating tags).
- `persist-credentials: false` on every `actions/checkout` invocation.
- `id-token: write` is granted only to jobs that require OIDC token minting: test jobs use it for Codecov tokenless coverage uploads; release and attestation jobs use it for Sigstore signing; `scorecard.yml` uses it for publishing results to the OpenSSF API. The token is never exposed to user-controlled steps.
- Release-gated jobs (`generate-dependency-sbom`, `attest-release`, `sbom-diff`, `append-verification-notes`, `publish-release`) run only when `release-please` produces a release (`needs.release-please.outputs.release_created == 'true'`).

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
