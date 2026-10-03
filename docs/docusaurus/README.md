---
title: Docusaurus Site Operations
description: Install, validate, test, serve, and troubleshoot the documentation site
author: Microsoft Robotics-AI Team
ms.date: 2026-10-03
ms.topic: how-to
---

<!-- cspell:words Chevrotain dagre ES2024 rjxr -->

Operate the repository documentation site from the repository root or from `docs/docusaurus`.

## 📋 Prerequisites

| Tool          | Requirement | Purpose                                  |
|---------------|-------------|------------------------------------------|
| Node.js       | 24+         | Docusaurus build and test runtime        |
| npm           | Bundled     | Locked dependency installation           |
| Google Chrome | Current     | Production-build Playwright verification |

## 🚀 Local Development

Install the locked dependency graph, then start the development server:

```bash
cd docs/docusaurus
npm ci
npm start
```

The site uses the `/physical-ai-toolchain/` base path.

## 🔒 Mermaid Dependency Policy

Mermaid is pinned to `12.1.0` in [package.json](package.json). It uses Chevrotain 13, which has no
`lodash-es` dependency. No `lodash-es` override is needed: `dagre-d3-es` resolves a patched copy.
Every resolved `lodash-es` copy must be `4.18.0` or later to address
[GHSA-r5fr-rjxr-66jc](https://github.com/advisories/GHSA-r5fr-rjxr-66jc) and
[GHSA-f23m-r3pf-42rh](https://github.com/advisories/GHSA-f23m-r3pf-42rh).
Preserve the other overrides, including `lodash`, and avoid unrelated dependency updates.

[docusaurus.config.js](docusaurus.config.js) explicitly selects `layout: 'dagre'` and `look: 'classic'` while retaining
the `neutral` light theme and `dark` dark theme. Keep these settings to avoid the Mermaid 12 default redesign.

The [Mermaid 12 browser baseline](https://github.com/mermaid-js/mermaid/releases/tag/mermaid%4012.0.0)
is ES2024 and Safari 17.4+. Node.js 24+ satisfies its Node.js 22.12+ requirement.

For future Mermaid bumps:

1. Regenerate the docs lockfile with npm against the public registry:
   `npm --prefix docs/docusaurus install --registry=https://registry.npmjs.org`.
2. Inspect the complete lockfile and run
   `npm --prefix docs/docusaurus ls lodash-es --all` after a clean `npm --prefix docs/docusaurus ci`.
   Reject any `lodash-es` copy below `4.18.0`, including nested copies, and confirm installation leaves the
   lockfile unchanged. A patched hoisted copy alone is not sufficient.
3. Run `npm run validate:docs` and the existing [browser validation](#-browser-validation). Check Mermaid SVGs,
   accessible names and descriptions, and visual presentation in both light and dark modes.
4. Require Dependency Review, required CI checks, and review approval before merging.

## ✅ Validation

Run the local-safe validation lane from the repository root:

```bash
npm run validate:docs
```

This command runs accessibility lint, label consistency, mandatory TypeScript checks, Jest behavior and axe tests,
LCOV coverage thresholds, Mermaid source validation, and a production build. It does not start a browser.

Accessibility lint uses `eslint-plugin-jsx-a11y-x`, a community fork of `eslint-plugin-jsx-a11y` that supports ESLint 9 and 10.
The [ESLint configuration](eslint.config.mjs) enables the fork's recommended `jsx-a11y-x` rules and permits
`tabIndex` on elements with `role="group"`. Use the `jsx-a11y-x/` namespace for rule overrides.

Run coverage alone when iterating on shared components:

```bash
npm run docs:test:coverage
```

Coverage output is written to `docs/docusaurus/coverage/`. Global statements, branches, functions, and lines must each
remain at or above 80 percent.

## 🌐 Browser Validation

Run the production-build system-Chrome suite separately:

```bash
npm run ci:docs:setup:e2e
npm run ci:docs:test:e2e
```

Run the setup command once per environment to install Google Chrome and its system dependencies. The test command
builds the site once, generates route and Mermaid manifests, starts a non-reused loopback server, executes
representative keyboard, search, adaptive, table, and Mermaid journeys, and crawls every deployed route with axe.

Keyboard tests wait for rendered disclosure visibility as well as `aria-expanded` before traversing links.
Mobile navigation tests also wait for the background to become inert before checking focus containment.
Use these observable states rather than fixed delays; keep the subsequent keyboard-focus assertions.

Page readiness does not wait for network idle. `expectPageReady` waits for hydration, a canonical link that matches
the address bar, loaded fonts, one visible `main` landmark, and the Mermaid diagram count recorded for the route in
`build/mermaid-routes.json`. On the search page with a query, it also waits for the announced result count. The
canonical check proves a client-side route has rendered, because Docusaurus keeps the previous page on screen until
the next one loads. Give any new client-rendered widget its own explicit readiness condition.

The exhaustive crawl remains one test with one evidence record. It visits two route states at a time, each in a fresh
browser context, so storage, theme, and instrumentation never carry between visits. Healthy visits close quietly;
failing visits keep a screenshot, and retried runs record a trace. If the collector is interrupted, it stops
scheduling, closes open contexts, and marks unfinished states as blocking findings.

Browser evidence is written to these ignored paths:

| Artifact                                         | Content                                             |
|--------------------------------------------------|-----------------------------------------------------|
| `build/deployed-routes.json`                     | Versioned deployed-route and exclusion contract     |
| `build/mermaid-routes.json`                      | Mermaid source, route, name, and description joins  |
| `test-results/browser-version.json`              | Google Chrome channel and runtime version           |
| `test-results/playwright-results.json`           | Machine-readable Playwright results                 |
| `test-results/site-crawl-results.json`           | Route status, axe violations, and incomplete checks |
| `playwright-report/` and `test-results/*/trace*` | Human-readable report and failure diagnostics       |

Set `DOCS_E2E_PORT` to an unoccupied loopback port when 3001 is unavailable. Set `DOCS_E2E_OUTPUT_DIR` and
`DOCS_E2E_REPORT_DIR` to isolate concurrent runs. `DOCS_E2E_FAST=1` may reuse a manually started server for local
iteration, but that mode does not produce acceptance evidence.

CI retains browser screenshots, traces, and the HTML report when browser validation fails or is cancelled. CI retains
LCOV output for 30 days and uploads the `docusaurus` flag to Codecov through OIDC. Local LCOV generation and workflow
validation prove the report and upload configuration. Only a GitHub Actions run proves Codecov ingestion, the named
80 percent status, and file attribution under `docs/docusaurus/src/`.

## 📦 Build and Serve

Build and preview the production output:

```bash
npm run docs:build
npm run docs:serve
```

The Pages workflow preserves test, build, and deploy ordering. Deployment runs only after the reusable Docusaurus
quality workflow passes. A local build does not prove Pages publication. Use the successful workflow artifact,
`github-pages` environment record, and deployment URL as provider evidence.

## 🔍 Troubleshooting

| Failure                           | Action                                                                                                                |
|-----------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| Missing or stale dependencies     | Run `npm ci` in `docs/docusaurus`                                                                                     |
| TypeScript or Jest failure        | Run `npm run docs:test:coverage`                                                                                      |
| Browser test timeout              | Confirm Google Chrome is installed and set `DOCS_E2E_PORT` to an unoccupied port                                      |
| Missing route in exhaustive crawl | Inspect `build/deployed-routes.json` and `test-results/site-crawl-results.json`; the route sets must match exactly    |
| Browser artifact collision        | Set unique `DOCS_E2E_PORT`, `DOCS_E2E_OUTPUT_DIR`, and `DOCS_E2E_REPORT_DIR` values for each concurrent run           |
| Axe failure                       | Inspect the JSON result, retained screenshot, trace, incomplete attachment, and HTML report                           |
| Mermaid metadata failure          | Inspect `build/mermaid-routes.json` and add one active non-empty title and description to each deployed Mermaid block |
| Known dependency audit findings   | Review the complete-lock audit record; do not apply forced downgrades                                                 |
| Broken-anchor build warning       | Repair the named source anchor without weakening strict link settings                                                 |

Image zoom, client redirects, stale-document automation, Terraform documentation drift checks, and GitHub Pages
deployment remain separate preserved capabilities. Image zoom and redirects are installed but have no active content
fixture, so configuration proves plugin loading rather than user-visible behavior. Terraform documentation drift is
advisory in both pull-request and main-branch workflows; its JSON artifact records findings without making the check a
blocking policy.

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
