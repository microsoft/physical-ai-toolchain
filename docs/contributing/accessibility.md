---
sidebar_position: 13
title: Accessibility Best Practices
description: Standards for accessible documentation, CLI output, and the dataviewer web application
author: Microsoft Robotics-AI Team
ms.date: 2026-09-19
ms.topic: reference
---

Apply accessibility requirements when authoring documentation, CLI output, and dataviewer user interfaces.

## Scope

This project applies accessibility best practices to three areas.

| Area          | What the project controls                                                  |
|---------------|----------------------------------------------------------------------------|
| Documentation | Markdown files rendered on GitHub and documentation sites                  |
| CLI output    | Shell scripts in `infrastructure/setup/` and `scripts/` that emit messages |
| Web UI        | React application in `data-management/viewer/frontend/`                    |

> [!NOTE]
> The dataviewer frontend enables the recommended `jsx-a11y` ESLint rules. Static linting is not evidence of WCAG conformance; interaction and assistive-technology checks remain part of UI review.

## Documentation Accessibility

All Markdown files follow these conventions, which are enforced by markdownlint (MD045, MD001) and PR review.

* Provide descriptive alt text for every image (`![Alt text](path)`)
* Follow heading hierarchy without skipping levels (H1 → H2 → H3)
* Use descriptive link text instead of raw URLs or "click here"
* Use tables and lists for structured data rather than dense paragraphs
* Use GitHub alerts (`> [!NOTE]`, `> [!WARNING]`) for important callouts
* Provide text equivalents for any diagrams or visual content

### Alt Text Guidelines

Alt text describes the content and purpose of an image.

| Image Type           | Alt Text Approach                                      |
|----------------------|--------------------------------------------------------|
| Architecture diagram | Summarize components and data flow shown               |
| Screenshot           | Describe the UI state and highlighted element          |
| Logo or badge        | State the badge name and status                        |
| Decorative image     | Use empty alt (`![](path)`) only when truly decorative |

## CLI Output Accessibility

Shell scripts support the [NO_COLOR](https://no-color.org) standard. When the `NO_COLOR` environment variable is set (any value), scripts suppress ANSI color codes so output works with screen readers and log aggregators.

Run any deployment script without color:

```bash
NO_COLOR=1 ./infrastructure/setup/01-deploy-robotics-charts.sh
```

### Implementation Pattern

Shared color functions in `scripts/lib/common.sh` check `NO_COLOR` before emitting escape sequences:

```bash
if [[ -z "${NO_COLOR+x}" ]]; then
  info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
  warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
  error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }
else
  info()  { printf '[INFO]  %s\n' "$*"; }
  warn()  { printf '[WARN]  %s\n' "$*" >&2; }
  error() { printf '[ERROR] %s\n' "$*" >&2; }
fi
```

## Dataviewer Accessibility

Use semantic controls, accessible names, visible keyboard focus, and keyboard-operable interactions when changing the viewer. Preserve the accessibility behavior of the existing Radix UI primitives. Run `npm run validate` from `data-management/viewer/frontend/` after installing its dependencies, and review keyboard navigation and screen-reader behavior for affected controls.

The [frontend ESLint configuration](../../data-management/viewer/frontend/eslint.config.js) defines the automated accessibility checks.

## Generated Artifacts

Automated pipelines, Terraform plans, and Helm chart outputs are out of scope. Accessibility standards apply only to human-authored content committed to the repository.

## OpenSSF Compliance

This page, combined with markdownlint enforcement (MD045, MD001) and NO_COLOR support in deployment scripts, satisfies the OpenSSF Best Practices Silver criterion `accessibility_best_practices`:

> *The project MUST include a statement about software accessibility in its documentation, addressing at minimum accessibility of the documentation itself and software output.*

## Related Documentation

* [Pull Request Process](pull-request-process.md)
* [Security Review](security-review.md)
