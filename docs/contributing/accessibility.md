---
sidebar_position: 13
title: Accessibility Best Practices
description: Standards for accessible documentation, CLI output, and project-owned web applications
author: Microsoft Robotics-AI Team
ms.date: 2026-09-19
ms.topic: reference
---

This document defines accessibility requirements for documentation, runtime web interfaces, generated evidence, and CLI output in this repository. Use it when changing user-facing content or interaction behavior.

## Scope

This project targets WCAG 2.2 Level AA for project-owned web interfaces. Section 508 and EN 301 549 require an explicit scope decision based on authoritative organizational facts.

| Area               | What the project controls                                                        |
|--------------------|----------------------------------------------------------------------------------|
| Documentation      | Markdown files rendered on GitHub and the Docusaurus site                        |
| Dataset Viewer     | Project-owned React interaction, adaptive layout, and assistive-technology paths |
| Docusaurus runtime | Local production build, navigation, search, content semantics, and visual states |
| Generated evidence | Project mappings, state proofs, composed bundles, and non-attestation summaries  |
| CLI output         | Shell scripts in `infrastructure/setup/` and `scripts/` that emit messages       |

GitHub Pages hosting behavior remains provider-owned. Accessibility criterion evidence targets the immutable local Docusaurus production build before publication.

> [!NOTE]
> The Dataset Viewer enables the recommended `jsx-a11y` ESLint rules. Static linting is not evidence of WCAG conformance; interaction and assistive-technology checks remain part of UI review.

## Documentation Accessibility

All Markdown files follow these conventions, which are enforced by markdownlint (MD045, MD001) and PR review.

* Provide descriptive alt text for every image (`![Alt text](path)`)
* Follow heading hierarchy without skipping levels (H1 → H2 → H3)
* Use descriptive link text instead of raw URLs or "click here"
* Use tables and lists for structured data rather than dense paragraphs
* Use GitHub alerts (`> [!NOTE]`, `> [!WARNING]`) for important callouts
* Provide text equivalents for any diagrams or visual content
* Confirm that headings, table relationships, alerts, code, lists, task states, and links retain their intended semantics after rendering
* Run source activation guards when content introduces media, timed behavior, forms, authentication, gestures, or new interactive components

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

## Runtime Accessibility Validation

The Docusaurus test workflow separates deterministic automation from qualified human judgment. Automated tooling can decide only the propositions its method is adequate to verify; it does not establish complete conformance.

| Evidence layer     | Required coverage                                                                                          |
|--------------------|------------------------------------------------------------------------------------------------------------|
| Source and unit    | Content activation guards, component semantics, route and feature inventories                              |
| Browser automation | Axe, keyboard paths, focus, live regions, accessibility-tree relationships, contrast, reflow, and geometry |
| Qualified review   | Spoken output, reading order, meaning, label quality, graphic equivalence, and exception approval          |
| Release evaluation | Current automated evidence plus approved, digest-bound qualified-human results                             |

Run deterministic Docusaurus checks against the local production build:

```bash
npm --prefix docs/docusaurus run test:coverage
npm --prefix docs/docusaurus run ci:test:e2e
```

The pull-request scope covers deterministic Docusaurus journeys DCS01-DCS12. Release scope adds DCS13 and every required qualified-human cell. Missing manual evidence remains `NOT_ASSESSED`; it does not block deterministic bundle production, but it prevents release completeness.

## Qualified Accessibility Review

Use Windows NVDA with Microsoft Edge and a human-led JAWS pass for the supported screen-reader baseline. VoiceOver and mobile assistive technology are unsupported until an explicit scope change adds them.

Review DCS02-DCS08 and DCS10-DCS11 against the exact local production build:

1. Record the source revision, build digest, browser and assistive-technology versions, route, state, viewport or zoom, input mode, and expected result.
2. Verify spoken search status and result position, heading and landmark navigation, table relationships, reading order, labels, link purpose, diagram equivalence, browser zoom, focus perception, and approved exceptions.
3. Classify each result as `PASS`, `FAIL`, `CANT_TELL`, `NOT_ASSESSED`, or `INAPPLICABLE`. Include a limitation and re-entry trigger for every `NOT_ASSESSED` result.
4. Store raw speech, transcripts, screenshots with personal data, and restricted observations outside the repository and ordinary CI artifacts.
5. Author a privacy-minimized result using the upstream `qualified-human-result.schema.json`. Include a non-secret reviewer identifier, qualification and approval record digests, approved observation summary, artifact digests, validity, and supersession fields.
6. Recompose against the prior bundle and an independently retained prior-bundle digest. Supply the reviewer-registry digest from caller-controlled CI configuration rather than the registry file.

Qualified review uses two cadences:

| Cadence           | Review boundary                                                                                        |
|-------------------|--------------------------------------------------------------------------------------------------------|
| Initial baseline  | Full-site evaluation of all applicable routes, states, complete processes, and qualified-human methods |
| Routine release   | Representative WCAG-EM sample plus a random 10 percent of the eligible page set                        |
| Full reevaluation | Repeat after changes to build identity, navigation, search, rendering, evidence methods, or scope      |

The composed bundle always retains `attestation: false`. A qualified result contributes evidence; it does not independently authorize a public conformance claim.

## Generated Artifacts

Accessibility requirements apply to generated content that people consume or use to make decisions, including diagrams, previews, reports, and evidence summaries. Terraform plans and Helm outputs remain outside the current user-journey inventory unless a project decision activates them.

Retained accessibility bundles contain approved summaries, method and scope limits, stable identifiers, and artifact digests. They exclude credentials, environment-specific endpoints, raw screen-reader speech, private reviewer identities, and restricted transcript paths.

## OpenSSF Compliance

This page, combined with markdownlint enforcement (MD045, MD001) and NO_COLOR support in deployment scripts, satisfies the OpenSSF Best Practices Silver criterion `accessibility_best_practices`:

> *The project MUST include a statement about software accessibility in its documentation, addressing at minimum accessibility of the documentation itself and software output.*

## Related Documentation

* [Pull Request Process](pull-request-process.md)
* [Security Review](security-review.md)
