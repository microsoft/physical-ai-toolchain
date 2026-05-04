---
name: AW Dependabot PR Review
description: Advisory agentic review of Dependabot dependency update PRs for physical-ai-toolchain
engine: copilot
timeout-minutes: 15
if: >
  github.event.workflow_run.event == 'pull_request' &&
  github.event.workflow_run.actor.login == 'dependabot[bot]' &&
  contains(fromJSON('["success","failure","cancelled","timed_out","neutral","skipped","action_required"]'),
    github.event.workflow_run.conclusion)
on:
  workflow_run:
    workflows: ["PR Validation"]
    types: [completed]
    branches: [main]
  bots: ["dependabot[bot]"]
  roles: [admin, maintainer, write]
permissions:
  contents: read
  pull-requests: read
  actions: read
  checks: read
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
  - name: Resolve Dependabot PR context from workflow_run
    id: resolve-pr
    uses: actions/github-script@373c709c69115d41ff229c7e5df9f8788daa9553 # v9.0.0
    with:
      script: |
        const run = context.payload.workflow_run;
        if (!run) {
          core.setFailed('workflow_run payload missing');
          return;
        }
        if (run.event !== 'pull_request') {
          core.exportVariable('PR_DEPENDABOT_SKIP_REASON', 'not-a-pr-run');
          return;
        }

        let pr = (run.pull_requests || [])[0];
        if (!pr) {
          // Fork PRs do not appear in workflow_run.pull_requests; fall back to search.
          const q = `repo:${context.repo.owner}/${context.repo.repo} is:pr sha:${run.head_sha}`;
          const { data } = await github.rest.search.issuesAndPullRequests({ q });
          const hit = (data.items || [])[0];
          if (!hit) {
            core.exportVariable('PR_DEPENDABOT_SKIP_REASON', 'pr-resolution-failed');
            return;
          }
          const { data: full } = await github.rest.pulls.get({
            owner: context.repo.owner,
            repo: context.repo.repo,
            pull_number: hit.number,
          });
          pr = full;
        } else {
          // Hydrate the full PR object so fields like `body` and `draft` are reliable.
          const { data: full } = await github.rest.pulls.get({
            owner: context.repo.owner,
            repo: context.repo.repo,
            pull_number: pr.number,
          });
          pr = full;
        }

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
        core.exportVariable('PR_HEAD_SHA', pr.head.sha);

        // PR Validation conclusion comes directly from the triggering workflow_run payload;
        // it is always final under `types: [completed]`.
        core.exportVariable('PR_VALIDATION_CONCLUSION', run.conclusion || 'unknown');
        core.exportVariable('PR_VALIDATION_RUN_URL', run.html_url || '');

        // Resolve per-surface check-runs ONCE here so the persona does not re-walk them.
        let failing = [];
        try {
          const { data: checks } = await github.rest.checks.listForRef({
            owner: context.repo.owner,
            repo: context.repo.repo,
            ref: pr.head.sha,
            per_page: 100,
          });
          failing = (checks.check_runs || [])
            .filter(c => c.status === 'completed'
              && !['success', 'neutral', 'skipped'].includes(c.conclusion))
            .map(c => ({ name: c.name, html_url: c.html_url, conclusion: c.conclusion }));
        } catch (err) {
          core.warning(`Failed to enumerate check-runs: ${err.message}`);
        }
        core.exportVariable('PR_VALIDATION_FAILING_CHECKS', JSON.stringify(failing));

        // Hydrate Dependabot enrichment input from REST so the agent does not depend on
        // the integrity-filtered MCP read of the PR body.
        core.exportVariable('PR_BODY', pr.body || '');

        core.info(`Resolved PR #${pr.number} (${pr.title}); PR Validation conclusion: ${run.conclusion}; failing checks: ${failing.length}`);
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
    target: "*"
  submit-pull-request-review:
    max: 1
    target: ${{ env.PR_NUMBER }}
  add-comment:
    max: 2
    target: ${{ env.PR_NUMBER }}
  noop:
    max: 1
imports:
  - ../agents/dependabot-pr-reviewer.agent.md
---

# Dependabot PR Review

Advisory-only review of Dependabot-authored pull requests in microsoft/physical-ai-toolchain. The agent classifies risk, enriches findings with GHSA/OSV/NVD intel and release notes, anchors validation on the deterministic `PR Validation` orchestrator that triggered this run, and posts a single review plus targeted inline comments. It never blocks merges.

## Trigger Posture

This workflow runs via `workflow_run` after the `PR Validation` orchestrator completes on `main` for a
`pull_request` event. Because `workflow_run` evaluates the workflow file from the default branch, the
agent step always uses the trusted, merged definition rather than fork content. The gh-aw compiler
auto-injects fork-PR exclusion and a `repository.id` guard into the lock file. The workflow-level
`if:` short-circuits any non-PR triggering event, any PR not authored by `dependabot[bot]` (gated on
`workflow_run.actor.login`), and any non-terminal conclusion before the resolver runs. The resolver then
reads the orchestrator's terminal conclusion directly from `context.payload.workflow_run.conclusion`,
which under `types: [completed]` is always one of `success`, `failure`, `cancelled`, `timed_out`,
`neutral`, `skipped`, or `action_required`.

The resolver step exports `PR Validation`'s final conclusion directly from
`context.payload.workflow_run.conclusion` (no separate `listWorkflowRunsForRepo` call), then enumerates
per-surface check-runs once via `checks.listForRef` so the agent never has to walk the checks API itself.
The `checks: read` permission grants exactly that scope and nothing more. The agent must never attempt
to run validation tooling (`uv`, `pytest`, `npm ci`, `terraform`, `go`) from the bash tool because those
binaries are not visible inside the AWF firewall sandbox.

The resolver step exports these environment variables for the agent to read:

* `PR_NUMBER` — the Dependabot PR number under review
* `PR_TITLE`, `PR_HEAD_REF`, `PR_BASE_REF`, `PR_AUTHOR`, `PR_HEAD_SHA`
* `PR_VALIDATION_CONCLUSION` — final terminal conclusion: `success`, `failure`, `cancelled`, `timed_out`, `neutral`, `skipped`, `action_required`, or `unknown`
* `PR_VALIDATION_RUN_URL` — direct link to the `PR Validation` run
* `PR_VALIDATION_FAILING_CHECKS` — JSON array of `{name, html_url, conclusion}` for non-success/non-neutral/non-skipped check-runs on `PR_HEAD_SHA`
* `PR_BODY` — the PR body, hydrated server-side so enrichment does not depend on the integrity-filtered MCP read
* `PR_DEPENDABOT_SKIP_REASON` (optional) — set when the resolver determined the trigger should be skipped (`not-a-pr-run`, `pr-resolution-failed`, `not-dependabot`, `draft`)

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

1. **Resolve context.** Read `PR_NUMBER`, `PR_HEAD_SHA`, `PR_VALIDATION_CONCLUSION`, `PR_VALIDATION_RUN_URL`, `PR_VALIDATION_FAILING_CHECKS`, and `PR_BODY` from the environment. If `PR_DEPENDABOT_SKIP_REASON` is set, emit `noop` and stop.
2. **Read CI signal.** Treat `PR_VALIDATION_CONCLUSION` as the final, non-stale conclusion. Parse `PR_VALIDATION_FAILING_CHECKS` (JSON) for the list of failing per-surface check-runs. Do NOT call `checks.listForRef` or `commits/{sha}/check-runs` — the resolver already did.
3. **Parse.** Read `PR_BODY` plus the file diff. Extract package name, ecosystem, old/new versions, `GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}` and `CVE-\d{4}-\d{4,7}` identifiers from the Dependabot body.
4. **Enrich.** Query GHSA (preferred), fall back to OSV (`api.osv.dev`) and NVD (`services.nvd.nist.gov`) for severity, affected ranges, and fixed versions. Fetch release notes or changelog via the relevant package registry (npm, PyPI, Go module proxy, Terraform registry).
5. **Classify.** Apply the persona's per-surface rubric. Flag ABI-sensitive pins (for example `numpy >=1.26.0,<2.0.0` in Isaac Sim training), pre-1.0 bumps, major version jumps, and missing upstream advisories.
6. **Review.** Post up to five inline `create-pull-request-review-comment` entries for specific risks, up to two `add-comment` status updates on the resolved PR, and exactly one `submit-pull-request-review` with `APPROVE` or `COMMENT`.
   When `PR_VALIDATION_CONCLUSION` is anything other than `success`, the verdict MUST be `COMMENT` and the body MUST quote each entry from `PR_VALIDATION_FAILING_CHECKS` (`name` plus `html_url`).
   Never skip enrichment on red CI — maintainers rely on advisory output to triage which package in a grouped PR caused the failure.

Keep comments factual and concise. Cite the advisory identifier, affected versions, and the Dependabot PR URL.
