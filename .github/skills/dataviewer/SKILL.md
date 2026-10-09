---
name: dataviewer
description: 'Start and interact with the Dataset Analysis Tool (dataviewer) for browsing, annotating, and exporting robotic training episodes'
---

# Dataviewer Skill

Launch and interact with the Dataset Analysis Tool, a full-stack application for analyzing and annotating robotic training data from episode-based datasets. Complete requested operations with persistence read-back; job acceptance is not inference completion or label application.

## Prerequisites

| Platform | Requirement                          |
|----------|--------------------------------------|
| All      | Python 3.12+, Node.js 24+, npm, `uv` |

The backend virtual environment and repository-root npm workspace dependencies are auto-created on first launch by `start.sh`.

## Launch and Connect Workflow

Follow these steps when a launch is required and authorized. Inspect existing terminals/task output first and reuse healthy services with matching configuration. Read-only inspection does not authorize restarts, inference or dataset writes. Use VS Code tasks for an already configured development session; use the launcher for requested overrides and readiness checks.

### Step 1 — Start the app

Launch `start.sh` as a background terminal process. The script prints `[OK] Both services are running` and the URLs when both services are healthy.

Use `./start.sh --check` to check installed launch prerequisites without starting or installing services. The launcher binds both services to loopback, requires the selected frontend port and returns failure when either service fails readiness.

```bash
cd data-management/viewer && ./start.sh
```

With a custom dataset path:

```bash
cd data-management/viewer && ./start.sh --data-dir /path/to/datasets
```

### Step 2 — Open SimpleBrowser

After confirming both services are running (look for `[OK] Backend is healthy` in terminal output), open the frontend in VS Code's SimpleBrowser using the `open_browser_page` tool:

```text
open_browser_page("http://localhost:5173")
```

SimpleBrowser is the visual interface for the user when that provider is available. Configured headless Playwright automation runs in the background. Native browser tools can operate a shared page directly; separate browser sessions share persisted backend state, not necessarily unsaved drafts.

If a non-default `FRONTEND_PORT` was set, substitute that port instead of `5173`.

### Step 3 — Load available browser tools

Use the host's tool search to discover available browser capabilities before calling deferred tools. Native tools include `read_page`, `run_playwright_code`, `navigate_page`, `click_element`, `type_in_page` and `screenshot_page`. Use those when available; an equivalent native read does not require MCP reconfiguration.

If using Playwright MCP, configure **headless mode** so it does not open a separate browser window. Its visual feedback goes through SimpleBrowser (Step 2). A Playwright MCP server configuration in `.vscode/mcp.json` can use:

```json
// .vscode/mcp.json
{
  "servers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--headless"]
    }
  }
}
```

> [!IMPORTANT]
> Use `--headless` for the MCP background workflow. Reconfiguring or installing a browser provider requires approval; do not change it merely to obtain inspection evidence.

Search by capability using the host's available tool-search schema, for example:

```text
Find browser tools to read an accessible page snapshot, navigate, click, type and capture screenshots.
```

If no suitable provider is available, report the limit and guide manual inspection. When the user chooses the configured MCP provider, they can run **MCP: Start Server** then select **playwright** in the Command Palette. An empty tool search alone does not prove that a server is stopped.

### Step 4 — Interact via Playwright MCP

Playwright operates headlessly on the same URL as SimpleBrowser. Both see the same backend state, so API-driven changes (labels, annotations) appear in both.

Once the tools are available, use the following patterns for all UI interaction:

| Action             | Playwright MCP Tool                           | Notes                                                   |
|--------------------|-----------------------------------------------|---------------------------------------------------------|
| Capture page state | `read_page` / `browser_snapshot`              | Call first to inspect current accessible roles/names    |
| Navigate to URL    | `navigate_page` / `browser_navigate`          | Preserve unsaved drafts before reload                   |
| Click an element   | `click_element` / `browser_click`             | Use current snapshot refs and names                     |
| Type into input    | `type_in_page` / `browser_type`               | For dataset filters or label inputs                     |
| Take a screenshot  | `screenshot_page` / `browser_take_screenshot` | Verify appearance, not persistence or keyboard behavior |

Take a fresh accessible snapshot before click/type actions and refresh refs after content changes. Use the [Frontend UI Structure](#frontend-ui-structure) below to orient; forced DOM clicks are not keyboard accessibility evidence.

## Quick Start

Start the dataviewer with the default dataset path:

```bash
cd data-management/viewer && ./start.sh
```

Start with a custom dataset path:

```bash
cd data-management/viewer && ./start.sh --data-dir /path/to/datasets
```

## Parameters Reference

| Parameter        | Default                                      | Description                                 |
|------------------|----------------------------------------------|---------------------------------------------|
| `DATA_DIR`       | `../../../datasets` (relative to `backend/`) | Directory containing dataset subdirectories |
| `BACKEND_PORT`   | `8000`                                       | FastAPI backend port                        |
| `FRONTEND_PORT`  | `5173`                                       | Vite frontend dev server port               |
| `HEALTH_TIMEOUT` | `30`                                         | Seconds to wait for backend health check    |

### Dataset Path Configuration

The `DATA_DIR` environment variable controls which datasets are visible in the app. Each subdirectory under this path is treated as a separate `dataset_id`.

Use one of these methods to set `DATA_DIR`.

### Launch argument

Use this method for workflow handoff and ad-hoc sessions:

```bash
./start.sh --data-dir /path/to/datasets
```

### Backend environment file

Use this method only when a persistent local default is requested:

```env
DATA_DIR=/path/to/datasets
```

### Session environment

Use this method for a shell-scoped override:

```bash
export DATA_DIR=/path/to/datasets
cd data-management/viewer && ./start.sh
```

Persist a path in `backend/.env` only when the user explicitly requests a local default
across restarts. Do not mutate `.env` for a profile-bound workflow handoff. The workflow
owns the viewer child and passes the manifest's exact dataset parent through
`--data-dir`.

When `accepted-dataset.json` exists, verify the dataset capabilities before browser
inspection. Require the returned dataset ID, output adapter/version, viewer adapter,
profile ID/hash, capture-provenance hash, export-validation hash, capture features, and
sensor roles to match the descriptor.

Read the [accepted-dataset contract reference](../../../data-management/viewer/README.md#-accepted-dataset-contract)
for the descriptor schema, artifact integrity checks, and invalid-contract behavior.

## Architecture

```text
data-management/viewer/
├── start.sh              # Orchestrator: launches backend + frontend
├── backend/
│   ├── .env              # DATA_DIR and test config
│   ├── pyproject.toml    # Python dependencies (uv)
│   └── src/api/
│       ├── main.py       # FastAPI app, CORS, router registration
│       ├── routers/      # REST endpoints: datasets, annotations, labels, export, detection, analysis
│       ├── routes/       # AI analysis routes
│       ├── services/     # Business logic and dataset service
│       ├── models/       # Pydantic models
│       └── storage/      # Persistence layer
├── frontend/
│   ├── vite.config.ts    # Dev server + API proxy to :8000
│   └── src/
│       ├── App.tsx       # Root: dataset selector, episode list, annotation workspace
│       ├── api/          # HTTP client and typed API functions
│       ├── components/   # UI components (annotation, dashboard, episode viewer, export)
│       ├── hooks/        # React Query hooks for datasets, episodes, annotations
│       ├── stores/       # Zustand stores for episode and dataset state
│       └── types/        # TypeScript type definitions
```

## API Reference

### Core Endpoints

| Endpoint                                           | Method | Description                                                                               |
|----------------------------------------------------|--------|-------------------------------------------------------------------------------------------|
| `/health`                                          | GET    | Health check                                                                              |
| `/api/datasets`                                    | GET    | List all datasets                                                                         |
| `/api/datasets/{id}`                               | GET    | Get dataset metadata                                                                      |
| `/api/datasets/{id}/capabilities`                  | GET    | Get format support, optional feature availability, and verified accepted-dataset contract |
| `/api/datasets/{id}/episodes`                      | GET    | List episodes in a dataset                                                                |
| `/api/datasets/{id}/episodes/{idx}`                | GET    | Get episode data with trajectory and metadata                                             |
| `/api/datasets/{id}/episodes/{idx}/trajectory`     | GET    | Get trajectory data only                                                                  |
| `/api/datasets/{id}/episodes/{idx}/frames/{frame}` | GET    | Get a single frame image                                                                  |
| `/api/datasets/{id}/episodes/{idx}/cameras`        | GET    | List available camera views                                                               |
| `/api/datasets/{id}/episodes/{idx}/video/{camera}` | GET    | Stream video for a camera                                                                 |
| `http://localhost:8000/docs`                       | GET    | Swagger UI documentation                                                                  |

### Label Endpoints

| Endpoint                                   | Method | Description                                           |
|--------------------------------------------|--------|-------------------------------------------------------|
| `/api/datasets/{id}/labels`                | GET    | Get all episode labels and available label options    |
| `/api/datasets/{id}/labels/options`        | GET    | List available label options                          |
| `/api/datasets/{id}/labels/options`        | POST   | Add a new label option (`{"label": "NAME"}`)          |
| `/api/datasets/{id}/episodes/{idx}/labels` | GET    | Get labels for one episode                            |
| `/api/datasets/{id}/episodes/{idx}/labels` | PUT    | Set labels for one episode (`{"labels": ["A", "B"]}`) |
| `/api/datasets/{id}/labels/save`           | POST   | Persist all labels to disk                            |

### VLM-as-Judge Endpoints

> [!NOTE]
> Mounted only when `VLM_JUDGE_ENABLED=true`. Dataset capabilities advertise this
> state, and the frontend does not request an episode judge status while disabled.

| Endpoint                                                 | Method | Description                                                                                   |
|----------------------------------------------------------|--------|-----------------------------------------------------------------------------------------------|
| `/api/datasets/{id}/episodes/{idx}/judge/snapshot`       | GET    | Resolve saved inputs and snapshot identity; optionally select `annotation_author_id`          |
| `/api/datasets/{id}/episodes/{idx}/judge`                | GET    | Current configuration/cache status and optional cached inference payload; not durable history |
| `/api/datasets/{id}/episodes/{idx}/judge`                | POST   | Accept one job with saved `snapshot_id`, optional author, `views`, `process_method`, `force`  |
| `/api/judge/episodes?dataset_id={id}`                    | GET    | Paginated actual episode IDs and inventory revision                                           |
| `/api/judge/jobs`                                        | POST   | Accept a sample or approved dataset job                                                       |
| `/api/judge/jobs/{job_id}`                               | GET    | Durable status, counts and paginated targets                                                  |
| `/api/judge/jobs/{job_id}/approve`                       | POST   | Approve reviewed sample evidence/configuration                                                |
| `/api/judge/jobs/{job_id}/cancel`                        | POST   | Cancel pending work without deleting saved evidence                                           |
| `/api/judge/jobs/{job_id}/retry`                         | POST   | Retry eligible unfinished work after revalidation                                             |
| `/api/judge/jobs/{job_id}/apply`                         | POST   | Explicit label application, optionally selected episode IDs                                   |
| `/api/judge/results?dataset_id={id}&episode_index={idx}` | GET    | Canonical evidence with current/stale/withdrawn applicability                                 |
| `/api/judge/approvals?dataset_id={id}`                   | GET    | Saved approvals for the current principal                                                     |
| `/api/judge/resets/preview`                              | POST   | Preview removal of AI-applied labels                                                          |
| `/api/judge/resets`                                      | POST   | Confirm a reviewed `preview_id`                                                               |
| `/api/judge/resets?dataset_id={id}`                      | GET    | Withdrawal status                                                                             |
| `/api/judge/resets/retry`                                | POST   | Retry eligible withdrawal work                                                                |

POST submission returns HTTP `202` with a durable job summary, `Location` and `Retry-After`, not a finished result. Use a stable `Idempotency-Key` for retries of the same payload; changed requests need a new key. Request-body instruction overrides are rejected with `422`: save instruction edits first and bind the resolved snapshot.

Poll the returned location, respecting `Retry-After`, until `succeeded`, `partial`, `failed` or `cancelled`. Paginate targets and separately retrieve canonical episode evidence before interpreting outcomes. Job success can contain an inconclusive model assessment; acceptance or a cache badge alone is not success. The evidence's result payload contains outcome votes/confidence, progress/VOC, milestones and failure mode, with run/result and saved-input identity on its canonical record.

#### Acceptance response

The HTTP `202` body is a job summary. This illustrative subset shows acceptance, not a completed judgment:

```json
{
  "id": "00000000000000000000000000000001",
  "dataset_id": "leisaac-pick-orange",
  "status": "queued",
  "total": 1,
  "judged": 0,
  "applied": 0,
  "errors": 0
}
```

`id` identifies the durable job to poll. `total` counts selected targets, `judged` counts successful judgments, `applied` counts applied label contributions and `errors` counts failed judgments. Inspect application status/errors separately; judged evidence is not proof that labels were applied.

#### Completed judgment fields

After terminal status, GET `/api/judge/results?dataset_id={id}&episode_index={idx}` returns a paginated `{items, total}` response. Each evidence item wraps `result` with `run_id`, `result_id`, saved `input`, runtime `config`, `config_revision`, `applicability` and `applied`. The following example is the composite `JudgeResult` inside `items[].result`, not the POST response (snake_case on the wire, camelCased by the frontend client):

```json
{
  "episode_id": "leisaac-pick-orange/episode_000007",
  "instruction": "Grab orange and place into plate",
  "judge_model": "Qwen/Qwen3-VL-4B-Instruct",
  "prompt_version": "outcome-mcq-v1+gvl-process-v1+milestones-v1+failuremode-v1",
  "n_frames": 12,
  "outcome_success": true,
  "outcome_confidence": 0.83,
  "outcome_n_valid_votes": 3,
  "progress_per_frame": [0, 9, 18, 27, 36, 45, 55, 64, 73, 82, 91, 100],
  "voc": 0.92,
  "milestones": [
    {
      "name": "approach_object",
      "completed": true,
      "frame_range": "0-3",
      "evidence": "Gripper moves toward the orange in the sampled frames."
    }
  ],
  "failure_mode": null
}
```

| Field                                         | Meaning                                                                                         |
|-----------------------------------------------|-------------------------------------------------------------------------------------------------|
| `episode_id`                                  | Dataset-qualified episode identity                                                              |
| `instruction`                                 | Saved task instruction evaluated by the judge                                                   |
| `judge_model`, `prompt_version`               | Model and scoring-prompt identity; inspect evidence configuration for the full runtime identity |
| `n_frames`                                    | Number of sampled frames                                                                        |
| `outcome_success`                             | `true` for success, `false` for failure, `null` for inconclusive                                |
| `outcome_confidence`, `outcome_n_valid_votes` | Outcome confidence in 0-1 and number of valid outcome votes                                     |
| `progress_per_frame`                          | Per-sampled-frame progress in 0-100; a populated curve contains `n_frames` values               |
| `voc`                                         | Value-order correlation in -1 to 1; it is process-order evidence, not a success probability     |
| `milestones`                                  | Optional completed-step evidence with sampled-frame ranges; can be empty                        |
| `failure_mode`                                | Failure attribution when available, otherwise `null`                                            |

The canonical `result` does not contain `cached`: read that flag from GET episode cache status or the job-status target. Cache status can also expose an optional result, but it is not the canonical evidence/history response. Check evidence `applicability` (`current`, `stale` or `withdrawn`) and saved-input identity before using an outcome.

Read the [backend environment example](../../../data-management/viewer/backend/.env.example) for runtime settings, `VLM_JUDGE_JOB_DIR`, `VLM_JUDGE_CAPACITY`, capacity scope and storage permissions. An empty `VLM_JUDGE_CACHE_DIR` disables only the fallback cache, not viewer per-dataset/snapshot caches. The [Storage and identity](#storage-and-identity) section distinguishes persistence from cache and export.

### Annotation Endpoints

| Endpoint                                             | Method | Description                            |
|------------------------------------------------------|--------|----------------------------------------|
| `/api/datasets/{id}/episodes/{idx}/annotations`      | GET    | Get structured annotations             |
| `/api/datasets/{id}/episodes/{idx}/annotations`      | PUT    | Update structured annotations          |
| `/api/datasets/{id}/episodes/{idx}/annotations`      | DELETE | Remove annotations                     |
| `/api/datasets/{id}/episodes/{idx}/annotations/auto` | POST   | Trigger auto-annotation                |
| `/api/datasets/{id}/annotations/summary`             | GET    | Get annotation summary across episodes |

### Export and Analysis Endpoints

| Endpoint                                   | Method | Description                  |
|--------------------------------------------|--------|------------------------------|
| `/api/datasets/{id}/export`                | POST   | Export dataset with filters  |
| `/api/datasets/{id}/export/stream`         | POST   | Stream export                |
| `/api/datasets/{id}/export/preview`        | GET    | Preview export configuration |
| `/api/datasets/{id}/episodes/{idx}/detect` | POST   | Run object detection         |
| `/api/analysis/trajectory-quality`         | POST   | Trajectory quality analysis  |
| `/api/analysis/anomaly-detection`          | POST   | Anomaly detection            |
| `/api/ai/suggest-annotation`               | POST   | AI-suggested annotations     |

## Annotation Workflow

Annotation combines API calls for efficiency with Playwright UI interaction for verification. Use the API for bulk operations and the UI for visual review and spot-checking.

### Annotation surfaces

The annotation panel exposes three structured surfaces in addition to free-form labels:

| Surface              | Storage                                  | Notes                                                                          |
|----------------------|------------------------------------------|--------------------------------------------------------------------------------|
| Labels               | `meta/episode_labels.json`               | Free-form tag set with shared dataset-level options                            |
| Episode annotation   | `EpisodeAnnotation` JSON                 | Task completeness, trajectory quality, data quality, anomalies                 |
| Language instruction | `EpisodeAnnotation.language_instruction` | Optional VLA payload (instruction, source, paraphrases, subtask decomposition) |

### Multi-camera selection

The annotation workspace supports multiple selected playback cameras. Read available controls from the current episode; selection is reconciled when media sources change. Video and frame extraction follow their selected sources. Judge `views` are separate saved-input configuration: playback selection does not prove which views a job evaluated.

### Language instruction (VLA annotation)

The `LanguageInstructionWidget` writes a structured payload through `PUT /api/datasets/{id}/episodes/{idx}/annotations`:

| Field                  | Purpose                                                         | Bounds                             |
|------------------------|-----------------------------------------------------------------|------------------------------------|
| `instruction`          | Primary natural-language task description                       | 1–1000 chars                       |
| `source`               | Provenance: `human`, `template`, `llm-generated`, `retroactive` | enum                               |
| `language`             | BCP-47 language tag, defaults to `en`                           | up to 10 chars                     |
| `paraphrases`          | Alternative phrasings for data augmentation                     | up to 50 entries, 1000 chars each  |
| `subtask_instructions` | Ordered subtask decomposition for hierarchical conditioning     | up to 100 entries, 1000 chars each |

When a dataset task description is available the widget seeds the instruction with `source = template`; otherwise it creates a blank instruction with `source = human`. The source dropdown allows changing the value at any time.

### Step 1 — Analyze trajectory data

Fetch episode trajectory data from the API to determine labels programmatically:

```bash
curl -s "http://localhost:8000/api/datasets/{dataset_id}/episodes/{idx}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
traj = d['trajectory_data']  # List of frames with joint_positions and timestamps
print(f'Frames: {len(traj)}')
print(f'First joint positions: {traj[0]["joint_positions"][:8]}')
print(f'Last joint positions: {traj[-1]["joint_positions"][:8]}')
"
```

Episode trajectory data is a list of frame dictionaries, each containing:

| Field             | Type        | Description                          |
|-------------------|-------------|--------------------------------------|
| `timestamp`       | float       | Time in seconds from episode start   |
| `frame`           | int         | Frame index                          |
| `joint_positions` | list[float] | Joint positions for all robot joints |

The `meta` field of the episode response contains `index`, `length`, `task_index`, and `has_annotations`.

### Step 2 — Determine labels from trajectory

Analyze gripper and joint data at multiple time points to classify episodes. Check the midpoint first, then 25% and 75% for episodes where grasp actions happen earlier or later:

Resolve gripper channels and units from the dataset's declared features and verified
capture profile before assigning grasp labels. The example below assumes a verified
16-joint bimanual layout; do not reuse its offsets for single-arm datasets.

```python
# Example: check grip values at multiple points for robust classification
for pct in [25, 50, 75]:
    idx = int(len(traj) * pct / 100)
    jp = traj[idx]["joint_positions"]
    right_grip = jp[7]  # Right arm gripper index
    left_grip = jp[15]  # Left arm gripper index
```

> [!IMPORTANT]
> Some episodes have late or early grasp actions, so checking only the midpoint may yield UNKNOWN results. Always check multiple time points (25%, 50%, 75%) and the minimum grip value across the full trajectory for robust classification.

### Step 3 — Apply labels via API

Successful PUT requests persist immediately. Label mutations require the latest
dataset-label ETag in `If-Match`, or `If-None-Match: *` when the GET response has no
ETag yet. Stop and reconcile after HTTP 412; do not overwrite another writer's changes.

These examples use the launcher's loopback-only development mode with
`DATAVIEWER_AUTH_DISABLED=true`. Authenticated servers also require authentication
and CSRF headers; the frontend supplies them automatically.

```bash
curl -i -fsS "http://localhost:8000/api/datasets/{dataset_id}/labels"
curl -fsS -X PUT "http://localhost:8000/api/datasets/{dataset_id}/episodes/{idx}/labels" \
  -H "Content-Type: application/json" \
  -H 'If-Match: "<ETag from the GET response>"' \
  -d '{"labels": ["RIGHT", "SUCCESS"]}'
```

For bulk annotation, loop over episodes in a script:

```python
from __future__ import annotations

import json
from urllib.request import Request, urlopen


def annotate(dataset_id: str, episode_idx: int, labels: list[str]) -> dict[str, object]:
    dataset_url = f"http://localhost:8000/api/datasets/{dataset_id}"
    with urlopen(f"{dataset_url}/labels") as response:
        etag = response.headers.get("ETag")
    headers = {"Content-Type": "application/json"}
    headers["If-Match" if etag else "If-None-Match"] = etag or "*"
    data = json.dumps({"labels": labels}).encode()
    req = Request(
        f"{dataset_url}/episodes/{episode_idx}/labels",
        data=data,
        method="PUT",
        headers=headers,
    )
    with urlopen(req) as response:
        return json.load(response)
```

### Step 4 — Verify persisted labels

Read back the saved labels after applying changes:

```bash
curl -fsS "http://localhost:8000/api/datasets/{dataset_id}/labels"
```

The optional `POST /labels/save` endpoint confirms persistence and also requires a
current revision precondition. It is not needed after a successful PUT.

#### Label storage on disk

In local storage mode, successful label mutations write inside the dataset's `meta/` directory:

```text
{DATA_DIR}/{dataset_id}/meta/episode_labels.json
```

For example, with the default dataset path:

```text
datasets/ur10e_episodes/meta/episode_labels.json
```

File structure:

```json
{
  "dataset_id": "ur10e_episodes",
  "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "LEFT", "RIGHT"],
  "episodes": {
    "0": ["LEFT", "SUCCESS"],
    "1": ["RIGHT", "SUCCESS"]
  }
}
```

When clearing labels is explicitly requested, send an empty `labels` array through
the episode-label PUT endpoint for each selected episode. Use the latest revision
precondition for every write and verify the result with GET. Do not overwrite label
files behind a running server.

### Step 5 — Verify in UI with Playwright

After applying labels via API, refresh the browser and verify using Playwright:

1. Preserve unsaved drafts before refreshing. Navigate to the app using `navigate_page` or `browser_navigate` on the configured port.
2. Wait for the episode list using the available provider and take a fresh accessible snapshot.
3. Take a screenshot to confirm labels appear in the sidebar.
4. Use label filter buttons in the sidebar to verify counts match expectations.
5. Click individual episodes and scroll to the "Episode Labels" section to verify correct labels are applied.

### Step 6 — Interactive annotation via UI

For individual episode review or correction:

1. Click an episode button in the sidebar using the current snapshot.
2. Scroll to "Episode Labels" through the provider or the agent's `scrollIntoView` example.
3. Toggle requested labels (SUCCESS, FAILURE, PARTIAL or custom labels); clicking a selected label removes it.
4. Click "Save Episode", wait for acknowledgment and independently GET the saved resources. Resolve partial saves and HTTP 412 before continuing.
5. Use the separate "Next Episode" control after persistence is verified. Retain unrelated and unmounted drafts.

Before judging, check saved-input readiness for every selected target and validation sample under the current source and principal. A clean active episode does not establish readiness elsewhere. Failed refreshes retain drafts and do not authorize work from stale acknowledgments. Interpret metadata warnings by source; HTTP 200, local-origin listings or provider availability do not prove Blob metadata synchronization.

## Frontend UI Structure

The React app has these key areas for Playwright interaction:

| Area               | Selector Pattern                                     | Description                                                 |
|--------------------|------------------------------------------------------|-------------------------------------------------------------|
| Dataset catalog    | Region "Dataset catalog", combobox "Filter datasets" | Browse/filter datasets; inspect stale warnings              |
| Dataset disclosure | Button "Dataset", dialog "Select dataset"            | Choose current dataset entries                              |
| Dataset batch      | Region "Dataset workspace" and its disclosure        | Targets, validation samples, approvals, jobs and withdrawal |
| Episode sidebar    | Current episode button roles/names within `aside`    | Select actual episode IDs and label filters                 |
| Main workspace     | `main`, Playback frame slider and camera controls    | Annotation and multi-camera playback                        |
| Save/navigation    | "Save Episode", "Previous Episode", "Next Episode"   | Separate persistence/navigation with busy/status states     |
| Analysis           | "Expand Episode Analysis", region "Judge assessment" | Episode judge controls when available                       |
| Workspace return   | "Return to episode" when present                     | Return while preserving retained drafts                     |

Use fresh roles/names, not fixed header selectors or heading-based clicks. Observe disabled, denied and checking-access states without enabling features merely to make controls appear. Check affected keyboard navigation, focus restoration, live-region announcements and narrow-layout reflow; visual inspection alone is not accessibility acceptance.

## Troubleshooting

| Issue                                    | Solution                                                                                                                        |
|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| Backend fails to start                   | Recreate the locked environment with `cd backend && uv sync --frozen --python 3.12 --group dev --extra analysis --extra export` |
| Frontend shows "Loading..." indefinitely | Verify backend is healthy: `curl http://localhost:8000/health`                                                                  |
| No datasets visible                      | Check `DATA_DIR` in `backend/.env` points to a directory with dataset subdirectories                                            |
| Port conflict                            | Set `BACKEND_PORT` or `FRONTEND_PORT` environment variables                                                                     |
| CORS errors                              | Backend allows localhost ports 5173-5177; check the frontend port is in range                                                   |
| Labels not persisted after restart       | Check the PUT response; resolve any HTTP 412 revision conflict, then verify the saved labels with GET                           |
| Playwright opens separate Chrome window  | For the MCP background workflow, use `--headless`; reconfigure/restart only with authority                                      |
| Snapshot refs stale after navigation     | Take a fresh `read_page` or `browser_snapshot` before clicking                                                                  |
| Slider not responding to automation      | Use the agent's native input setter fallback as a state probe, not keyboard acceptance                                          |
| Sidebar not scrolling                    | Use the agent's `aside ul` scrolling example through the available evaluation tool                                              |

## VLM-as-Judge Workflow

The VLM judge scores each episode with an outcome MCQ (success/fail with N-sample self-consistency), process reward (per-frame progress + Spearman VOC), milestone decomposition and failure-mode attribution. Viewer and CLI share evaluator code, not automatically storage or identity.

### Storage and identity

| Surface               | Purpose                                          | Boundary                                                                     |
|-----------------------|--------------------------------------------------|------------------------------------------------------------------------------|
| Inference cache       | Disposable optimization                          | Viewer jobs select per-dataset/snapshot caches independently of fallback     |
| Durable jobs/evidence | Restart-safe lifecycle, approvals and provenance | Local job root or Azure dataset-container job store; source/principal scoped |
| JSONL export          | Portable result export with run/result IDs       | Not a job store, approval or proof of viewer reuse                           |

CLI local defaults use the dataset parent's `.curation/judge`; viewer local defaults use `DATA_DIR/.curation/judge`. Azure viewer jobs use the dataset container. Keep durable stores on persistent writable storage separate from scratch cache.

Cross-client reuse requires matching canonical dataset/source, principal scope, saved author/revision/snapshot, media identity/windows and runtime configuration (model revision, prompt, views and scoring settings), plus access to the same canonical evidence store. Prove matching run/result and snapshot identity through a CLI-to-viewer evidence read. Matching model names, a cache badge or JSONL filename is insufficient.

### Enable the judge

Pass the judge settings through the launcher's child environment. Store them in
`backend/.env` only when persistent defaults are explicitly requested:

```env
DATA_DIR=/abs/path/to/datasets
DATAVIEWER_AUTH_DISABLED=true

VLM_JUDGE_ENABLED=true
VLM_JUDGE_BACKEND=echo                        # echo | qwen3-vl | openai-compat
VLM_JUDGE_MODEL_ID=Qwen/Qwen3-VL-4B-Instruct
VLM_JUDGE_N_FRAMES=12
VLM_JUDGE_CACHE_DIR=outputs/vlm-judge/cache
```

> [!IMPORTANT]
> Restart only an owned, authorized backend after changing launch settings. Code reload does not refresh environment variables. Require `vlm_judge_enabled` from capabilities before submission; do not enable the judge for read-only inspection. Approve downloads and remote transmission of frames/saved instructions before inference.

### Backends at a glance

| Backend         | Use case                         | Notes                                                                           |
|-----------------|----------------------------------|---------------------------------------------------------------------------------|
| `echo`          | UI smoke / wiring tests          | Deterministic stub, no GPU, no network                                          |
| `qwen3-vl`      | Local HF inference               | Requires approved pinned weights and target GPU/runtime validation              |
| `openai-compat` | vLLM / NVIDIA NIM / Azure OpenAI | Set `VLM_JUDGE_BASE_URL` (+ `VLM_JUDGE_API_KEY` if needed); identical code path |

### UI workflow (Trajectory tab)

1. Open or reuse the dataviewer page on the configured port.
2. Choose a dataset, select an episode and switch to **Trajectory**.
3. Expand **Episode Analysis** and locate **Judge assessment**. A "VLM Judge" heading may instead identify an unavailable state.
4. Observe checking, disabled and denied states. If authorized and ready, save edits, verify read-back and resolve the saved snapshot before clicking **Run judge**.
5. Track the durable job to terminal status, then read canonical evidence/applicability before interpreting the outcome badge, progress, VOC, milestones and failure mode.
6. **Force fresh** bypasses inference cache, not saved-input or approval checks. Judge-only evidence does not apply labels.

### Playwright UI verification

Use a fresh `read_page` or `browser_snapshot`, then click current accessible controls. With `run_playwright_code`, a locator-based pattern is:

```javascript
const assessment = page.getByRole('region', { name: 'Judge assessment' });
await assessment.scrollIntoViewIfNeeded();
await assessment.getByRole('button', { name: 'Run judge', exact: true }).click();
```

Use that pattern only when submission is authorized and the control is available. Do not force clicks or use an unavailable-state heading to find the action. After terminal status and evidence read-back, wait for the evidence's outcome text through the provider:

```text
Wait for SUCCESS, FAILURE or Inconclusive, matching the retrieved evidence.
```

### Direct API calls (curl / Python)

Authenticated POST requires authentication and CSRF headers; the frontend hook handles those. Keep credentials/cookies outside Git and model-visible output. The example below uses loopback-only development with `DATAVIEWER_AUTH_DISABLED=true`, one actual inventory ID and an approved saved instruction:

```bash
API=http://localhost:8000
DATASET_ID='<dataset-id>'
EPISODE_ID='<actual-episode-id>'
EPISODE_URL="$API/api/datasets/$DATASET_ID/episodes/$EPISODE_ID/judge"
SNAPSHOT_ID=$(curl -fsS "$EPISODE_URL/snapshot" | jq -r .snapshot_id)
REQUEST_ID='<stable-request-id>'
curl -fsS "$EPISODE_URL" | jq
curl -i -fsS -X POST "$EPISODE_URL" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $REQUEST_ID" \
  -d "$(jq -n --arg snapshot "$SNAPSHOT_ID" '{snapshot_id: $snapshot, force: false}')"
```

Read `Location` and `Retry-After` from acceptance, poll that location through the client's polling facility until terminal and retrieve canonical evidence separately:

```bash
JOB_ID='<accepted-job-id>'
curl -fsS "$API/api/judge/jobs/$JOB_ID" | jq
curl -fsS --get "$API/api/judge/results" \
  --data-urlencode "dataset_id=$DATASET_ID" \
  --data-urlencode "episode_index=$EPISODE_ID" | jq
```

Paginate status/results as needed. `404` indicates missing/inaccessible resources; `422` indicates invalid input including instruction overrides; `409` requires saved-state/configuration reconciliation. Never report `202` as inference success.

### Single-episode CLI smoke (no dataviewer needed)

Use the existing frozen root evaluator environment. Set `DATASET` to an approved isolated fixture, `EPISODE_ID` to one actual inventory ID, and `JOB_DIR`/`OUTPUT` to isolated paths. Commands write durable state and exports; do not test on user datasets without permission.

```bash
uv run --frozen python -m evaluation.vlm_judge.run \
  --dataset "$DATASET" --indices "$EPISODE_ID" \
  --single --mode judge --backend echo --n-frames 6 \
  --job-dir "$JOB_DIR" --request-id "$REQUEST_ID" --output "$OUTPUT"
```

Inspect terminal status and use `--operation results --episode-index "$EPISODE_ID"` with matching dataset/principal/store arguments to retrieve evidence. Echo proves wiring, not model quality or GPU readiness. Never issue repeated single submissions to bypass dataset approval.

### Bulk evaluation via the CLI

1. Enumerate actual target and sample IDs from dataset metadata, not a count-derived range. HTTP users can paginate `/api/judge/episodes`, retaining its inventory revision across pages.
2. Save human sample annotations. Resolve saved `annotation_author_id`, `annotation_revision` and `snapshot_id` with the matching saved-input resolver. Use viewer snapshot references for CLI only after proving source/principal identity equivalence. Store a JSON object keyed by actual sample episode ID; each value contains exactly those three non-empty fields. Drafts or guessed revisions are not valid references.
3. Define `SAMPLE_IDS`/`TARGET_IDS` as comma-separated actual IDs, `SAMPLE_REFERENCES` as the saved JSON path and `PRINCIPAL_ID` as the matching scope. Keep shared runtime arguments unchanged throughout sample, approval and batch. For real inference, replace echo with the approved backend and pinned model/runtime before sampling; an echo approval does not approve another runtime.

```bash
JUDGE_ARGS=(--dataset "$DATASET" --job-dir "$JOB_DIR" \
  --principal-scope-id "$PRINCIPAL_ID" --backend echo --n-frames 6)
uv run --frozen python -m evaluation.vlm_judge.run "${JUDGE_ARGS[@]}" \
  --mode sample --indices "$SAMPLE_IDS" --sample-references "$SAMPLE_REFERENCES" \
  --request-id "$SAMPLE_REQUEST_ID" --output "$SAMPLE_OUTPUT"
```

Read the returned sample job ID, terminal status and evidence for each sample. Compare with saved human outcomes and review disagreements/inconclusive evidence. Only after explicit review, approve the sample job. Add `--acknowledge-exceptions` only when the reviewer explicitly accepts those exceptions.

```bash
uv run --frozen python -m evaluation.vlm_judge.run "${JUDGE_ARGS[@]}" \
  --operation approve --job-id "$SAMPLE_JOB_ID"
```

Read the returned approval `id` and submit the selected batch. Judge mode produces evidence only; `--mode judge-and-label` additionally requires explicit label-write authority.

```bash
uv run --frozen python -m evaluation.vlm_judge.run "${JUDGE_ARGS[@]}" \
  --mode judge --indices "$TARGET_IDS" --approval-id "$APPROVAL_ID" \
  --request-id "$BATCH_REQUEST_ID" --output "$BATCH_OUTPUT"
```

If approval is stale, stop, refresh saved inputs/configuration, rerun samples and review a new approval. Do not force or fan out single submissions. HTTP follows the same sequence: sample POST `/api/judge/jobs` with `episode_indices`, `samples` and `snapshot_ids`, review evidence, POST `/jobs/{id}/approve`, then submit judge/judge-and-label with `approval_id` and current snapshots. Dataset submissions require `Idempotency-Key`.

Dataset/policy wrapper scripts under `evaluation/vlm_judge/scripts/` are conveniences, not approval bypasses. Inspect their accepted lifecycle arguments before use; fall back to the generic CLI when a wrapper cannot carry the required selection, references or approval.

### Status, cancellation and restart recovery

Use the same dataset/principal/job root with `--operation status --job-id`, `--operation cancel --job-id` or `--operation retry --job-id`. Status describes saved work, not worker health. Cancellation fences queued/running work and queued application; saved evidence remains. Retry queues eligible failed/cancelled targets in partial, failed or cancelled jobs; the worker revalidates saved inputs/approval during execution. Withdrawn jobs cannot be retried.

Submit/retry normally runs a local worker until terminal and exports available results. `--detach` returns acceptance only and requires a matching `--operation worker` process. Client interruption is not cancellation. After restart, inspect the existing job before resubmitting; retain durable storage and allow worker lease recovery rather than deleting jobs. Workers sharing inference capacity must use the same store, capacity limit and scope.

### Apply or withdraw AI labels

After reviewing evidence and obtaining label-write authority, use HTTP `/jobs/{id}/apply` (optionally selected `episode_indices`) or CLI `--operation apply --job-id`. Application has separate counts/errors and conditional persistence: verify saved labels and provenance independently. Judge-only success is not applied-label success.

For requested withdrawal, obtain CLI `--operation reset-preview` or HTTP `/resets/preview`; review affected AI-applied labels and conflicts, then confirm that exact preview with `--operation reset-confirm --preview-id` or HTTP `/resets`. Read `reset-status` and use `reset-retry` for eligible failures. A stale/changed preview requires a new preview and confirmation.

Withdrawal selectively removes AI-applied values while preserving human edits and retained evidence/history; do not clear all labels or delete cache/job files as a substitute.

### Verification and reporting

Use isolated echo cases for single acceptance/polling/evidence, approved batch, stale approval rejection and preview-bound withdrawal preserving human edits/history. Prove CLI-to-viewer reuse with matching canonical evidence identity. Source inspection alone does not claim these cases executed.

For behavior changes, follow scoped viewer guidance: failing-first tests, focused grouped development checks and configured gates after coherent changes. Use frozen dependencies and isolated storage, without changing user `.env` or restarting user-owned services for evidence.

Report terminal judged/applied/error counts, evidence applicability, runtime/model/prompt identity and output paths. Separate unit/static, real-HTTP, browser collection, native browser execution, simulation, assistive-technology and target GPU evidence. Missing prerequisites remain explicit gaps; prior passing counts or browser collection do not close unexecuted acceptance gates.

> Brought to you by physical-ai-toolchain
