---
title: Docusaurus Site Operations
description: Install, validate, test, serve, and troubleshoot the documentation site
author: Microsoft Robotics-AI Team
ms.date: 2026-09-14
ms.topic: how-to
---

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

## ✅ Validation

Run the local-safe validation lane from the repository root:

```bash
npm run validate:docs
```

This command runs accessibility lint, label consistency, mandatory TypeScript checks, Jest behavior and axe tests,
LCOV coverage thresholds, Mermaid source validation, and a production build. It does not start a browser.

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
