---
name: AW Dependabot PR Review
description: Advisory agentic review of Dependabot dependency update PRs for physical-ai-toolchain
engine: copilot
timeout-minutes: 15
if: >
  github.event.pull_request.draft == false &&
  github.event.pull_request.user.login == 'dependabot[bot]'
on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - "**/package.json"
      - "**/package-lock.json"
      - "**/pnpm-lock.yaml"
      - "**/pyproject.toml"
      - "**/uv.lock"
      - "**/requirements*.txt"
      - "**/go.mod"
      - "**/go.sum"
      - "**/*.tf"
      - "**/*.tfvars"
      - "**/Dockerfile*"
      - "!.github/workflows/**"
  bots: ["dependabot[bot]"]
  reaction: eyes
  status-comment: true
permissions:
  contents: read
  pull-requests: read
network:
  allowed:
    - defaults
    - github
    - python
    - node
    - go
    - terraform
    - containers
    - api.osv.dev
    - services.nvd.nist.gov
runtimes:
  node:
    version: lts/*
    action-repo: actions/setup-node
    action-version: 53b83947a5a98c8d113130e565377fae1a50d02f # v6.3.0
  python:
    version: "3.12"
    action-repo: actions/setup-python
    action-version: a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0
  uv:
    action-repo: astral-sh/setup-uv
    action-version: cec208311dfd045dd5311c1add060b2062131d57 # v8.0.0
  go:
    action-repo: actions/setup-go
    action-version: 4a3601121dd01d1626a1e23e37211e3254c1c06c # v6.4.0
steps:
  - name: Install jq for Dependabot body and JSON intel parsing
    shell: bash
    run: |
      set -euo pipefail
      if ! command -v jq >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y --no-install-recommends jq
      fi
      jq --version
  - name: Set up Terraform
    uses: hashicorp/setup-terraform@5e8dbf3c6d9deaf4193ca7a8fb23f2ac83bb6c85 # v4.0.0
    with:
      terraform_version: "1.9.8"
      terraform_wrapper: false
  - name: Set up TFLint
    uses: terraform-linters/setup-tflint@b480b8fcdaa6f2c577f8e4fa799e89e756bb7c93 # v6.2.2
    with:
      tflint_version: latest
tools:
  github:
    toolsets: [context, repos, pull_requests]
  web-fetch:
  bash:
    - "cat **/*.json"
    - "cat **/*.toml"
    - "cat **/go.mod"
    - "cat **/*.tf"
    - "grep -R --line-number * -- :!node_modules :!.venv :!external"
    - "jq . **/*.json"
    - "npm view *"
    - "uv tree"
    # Validation commands for high-risk bumps
    - "cd data-management/viewer && uv sync --extra dev --extra all && uv run ruff check backend/src/"
    - "cd data-management/viewer && uv run pytest backend/tests/ --tb=short -q"
    - "cd data-management/viewer/frontend && npm ci && npm run validate"
    - "cd evaluation && uv sync && uv run ruff check . && uv run pytest --tb=short -q"
    - "cd training && uv sync && uv run ruff check . && uv run pytest --tb=short -q"
    - "cd infrastructure/terraform && terraform init -backend=false && terraform validate"
    - "cd infrastructure/terraform && terraform fmt -check -recursive"
    - "go vet ./..."
    - "go build ./..."
    - "go mod verify"
safe-outputs:
  create-pull-request-review-comment:
    max: 5
  submit-pull-request-review:
    max: 1
  add-comment:
    max: 2
    target: triggering
  noop:
    max: 1
imports:
  - ../agents/dependabot-pr-reviewer.agent.md
---

# Dependabot PR Review

Advisory-only review of Dependabot-authored pull requests in microsoft/physical-ai-toolchain. The agent classifies risk, enriches findings with GHSA/OSV/NVD intel and release notes, and posts a single review plus targeted inline comments. It never blocks merges.

## Posture

* **Advisory only.** Submit exactly one review with `event: APPROVE` or `event: COMMENT`. `REQUEST_CHANGES` is forbidden.
* **High-risk findings** surface as a `⚠️ Maintainer review recommended` banner in the review body; the verdict still stays on the `APPROVE` / `COMMENT` allowlist.
* **Scope.** Only Dependabot pull requests that touch declared dependency manifests (npm, uv/pip, Go modules, Terraform, Docker). All other diffs are out of scope.

## Gating

Skip the review and emit a `noop` when any of the following hold:

* Pull request author is not `dependabot[bot]`.
* Pull request is a draft (`github.event.pull_request.draft == true`).
* Diff touches `.github/workflows/**` — workflow changes are reviewed by `dependency-review`, `workflow-permissions-scan`, and `sha-staleness-check` instead.
* Diff contains no recognized dependency manifest change.

## Agent Persona

The full reviewer persona, risk rubric, ecosystem-specific checks, and enrichment playbook are defined in the imported agent file [`.github/agents/dependabot-pr-reviewer.agent.md`](../agents/dependabot-pr-reviewer.agent.md). Follow it verbatim.

## Step-by-Step

1. **Parse.** Read the pull request title, body, and file diff. Extract package name, ecosystem, old/new versions, `GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}` and `CVE-\d{4}-\d{4,7}` identifiers from the Dependabot body.
2. **Enrich.** Query GHSA (preferred), fall back to OSV (`api.osv.dev`) and NVD (`services.nvd.nist.gov`) for severity, affected ranges, and fixed versions. Fetch release notes or changelog via the relevant package registry (npm, PyPI, Go module proxy, Terraform registry).
3. **Classify.** Apply the persona's per-surface rubric. Flag ABI-sensitive pins (for example `numpy >=1.26.0,<2.0.0` in Isaac Sim training), pre-1.0 bumps, major version jumps, and missing upstream advisories.
4. **Review.** Post up to five inline `create-pull-request-review-comment` entries for specific risks, up to two `add-comment` status updates on the triggering PR, and exactly one `submit-pull-request-review` with `APPROVE` or `COMMENT`. When nothing actionable is found, emit `noop`.

Keep comments factual and concise. Cite the advisory identifier, affected versions, and the Dependabot PR URL.
