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
├── lib/                 Shared utility modules
├── linting/             PowerShell linting and validation scripts
├── security/            Security scanning and dependency pinning scripts
├── tests/               Pester test organization
├── Update-TerraformDocs.ps1
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

| Script                       | Purpose                                |
|------------------------------|----------------------------------------|
| `Test-DependencyPinning.ps1` | Validate dependency pinning compliance |
| `Test-SHAStaleness.ps1`      | Check for outdated SHA pins            |
| `zap-to-sarif.py`            | Convert ZAP results to SARIF format    |

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
