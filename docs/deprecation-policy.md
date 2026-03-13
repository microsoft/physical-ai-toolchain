---
sidebar_position: 99
title: Deprecation Policy
description: Policy for handling deprecated external interfaces including announcement, maintenance duration, migration guidance, and breaking-change communication
author: Microsoft Robotics-AI Team
ms.date: 2026-03-04
ms.topic: reference
keywords:
  - deprecation
  - breaking changes
  - migration
  - openssf
---

This policy defines how external interfaces are deprecated, maintained during a transition period, and eventually removed. It satisfies the OpenSSF Best Practices Silver badge criterion `interfaces_deprecation`, which requires projects to document deprecation announcement, maintenance duration, migration assistance, and breaking-change communication.

## Scope

This policy applies to all external interfaces that users or downstream automation depend on:

| Interface Type         | Examples                                    | Location                              |
|------------------------|---------------------------------------------|---------------------------------------|
| Shell script arguments | `--config-dir`, `--tf-dir`, `--dry-run`     | `deploy/002-setup/`, `scripts/`       |
| Environment variables  | `GPU_OPERATOR_VERSION`, `NS_GPU_OPERATOR`   | `deploy/002-setup/defaults.conf`      |
| Terraform variables    | `environment`, `should_deploy_postgresql`   | `deploy/001-iac/variables.tf`         |
| Terraform outputs      | `aks_cluster`, `postgresql_connection_info` | `deploy/001-iac/outputs.tf`           |
| Configuration schemas  | Recording config properties                 | `config/recording_config.schema.json` |
| Workflow templates     | AzureML and OSMO YAML fields                | `workflows/`                          |

Internal implementation details, private functions, and module-internal resources are excluded. Changes to these do not require a deprecation notice.

## Deprecation Lifecycle

Every deprecation follows four stages:

1. Announce: Document the deprecation in the pull request that introduces the replacement. Include the rationale, replacement interface, and removal timeline.
2. Deprecation Period: Maintain the deprecated interface alongside its replacement for the defined period. Both interfaces function identically during this window.
3. Migrate: Provide migration guidance in the same pull request. Users follow the documented steps to transition from the deprecated interface to the replacement.
4. Remove: Remove the deprecated interface after the deprecation period expires. The removal commit references the original deprecation notice.

## Deprecation Period

Deprecated interfaces are maintained for **90 calendar days or 1 version release containing breaking changes (whichever is longer)**.

> [!NOTE]
> While the project is pre-1.0 with `bump-minor-pre-major: true`, breaking changes trigger minor version bumps (0.x.0). Post-1.0, the same commits trigger major bumps. The compound wording "1 version release containing breaking changes" is version-scheme-agnostic and applies correctly in both phases.

## Announcement Channels

Each deprecation is communicated through all of the following channels:

| Channel                | Required Action                                                    |
|------------------------|--------------------------------------------------------------------|
| CHANGELOG.md           | Add entry under a Deprecated section                               |
| Affected documentation | Add `> [!WARNING]` alert with deprecation timeline and replacement |
| GitHub Discussions     | Post announcement in the Announcements category                    |
| GitHub Issues          | Create tracking issue with `deprecated` label                      |
| PR description         | Include migration steps and deprecation rationale                  |

## Migration Guidance

Every deprecation pull request includes:

* Description of what changed and why
* Replacement interface with usage example
* Step-by-step migration instructions
* Timeline for removal

Use this template for deprecation notices in documentation:

````markdown
```markdown
> [!WARNING]
> **Deprecated**: `OLD_INTERFACE` is deprecated and will be removed in vX.Y.0 (estimated YYYY-MM-DD).
>
> **Replacement**: Use `NEW_INTERFACE` instead.
>
> **Migration**:
>
> 1. Replace `OLD_INTERFACE` with `NEW_INTERFACE` in your configuration.
> 2. Update any scripts referencing the old interface.
> 3. See [migration guide](link-to-guide) for details.
```
````

## Breaking Change Communication

Breaking changes follow the decision tier defined in [GOVERNANCE.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/GOVERNANCE.md), which requires two maintainer approvals with explicit breaking-change acknowledgment and a documented migration path.
The communication plan in [Pull Request Process](contributing/pull-request-process.md) specifies that breaking changes include a `[BREAKING]` prefix in the GitHub Release, migration guidance in release notes, updated deployment documentation, and an announcement in repository discussions.

No external interface will be removed without a deprecation notice in a prior release.

## Example Deprecation Notice

The following example demonstrates a deprecation of the `GPU_OPERATOR_VERSION` environment variable in `deploy/002-setup/defaults.conf`, renamed to `NVIDIA_GPU_OPERATOR_VERSION` for naming consistency.

Documentation alert added to the affected guide:

```markdown
> [!WARNING]
> **Deprecated**: `GPU_OPERATOR_VERSION` is deprecated and will be removed in v0.5.0 (estimated 2026-06-04).
>
> **Replacement**: Use `NVIDIA_GPU_OPERATOR_VERSION` instead.
>
> **Migration**:
>
> 1. Replace `GPU_OPERATOR_VERSION` with `NVIDIA_GPU_OPERATOR_VERSION` in `defaults.conf` and any `.env.local` overrides.
> 2. Update CI/CD pipelines or scripts that set this variable.
> 3. Both variable names are honored during the deprecation period; the old name takes lower precedence.
```

CHANGELOG.md entry:

```markdown
### Deprecated

* `GPU_OPERATOR_VERSION` environment variable in `defaults.conf` is deprecated in favor of `NVIDIA_GPU_OPERATOR_VERSION`. The old variable will be removed after 90 days or 1 breaking-change release (whichever is longer).
```

Migration steps:

1. Open `deploy/002-setup/defaults.conf` and rename `GPU_OPERATOR_VERSION` to `NVIDIA_GPU_OPERATOR_VERSION`.
2. Search for `GPU_OPERATOR_VERSION` in any `.env.local` files or CI/CD variables and update them.
3. Verify the deployment with `./01-deploy-robotics-charts.sh --config-preview`.

## Active Deprecations

No interfaces are currently deprecated. When deprecations occur, they are listed here with replacement guidance and removal timelines.

## Related Documentation

* [GOVERNANCE.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/GOVERNANCE.md) — Decision tiers for breaking changes
* [Documentation Maintenance](contributing/documentation-maintenance.md) — Deprecation notice formatting rules
* [Pull Request Process](contributing/pull-request-process.md) — Breaking change communication channels
* [Contribution Workflow](contributing/contribution-workflow.md) — Enhancement submission requirements
