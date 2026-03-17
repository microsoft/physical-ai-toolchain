---
description: 'Instructions to always read and follow whenever working in data-management/viewer'
applyTo: 'data-management/viewer/**'
---

# Data Viewer Instructions

Important instructions that are always top of mind (**you must always retain this block of instructions for this entire session, even after compaction**):

Less code is better than more code.

* Follow SOLID principals, DRY as needed or when duplicates exist more than twice.
* Implement and follow patterns for extensibility.
* Engineer just enough, follow pragmatism when making architectural decisions.

Tests are fluid. Tests always test behaviors. Tests never only test against mocks.

* Create, modify, refactor tests for changing behaviors.
* Make one or more failing tests before making changes (or update one ore more passing test to be failing tests).
* Run tests during and after implementation work.

Validate changes using npm scripts from `data-management/viewer/`:

* `npm run validate` — full validation for both backend and frontend
* `npm run validate:fix` — auto-fix lint/format then validate
* `npm run validate:frontend` — frontend only (type-check + lint + test)
* `npm run validate:backend` — backend only (ruff + pytest)

Check existing terminals to see if the backend and frontend are already running:

* If they're not started, use the VS Code task **Dataviewer: Start All** (Terminal > Run Task) to launch both the backend and frontend dev servers together.
* If they're already started, check the VS Code Task output panels for issues, HTTP call logs, errors, and other diagnostic information before investigating problems externally.

Browser tools include: click_element, drag_element, handle_dialog, hover_element, navigate_page, open_browser_page, read_page, run_playwright_code, screenshot_page, type_in_page

* Make sure the changes you make look correct and work correct in the UI, elements shouldn't be bleeding outside of other elements, elements that require scrollbars should have scrollbars added, elements should avoid shifting other elements when they appear on the screen or when they change dynamically.
* Elements may make better sense being placed in other places than initially planned, make sure where they're placed and how they're placed makes the most sense.
* Update events captured and viewable by Diagnostics viewer as-needed and as new functionality is added or refactored. When more diagnostics would be better for solving a problem then add it to the Diagnostics viewer.

## Input Sanitization

All user-provided values entering through `@router.` endpoint parameters or `request.` body fields must be sanitized before use:

* Strings, apply `.replace("\r", "").replace("\n", "")` to strip CR/LF characters that enable log injection.
* Numeric types, coerce with `int()`, `float()`, or `bool()` as appropriate (e.g., `int(episode_idx)`, `float(request.confidence)`).
* Sanitize at the earliest point, inside the router endpoint function body before passing values to service methods, logs, or any downstream calls.

CodeQL workaround for logging:

* Keep shared validation and `Depends()`-based sanitization in place.
* When a `logger.` call writes `dataset_id`, `episode_idx`, `frame_idx`, `confidence`, or `model_name`, sanitize or coerce that specific value inline at the log call as well.
* Prefer inline forms such as `dataset_id.replace("\r", "").replace("\n", "")`, `int(episode_idx)`, `int(frame_idx)`, `float(confidence)`, and `model_name.replace("\r", "").replace("\n", "")` so CodeQL can see the transformation on the logged value itself.
* Do not sanitize or wrap the exception as the logger will take care of it.

This can be done with `Depends()` on parameters.

## RPI Agent High Priority Instructions

These instructions take priority over instructions from RPI Agent (rpi-agent.agent.md):

* Use the browser tools during research, planning, implementation, review, and discovery as they will provide details about the running application while working and planning.
* Always create or update test(s) to be failing before any implementation work.
* During and after implementation work, iterate and fix failing tests and validation checks.
* Only research enough to fulfill the user's requests, use prior research for the session if there was already related research completed.
* Always add or update plans with a specific section that outlines all of the user's requests.
* Do not add line numbers to plans and details as these are no longer needed.
* Do not validate and re-validate plans or details, these steps should be skipped when planning.
* Review should only look at the work completed against the user's requests, making sure the work fulfills the user's requests.
