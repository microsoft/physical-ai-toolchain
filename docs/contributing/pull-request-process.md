---
sidebar_position: 5
title: Pull Request Process
description: PR workflow, reviewer assignment, review cycles, approval criteria, and update process
author: Microsoft Robotics-AI Team
ms.date: 2026-09-21
ms.topic: how-to
keywords:
  - pull request
  - code review
  - approval
  - contributing
---

> [!NOTE]
> This guide expands on the [Pull Request Process](README.md#-pull-request-process) section of the main contributing guide.

Automated linting, security checks, and component tests form the validation baseline. Deployment and workflow validation supplement these checks for environment-dependent changes; document any untested scenarios and costs in the PR.

## PR Workflow Steps

1. Fork and Branch: Create a feature branch from your fork's main branch
2. Make Changes: Implement improvements following style guides
3. Validate Locally: Run applicable static checks and tests, plus plan or deployment validation when needed
4. Create Draft PR: Open draft PR with validation documentation
5. Request Review: Mark PR ready when validation complete

## Review Process

### Reviewer Assignment

Maintainers assign reviewers based on contribution type:

| Contribution Type    | Primary Reviewer              |
|----------------------|-------------------------------|
| Terraform modules    | Cloud Infrastructure Engineer |
| Kubernetes manifests | DevOps/SRE Engineer           |
| Training scripts     | ML Engineer                   |
| OSMO workflows       | Robotics Developer            |
| Documentation        | Any maintainer                |
| Security changes     | Security Contributor          |

### Review Cycles

* First review: Focus on architecture, security, cost implications
* Subsequent reviews: Address specific feedback
* Final review: Verify all validation documentation complete

### Approval Criteria

* [ ] Follows style guides (commit messages, markdown, infrastructure)
* [ ] Appropriate validation level completed
* [ ] Testing documentation provided in PR description
* [ ] No security vulnerabilities introduced
* [ ] Cost implications documented (if applicable)
* [ ] Breaking changes clearly communicated
* [ ] Accessibility guidelines followed (alt text, semantic headings, descriptive links)

## Update Process

This project uses release-please for automated semantic versioning and changelog generation. With `bump-minor-pre-major: true`, breaking changes increment the minor version before 1.0 and the major version from 1.0 onward. Users who fork the blueprint review release notes and migration guidance before adopting updates.

## Update Types

### Documentation Updates

* Continuous improvements to READMEs, guides, and troubleshooting docs
* No announcement needed for minor clarifications
* Significant new guides announced via repository discussions

### Enhancement Updates

* New capabilities (e.g., new network mode, new Azure service integration)
* Announced via GitHub Releases with usage examples
* Backward compatible when possible

### Breaking Changes

* Infrastructure modifications that require resource recreation
* Terraform variable/output changes
* Deployment script interface changes

### Breaking Change Communication

* Conventional Commit breaking-change marker (`!` or a `BREAKING CHANGE:` footer) for release-please
* Breaking changes highlighted in the GitHub Release
* Migration guide in release notes
* Updated deployment documentation
* Announcement in repository discussions

## Component Updates

### Dependency Management

Update dependencies regularly for security patches and feature improvements:

```bash
# Update Terraform provider versions
terraform init -upgrade

# Update Helm chart versions
helm repo update
helm search repo nvidia-gpu-operator --versions

# Restore the affected Python project from its committed lock
uv sync --frozen
```

Use Dependabot for updates to existing Python dependencies. Run `uv lock` for
dependency additions or deliberate lock regeneration only from an approved
environment with direct access to the public registry. Proxy-restricted
contributors must not regenerate locks locally. Use `uv sync --frozen` as the
local restore check. The read-only `uv lock --check` CI gate fails any PR whose
lock drifts from its `pyproject.toml`.

### Migration Approach

When pulling upstream updates:

```bash
# Create branch for upstream updates
git checkout -b upstream-updates

# Pull latest changes
git fetch upstream
git merge upstream/main

# Resolve conflicts (prioritize your customizations)
# Test deployment in dev environment
# Merge to your main branch after validation
```

## Staying Updated

* Watch repository for releases
* Review release notes before pulling updates
* Test updates in dev environment before production
* Maintain customizations in separate branch/overlay

## Pull Request Inactivity Policy

Pull requests that remain inactive accumulate merge conflicts and delay feedback loops. This section defines closure timelines for inactive PRs. Automation that enforces this policy is a separate effort that references these thresholds.

For issue and discussion inactivity policy, see [Inactivity Closure Policy](https://github.com/microsoft/physical-ai-toolchain/blob/main/GOVERNANCE.md#inactivity-closure-policy) in GOVERNANCE.md.

### Active Pull Requests

The inactivity clock runs only when the PR is waiting on the author. Reviewer-side delays do not count against the author.

| Stage  | Trigger                                                           | Label                 | Action                  |
|:-------|:------------------------------------------------------------------|:----------------------|:------------------------|
| Active | Author activity within the past 14 days while `waiting-on-author` | (none)                | Normal review cycle     |
| Paused | PR is labeled `waiting-on-reviewer`                               | `waiting-on-reviewer` | Inactivity clock paused |
| Stale  | 14 days without author activity while `waiting-on-author`         | `stale`               | Reminder comment posted |
| Closed | 7 days after `stale` label without author activity                | `closed-stale`        | PR closed with summary  |

Label usage:

* `waiting-on-author` is applied when the reviewer requests changes or the author needs to resolve conflicts. The inactivity clock starts.
* `waiting-on-reviewer` is applied when the author has addressed feedback and awaits re-review. The inactivity clock pauses.

### Draft Pull Requests

Draft PRs are fully exempt from inactivity closure. Converting a draft to "ready for review" starts the normal active PR lifecycle.

### Exemptions

The following conditions prevent automatic closure of a pull request:

* PR is in draft state
* PR is labeled `do-not-close`
* PR is labeled `waiting-on-reviewer`

Reopening rules:

* Authors can reopen a stale-closed PR at any time with updated changes
* Reopening removes the `stale` label and resets the inactivity clock

## Related Documentation

* [Contributing Guide](README.md) - Main contributing guide with all sections
* [Contribution Workflow](contribution-workflow.md) - Legal, bug reports, enhancements, first contributions
* [Deployment Validation](deployment-validation.md) - Validation levels and testing templates
