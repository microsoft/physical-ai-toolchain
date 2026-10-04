---
name: dataviewer
description: 'Start and interact with the Dataset Analysis Tool (dataviewer) for browsing, annotating, and exporting robotic training episodes'
---

# Dataviewer Skill

Launch and interact with the Dataset Analysis Tool — a full-stack application for analyzing and annotating robotic training data from episode-based datasets.

## Prerequisites

| Platform | Requirement                                                                                                           |
|----------|-----------------------------------------------------------------------------------------------------------------------|
| All      | Python 3.12+, Node.js 24+, npm, `uv`                                                                                  |
| All      | `ffmpeg` on `PATH` outside the backend container, for HDF5 camera video (OpenCV also works) and LeRobot episode clips |

The backend virtual environment and repository-root npm workspace dependencies are auto-created on first launch by `start.sh`.

## Launch and Connect Workflow

Follow these steps in order every time you start or connect to the dataviewer.

When a caller or workflow supplies the URL of a Dataviewer it already runs, such as the instance the Sim Workspace Command Center opens for a workspace, skip Step 1 and open that URL in Step 2. Start `start.sh` only when no running instance is supplied, so one dataset folder never gets a second Dataviewer.

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

### Step 2 — Open the app in the browser

After confirming both services are running (look for `[OK] Backend is healthy` in terminal output), or when a running URL was supplied, open the frontend with the integrated browser's `open_browser_page` tool:

```text
open_browser_page("http://localhost:5173")
```

That page is what the user sees. Use the supplied URL, or substitute a non-default `FRONTEND_PORT` for `5173`.

### Step 3 — Load browser automation tools

Drive the UI with whichever browser tool family the host provides:

| Tool family        | Tools                                                                                                                        | How it works                                       |
|--------------------|------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------|
| Integrated browser | `open_browser_page`, `navigate_page`, `read_page`, `click_element`, `type_in_page`, `run_playwright_code`, `screenshot_page` | Acts on the page the user sees                     |
| Playwright MCP     | `browser_snapshot`, `browser_navigate`, `browser_click`, `browser_type`, `browser_evaluate`, `browser_take_screenshot`       | Acts headlessly on a separate copy of the same URL |

Search for deferred tools before their first use. The Playwright MCP server must be declared in `.vscode/mcp.json` with the `--headless` flag:

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
> The `--headless` flag is required. Without it, Playwright opens a separate Chromium window instead of working behind the page the user sees.

Load the Playwright MCP tools with:

```text
tool_search_tool_regex("playwright|browser_snapshot|browser_navigate|browser_click|browser_type")
```

If that search returns no results, the MCP server has not started; ask the user to run **MCP: Start Server** → **playwright** from the VS Code Command Palette, or use the integrated browser tools. If neither family is available, open the page and guide the user through the steps.

### Step 4 — Interact with the UI

Both tool families see the same backend state, so API-driven changes (labels, annotations) appear in both.

| Action             | Integrated browser    | Playwright MCP            | Notes                                        |
|--------------------|-----------------------|---------------------------|----------------------------------------------|
| Capture page state | `read_page`           | `browser_snapshot`        | Call first before any click/type to orient   |
| Navigate to URL    | `navigate_page`       | `browser_navigate`        | Use to reload or go to a route               |
| Click an element   | `click_element`       | `browser_click`           | Target `aside li button` for episodes        |
| Type into input    | `type_in_page`        | `browser_type`            | For search or label inputs                   |
| Run a script       | `run_playwright_code` | `browser_evaluate`        | For sliders, scrolling and multi-step checks |
| Take a screenshot  | `screenshot_page`     | `browser_take_screenshot` | Use to verify visual state                   |

Always read the current page state before issuing click or type actions. Reference the selector patterns in the [Frontend UI Structure](#frontend-ui-structure) section below.

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

| Endpoint                                  | Method | Description                                                                                         |
|-------------------------------------------|--------|-----------------------------------------------------------------------------------------------------|
| `/api/datasets/{id}/episodes/{idx}/judge` | GET    | Cache lookup: returns any persisted judgment for the episode without invoking the model             |
| `/api/datasets/{id}/episodes/{idx}/judge` | POST   | Run the multi-step judge (cache-first unless `force: true`); body: `{instruction?, views?, force?}` |

`POST` response (snake_case on the wire, camelCased by the frontend client) is the composite `JudgeResult`:

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
  "progress_per_frame": [0, 14, 28, 42, 57, 71, 85, 100],
  "voc": 0.92,
  "milestones": [
    {"name": "approach_object", "completed": true, "frame_range": "0-3", "evidence": "..."}
  ],
  "failure_mode": null,
  "cached": false
}
```

Key knobs (`backend/.env`):

| Variable              | Default                     | Purpose                                                                      |
|-----------------------|-----------------------------|------------------------------------------------------------------------------|
| `VLM_JUDGE_ENABLED`   | `false`                     | Mount the router                                                             |
| `VLM_JUDGE_BACKEND`   | `echo`                      | `qwen3-vl` (local HF) / `openai-compat` (vLLM, NIM, AOAI) / `echo` (offline) |
| `VLM_JUDGE_MODEL_ID`  | `Qwen/Qwen3-VL-4B-Instruct` | HF id or remote model name                                                   |
| `VLM_JUDGE_BASE_URL`  | _(unset)_                   | OpenAI-compatible server URL (`openai-compat` only)                          |
| `VLM_JUDGE_API_KEY`   | _(unset)_                   | Bearer token for the remote backend                                          |
| `VLM_JUDGE_N_FRAMES`  | `12`                        | Frames sampled per episode                                                   |
| `VLM_JUDGE_CACHE_DIR` | `outputs/vlm-judge/cache`   | SHA256-keyed result cache; empty disables disk cache                         |

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

When an episode's files can't be read, `GET /api/datasets/{id}/episodes/{idx}`, `detect`, `annotations/auto` and `export/preview` return HTTP 500 with `{"code": "EPISODE_LOAD_FAILED"}`, and the backend log has the cause.
A failed export returns `"error": "Export failed"`, or a `complete` event with `"success": false` from the stream; the backend log has the reason.

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

Datasets that record multiple camera streams expose a camera selector in the annotation workspace header. Default selection is `episode.cameras[0]` (or the first key of `videoUrls` when `cameras` is empty). User selections persist for the current episode; switching to an episode that no longer contains the selected camera resets selection back to `cameras[0]`. Both video playback and `/frames/{idx}` thumbnail extraction follow the active camera.

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
A LeRobot export can write the most recently saved instruction as LeRobot rows: the instruction and paraphrases as `task_aug`, and the subtask instructions as one numbered `plan` row at the first frame.

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

1. Navigate to the app: `browser_navigate` to `http://localhost:5173`.
2. Wait for episode list to load: `browser_wait_for` with text like `"64 Episodes"`.
3. Take a screenshot to confirm labels appear in the sidebar.
4. Use label filter buttons in the sidebar to verify counts match expectations.
5. Click individual episodes and scroll to the "Episode Labels" section to verify correct labels are applied.

### Step 6 — Interactive annotation via UI

For individual episode review or correction:

1. Click an episode in the sidebar (`aside li button` elements).
2. Scroll to the "Edit Tools" / "Episode Labels" section with `run_playwright_code` or `browser_evaluate` and `scrollIntoView`.
3. Toggle label buttons (SUCCESS, FAILURE, PARTIAL, or custom labels) — clicking a selected label removes it.
4. Click "Save & Next Episode" to persist and continue, or "Save Episode" on the final episode.

The Edit Tools trajectory editor adjusts state channels per frame, labelled with the dataset's own channel names, and the trajectory plot previews each adjustment.
HDF5 exports keep the recorded joint positions as `data/qpos` and add the adjustments beside them as `data/qpos_adjusted`, with `data/qpos_adjusted_mask` marking the edited rows and the adjustment list in the episode's `.meta.json`.
Velocities, actions and the other exported arrays stay as recorded.

LeRobot v3.0 sources, including sim captures, export to a new LeRobot v3.0 dataset at the output path. That path must be new or empty and outside the source; an existing empty directory, such as a mount point, is kept.
The export locks the directory with a hidden `.dataviewer-export.lock` file and stages in a hidden `.dataviewer-export.partial` directory, so a second export to the same directory fails while the first runs.
If the backend stops mid-export, or an export fails and can't remove what it moved, the next export to that directory cleans up first. It removes the earlier export's staging and the moved files that still match the identities that export recorded, and keeps a dataset whose move finished. Anything else stays, and exports there fail until it's removed.
LeRobot exports need a filesystem with file locking; on one without, the export fails and can leave an empty `.dataviewer-export.lock`.
The export keeps `observation.state` and every other recorded feature, and adds adjustments as `adjusted.observation.state` with `adjusted.observation.state_mask`. The `adjusted.` prefix keeps both out of LeRobot policy inputs.
Removing or inserting frames renumbers `frame_index` and `timestamp`. `dataviewer-export.json` maps every output frame to its source frame and records the edits and remapped subtasks.
Subtasks shrink to the frames that survive the edits, and a LeRobot export also writes each one as a LeRobot `subtask` row in `language_persistent`, starting at its first output frame.
LeRobot keeps a subtask active until the next one starts, so frames between two subtasks read as the earlier one; `dataviewer-export.json` keeps the exact ranges.
Opening an export shows its subtasks in the editor: LeRobot rows run until the next one starts, and HDF5 `.subtasks.json` files keep exact ranges. Re-exporting keeps unchanged subtasks as recorded, writes changed ones in their place, and removes them when all are deleted; a saved draft takes precedence. `dataviewer-export.json` records `subtasks` as `null` when the export kept the recorded ones and `[]` when it removed them.
Recorded language annotations move with the edited frames, apart from rows the exported subtasks or language instructions replace. Clearing **Include subtasks as LeRobot subtask annotations** in the export dialog leaves your subtask changes out; recorded annotations are still exported. For HDF5 the option reads **Include subtask metadata**, and clearing it still carries a recorded `.subtasks.json` forward.
A LeRobot export has language columns when its source has them or when an exported episode gets subtask or language-instruction rows.
**Include language instructions as LeRobot task phrasings and plan**, on by default for LeRobot sources, writes each episode's most recently saved language instruction as `task_aug` and `plan` rows that replace the recorded ones; `dataviewer-export.json` records whose instruction was used.
API exports through `/export` and `/export/stream` include them too, unless the request body sets `"includeLanguageInstructions": false`.
Each export is a separate dataset. LeRobot merges datasets only when their features match, so a cropped or adjusted export won't merge with an unedited one.

## Frontend UI Structure

The React app has these key areas for browser automation:

| Area             | Selector Pattern                  | Description                                     |
|------------------|-----------------------------------|-------------------------------------------------|
| Header           | `header`                          | Contains title and dataset selector dropdown    |
| Dataset selector | `header select` or `header input` | Dropdown (multi-dataset) or text input (single) |
| Episode sidebar  | `aside`                           | Scrollable episode list with selection state    |
| Episode item     | `aside li button`                 | Clickable episode entry with index and metadata |
| Main workspace   | `main`                            | Annotation workspace with frame viewer          |
| Label filter     | Label filter component in sidebar | Filter episodes by annotation labels            |

## Troubleshooting

| Issue                                    | Solution                                                                                                                                          |
|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| Backend fails to start                   | Recreate the locked environment with `cd backend && uv sync --frozen --python 3.12 --group dev --extra analysis --extra export`                   |
| Frontend shows "Loading..." indefinitely | Verify backend is healthy: `curl http://localhost:8000/health`                                                                                    |
| No datasets visible                      | Check `DATA_DIR` in `backend/.env` points to a directory with dataset subdirectories                                                              |
| "The episode's files couldn't be read"   | The API returned 500 `EPISODE_LOAD_FAILED`; the backend log names the file and error                                                              |
| Export reports "Export failed"           | Check the backend log: another export may be writing to the directory, it may not be new or empty, or its filesystem may not support file locking |
| Port conflict                            | Set `BACKEND_PORT` or `FRONTEND_PORT` environment variables                                                                                       |
| CORS errors                              | Backend allows localhost ports 5173-5177; check the frontend port is in range                                                                     |
| Labels not persisted after restart       | Check the PUT response; resolve any HTTP 412 revision conflict, then verify the saved labels with GET                                             |
| Playwright opens separate Chrome window  | Ensure `--headless` is in the Playwright MCP args in `.vscode/mcp.json`; restart the MCP server after changing                                    |
| Snapshot refs stale after navigation     | Read the page again with `read_page` or `browser_snapshot` before clicking; refs change on page updates                                           |
| Slider not responding to automation      | Use `run_playwright_code` or `browser_evaluate` with native input value setter and dispatch `input` + `change` events                             |
| Sidebar not scrolling                    | Scroll the `aside ul` element directly via `run_playwright_code` or `browser_evaluate` with `element.scrollTop = N`                               |

## VLM-as-Judge Workflow

The VLM judge scores each episode with an outcome MCQ (success/fail with N-sample self-consistency), a GVL process reward (per-frame 0-100 progress + Spearman VOC), milestone decomposition, and failure-mode attribution. Both the dataviewer UI and the CLI under `evaluation/vlm_judge/` consume the same `JudgeService`, so cache hits and prompt versions stay aligned across surfaces.

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
> Restart an owned backend after changing launch settings. Uvicorn `--reload` re-reads code, not env vars. The frontend reads `vlm_judge_enabled` from `GET /api/datasets/{id}/capabilities` before requesting an episode judgment.

### Backends at a glance

| Backend         | Use case                         | Notes                                                                           |
|-----------------|----------------------------------|---------------------------------------------------------------------------------|
| `echo`          | UI smoke / wiring tests          | Deterministic stub, no GPU, no network                                          |
| `qwen3-vl`      | Local HF inference               | First call downloads weights; ~10 GB GPU for `Qwen3-VL-4B-Instruct` BF16        |
| `openai-compat` | vLLM / NVIDIA NIM / Azure OpenAI | Set `VLM_JUDGE_BASE_URL` (+ `VLM_JUDGE_API_KEY` if needed); identical code path |

### UI workflow (Trajectory tab)

1. Open the dataviewer (`open_browser_page("http://localhost:5173")`).
2. Pick a dataset, select an episode, switch to the **Trajectory** tab.
3. The **VLM Judge** panel sits between **Episode Labels** and **Language Instructions**.
4. Click **Run judge** → outcome badge, progress sparkline, VOC, optional milestones + failure mode appear. The result also lands on disk under `VLM_JUDGE_CACHE_DIR`.
5. Re-visiting the same episode shows a `cached` badge. Click **Force fresh** to bypass the cache and re-run.

### Browser UI verification

Use the same browser tooling as the rest of the skill, but route through the new panel selectors. After reading the page with `read_page` or `browser_snapshot`, click using its element refs. As a script fallback when the page state lacks button refs, run this body with `run_playwright_code` or `browser_evaluate`:

```javascript
browser_evaluate: () => {
  const headers = Array.from(document.querySelectorAll('h3'))
  const judge = headers.find((el) => el.textContent?.includes('VLM Judge'))
  judge?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  const run = Array.from(judge?.parentElement?.querySelectorAll('button') ?? [])
    .find((b) => /run judge|re-evaluate/i.test(b.textContent ?? ''))
  run?.click()
  return run ? 'clicked' : 'panel not visible'
}
```

To assert the result rendered, wait for the outcome badge text, here with Playwright MCP (with the integrated browser, wait for the same text in `run_playwright_code`):

```text
browser_wait_for(text="SUCCESS")  # or "FAILURE", "Inconclusive"
```

### Direct API calls (curl / Python)

CSRF must be honored on POST. The frontend hook does this transparently; for ad-hoc shell:

```bash
CSRF=$(curl -s http://localhost:8000/api/csrf-token -c /tmp/dv-cookie | jq -r .csrf_token)

# Cache lookup (no inference)
curl -s http://localhost:8000/api/datasets/leisaac-pick-orange/episodes/0/judge | jq

# Run the judge (cache-first)
curl -s -X POST http://localhost:8000/api/datasets/leisaac-pick-orange/episodes/0/judge \
  -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b /tmp/dv-cookie \
  -d '{"force": false}' | jq
```

### Bulk evaluation via the CLI (no dataviewer needed)

```bash
# Single dataset
evaluation/vlm_judge/scripts/evaluate-leisaac-pick-orange.sh --limit 5

# Generic
python -m evaluation.vlm_judge.run \
  --dataset datasets/cnc_lerobot \
  --views observation.images.color \
  --backend qwen3-vl \
  --model-id Qwen/Qwen3-VL-4B-Instruct \
  --output outputs/vlm-judge/cnc_lerobot.jsonl \
  --limit 5

# Policy-rollout MP4s (e.g. leisaac-tests/pickup-orange/)
evaluation/vlm_judge/scripts/evaluate-policy-rollouts.sh
```

The CLI writes a JSONL with the same composite schema as the API, keyed on the same SHA256 cache, so a CLI run primes the dataviewer's cache (and vice versa).

> Brought to you by physical-ai-toolchain
