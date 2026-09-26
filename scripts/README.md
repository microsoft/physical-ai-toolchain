---
title: Scripts
description: CI/CD scripts, shared libraries, linting, security, and Pester tests for the Physical AI Toolchain.
author: Microsoft Robotics-AI Team
ms.date: 2026-09-25
ms.topic: reference
keywords:
  - scripts
  - linting
  - security
  - testing
  - ci
---

CI/CD automation scripts for linting, validation, security scanning, and shared utilities used across the repository.

> [!NOTE]
> Submission scripts for training and inference live in their respective domain directories (`training/rl/scripts/`, `training/il/scripts/`, `evaluation/sil/scripts/`). See [Script Reference](../docs/reference/scripts.md) for details.

## 📁 Directory Structure

```text
scripts/
├── ci/                       CI bootstrap and release automation
├── lib/                      Shared utility modules
├── linting/                  PowerShell linting and validation scripts
├── security/                 Security scanning and dependency pinning scripts
├── tests/                    Pester test organization
├── update-chart-hashes.sh    Refresh pinned Helm chart versions and SHA-256 hashes
├── Update-TerraformDocs.ps1  Regenerate Terraform module documentation
└── README.md
```

## 📦 Library

Shared utility modules used across scripts and workflows.

| File                           | Purpose                                                |
|--------------------------------|--------------------------------------------------------|
| `lib/common.sh`                | Shell logging, Terraform output accessors, AKS helpers |
| `lib/terraform-outputs.sh`     | jq-path accessor (`get_output`) for submission scripts |
| `lib/terraform-outputs.ps1`    | PowerShell Terraform output accessors                  |
| `lib/Get-VerifiedDownload.ps1` | Download files with SHA verification                   |
| `lib/Modules/CIHelpers.psm1`   | CI output formatting, annotations, step summaries      |

## 🚀 CI Scripts

CI bootstrap and release automation.

| Script                                | Purpose                                                 |
|---------------------------------------|---------------------------------------------------------|
| `ci/Add-ReleaseVerificationNotes.ps1` | Add release verification notes                          |
| `ci/Close-ReleaseMilestone.ps1`       | Close the milestone associated with a completed release |
| `ci/Install-Gitsign.ps1`              | Install the pinned gitsign release                      |
| `ci/New-SignedReleaseTag.ps1`         | Create a signed release tag                             |
| `ci/New-SigningArtifacts.ps1`         | Generate release signing artifacts                      |
| `ci/Update-ChangelogMsDate.ps1`       | Refresh changelog metadata dates                        |

### Required validation gates

`ci/ci-contract.json` declares lane ownership, selection, permissions, operation identities,
required reports, matrix shards, and reusable output bindings. `ci/select-checks.mjs` compares
the PR event base to the tested merge SHA and publishes explicit selection reasons.
Selected PR lanes and every main lane disable inner changed-file filtering.
Execution bindings pin each required `run` scalar by SHA-256 and each action by its immutable
`uses` reference and the SHA-256 of `JSON.stringify` of its parsed `with` mapping.
Review operation changes before updating these hashes; do not regenerate them to dismiss
unexplained workflow drift.

| Status         | Required-gate behavior                                                        |
|----------------|-------------------------------------------------------------------------------|
| `success`      | Require all expected operations, valid reports, counts, and artifact identity |
| `failure`      | Block the aggregate and report the first failed or missing operation          |
| `cancelled`    | Block the aggregate; report cancellation separately from validation failure   |
| `planned-skip` | Accept only a lane excluded by verified PR selection                          |
| Missing        | Block the aggregate; a green job result alone is not execution evidence       |

GitHub omits empty job outputs. An absent `first-failure` value is accepted only with otherwise
valid success or verified planned-skip evidence; required status, count, and artifact fields
must remain present.

`.github/actions/ci-outcome` validates actual step outcomes and suite-specific reports before
publishing receipts. Matrix aggregates require the complete expected job/shard inventory and
current run ID, attempt, and commit SHA. Test counts exclude skipped cases. Summary JSON and
Markdown retain comparison SHAs, selection reasons, operation/test counts, first failures,
tool versions, and artifact links without copying arbitrary step outputs.
Summaries revalidate mandatory output receipts and raw child reports from the current attempt
and reconcile their counts with exposed workflow outputs. Missing, duplicate, stale, or
contradictory mandatory evidence blocks the gate even when GitHub retains earlier successful
job outputs. Missing advisory receipts produce warnings without blocking required checks.

Receipt readers validate and read the same open file descriptor, reject linked or replaced
receipt files, and close descriptors on success and failure. CodeQL SARIF evidence uses the
native `CodeQL` driver name; keep the workflow report declaration and canonical contract aligned.
PowerShell workflow steps pass named switches through hashtable splatting rather than arrays
of flag-shaped strings.

`pr-validation-summary` remains the stable required check. `main-validation-summary` evaluates
full execution before `release-please` can start. Markdown links, Terraform tests, Terraform
documentation freshness, OSV, and Terraform security remain advisory and visible in summaries.
Container findings are advisory; container discovery, scanning, and report publication remain
required execution. Only superseded PR runs cancel automatically; main and release runs do not.
Summaries consume the workflow cancellation context even when no child started. A cancelled
or superseded run never produces a successful release gate.

Use a full workflow rerun after a failure. Partial reruns cannot reuse receipts from an earlier
attempt. Run `npm run lint:ci` and `npm run test:ci` for graph validation, negative mutations,
report-adapter tests, and the existing 80% line/branch/function coverage gate.
Local validation does not verify hosted PR checks, branch protection, or post-merge execution;
verify those separately without treating a local green result as hosted evidence.

## 🔍 Linting Scripts

PowerShell scripts for validating code quality and documentation.

| Script                              | Purpose                                     |
|-------------------------------------|---------------------------------------------|
| `Invoke-PSScriptAnalyzer.ps1`       | Static analysis for PowerShell files        |
| `Invoke-FrontmatterValidation.ps1`  | Validate YAML frontmatter in markdown files |
| `Invoke-LinkLanguageCheck.ps1`      | Detect en-us language paths in URLs         |
| `Link-Lang-Check.ps1`               | Link language checking entry point          |
| `Markdown-Link-Check.ps1`           | Validate markdown links                     |
| `Invoke-YamlLint.ps1`               | YAML file validation                        |
| `Invoke-TFLint.ps1`                 | Terraform linting                           |
| `Invoke-TerraformValidation.ps1`    | Terraform format and validate               |
| `Invoke-UvLockConsistencyCheck.ps1` | Require current locks for Python projects   |
| `Invoke-TerraformTest.ps1`          | Terraform test runner                       |
| `Invoke-GoLint.ps1`                 | Go linting via golangci-lint                |
| `Invoke-GoTest.ps1`                 | Go test runner                              |
| `Invoke-MsDateFreshnessCheck.ps1`   | Check ms.date frontmatter freshness         |
| `ConvertTo-JUnitXml.ps1`            | Convert test results to JUnit XML           |

`Invoke-TerraformValidation.ps1` writes `logs/terraform-validation-results.json` after format and per-directory validation.
Each directory records its native `exit_code` (or `null` when skipped), with initialization and parsing failures preserved as errors.
The script returns the first nonzero Terraform exit code and prints native failure output; an invalid JSON response with a zero
native exit returns `1`. The CI artifact upload reports a missing results file as an error when validation otherwise succeeds.

`Invoke-UvLockConsistencyCheck.ps1` discovers every repository Python manifest, requires a neighboring `uv.lock`, and runs
`uv lock --check` without updating the lock. The main workflow checks all projects; pull requests check changed projects.
The hosted lock check installs the interpreter pinned in `.python-version` for the root project. An empty full-repository
selection fails rather than reporting a successful no-op.
The dataviewer backend includes the editable VLM judge package in its locked `dev` and `vlm-judge` extras. The CI job
installs the `dev` extra from the backend lock without `--with-editable`, then runs tests with `uv run --no-sync` to
preserve the selected extras. The judge's Qwen dependencies remain optional.
Coverage artifacts from the Python validation jobs require a report after a successful test run. An upload error after a
test failure does not replace the test failure.

## 🔒 Security Scripts

Security scanning and dependency management scripts.

| Script                                     | Purpose                                                                                       |
|--------------------------------------------|-----------------------------------------------------------------------------------------------|
| `security/Test-DependencyPinning.ps1`      | Validate dependency pinning compliance                                                        |
| `security/Test-SHAStaleness.ps1`           | Check for outdated SHA pins                                                                   |
| `security/Test-BinaryFreshness.ps1`        | Validate pinned binary hashes and Helm chart versions; emits SARIF for GitHub Security tab    |
| `security/Modules/PinnedToolVersions.psm1` | Provide pin discovery functions for binary freshness checks                                   |
| `security/Test-HveCoreFreshness.ps1`       | Check hve-core-derived files against their reviewed release or source-header baselines        |
| `security/zap-to-sarif.py`                 | Convert ZAP results to SARIF format                                                           |
| `security/gitleaks-scan.mjs`               | Scan tested-revision history and report explicit secret-scan outcomes                         |
| `update-chart-hashes.sh`                   | Refresh pinned Helm chart versions and SHA-256 hashes in `infrastructure/setup/defaults.conf` |

### Gitleaks Scan Scope

The [Gitleaks workflow](../.github/workflows/gitleaks-scan.yml) scans the complete
history reachable from the exact checked-out revision. PR checks scan the tested
merge revision; main-push checks scan the pushed revision. Other fetched branches
and tags are excluded unless their commits are reachable from that revision.
Full checkout history remains required.

The helper uses `--full-history --diff-merges=first-parent <revision>`: merge
resolution changes are scanned without restricting ancestry traversal to the first
parent. Secrets introduced and later removed remain detectable. This is not a
repository-wide all-ref audit or a changed-lines-only scan.

Run from the repository root with Node 24, Git, and the workflow-pinned Gitleaks
8.30.0 binary:

```powershell
$env:GITLEAKS_BIN = (Get-Command gitleaks).Source
$revision = git rev-parse HEAD
node scripts/security/gitleaks-scan.mjs scan --expected-revision $revision
node --test scripts/tests/security/gitleaks-scan.test.mjs
```

In CI the expected revision comes from `GITHUB_SHA`; the helper rejects mismatched
or shallow checkouts. Reports use `logs/gitleaks-results.sarif`, redact detected
values, and retain the existing 90-day artifact policy. Each scan removes its old
untracked report first so stale output cannot validate a failed run.
Report type and size checks apply to one opened file descriptor, and reads use
that descriptor rather than reopening the pathname. Reads are bounded to 64 MiB;
observed size or metadata changes fail closed. The descriptor is closed on every
outcome. This avoids pathname-replacement races without claiming an immutable
filesystem snapshot.

| Result     | Meaning                                                                   | Exit behavior                                                 |
|------------|---------------------------------------------------------------------------|---------------------------------------------------------------|
| `clean`    | Scanner exits 0 with a valid empty SARIF report                           | Success                                                       |
| `findings` | Scanner exits 1 with a valid nonempty report                              | Failure unless `--soft-fail true` is explicitly supplied      |
| `error`    | Invalid identity, scanner failure, missing report, or inconsistent report | Failure even with soft-fail                                   |
| `not-run`  | Summary has no scan outputs and the scan never ran                        | Never reported as clean; earlier job failures remain failures |

The workflow emits `scan-status`, `scan-revision`, and `scan-exit-code`. Its
always-run summary checks those outputs against the scan step outcome before
claiming `No Secrets Found`. Findings remain visible when soft-fail is enabled.
Operational failures do not become secret-free results.

Native regressions run against isolated repositories and the checksum-verified
scanner before the real scan. Missing test prerequisites fail rather than skip.
Existing exact ignore-file behavior is preserved; this scope correction adds no
suppression. Any intentional all-ref audit needs separate ownership and
false-positive adjudication rather than attributing another branch's findings to
a PR.

### Binary and Derived-File Checks

The `Test-BinaryFreshness.ps1` script is invoked by the `check-binary-integrity.yml` workflow on a weekly schedule. It downloads each pinned GPG key, installer, and CLI archive, compares SHA-256 hashes against the canonical pin files listed below, and queries upstream Helm repositories for chart version drift.

Findings are written to `binary-freshness-results.sarif` with per-rule `helpUri` values pointing at the appropriate remediation script.

The `Test-HveCoreFreshness.ps1` script runs weekly through `check-hve-core-freshness.yml`. Each derived file declares a baseline. `release` files compare the **upstream** blob SHA at `HVE_CORE_DERIVED_FILES_REF` with the resolved newest non-draft release. `source-header` files compare the revision recorded in their header with a resolved upstream `main` revision. This reports relevant upstream changes before they appear in a release.

Source-header files must include `Adapted from microsoft/hve-core <upstream-path> as of commit <40-hex SHA>`. Comparing upstream blobs avoids false drift from intentional local adaptations.

### 🔗 Where Pins Live

The `check-binary-freshness.yml` workflow discovers supported literal assignment forms for its configured developer tools in tracked shell, PowerShell, JSON, and JSONC files through `security/Modules/PinnedToolVersions.psm1`. Shell assignments may use supported declarations, defaults, control-flow prefixes, and parenthesized command groups. PowerShell assignments may be unscoped, explicitly scoped, or environment scoped.

New assignment sites in those formats do not require workflow registration. Discovery excludes `scripts/tests/Fixtures/` and `*.Tests.ps1`; executable test helpers remain monitored. Pins in other file formats require discovery support or explicit workflow handling.

Integrity pins live with the bootstrap path that consumes them. Each location owns a different artifact class:

| Artifact class                                            | Canonical location                                                              | Why it lives there                                                                                        |
|-----------------------------------------------------------|---------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Helm chart versions + SHAs                                | `infrastructure/setup/defaults.conf`                                            | Sourced by runtime shell deploy scripts (`infrastructure/setup/*.sh`); bash-overridable via `.env.local`. |
| Dev container binaries (TFLint, OSMO CLI, NGC CLI) + SHAs | `.devcontainer/devcontainer.json`                                               | Consumed during Docker image build, before any shell can source bash variables.                           |
| VM development dependencies + SHAs                        | `infrastructure/setup/optional/isaac-sim-vm/scripts/install-dev-deps.sh`        | Consumed by the Isaac Sim VM bootstrap.                                                                   |
| ThinLinc server archive + SHA-256                         | `infrastructure/setup/optional/isaac-sim-vm/scripts/install-thinlinc-silent.sh` | Consumed by the optional ThinLinc bootstrap.                                                              |
| PowerShell bootstrap uv installer + SHA-256               | `setup-dev.ps1`                                                                 | Consumed by the PowerShell development bootstrap and verified before execution.                           |

The uv version is intentionally replicated across bootstrap entry points. Update every discovered assignment together; the freshness workflow reports inconsistent values.

All other references to these pins are read-only consumers:

| Consumer                                    | Role                                                                                                 |
|---------------------------------------------|------------------------------------------------------------------------------------------------------|
| `scripts/update-chart-hashes.sh`            | Writes chart versions + SHAs back into `defaults.conf` via `sed`; no other file touched.             |
| `scripts/security/Test-BinaryFreshness.ps1` | Reads the canonical pin files with format-specific extractors to compare artifacts against upstream. |
| `docs/contributing/component-updates.md`    | Documents `defaults.conf` as authoritative for chart pins.                                           |
| `.env.local.example`                        | User-override stubs only — does not redefine defaults.                                               |

### 🔄 Updating Chart Pins

Run `scripts/update-chart-hashes.sh` locally after bumping any pinned Helm chart version. The script runs `helm pull` for each chart, computes the SHA-256, and rewrites the matching `VAR="${VAR:-...}"` line in `infrastructure/setup/defaults.conf` so the runtime default stays in sync with the upstream digest. Commit the resulting `defaults.conf` diff alongside the chart-version bump.

Binary pins in `.devcontainer/devcontainer.json` are updated by hand when the weekly freshness check flags drift; the validator's SARIF output links to the exact file and pin to change.

## 🧪 Tests

Pester test organization matching the scripts structure. Run all tests:

```bash
npm run test:ps
```

See [tests/README.md](tests/README.md) for test organization and coverage details.

## 🚀 Usage

All scripts run both locally and in GitHub Actions workflows. They support common parameters like `-Verbose` and `-Debug` for troubleshooting.

```bash
# Run PSScriptAnalyzer on changed files
npm run lint:ps

# Run all linting
npm run lint:all

# Run Pester tests
npm run test:ps
```

## 📚 Related Documentation

* [Script Reference](../docs/reference/scripts.md) — CLI arguments and configuration
* [Script Examples](../docs/reference/scripts-examples.md) — Submission examples
* [Tests README](tests/README.md) — Pester test organization
<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
