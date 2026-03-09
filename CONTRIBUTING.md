---
title: Contributing
description: How to contribute to the Azure NVIDIA Robotics Reference Architecture
author: Microsoft Robotics-AI Team
ms.date: 2026-02-11
ms.topic: how-to
keywords:
  - contributing
  - development workflow
  - pull requests
  - code review
---

Contributions are welcome across infrastructure code, deployment automation, documentation, training scripts, and ML workflows. Read the relevant sections below before making your contribution.

If you are new to the project, start with issues labeled `good first issue` or documentation updates before making larger changes.

## Getting Started

1. Read the [Contributing Guide](docs/contributing/README.md) for prerequisites, workflow, and conventions
2. Review the [Prerequisites](docs/contributing/prerequisites.md) for required tools and Azure access
3. Fork the repository and clone your fork locally
4. Review the [README](README.md) for project overview and architecture
5. Create a descriptive feature branch (for example, `feature/...` or `fix/...`) and follow [Conventional Commits](docs/contributing/README.md#-commit-messages) for commit messages
6. Run [validation](#build-and-validation) before submitting

## Contributing Guides

Detailed documentation lives in [`docs/contributing/`](docs/contributing/):

| Guide                                                                       | Description                                                   |
|-----------------------------------------------------------------------------|---------------------------------------------------------------|
| [Contributing Guide](docs/contributing/README.md)                           | Main hub — prerequisites, workflow, commit messages, style    |
| [Prerequisites](docs/contributing/prerequisites.md)                         | Required tools, Azure access, NGC credentials, build commands |
| [Contribution Workflow](docs/contributing/contribution-workflow.md)         | Bug reports, feature requests, first contributions            |
| [Pull Request Process](docs/contributing/pull-request-process.md)           | PR workflow, reviewers, approval criteria                     |
| [Infrastructure Style](docs/contributing/infrastructure-style.md)           | Terraform conventions, shell scripts, copyright headers       |
| [Deployment Validation](docs/contributing/deployment-validation.md)         | Validation levels, testing templates, cost optimization       |
| [Cost Considerations](docs/contributing/cost-considerations.md)             | Component costs, budgeting, regional pricing                  |
| [Security Review](docs/contributing/security-review.md)                     | Security checklist, credential handling, dependency updates   |
| [Accessibility](docs/contributing/accessibility.md)                         | Accessibility scope, documentation and CLI output guidelines  |
| [Updating External Components](docs/contributing/component-updates.md)      | Process for updating reused externally-maintained components  |
| [Documentation Maintenance](docs/contributing/documentation-maintenance.md) | Documentation update triggers, ownership, freshness policy    |

## I Have a Question

Search existing resources before asking:

- Search [GitHub Issues](https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/issues) for similar questions or problems
- Check [GitHub Discussions](https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/discussions) for community Q&A
- Review the [docs/](docs/) directory for troubleshooting guides

If you cannot find an answer, open a [new discussion](https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/discussions/new) in the Q&A category. Provide context about what you are trying to accomplish, what you have tried, and any error messages. For bugs or feature requests, use [GitHub Issues](https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/issues/new) instead.

## Development Environment

Run the setup script to configure your local development environment:

```bash
./setup-dev.sh
```

This installs npm dependencies for linting, spell checking, and link validation. See the [Prerequisites](docs/contributing/prerequisites.md) guide for required tools and version requirements.

## Cleanup and Uninstall

Reverse the changes made by `setup-dev.sh` and remove deployed Azure resources.

### Remove Python Environment

The setup script creates a virtual environment at `.venv/` and syncs dependencies from `pyproject.toml`.

```bash
# Deactivate if currently active
command -v deactivate &>/dev/null && deactivate

# Remove the virtual environment
rm -rf .venv
```

### Remove External Dependencies

The setup script clones IsaacLab for IntelliSense support.

```bash
# Remove IsaacLab clone
rm -rf external/IsaacLab

# Remove Node.js linting dependencies (if installed separately via npm install)
rm -rf node_modules
```

### Clear Package Caches (Optional)

Free disk space by clearing uv and npm caches. This affects all projects using these tools, not just this repository.

```bash
# Clear uv download and build cache
uv cache clean

# Clear npm cache
npm cache clean --force
```

### Destroy Azure Infrastructure

Remove all deployed Azure resources:

```bash
cd deploy/001-iac
terraform destroy -var-file=terraform.tfvars
```

> [!WARNING]
> `terraform destroy` permanently deletes all deployed Azure resources including AKS clusters, storage accounts, Key Vault, and networking. Back up training data and model checkpoints before running this command.

For automation deployments:

```bash
cd deploy/001-iac/automation
terraform destroy -var-file=terraform.tfvars
```

Verify no orphaned resources remain:

```bash
az group list --query "[?starts_with(name, 'your-prefix')].name" -o tsv
```

See [Cost Considerations](docs/contributing/cost-considerations.md) for component costs and cleanup timing.

## Build and Validation

Run these commands to validate changes before submitting a PR:

```bash
npm run lint:md        # Markdownlint
npm run lint:links     # Markdown link validation
npm run spell-check    # cspell
```

For Terraform and shell script validation, see the [Prerequisites](docs/contributing/prerequisites.md#build-and-validation-requirements) guide.

## Updating External Components

Reused externally-maintained components (Helm charts, container images, Terraform providers, Python packages, GitHub Actions) require periodic updates for security patches and compatibility. Dependabot automates updates for Python, Terraform, and GitHub Actions ecosystems. Helm charts and container images require manual updates.

See the [Updating External Components](docs/contributing/component-updates.md) guide for the full process including component inventory, vetting criteria, and breaking change handling.

## Issue Title Conventions

Use structured titles to maintain consistency and enable automation.

### Convention Tiers

| Format         | Use Case       | Example                            |
|----------------|----------------|------------------------------------|
| `type(scope):` | Code changes   | `feat(ci): Add pytest workflow`    |
| `[Task]:`      | Work items     | `[Task]: Achieve OpenSSF badge`    |
| `[Policy]:`    | Governance     | `[Policy]: Define code of conduct` |
| `[Docs]:`      | Doc planning   | `[Docs]: Publish security policy`  |
| `[Infra]:`     | Infrastructure | `[Infra]: Sign release tags`       |

### Conventional Commits Types

| Type       | Description                             |
|------------|-----------------------------------------|
| `feat`     | New feature or capability               |
| `fix`      | Bug fix                                 |
| `docs`     | Documentation only                      |
| `refactor` | Code change that neither fixes nor adds |
| `test`     | Adding or correcting tests              |
| `ci`       | CI configuration changes                |
| `chore`    | Maintenance tasks                       |

### Repository Scopes

| Scope       | Area                     |
|-------------|--------------------------|
| `terraform` | Infrastructure as Code   |
| `scripts`   | Shell and Python scripts |
| `training`  | ML training code         |
| `workflows` | AzureML/Osmo workflows   |
| `ci`        | GitHub Actions           |
| `deploy`    | Deployment artifacts     |
| `docs`      | Documentation            |
| `security`  | Security-related changes |

### Title Examples

```text
feat(ci): Add CodeQL security scanning workflow
fix(terraform): Correct AKS node pool configuration
docs(deploy): Add VPN deployment documentation
refactor(scripts): Consolidate common functions
test(training): Add pytest fixtures
[Task]: Achieve code coverage target
[Policy]: Define input validation requirements
```

## Release Process

This project uses [release-please](https://github.com/googleapis/release-please) for automated version management. All commits to `main` must follow [Conventional Commits](https://www.conventionalcommits.org/) format:

- `feat:` commits trigger a **minor** version bump
- `fix:` commits trigger a **patch** version bump
- `docs:`, `chore:`, `refactor:` commits appear in the changelog without a version bump
- Commits with `BREAKING CHANGE:` footer trigger a **major** version bump

After merging to `main`, release-please automatically creates a release PR with updated `CHANGELOG.md` and version bumps. Merging that PR creates a GitHub Release and git tag.

For commit message format details, see [commit-message.instructions.md](.github/instructions/commit-message.instructions.md).

## Testing Requirements

All contributions require appropriate tests. This policy supports code quality and the project's [OpenSSF Best Practices](https://www.bestpractices.dev/) goals.

### Policy

- New features require accompanying unit tests.
- Bug fixes require regression tests that reproduce the fixed behavior.
- Refactoring changes must not reduce test coverage.

### Regression Testing

At least half of all bug fix PRs must include a regression test.

A regression test is required when:

- The bug affected user-facing functionality
- The fix changes control flow
- The bug could reasonably recur

A regression test may be omitted when:

- The bug was in documentation only
- The fix is purely cosmetic (whitespace, formatting)
- A test is technically impractical (requires external services that cannot be mocked)

#### What Counts as a Regression Test

| Test Type                              | Counts as Regression Test             |
|----------------------------------------|---------------------------------------|
| Unit test verifying the fix            | Yes                                   |
| Integration test covering the scenario | Yes                                   |
| Manual test documented in PR           | Only if automated test is impractical |
| Informal local verification            | No                                    |

#### Bug Fix PR Requirements

When submitting a bug fix:

1. Link to the issue being fixed
2. Include a regression test, or document why one is omitted
3. Describe what the test verifies

Reviewers verify regression tests are included. Compliance is tracked over time via PR labels (`has-regression-test`, `regression-test-omitted`).

### Running Tests

Once a `tests/` directory exists, run the full test suite:

```bash
pytest tests/
```

Run tests within the devcontainer:

```bash
uv run pytest tests/
```

Run tests with coverage reporting:

```bash
coverage run -m pytest tests/
coverage report -m
```

### Test Organization

Tests mirror the source directory structure under `tests/`:

| Source Path                     | Test Path                     |
|---------------------------------|-------------------------------|
| `src/training/utils/env.py`     | `tests/unit/test_env.py`      |
| `src/training/utils/metrics.py` | `tests/unit/test_metrics.py`  |
| `src/common/cli_args.py`        | `tests/unit/test_cli_args.py` |

### Test Categories

| Marker        | Description                        | Planned CI Behavior           |
|---------------|------------------------------------|-------------------------------|
| *(default)*   | Unit tests, fast, no external deps | Always runs                   |
| `slow`        | Tests exceeding 5 seconds          | Runs on main, optional on PRs |
| `integration` | Requires external services         | Runs on main only             |
| `gpu`         | Requires CUDA runtime              | Excluded from standard CI     |

Skip categories selectively:

```bash
pytest tests/ -m "not slow and not gpu"
```

### Coverage Targets

Coverage thresholds increase with each milestone:

| Milestone | Minimum Coverage |
|-----------|------------------|
| v0.4.0    | 40%              |
| v0.5.0    | 60%              |
| v0.6.0    | 80%              |

These coverage levels are contribution targets for local test runs. CI enforcement of coverage thresholds is planned for a future milestone.

### Configuration

Pytest and coverage are not yet centrally configured in `pyproject.toml`. When adding tests, follow standard pytest conventions (a `tests/` directory with shared fixtures as needed) and align with existing tests in this repository.

### Shell and Infrastructure Tests

Use [BATS-core](https://github.com/bats-core/bats-core) for shell script tests, [Pester v5](https://pester.dev/) for PowerShell tests, and the native `terraform test` framework for Terraform modules. When adding tests, include framework-specific details in the README for each area.

## Documentation Maintenance

Documentation stays current through update triggers, ownership rules, and freshness reviews. See the [Documentation Maintenance](docs/contributing/documentation-maintenance.md) guide for the complete policy including review criteria, PR requirements, and release lifecycle.

## Governance

This project uses a corporate-sponsored maintainer model. See [GOVERNANCE.md](GOVERNANCE.md) for decision-making processes, roles, and how governance can change.

## Internationalization

This project currently produces no user-facing applications or localizable content. All technical documentation is maintained in English.

If user-facing components are added in the future, follow [W3C Internationalization](https://www.w3.org/International/) guidelines and [Unicode CLDR](https://cldr.unicode.org/) for locale data. Use [BCP 47](https://www.rfc-editor.org/info/bcp47) language tags for locale identifiers.

## Code of Conduct

This project adopts the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). See [CODE_OF_CONDUCT.md](.github/CODE_OF_CONDUCT.md) for details, or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with questions.

## Reporting Security Issues

**Do not** report security vulnerabilities through public GitHub issues. See [SECURITY.md](SECURITY.md) for reporting instructions.

## Support

For questions and community discussion, see [SUPPORT.md](SUPPORT.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

## Attribution

This contributing guide is adapted for reference architecture contributions and Azure + NVIDIA robotics infrastructure.

Copyright (c) Microsoft Corporation. Licensed under the MIT License.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
