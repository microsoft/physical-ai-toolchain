# Pull Request

## Description

<!-- Brief description of changes. Link related issues using Closes #123 -->

Closes #

## Type of Change
<!-- Mark relevant options with [x] -->

- [ ] 🐛 Bug fix (non-breaking change fixing an issue)
- [ ] ✨ New feature (non-breaking change adding functionality)
- [ ] 💥 Breaking change (fix or feature causing existing functionality to change)
- [ ] 📚 Documentation update
- [ ] 🏗️ Infrastructure change (Terraform/IaC)
- [ ] ♻️ Refactoring (no functional changes)

## Component(s) Affected
<!-- Mark all that apply -->

- [ ] `infrastructure/terraform/prerequisites/` - Azure subscription setup
- [ ] `infrastructure/terraform/` - Terraform infrastructure
- [ ] `infrastructure/setup/` - OSMO control plane / Helm
- [ ] `workflows/` - Training and evaluation workflows
- [ ] `training/` - Training pipelines and scripts
- [ ] `docs/` - Documentation

## Testing Performed
<!-- Describe testing. Check applicable items -->

- [ ] Terraform `plan` reviewed (no unexpected changes)
- [ ] Terraform `apply` tested in dev environment
- [ ] Training scripts tested locally with Isaac Sim
- [ ] OSMO workflow submitted successfully
- [ ] Smoke tests passed (`smoke_test_azure.py`)

## Documentation Impact
<!-- Select one -->

- [ ] No documentation changes needed
- [ ] Documentation updated in this PR
- [ ] Documentation issue filed

## Bug Fix Checklist

*Complete this section for bug fix PRs. Skip for other contribution types.*

- [ ] Linked to issue being fixed
- [ ] Regression test included, OR
- [ ] Justification for no regression test:

## Checklist

- [ ] My code follows the [project conventions](copilot-instructions.md)
- [ ] Commit messages follow [conventional commit format](instructions/commit-message.instructions.md)
- [ ] I have performed a self-review
- [ ] Documentation impact assessed above
- [ ] No new linting warnings introduced
