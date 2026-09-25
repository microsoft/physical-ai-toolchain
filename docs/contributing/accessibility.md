---
sidebar_position: 13
title: Accessibility Best Practices
description: Standards for accessible documentation, CLI output, and project-owned web applications
author: Microsoft Robotics-AI Team
ms.date: 2026-09-24
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

## Documentation Collection and Promotion

Documentation publication is asynchronous. A push to `main` collects release-scope automated evidence, including the DCS13 qualified-review obligations, but requires only automated completeness. Collection does not publish the site or imply reviewer approval.

The collection artifact is named `docusaurus-accessibility-evidence-release-<run-id>`.
It retains the exact tested production build and a repository-relative mirror of source envelopes, composition inputs,
the original bundle, contrast crops, diagnostics, validation results, and reviewer handoff.
Its package manifest verifies paths, sizes, digests, and closure from a fresh directory.
Release artifacts have 90-day retention; other cadences have 30-day retention.
Incomplete diagnostic packages cannot be promoted.

### Protected Review Configuration

Configure these controls before dispatching **Docusaurus Accessibility Promotion**:

| Control                                                       | Required configuration                                                                                                                                                                                                          |
|---------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `accessibility-release` environment                           | Require authorized reviewers, prevent self-approval, and restrict deployment branches to `main`.                                                                                                                                |
| `ACCESSIBILITY_REVIEWER_EVIDENCE_BRANCH` environment variable | Name the same-repository protected branch containing approved, privacy-minimized reviewer evidence. No default is provided.                                                                                                     |
| `ACCESSIBILITY_REVIEW_REGISTRY_DIGEST` environment secret     | Store the independently approved HVE canonical reviewer-registry digest. Do not derive it from the submitted package during promotion.                                                                                          |
| Reviewer-evidence branch                                      | Restrict writes to the authorized review process, prohibit force pushes, and enable branch protection or an applicable active ruleset. The GitHub branch API must report `protected: true`; unreadable protection fails closed. |
| `github-pages` environment                                    | Retain the Pages deployment approval and branch controls.                                                                                                                                                                       |

The registry anchor uses the HVE domain `hve-a11y:review-registry:v1`, canonicalizing the registry without its `digest` field. It is not the raw file SHA-256. The separately supplied prior-bundle anchor uses `hve-a11y:evidence-bundle:v1`, excluding `bundleDigest`. Copy the prior anchor from the independently retained collection record, not from newly submitted reviewer data.

Configure the reviewer-evidence branch variable and registry-digest secret in both protected environments:
`accessibility-release` for promotion and `github-pages` for the final publication check.
After Pages approval, deployment verifies that the original reviewer commit remains reachable from the protected
branch and reads the current registry as inert Git data.
Its canonical digest must still match both the promoted registry and the protected Pages trust anchor.
A changed or revoked registry record invalidates the promotion and requires fresh authorized approval.

### Reviewer-Evidence Package

Commit only the approved JSON data under `reviewer-evidence/docusaurus/<source-sha>/` on the protected evidence branch:

```text
reviewer-evidence/docusaurus/<source-sha>/
├── package-manifest.json
├── review-registry.json
└── supplements/
    └── <supplement-id>.json
```

The package manifest contains `schemaVersion: "1.0.0"`, the exact `sourceRevision`, and a `files` array. Each entry records the relative `path`, raw-file `sha256`, and `sizeBytes` for the registry or a supplement. Do not include the manifest itself in that array. Include every completed supplement and preserve prior supplement history. Supplement filenames contain only letters, digits, hyphens, and underscores before `.json`.

Promotion reads Git tree metadata and blobs without checking out the evidence revision.
The exact 40-character commit must be an ancestor of the configured protected branch head.
Extra paths inside the selected evidence directory, hidden entries, nested supplement directories, symlinks,
executable files, duplicate JSON keys, missing files, and mismatched digests are rejected.
Unrelated repository paths are never read as reviewer data.
Raw speech, restricted transcripts, credentials, screenshots containing personal data, and private reviewer identities
remain outside Git and CI artifacts.

### Promotion and Publication Procedure

1. Wait for a successful `push` run named **CI** from `.github/workflows/main.yml` on the still-current `main` revision. Record its run ID, immutable collection artifact ID, and independently retained prior-bundle digest. Promotion requires both the workflow path and name to match the artifact's producing run.
2. Download and verify the complete retained package. Perform qualified review against its exact local production build, not a rebuilt or currently hosted site.
3. Commit the approved registry, completed supplements, and closed package manifest to the protected reviewer-evidence branch. Record the exact commit SHA.
4. Dispatch **Docusaurus Accessibility Promotion** from current `main` and obtain the `accessibility-release` environment approval.
5. Supply all four inputs below. Promotion verifies the same-repository run, exact workflow, event, branch, current revision, artifact ownership, retention, protected evidence ancestry, and both independent digest anchors before recomposition.
6. Promotion recomposes the retained source envelopes with the original prior bundle and every supplement. It preserves source, build, configuration, fixture, campaign, and observation identity while advancing only evaluation time so expired review records remain blocking. Both pinned HVE and project validation require release completeness.
7. A successful promotion retains `docusaurus-accessibility-promoted-<promotion-run-id>` for 90 days. **Deploy Documentation Site** accepts only that successful same-repository promotion and verifies its closed manifest, original collection, release bundle, validation manifest, and build digest. It uploads the retained build directly to Pages without rebuilding.

| Dispatch input             | Required value                                                                        |
|----------------------------|---------------------------------------------------------------------------------------|
| `collection-run-id`        | Positive immutable run ID of the successful current-main collection.                  |
| `collection-artifact-id`   | Positive immutable artifact ID belonging to that exact run and release artifact name. |
| `review-evidence-revision` | Exact lowercase 40-character reviewer-evidence commit SHA.                            |
| `prior-bundle-digest`      | Independently retained lowercase 64-character HVE bundle digest.                      |

Promotion retains every original run-context binding, including the collection's `harnessDigest`, tool and lock digests, environment, and run identifier. It does not recalculate those bindings from a fresh runtime or self-test outputs. The acquired HVE Git revision, skill tree, and pinned input hashes establish runtime provenance independently; a collection-time output hash is not proof of the runtime pin.

Deployment executes only trusted `main` code, never code from an artifact or reviewer ref.
The build job verifies the complete package before uploading the retained build to Pages.
After Pages environment approval, the publish job repeats artifact, registry, evidence-validity, and current-main
verification with read-only repository and Actions access. Pages and identity-token write permissions remain confined
to the publishing jobs.
An unavailable verification fails closed.
Any intervening main revision invalidates the candidate: collect and review the new build.
Missing, expired, stale, conflicting, quarantined, or incomplete evidence blocks publication; no successful results are manufactured.

> [!WARNING]
> Production promotion is externally blocked by the pinned HVE composer at `56c30bcbbba1a8235970c44f5e79a9d83e296f54`.
> It reports release incompleteness when any informing result is `CANT_TELL`, even when deciding results pass.
> This implementation preserves that failure and `--require-completeness release`; it does not patch HVE, discard
> informing evidence, or claim a successful production release.
> A reviewed upstream correction and pin update, followed by a real complete release validation, are required before
> publication can succeed.

## Generated Artifacts

Accessibility requirements apply to generated content that people consume or use to make decisions, including diagrams, previews, reports, and evidence summaries. Terraform plans and Helm outputs remain outside the current user-journey inventory unless a project decision activates them.

Retained accessibility bundles contain approved summaries, method and scope limits, stable identifiers, and artifact digests. They exclude credentials, environment-specific endpoints, raw screen-reader speech, private reviewer identities, and restricted transcript paths.

## OpenSSF Compliance

This page, combined with markdownlint enforcement (MD045, MD001) and NO_COLOR support in deployment scripts, satisfies the OpenSSF Best Practices Silver criterion `accessibility_best_practices`:

> *The project MUST include a statement about software accessibility in its documentation, addressing at minimum accessibility of the documentation itself and software output.*

## Related Documentation

* [Pull Request Process](pull-request-process.md)
* [Security Review](security-review.md)
