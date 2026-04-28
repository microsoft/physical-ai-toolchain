---
name: AW Dependabot PR Review
description: Advisory agentic review of Dependabot dependency update PRs for physical-ai-toolchain
engine: copilot
timeout-minutes: 15
if: >
  github.event.workflow_run.event == 'pull_request' &&
  github.event.workflow_run.conclusion != null
on:
  workflow_run:
    workflows: ["PR Validation"]
    types: [completed]
    branches:
      - "dependabot/**"
permissions:
  contents: read
  pull-requests: read
  actions: read
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
  - name: Resolve Dependabot PR context from triggering workflow_run
    id: resolve-pr
    uses: actions/github-script@373c709c69115d41ff229c7e5df9f8788daa9553 # v9.0.0
    with:
      script: |
        const wr = context.payload.workflow_run;
        if (!wr) {
          core.setFailed('workflow_run payload missing');
          return;
        }
        core.exportVariable('PR_VALIDATION_CONCLUSION', wr.conclusion || 'unknown');
        core.exportVariable('PR_VALIDATION_RUN_URL', wr.html_url || '');
        core.exportVariable('PR_HEAD_SHA', wr.head_sha || '');

        const prs = wr.pull_requests || [];
        let prNumber = prs.length ? prs[0].number : null;
        if (!prNumber && wr.head_branch) {
          // workflow_run may not populate pull_requests for forks; resolve via search.
          const { data: search } = await github.rest.search.issuesAndPullRequests({
            q: `repo:${context.repo.owner}/${context.repo.repo} is:pr head:${wr.head_branch} state:open`,
            per_page: 1,
          });
          if (search.items.length) prNumber = search.items[0].number;
        }
        if (!prNumber) {
          core.warning('Could not resolve a PR for this workflow_run; emitting noop.');
          core.exportVariable('PR_DEPENDABOT_SKIP_REASON', 'no-pr-resolved');
          return;
        }
        const { data: pr } = await github.rest.pulls.get({
          owner: context.repo.owner,
          repo: context.repo.repo,
          pull_number: prNumber,
        });
        if (pr.user.login !== 'dependabot[bot]') {
          core.exportVariable('PR_DEPENDABOT_SKIP_REASON', 'not-dependabot');
          return;
        }
        if (pr.draft) {
          core.exportVariable('PR_DEPENDABOT_SKIP_REASON', 'draft');
          return;
        }
        core.exportVariable('PR_NUMBER', String(pr.number));
        core.exportVariable('PR_TITLE', pr.title);
        core.exportVariable('PR_HEAD_REF', pr.head.ref);
        core.exportVariable('PR_BASE_REF', pr.base.ref);
        core.exportVariable('PR_AUTHOR', pr.user.login);
        core.info(`Resolved PR #${pr.number} (${pr.title}); PR Validation conclusion: ${wr.conclusion}`);
tools:
  github:
    toolsets: [context, repos, pull_requests]
  web-fetch:
  bash:
    - "cat **/*.json"
    - "cat **/*.toml"
    - "cat **/go.mod"
    - "cat **/*.tf"
    - "cat training/rl/requirements.txt"
    - "cat training/rl/scripts/train.sh"
    - "grep -R --line-number * -- :!node_modules :!.venv :!external"
    - "jq . **/*.json"
    - "npm view *"
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

Advisory-only review of Dependabot-authored pull requests in microsoft/physical-ai-toolchain. The agent classifies risk, enriches findings with GHSA/OSV/NVD intel and release notes, anchors validation on the deterministic `PR Validation` orchestrator that triggered this run, and posts a single review plus targeted inline comments. It never blocks merges.

## Trigger Posture

This workflow runs via `workflow_run` after the `PR Validation` orchestrator completes on a PR targeting `main`. The deterministic CI conclusion is the canonical validation signal — read it from the `PR_VALIDATION_CONCLUSION` environment variable injected by the resolver step. The agent must never attempt to run validation tooling (`uv`, `pytest`, `npm ci`, `terraform`, `go`) from the bash tool because those binaries are not visible inside the AWF firewall sandbox.

The resolver step exports these environment variables for the agent to read:

* `PR_NUMBER` — the Dependabot PR number under review
* `PR_TITLE`, `PR_HEAD_REF`, `PR_BASE_REF`, `PR_AUTHOR`, `PR_HEAD_SHA`
* `PR_VALIDATION_CONCLUSION` — `success`, `failure`, `cancelled`, `neutral`, `skipped`, `timed_out`, or `action_required`
* `PR_VALIDATION_RUN_URL` — direct link to the `PR Validation` run
* `PR_DEPENDABOT_SKIP_REASON` (optional) — set when the resolver determined the trigger should be skipped (`no-pr-resolved`, `not-dependabot`, `draft`)

When `PR_DEPENDABOT_SKIP_REASON` is set, emit a `noop` with the reason as the rationale and stop.

## Posture

* **Advisory only.** Submit exactly one review with `event: APPROVE` or `event: COMMENT`. `REQUEST_CHANGES` is forbidden.
* **High-risk findings** surface as a `⚠️ Maintainer review recommended` banner in the review body; the verdict still stays on the `APPROVE` / `COMMENT` allowlist.
* **Scope.** Only Dependabot pull requests that touch declared dependency manifests (npm, uv/pip, Go modules, Terraform, Docker). All other diffs are out of scope.

## Gating

Skip the review and emit a `noop` when any of the following hold:

* `PR_DEPENDABOT_SKIP_REASON` is set by the resolver step (PR could not be resolved, author is not `dependabot[bot]`, or PR is a draft).
* Diff touches `.github/workflows/**` — workflow changes are reviewed by `dependency-review`, `workflow-permissions-scan`, and `sha-staleness-check` instead.
* Diff contains no recognized dependency manifest change.

## Agent Persona

The full reviewer persona, risk rubric, ecosystem-specific checks, and enrichment playbook are defined in the imported agent file [`.github/agents/dependabot-pr-reviewer.agent.md`](../agents/dependabot-pr-reviewer.agent.md). Follow it verbatim.

## Step-by-Step

1. **Resolve context.** Read `PR_NUMBER`, `PR_HEAD_SHA`, `PR_VALIDATION_CONCLUSION`, and `PR_VALIDATION_RUN_URL` from the environment. If `PR_DEPENDABOT_SKIP_REASON` is set, emit `noop` and stop.
2. **Read CI signal.** Use the `github` MCP `pull_requests` toolset (or `GET /repos/{owner}/{repo}/commits/{sha}/check-runs`) on `PR_HEAD_SHA` to enumerate per-surface check-run conclusions. Map them through the surface table in the persona.
3. **Parse.** Read the pull request title, body, and file diff. Extract package name, ecosystem, old/new versions, `GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}` and `CVE-\d{4}-\d{4,7}` identifiers from the Dependabot body.
4. **Enrich.** Query GHSA (preferred), fall back to OSV (`api.osv.dev`) and NVD (`services.nvd.nist.gov`) for severity, affected ranges, and fixed versions. Fetch release notes or changelog via the relevant package registry (npm, PyPI, Go module proxy, Terraform registry).
5. **Classify.** Apply the persona's per-surface rubric. Flag ABI-sensitive pins (for example `numpy >=1.26.0,<2.0.0` in Isaac Sim training), pre-1.0 bumps, major version jumps, and missing upstream advisories.
6. **Review.** Post up to five inline `create-pull-request-review-comment` entries for specific risks, up to two `add-comment` status updates on the triggering PR, and exactly one `submit-pull-request-review` with `APPROVE` or `COMMENT`.
   When `PR_VALIDATION_CONCLUSION` is anything other than `success`, the verdict MUST be `COMMENT` and the body MUST quote the failing per-surface check-run names plus their `html_url`.
   Never skip enrichment on red CI — maintainers rely on advisory output to triage which package in a grouped PR caused the failure.

Keep comments factual and concise. Cite the advisory identifier, affected versions, and the Dependabot PR URL.
