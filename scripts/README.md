---
title: Scripts
description: CI/CD scripts, shared libraries, linting, security, and Pester tests for the Physical AI Toolchain.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-02
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

## 🔍 Linting Scripts

PowerShell scripts for validating code quality and documentation.

| Script                             | Purpose                                     |
|------------------------------------|---------------------------------------------|
| `Invoke-PSScriptAnalyzer.ps1`      | Static analysis for PowerShell files        |
| `Invoke-FrontmatterValidation.ps1` | Validate YAML frontmatter in markdown files |
| `Invoke-LinkLanguageCheck.ps1`     | Detect en-us language paths in URLs         |
| `Link-Lang-Check.ps1`              | Link language checking entry point          |
| `Markdown-Link-Check.ps1`          | Validate markdown links                     |
| `Invoke-YamlLint.ps1`              | YAML file validation                        |
| `Invoke-TFLint.ps1`                | Terraform linting                           |
| `Invoke-TerraformValidation.ps1`   | Terraform format and validate               |
| `Invoke-TerraformTest.ps1`         | Terraform test runner                       |
| `Invoke-GoLint.ps1`                | Go linting via golangci-lint                |
| `Invoke-GoTest.ps1`                | Go test runner                              |
| `Invoke-MsDateFreshnessCheck.ps1`  | Check ms.date frontmatter freshness         |
| `ConvertTo-JUnitXml.ps1`           | Convert test results to JUnit XML           |

## 🔒 Security Scripts

Security scanning and dependency management scripts.

| Script                         | Purpose                                                                                    |
|--------------------------------|--------------------------------------------------------------------------------------------|
| `security/Test-DependencyPinning.ps1` | Validate dependency pinning compliance                                                     |
| `security/Test-SHAStaleness.ps1`      | Check for outdated SHA pins                                                                |
| `security/Test-BinaryFreshness.ps1`   | Validate pinned binary hashes and Helm chart versions; emits SARIF for GitHub Security tab |
| `security/zap-to-sarif.py`            | Convert ZAP results to SARIF format                                                        |
| `update-chart-hashes.sh`              | Refresh pinned Helm chart versions and SHA-256 hashes in `infrastructure/setup/defaults.conf` |

The `Test-BinaryFreshness.ps1` script is invoked by the `check-binary-integrity.yml` workflow on a weekly schedule. It downloads each pinned GPG key, installer, and CLI archive, compares SHA-256 hashes against the values pinned in `.devcontainer/install-dev-deps.sh` and `.devcontainer/devcontainer.json`, and queries upstream Helm repositories for chart version drift. Findings are written to `binary-freshness-results.sarif` with per-rule `helpUri` values pointing at the appropriate remediation script.

### 🔗 Where Pins Live

Pins are split across two files by structural necessity, not duplication. Each file owns a different class of artifact:

| Artifact class               | Canonical location                                | Why it lives there                                                                                        |
|------------------------------|---------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Helm chart versions + SHAs   | `infrastructure/setup/defaults.conf`              | Sourced by runtime shell deploy scripts (`infrastructure/setup/*.sh`); bash-overridable via `.env.local`. |
| Dev container binaries (OSMO CLI, NGC CLI) + SHAs | `.devcontainer/devcontainer.json` | Consumed during Docker image build, before any shell can source bash variables.                           |

All other references to these pins are read-only consumers:

| Consumer                                | Role                                                                                     |
|-----------------------------------------|------------------------------------------------------------------------------------------|
| `scripts/update-chart-hashes.sh`        | Writes chart versions + SHAs back into `defaults.conf` via `sed`; no other file touched. |
| `scripts/security/Test-BinaryFreshness.ps1` | Reads both canonical files (`Get-ShellVariable`, `Get-JsonVariable`) to compare against upstream. |
| `docs/contributing/component-updates.md`    | Documents `defaults.conf` as authoritative for chart pins.                           |
| `.env.local.example`                        | User-override stubs only — does not redefine defaults.              |

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
