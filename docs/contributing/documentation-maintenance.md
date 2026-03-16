---
sidebar_position: 11
title: Documentation Maintenance Policy
description: Update triggers, ownership, review criteria, freshness policy, and release lifecycle for project documentation
author: Microsoft Robotics-AI Team
ms.date: 2026-03-09
ms.topic: reference
keywords:
  - documentation
  - maintenance
  - openssf
  - release lifecycle
  - code review
---

This guide expands on the [Documentation Maintenance](README.md#-contributing-guides) section of the main contributing guide.

Documentation accuracy directly affects deployment success and contributor confidence. This policy defines when documentation updates are required, who owns review, and how freshness is maintained. It satisfies the OpenSSF Best Practices Silver `documentation_current` criterion.

## Update Triggers

Documentation updates are required when changes affect what users read, follow, or depend on.

| Trigger Category                          | Examples                                                           |
|-------------------------------------------|--------------------------------------------------------------------|
| API, CLI, or configuration schema changes | New Terraform variable, changed Helm value, updated script flag    |
| Deployment steps or prerequisites         | New Azure provider registration, changed kubectl version, new tool |
| Feature additions, deprecations, removals | New training workflow, deprecated OSMO backend, removed script     |
| User-reported gaps                        | Confusing instructions, missing steps, outdated screenshots        |

When a code PR introduces any of these changes, the author must update affected documentation in the same PR or file a documentation issue referencing the PR.

## Ownership

Documentation ownership maps areas to responsible teams. The `.github/CODEOWNERS` file configures automatic review requests for code owners.

| Area                                           | Owner                                                      |
|------------------------------------------------|------------------------------------------------------------|
| `/docs/**/*.md`                                | Repository maintainers (`@microsoft/edge-ai-core-dev`) |
| `/deploy/**/README.md`                         | Repository maintainers (`@microsoft/edge-ai-core-dev`) |
| `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md` | Repository maintainers (`@microsoft/edge-ai-core-dev`) |
| Root `README.md`                               | Repository maintainers (`@microsoft/edge-ai-core-dev`) |

> [!TIP]
> Full role definitions and governance structure are tracked in [#98](https://github.com/microsoft/physical-ai-toolchain/issues/98) and [#99](https://github.com/microsoft/physical-ai-toolchain/issues/99). Current CODEOWNERS configuration uses the `@microsoft/edge-ai-core-dev` team as code owners for all documentation paths.

## Review Process

Documentation reviewers verify four criteria on every PR that touches Markdown files:

| Criterion                | What to Check                                                    |
|--------------------------|------------------------------------------------------------------|
| Technical accuracy       | Content matches current code behavior, commands run successfully |
| Cross-reference validity | Internal links resolve, external URLs return 200, anchors exist  |
| Frontmatter currency     | `ms.date` reflects the edit date in ISO 8601 format (YYYY-MM-DD) |
| Completeness             | New parameters documented, removed features cleaned up, no stubs |

Run validation commands before approving documentation PRs:

```bash
npm run lint:md        # Markdownlint
npm run lint:links     # Link validation
npm run spell-check    # cspell
```

## Freshness Policy

Documentation freshness is tracked through the `ms.date` frontmatter field.

* Update `ms.date` on every edit to a Markdown file.
* A full documentation review occurs at each milestone release. Maintainers verify all guides against current deployment behavior and update `ms.date` for reviewed files.
* Report stale or inaccurate content using the [documentation issue template](https://github.com/microsoft/physical-ai-toolchain/issues/new?template=05-documentation.yml). Include the file path and a description of the inaccuracy.

> [!NOTE]
> Automated `ms.date` freshness checking runs in two contexts:
>
> * **Weekly scans**: [weekly-validation.yml](../../.github/workflows/weekly-validation.yml) runs every Monday at 9 AM UTC, checking all markdown files and failing on stale documentation (90+ days since last update). Creates one GitHub issue per stale file with automatic duplicate detection.
> * **Pull request checks**: [pr-validation.yml](../../.github/workflows/pr-validation.yml) checks only modified files during PR review and blocks merges when stale documentation is detected
>
> When stale files are detected:
>
> * Weekly runs create or update GitHub issues tagged with `stale-docs`, `documentation`, `automated`, and `needs-triage` labels
> * PR validation fails and must be resolved before merging (update `ms.date` in the PR)
> * Download artifacts (msdate-freshness-results.json) or view job summaries for detailed file lists

### Fixing Stale Documentation

When files exceed the 90-day freshness threshold:

1. Review the file content for accuracy and relevance
2. Update any outdated information, links, or examples
3. Update the `ms.date` field in frontmatter to today's date (YYYY-MM-DD format)
4. Commit changes with a descriptive message referencing the content updates

The `ms.date` field should be updated on every substantive content edit, not just when flagged by the freshness check. This tracking helps readers assess documentation currency.

## PR Documentation Requirements

Code PRs must include documentation updates when any of the following apply:

* User-facing behavioral changes (new output, changed defaults, different error messages)
* New CLI parameters or configuration options (Terraform variables, Helm values, script flags)
* Changed prerequisites or environment requirements (tool versions, Azure provider registrations)
* Deprecated or removed functionality (scripts, workflows, configuration options)

The PR template includes a Documentation Impact section. Select the appropriate option:

* No documentation impact: internal refactors, CI-only changes, dependency bumps
* Documentation updated in this PR: changes included alongside code
* Documentation issue filed: separate issue tracks the documentation work

## Release Lifecycle

This section defines versioning, release notes, deprecation notices, and breaking change communication.

### Versioning

This project uses [release-please](https://github.com/googleapis/release-please) for automated semantic versioning. Version bumps follow conventional commit types:

| Commit Type        | Version Bump | Example                                        |
|--------------------|--------------|------------------------------------------------|
| `feat:`            | Minor        | `feat(terraform): add GPU monitoring module`   |
| `fix:`             | Patch        | `fix(scripts): correct AKS credential path`    |
| `BREAKING CHANGE:` | Major        | Footer in commit triggers major bump           |
| `security:`        | Patch        | `security: fix CVE-2024-XXXX input validation` |
| `docs:`, `chore:`  | None         | Appears in changelog without version bump      |

`CHANGELOG.md` is updated automatically by release-please when a release PR merges.

### Release Notes

Release notes are generated from conventional commit messages. Maintainers review the release-please PR before merging to verify:

* Commit categorization is accurate (features, fixes, documentation)
* Breaking changes are highlighted with migration guidance
* No sensitive information appears in commit messages

### Deprecation Notices

Deprecation requires advance notice to give users time to adapt:

* Announce deprecations at minimum one milestone before removal.
* Document the deprecation in `CHANGELOG.md` under a Deprecated section.
* Update affected guides with a `> [!WARNING]` alert indicating the deprecation timeline and replacement.
* Remove deprecated functionality only after the announced milestone.

For the complete deprecation lifecycle, scope, and deprecation period definitions, see the [Deprecation Policy](../deprecation-policy.md).

### Breaking Changes

Breaking changes require explicit communication and migration support:

* Use the `BREAKING CHANGE:` footer in the commit message to trigger a major version bump.
* Include migration guidance in the PR description explaining what changed and how to update.
* Update all affected documentation (READMEs, guides, workflow templates) in the same PR.
* Add a `> [!CAUTION]` alert in relevant guides describing the breaking change and linking to migration steps.

## Related Documentation

* [Pull Request Process](pull-request-process.md) - PR workflow, reviewer assignment, approval criteria
* [Contributing Guide](README.md) - Prerequisites, workflow, commit messages
* [Documentation Issue Template](https://github.com/microsoft/physical-ai-toolchain/issues/new?template=05-documentation.yml) - Report stale or inaccurate content

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
