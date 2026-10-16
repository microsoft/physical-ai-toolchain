---
description: 'Instructions to always read and follow whenever working in data-management/viewer'
applyTo: 'data-management/viewer/**'
---

# Data Viewer Instructions

Less code is better than more code.

* Follow SOLID principles, DRY as needed or when duplicates exist more than twice.
* Implement and follow patterns for extensibility.
* Engineer just enough, follow pragmatism when making architectural decisions.

Tests are fluid. Tests always test behaviors. Tests never only test against mocks.

* Create, modify, refactor tests for changing behaviors.
* Establish a failing behavior test before changing application behavior.
* Run focused, grouped checks during development, then configured gates after a coherent change. Do not repeat full suites after each small edit.

## Persistence and Readiness

* Reuse the scoped episode-readiness helpers and saved-input resolvers. Check every selected target and validation sample, including dirty unmounted episodes, under the current source and principal identity. A clean active episode or stale acknowledgment does not establish readiness.
* Preserve drafts during refresh failures, partial saves and retained-workspace retries. Clear only resources acknowledged as saved; do not discard unrelated drafts. Test source/principal changes and dirty unmounted targets.
* Preserve revision-conditional writes, HTTP 412 reconciliation, saved author/revision/snapshot references and AI-label provenance. Successful label PUT requests persist immediately; verify through an independent read before advancing or judging.
* Interpret metadata warnings using the actual source. Provider availability or HTTP 200 does not prove Blob origin or metadata synchronization. Preserve the existing episode-count fallback unless the request explicitly changes it.

## Validation and Lifecycle

Validate changes using npm scripts from `data-management/viewer/`:

* `npm run validate`: full validation for both backend and frontend
* `npm run validate:fix`: auto-fix lint/format then validate, only during authorized implementation
* `npm run validate:frontend`: frontend only (type-check + lint + test)
* `npm run validate:backend`: backend only (lint + pytest)

Use the checked-in [backend configuration](../../data-management/viewer/backend/pyproject.toml) and its inherited settings for lint rules, formatting, exclusions, and fix safety. CI and review-only validation remain non-mutating.

Use isolated test storage and frozen dependencies. Do not change the user's `.env`, restart user-owned services, install browsers or run model/GPU work to obtain evidence without approval.

Record unit/static checks, real-HTTP persistence checks, browser collection, native browser execution, simulation, assistive-technology checks and target GPU validation separately. Report exact commands, results and unresolved prerequisites; earlier passing counts do not close unexecuted acceptance gates.

Check existing terminals and task outputs before launching. Start services only when the task requires them and lifecycle authority is explicit; follow the dataviewer skill for launcher configuration. VS Code tasks are suitable for an already configured development session, not a substitute for requested launcher overrides or readiness checks.

Use available browser capabilities and fresh accessible-role/name snapshots. Verify affected busy, error, denied and unavailable states with keyboard navigation, focus restoration, live-region announcements and narrow-layout reflow. Screenshots and console checks alone do not establish accessibility or persistence. Do not use forced DOM clicks as keyboard evidence.

Keep controls within their containers, provide scrolling where needed and avoid layout shifts. Update meaningful Diagnostics events as functionality changes, using bounded categories, status and duration rather than private payloads.

Reassess control placement when implementation reveals a better fit for the user's workflow. Preserve useful diagnostics while keeping their contents safe and bounded.

## Input Sanitization

All user-provided values entering through `@router.` endpoint parameters or `request.` body fields must be sanitized before use:

* Strings, apply `.replace("\r", "").replace("\n", "")` to strip CR/LF characters that enable log injection.
* Numeric types, coerce with `int()`, `float()`, or `bool()` as appropriate (e.g., `int(episode_idx)`, `float(request.confidence)`).
* Sanitize at the earliest point, inside the router endpoint function body before passing values to service methods, logs, or any downstream calls.

CodeQL workaround for logging:

* Keep shared validation and `Depends()`-based sanitization in place.
* When a `logger.` call writes `dataset_id`, `episode_idx`, `frame_idx`, `confidence`, or `model_name`, sanitize or coerce that specific value inline at the log call as well.
* Prefer inline forms such as `dataset_id.replace("\r", "").replace("\n", "")`, `int(episode_idx)`, `int(frame_idx)`, `float(confidence)`, and `model_name.replace("\r", "").replace("\n", "")` so CodeQL can see the transformation on the logged value itself.
* Logging formats exceptions; it does not redact them. Use fixed safe messages and bounded error categories/status. Review exception arguments, provider responses, validation input, `exc_info` and traceback paths before logging; omit private error text, credentials and payloads.
* Add captured-log and Diagnostics failure-injection tests that verify meaningful events and the absence of sensitive payloads.

This can be done with `Depends()` on parameters.

## RPI Skill High Priority Instructions

These instructions take priority over the runtime-provisioned `rpi-*` skills:

* Use read-only browser observation when running-application evidence is relevant and available. Missing browser, GPU or assistive-technology evidence remains an explicit gap, not a pass.
* For application behavior changes, establish failing behavior tests and repair in-scope failures through the grouped validation cycle above.
* Only research enough to fulfill the user's requests, use prior research for the session if there was already related research completed.
* Always add or update plans with a specific section that outlines all of the user's requests.
* Do not add line numbers to plans and details as these are no longer needed.
* Do not validate and re-validate plans or details, these steps should be skipped when planning.
* Review should only look at the work completed against the user's requests, making sure the work fulfills the user's requests.
