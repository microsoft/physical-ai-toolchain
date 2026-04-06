---
title: PowerShell Testing
description: Testing patterns, directory structure, and execution guide for Pester-based PowerShell tests
---

PowerShell tests use [Pester 5.7.1](https://pester.dev/) with a split configuration and execution model. The `pester.config.ps1` file builds a `[PesterConfiguration]` object, and `Invoke-PesterTests.ps1` invokes `Invoke-Pester` with that configuration.

## 🏗️ Directory Structure

```text
scripts/tests/
├── Invoke-PesterTests.ps1              # Runner wrapper for local and CI execution
├── pester.config.ps1                   # Pester 5.x configuration generator
├── Fixtures/                           # Static test data organized by domain
│   ├── Npm/                            # package.json fixtures
│   ├── Security/                       # Checksum and download fixtures
│   └── Workflows/                      # GitHub Actions workflow fixtures
├── Mocks/                              # Reusable mock modules
│   └── GitMocks.psm1                   # Git CLI and CI environment mocks
└── security/                           # Security-related test files
    ├── SecurityHelpers.Tests.ps1
    └── Test-DependencyPinning.Tests.ps1
```

## 🚀 Running Tests

### Local Execution

```powershell
./scripts/tests/Invoke-PesterTests.ps1
```

Run with code coverage:

```powershell
./scripts/tests/Invoke-PesterTests.ps1 -CodeCoverage
```

Run tests in a specific subdirectory:

```powershell
./scripts/tests/Invoke-PesterTests.ps1 -TestPath ./scripts/tests/security
```

### npm Script

```bash
npm run test:ps
```

### CI Mode

CI mode enables NUnit XML output, non-zero exit on failure, and GitHub Actions annotations:

```powershell
./scripts/tests/Invoke-PesterTests.ps1 -CI -CodeCoverage
```

## ⚙️ Conventions

### Test File Naming

Test files use `.Tests.ps1` extension and mirror source layout:

| Source Path                                   | Test Path                                                 |
|-----------------------------------------------|-----------------------------------------------------------|
| `scripts/security/Test-DependencyPinning.ps1` | `scripts/tests/security/Test-DependencyPinning.Tests.ps1` |
| `scripts/linting/Check-Something.ps1`         | `scripts/tests/linting/Check-Something.Tests.ps1`         |

Test subdirectories mirror the `scripts/` source layout. Create matching directories under `scripts/tests/` as you add tests for new source areas.

### Tags

Pester tags control which tests run in different contexts:

| Tag           | Purpose                                               | Default Behavior |
|---------------|-------------------------------------------------------|------------------|
| `Unit`        | Standard unit tests                                   | Included         |
| `Integration` | Tests requiring live services or network access       | Excluded         |
| `Slow`        | Long-running tests unsuitable for quick feedback loop | Excluded         |

Apply tags at the `Describe` block level:

```powershell
Describe 'Test-SHAPinning' -Tag 'Unit' {
    # ...
}
```

### Fixtures

Place static test data under `Fixtures/` in domain-specific subdirectories.

Reference fixtures using `$PSScriptRoot` relative paths in `BeforeAll`:

```powershell
BeforeAll {
    $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Workflows'
}
```

### Mocks

Reusable mock modules live under `Mocks/`. Import them in `BeforeAll` with `-Force` to ensure a clean state:

```powershell
BeforeAll {
    $mockPath = Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1'
    Import-Module $mockPath -Force
}
```

`GitMocks.psm1` provides helpers for saving, restoring, and simulating CI environment variables across GitHub Actions and Azure DevOps contexts.

### Code Coverage

Coverage targets scripts under `linting/`, `security/`, and `lib/` directories. The threshold is 80%.

Enable coverage locally with `-CodeCoverage` on the runner or `pester.config.ps1`. CI workflows produce JaCoCo XML at `logs/coverage.xml` for artifact upload.

Files matching `*.Tests.ps1` are excluded from coverage analysis automatically.
