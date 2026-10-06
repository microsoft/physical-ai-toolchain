---
sidebar_position: 4
title: Workflow Permissions
description: GitHub Actions permission scopes and OSSF Scorecard Token-Permissions exception rationale
author: Microsoft Robotics-AI Team
ms.date: 2026-10-05
ms.topic: reference
keywords:
  - security
  - github-actions
  - permissions
  - ossf-scorecard
  - token-permissions
---

GitHub Actions permission and command-interpolation controls for workflows and composite actions in this repository.

## 📋 Overview

All workflows follow the [OpenSSF Scorecard Token-Permissions](https://github.com/ossf/scorecard/blob/main/docs/checks.md#token-permissions) principle:

- Top-level `permissions:` is `contents: read` (read-only by default).
- Jobs under a populated workflow-level grant declare their own `permissions:` block, including read-only jobs.
- Write-scoped permissions are declared at the job level only when a specific step requires them.
- No workflow grants `permissions: write-all` or omits an explicit top-level `permissions:` block.

An empty workflow-level `permissions: {}` block grants no token scopes. Jobs that inherit this empty grant pass without a redundant job-level block; jobs that declare permissions remain explicit.

## 🔍 Enforced Rules

| Script                                          | Rule                                                                                                                                                                       |
|-------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `scripts/security/Test-WorkflowPermissions.ps1` | Reject workflows without top-level permissions and jobs that implicitly inherit a populated workflow-level grant.                                                         |
| `scripts/security/Test-DangerousWorkflow.ps1`   | Reject direct interpolation of attacker-controlled event values or non-boolean inputs into `run` and `actions/github-script`, plus untrusted `pull_request_target` checkout. |

Both linters run under `npm run test:ps` and standalone in `workflow-permissions-scan.yml`.

Map non-boolean inputs to a step-level environment variable before using them in shell or script code:

```yaml
- name: Run command
  env:
    COMMAND: ${{ inputs.command }}
  run: ./tool --command "$COMMAND"
```

Boolean inputs may be interpolated directly because they resolve only to `true` or `false`.

This document enumerates every job-scoped `security-events`, `contents`, and `attestations` write grant across `.github/workflows/` and records the justification so security auditors and Scorecard reviewers can verify each exception.

## 🔀 Merge Queue Trust Boundary

The aggregate PR Validation workflow handles both `pull_request` and
`merge_group` events. Reusable workflows remain `workflow_call`-only. The
aggregate checks out the tested `github.sha`, verifies an immutable change
range, and runs all selectors when it cannot prove that range.

Merge-group execution changes OIDC claims such as `event_name`, `ref`, and
`sub`. Before enabling the hosted queue, map every queue-reachable
`id-token: write` grant to its consuming step and provider. Retain dated
provider evidence for these restrictions:

| Claim or boundary   | Required evidence                                      |
|---------------------|--------------------------------------------------------|
| Repository          | Only `microsoft/physical-ai-toolchain` is accepted      |
| Event               | The intended `merge_group` identity is accepted         |
| Ref or subject      | Queue refs are bounded without accepting unrelated refs |
| Audience            | The expected provider audience is required              |
| Reusable workflow   | `job_workflow_ref` identifies the intended workflow     |
| Negative validation | An out-of-scope identity is rejected                     |

Use a provider configuration export, screenshot, or administrator attestation.
If the provider cannot express the required boundary, change the workflow so
the queue does not reach that token consumer and rerun the CI contracts.
Inaccessible provider policy blocks hosted activation.

The CI contract rejects secret references in workflows reachable from the
aggregate PR Validation workflow. Add no merge-group-reachable secret or
write-scoped permission without updating the contract, tests, and this
inventory.

## 🔒 Job-Scoped Write Permissions

The 22 write permissions below are required by the action or CLI invoked in the corresponding job. Each grant is the minimum scope needed.

| Workflow                           | Job                         | Permission               | Rationale                                                                                                                    |
|------------------------------------|-----------------------------|--------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `check-binary-integrity.yml`       | `check-hashes`              | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish binary integrity findings to the Security tab.                    |
| `check-image-digest-freshness.yml` | `check-freshness`           | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish container image digest drift findings to the Security tab.        |
| `codeql-analysis.yml`              | `analyze`                   | `security-events: write` | Required by `github/codeql-action/analyze` to upload CodeQL SARIF results to the Security tab.                               |
| `container-scan.yml`               | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish Trivy base-image CVE findings to the Security tab.                |
| `dast-zap-scan.yml`                | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish ZAP DAST findings to the Security tab.                            |
| `dependency-pinning-scan.yml`      | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish SHA-pinning findings to the Security tab.                         |
| `gitleaks-scan.yml`                | `scan`                      | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish secret-scanning findings to the Security tab.                     |
| `main.yml`                         | `dependency-pinning`        | `security-events: write` | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                              |
| `main.yml`                         | `codeql-analysis`           | `security-events: write` | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                      |
| `main.yml`                         | `generate-dependency-sbom`  | `contents: write`        | Required by `gh release upload "${TAG}" dependencies.spdx.json --clobber` to attach the dependency SBOM to the release.      |
| `main.yml`                         | `attest-release`            | `attestations: write`    | Required by `actions/attest-build-provenance` and `actions/attest` to create Sigstore provenance attestations.               |
| `main.yml`                         | `attest-release`            | `contents: write`        | Required by `gh release upload` to attach `*.sigstore.json` and `*.intoto.jsonl` attestation artifacts to the release.       |
| `main.yml`                         | `sbom-diff`                 | `contents: write`        | Required by `gh release upload "${TAG}" dependency-diff.md --clobber` to attach the dependency-change report to the release. |
| `main.yml`                         | `append-verification-notes` | `contents: write`        | Required by `gh release edit` to append artifact-verification instructions to the release body.                              |
| `pr-validation.yml`                | `dependency-pinning`        | `security-events: write` | Inherited by reusable `dependency-pinning-scan.yml`; required for SARIF upload.                                              |
| `pr-validation.yml`                | `codeql-analysis`           | `security-events: write` | Inherited by reusable `codeql-analysis.yml`; required for SARIF upload.                                                      |
| `pr-validation.yml`                | `container-scan`            | `security-events: write` | Inherited by reusable `container-scan.yml`; required for SARIF upload.                                                       |
| `pr-validation.yml`                | `osv-scanner`               | `security-events: write` | Required by `google/osv-scanner-action` to publish OSV dependency findings to the Security tab.                              |
| `pr-validation.yml`                | `terraform-security`        | `security-events: write` | Inherited by reusable `terraform-security.yml`; required for SARIF upload.                                                   |
| `scorecard.yml`                    | `scorecard`                 | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish OpenSSF Scorecard findings to the Security tab.                   |
| `terraform-security.yml`           | `checkov`                   | `security-events: write` | Required by `github/codeql-action/upload-sarif` to publish Checkov findings to the Security tab.                             |
| `weekly-validation.yml`            | `container-rescan`          | `security-events: write` | Inherited by reusable `container-scan.yml`; required for SARIF upload of the soft-fail base-image rescan.                    |

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
