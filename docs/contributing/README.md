---
sidebar_position: 1
title: Contributing to Physical AI Toolchain
description: Guide for contributing including prerequisites, deployment validation, and style conventions
author: Microsoft Robotics-AI Team
ms.date: 2026-02-11
ms.topic: how-to
keywords:
  - azure
  - nvidia
  - robotics
  - kubernetes
  - terraform
  - contributing
  - reference architecture
---

Contributions improve this reference architecture for the robotics and AI community. This project accepts contributions for infrastructure code (Terraform/shell), deployment automation, documentation, training scripts, and ML workflows.

Reference architectures emphasize deployment validation over automated testing. Contributors validate changes through actual Azure deployments, which incurs cost. This guide provides cost-transparent guidance for different contribution scopes.

Contributions can include:

* Infrastructure improvements (Terraform modules, networking, security)
* Deployment automation enhancements (shell scripts, Kubernetes manifests)
* Documentation updates (guides, troubleshooting, architecture diagrams)
* Training script optimizations (Python, MLflow integration)
* Workflow templates (AzureML, OSMO)
* Bug fixes and issue resolution

## 📖 Contributing Guides

| Guide                                                     | Description                                                      |
|-----------------------------------------------------------|------------------------------------------------------------------|
| [Prerequisites](prerequisites.md)                         | Required tools, Azure access, NGC credentials, build commands    |
| [Contribution Workflow](contribution-workflow.md)         | Bug reports, feature requests, first contributions               |
| [Pull Request Process](pull-request-process.md)           | PR workflow, review process, update procedures                   |
| [Infrastructure Style](infrastructure-style.md)           | Terraform conventions, shell script standards, copyright headers |
| [Deployment Validation](deployment-validation.md)         | Validation levels, testing templates, cost optimization          |
| [Cost Considerations](cost-considerations.md)             | Component costs, budgeting, regional pricing                     |
| [Security Review](security-review.md)                     | Security checklist, credential handling, dependency updates      |
| [Accessibility](accessibility.md)                         | Accessibility scope, documentation and CLI output guidelines     |
| [Updating External Components](component-updates.md)      | Process for updating reused externally-maintained components     |
| [Documentation Maintenance](documentation-maintenance.md) | Update triggers, ownership, review criteria, freshness policy    |
| [Fuzzing and Property-Based Testing](fuzzing.md)          | Fuzz targets, property tests, Hypothesis and fast-check patterns |
| [Roadmap](ROADMAP.md)                                     | 12-month project roadmap, priorities, and success metrics        |

### Quick Reference

| Changing...                | Read...                                                                                                 |
|----------------------------|---------------------------------------------------------------------------------------------------------|
| Terraform modules          | [Infrastructure Style](infrastructure-style.md), then [Deployment Validation](deployment-validation.md) |
| Shell scripts              | [Infrastructure Style](infrastructure-style.md)                                                         |
| Training workflows         | [Deployment Validation](deployment-validation.md) (Level 4)                                             |
| Security-sensitive code    | [Security Review](security-review.md)                                                                   |
| Any PR                     | [Cost Considerations](cost-considerations.md) for testing budget                                        |
| Accessibility requirements | Follow [Accessibility](accessibility.md) for docs and CLI output                                        |
| Documentation policy       | [Documentation Maintenance](documentation-maintenance.md)                                               |

## 📋 Prerequisites

Install required tools and configure Azure access before contributing. See [Prerequisites and Build Validation](prerequisites.md) for complete details including Azure access requirements, NVIDIA NGC setup, and build validation commands.

| Tool       | Minimum Version |
|------------|-----------------|
| Terraform  | 1.9.8           |
| Azure CLI  | 2.65.0          |
| kubectl    | 1.31            |
| Helm       | 3.16            |
| Node.js    | 20+ LTS         |
| Python     | 3.11+           |
| shellcheck | 0.10+           |

## 📜 Code of Conduct

This project adopts the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).

For more information, see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with questions or comments.

## ❓ I Have a Question

Search existing resources before asking questions:

* Search [GitHub Issues](https://github.com/microsoft/physical-ai-toolchain/issues) for similar questions or problems
* Check [GitHub Discussions](https://github.com/microsoft/physical-ai-toolchain/discussions) for community Q&A
* Review [docs/](https://github.com/microsoft/physical-ai-toolchain/tree/main/docs) for troubleshooting guides
* See [azureml-validation-job-debugging.md](../evaluation/azureml-validation-job-debugging.md) for common deployment and workflow issues

If you cannot find an answer:

1. Open a [new discussion](https://github.com/microsoft/physical-ai-toolchain/discussions/new) in the Q&A category
2. Provide context: What you are trying to accomplish, what you have tried, error messages or unexpected behavior
3. Include relevant details: Azure region, Terraform version, deployment step, error logs

Maintainers and community members respond to discussions. For bugs or feature requests, use GitHub Issues instead.

## 🤝 I Want To Contribute

See the [Contribution Workflow](contribution-workflow.md) guide for detailed instructions on:

* Legal notice and CLA requirements
* Reporting bugs with deployment context
* Suggesting enhancements
* Making your first code contribution
* Improving documentation

## 💬 Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <subject>
```

Types: `feat` (new feature), `fix` (bug fix), `docs` (documentation), `refactor` (code refactoring), `chore` (maintenance), `ci` (CI/CD changes), `security` (CVE fixes)

Scopes: `terraform`, `k8s`, `azureml`, `osmo`, `scripts`, `docs`, `deploy`

Use present tense, keep subject under 100 characters, capitalize subject line. Provide detailed body for non-trivial changes.

For complete commit message guidance with examples, see [commit-message.instructions.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/.github/instructions/commit-message.instructions.md).

## 📝 Markdown Style

All Markdown documents require YAML frontmatter:

```yaml
---
title: Document Title
description: Brief description (150 chars max)
author: Microsoft Robotics-AI Team
ms.date: YYYY-MM-DD
ms.topic: concept | how-to | reference | tutorial
---
```

Use ATX-style headers, tables for structured data, GitHub alert syntax for callouts, and language-specified code blocks. Validate with `npm run lint:md`.

For complete Markdown guidance, see [docs-style-and-conventions.instructions.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/.github/instructions/docs-style-and-conventions.instructions.md).

## 🏗️ Infrastructure as Code Style {#infrastructure-as-code-style}

Infrastructure code follows strict conventions for consistency, security, and maintainability.

### Key Terraform Conventions

* Format with `terraform fmt -recursive deploy/` before committing
* Use descriptive snake_case variables with prefixes (`enable_`, `is_`, `aks_`)
* Include standard tags on all Azure resources
* Prefer managed identities over service principals
* Store secrets in Key Vault, never in code

### Key Shell Script Conventions

* Begin scripts with `#!/usr/bin/env bash` and `set -euo pipefail`
* Include header documentation with prerequisites, environment variables, and usage
* Validate with `shellcheck` before committing

### Copyright Headers

All source files require the Microsoft copyright header:

```text
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
```

For complete conventions with examples, see [Infrastructure Style Guide](infrastructure-style.md).

## 🧪 Deployment Validation

This reference architecture validates through deployment rather than automated testing. Choose validation level based on contribution scope and cost constraints.

### Validation Levels

| Level                   | What                                                        | When to Use                  | Cost   |
|-------------------------|-------------------------------------------------------------|------------------------------|--------|
| **Level 1: Static**     | `npm run lint:tf:validate`, `shellcheck`, `npm run lint:md` | Every contribution           | $0     |
| **Level 2: Plan**       | `terraform plan` with documented output                     | Terraform changes            | $0     |
| **Level 3: Deployment** | Full deployment in dev subscription                         | Major infrastructure changes | $25-50 |
| **Level 4: Workflow**   | Training job execution                                      | Script/workflow changes      | $5-30  |

Static validation is required for all PRs:

```bash
npm run lint:tf:validate
shellcheck infrastructure/**/*.sh scripts/**/*.sh
npm run lint:md
```

For complete validation procedures, testing templates, and cost optimization strategies, see [Deployment Validation Guide](deployment-validation.md).

## 🔄 Pull Request Process

See the [Pull Request Process](pull-request-process.md) guide for the complete workflow including reviewer assignment, review cycles, approval criteria, and update process.

## 💰 Cost Considerations

Full deployment testing incurs Azure costs. Plan accordingly and destroy resources promptly.

> [!NOTE]
> Cost estimates are approximate and subject to change.
> Use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for current rates.

### Testing Budget Summary

| Contribution Type   | Typical Cost | Testing Approach          |
|---------------------|--------------|---------------------------|
| Documentation       | $0           | Linting only              |
| Terraform modules   | $10-25       | Plan + short deployment   |
| Training scripts    | $15-30       | Single training job       |
| Full infrastructure | $25-50       | Complete deployment cycle |

### Key Cost Drivers

* GPU VMs: ~$3.06/hour per Standard_NC24ads_A100_v4 node
* Managed services: ~$50-100/month combined (Storage, Key Vault, PostgreSQL, Redis)

### Cost Minimization

```bash
# Single GPU node, public network mode
terraform apply -var="gpu_node_count=1" -var="network_mode=public"

# Always destroy after testing
terraform destroy -auto-approve -var-file=terraform.tfvars
```

For component cost breakdowns, budgeting commands, and regional pricing, see [Cost Considerations Guide](cost-considerations.md).

## 🔒 Security Review Process

Security-sensitive contributions require additional review to ensure Azure best practices.

### Security Review Scope

* RBAC and permissions changes
* Private endpoints and networking configuration
* Credential handling and secrets management
* Network policies and firewall rules
* Workload identity configuration

### Key Requirements

* Managed identities over service principals
* Secrets in Key Vault, never in code
* Least privilege RBAC assignments
* Security scanning before PR submission

### Reporting Security Issues

**DO NOT** report vulnerabilities through public GitHub issues. Report to Microsoft Security Response Center (MSRC). See [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md).

For the complete security checklist, dependency update process, and scanning requirements, see [Security Review Guide](security-review.md).

## ♿ Accessibility

All contributions follow the project's [Accessibility Best Practices](accessibility.md).

Documentation standards:

* Provide descriptive alt text for every image
* Follow heading hierarchy without skipping levels
* Use descriptive link text instead of raw URLs
* Use tables and lists for structured data

CLI output standards:

* Support the [NO_COLOR](https://no-color.org) standard in shell scripts
* Shared color functions in `scripts/lib/common.sh` check `NO_COLOR` before emitting ANSI escape sequences

See [Accessibility](accessibility.md) for full guidelines.

## 📚 Attribution

This contributing guide is adapted for reference architecture contributions and Azure + NVIDIA robotics infrastructure.

Copyright (c) Microsoft Corporation. Licensed under the MIT License.
