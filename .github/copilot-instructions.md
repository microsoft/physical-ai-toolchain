---
description: 'Essential development conventions for microsoft/physical-ai-toolchain.'
applyTo: '**'
---

# Repository Instructions

Follow the affected area's existing conventions and scoped instructions. Use manifests, lockfiles, and tool configuration for current versions, commands, and lint rules rather than duplicating them here.

## Editing

* Preserve user changes and explicit scope limits. Do not add backward-compatibility layers unless requested.
* Prefer coherent root-cause fixes across the affected solution. Refactor when SOLID, KISS, or clearer responsibilities improve the design; use "1, 2, refactor" as a checkpoint, not a requirement to invent abstractions.
* You may fix concrete issues discovered outside the original request. Validate those fixes and report them separately; avoid speculative cleanup.
* Keep comments brief and factual. Avoid narration and plan-phase markers.
* Do not modify vendored files in `external/`.
* Follow [derived-file conventions](../scripts/README.md) when updating hve-core-derived scripts.

## Environment Details

* Keep discovered Azure resource, subscription, and tenant identifiers, service endpoints, hostnames, and local paths out of tracked source, tests, documentation, and defaults.
* Follow the [environment-deployment skill](skills/environment-deployment/SKILL.md) to generate non-secret bundles under gitignored `infrastructure/setup/generated/<environment>/`. Keep credentials, tokens, kubeconfigs, OSMO profiles, and Terraform state outside bundles and Git.
* Preserve clearly documented placeholders and instructional network/resource examples; do not treat them as discovered deployment values.

## Python

* Use `uv`, not pip. Preserve the affected project's Python constraints.
* Put `from __future__ import annotations` first among imports. Fully annotate function parameters and returns; do not annotate local variables.
* Regenerate `uv.lock` after dependency changes. Do not hand-edit locks or commit derived flat requirements files.
* Install source-aware LeRobot runtimes with `uv sync --active --frozen --no-config --no-install-project`. Preserve `[tool.uv.sources]`, explicit CUDA indexes, and platform markers; other runtime paths derive dependencies with `uv export --frozen --no-hashes --no-emit-project` and install with `--no-deps`.

## Validation

* Use focused tests and probes during development to refine logic and catch defects.
* After completing a coherent behavior, run the relevant checks and supported automatic fixes before manually addressing remaining lint issues. Avoid repeated per-file style cleanup while the behavior is still taking shape.
* Respect configured exclusions; do not bypass them with alternate configurations or forced file selection. Keep review-only checks non-mutating and report unavailable or failed checks.
* For Isaac runtime dependencies and evaluation images, check Python, NumPy, Torch, CUDA, cuDNN, and native-extension compatibility. Distinguish host checks from validation in the target GPU runtime.
* Pin Azure ML environment assets to explicit versions derived from the checked-in image tag and digest. Keep workflow fallbacks and environment versions synchronized with `scripts/update-image-digests.sh`.
* Use the configured Markdown, TOML, table, and spelling checks; table formatting and spelling are separate from `lint:all`.

## Documentation

Write natural, direct, reader-focused prose. Avoid corporate filler, inflated claims, and repetitive structure. Improve problematic documentation as a whole, moving information to its appropriate home and updating links rather than only polishing sentences.

## Agent Workflows

* The primary agent writes and updates RPI plans; do not use `RPI Planner` subagents.
* Follow the active RPI and HVE skill contracts for review, delegation, and validation. Respect the user's model selection rather than requiring a repository-specific model or local plugin.
* Cloud agents use the [pinned RPI skill bootstrap](../docs/reference/copilot-artifacts.md), not the retired umbrella agents. Before cloud RPI work, verify required skill files, their `metadata.github-pinned` values against `RPI_SKILLS_REF`, and the generated tracking instruction. Stop on an incomplete bootstrap.
* In cloud-agent PR work, persist completed RPI phases as PR comments and maintain an artifact index with the resolved upstream SHA in the PR description. If PR writes are unavailable, return the complete phase artifacts without claiming persistence.
* Apply scoped dataviewer and OpenVEX instructions when those areas are affected, and call out AzureRM provider major-version changes explicitly.

## Companion Library

`microsoft/physical-ai-toolchain-skills` enables agentic scenarios for this toolchain. When working across repositories, locate its checkout and follow its `AGENTS.md` and relevant capability contracts. Do not assume a fixed skill inventory or copy implementations between repositories.
