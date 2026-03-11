---
name: dataviewer
description: 'Start and interact with the Dataset Analysis Tool (dataviewer) for browsing, annotating, and exporting robotic training episodes'
---

# Dataviewer Skill

Launch and interact with the Dataset Analysis Tool — a full-stack application for analyzing and annotating robotic training data from episode-based datasets.

## Prerequisites

| Platform | Requirement |
|----------|-------------|
| All | Python 3.11+, Node.js 18+, npm, `uv` |

The backend virtual environment and frontend `node_modules` are auto-created on first launch by `start.sh`.

## Launch and Connect Workflow

Follow these steps in order every time the dataviewer is started.

### Step 1 — Start the app

Launch `start.sh` as a background terminal process. The script prints `[OK] Both services are running` and the URLs when both services are healthy.

```bash
cd src/dataviewer && ./start.sh
```

With a custom dataset path:

```bash
cd src/dataviewer && HMI_DATA_PATH=/path/to/datasets ./start.sh
```

### Step 2 — Open SimpleBrowser

After confirming both services are running (look for `[OK] Backend is healthy` in terminal output), open the frontend in VS Code's SimpleBrowser using the `open_browser_page` tool:

```text
open_browser_page("http://localhost:5173")
```

SimpleBrowser is the primary visual interface for the user. All Playwright automation operates headlessly in the background — the user sees results in SimpleBrowser.

If a non-default `FRONTEND_PORT` was set, substitute that port instead of `5173`.

### Step 3 — Load the Playwright MCP tools

Playwright runs in **headless mode** so it does not open a separate browser window. All visual feedback goes through SimpleBrowser (Step 2). The Playwright MCP server must be declared in `.vscode/mcp.json` with the `--headless` flag:

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
> The `--headless` flag is required. Without it, Playwright opens a separate Chromium window instead of working invisibly behind SimpleBrowser.

Before issuing any browser actions, always load the Playwright tools with:

```text
tool_search_tool_regex("playwright|browser_snapshot|browser_navigate|browser_click|browser_type")
```

If the search returns no results the MCP server has not started. Ask the user to open the VS Code Command Palette and run **MCP: Start Server** → **playwright**, then retry the search.

### Step 4 — Interact via Playwright MCP

Playwright operates headlessly on the same URL as SimpleBrowser. Both see the same backend state, so API-driven changes (labels, annotations) appear in both.

Once the tools are available, use the following patterns for all UI interaction:

| Action | Playwright MCP Tool | Notes |
|--------|-------------------|-------|
| Capture page state | `browser_snapshot` | Call first before any click/type to orient |
| Navigate to URL | `browser_navigate` | Use to reload or go to a route |
| Click an element | `browser_click` | Target `aside li button` for episodes |
| Type into input | `browser_type` | For search or label inputs |
| Take a screenshot | `browser_take_screenshot` | Use to verify visual state |

Always call `browser_snapshot` first to inspect the current DOM before issuing click or type actions. Reference the selector patterns in the [Frontend UI Structure](#frontend-ui-structure) section below.

## Quick Start

Start the dataviewer with the default dataset path:

```bash
cd src/dataviewer && ./start.sh
```

Start with a custom dataset path:

```bash
cd src/dataviewer && HMI_DATA_PATH=/path/to/datasets ./start.sh
```

## Parameters Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `HMI_DATA_PATH` | `../../../datasets` (relative to `backend/`) | Directory containing dataset subdirectories |
| `BACKEND_PORT` | `8000` | FastAPI backend port |
| `FRONTEND_PORT` | `5173` | Vite frontend dev server port |
| `HEALTH_TIMEOUT` | `30` | Seconds to wait for backend health check |

### Dataset Path Configuration

The `HMI_DATA_PATH` environment variable controls which datasets are visible in the app. Each subdirectory under this path is treated as a separate `dataset_id`.

**Methods to set `HMI_DATA_PATH`:**

1. **Environment variable override** (recommended for ad-hoc use):

    ```bash
    HMI_DATA_PATH=/path/to/datasets ./start.sh
    ```

2. **Edit `backend/.env`** (persists across restarts):

    ```env
    HMI_DATA_PATH=/path/to/datasets
    ```

3. **Export before launch** (session-scoped):

    ```bash
    export HMI_DATA_PATH=/path/to/datasets
    cd src/dataviewer && ./start.sh
    ```

When a dataset path is provided, update `backend/.env` so the value persists:

1. Read the current `backend/.env` file.
2. Replace the `HMI_DATA_PATH=` line with the new absolute path.
3. Start the app with `./start.sh`.

## Architecture

```text
src/dataviewer/
├── start.sh              # Orchestrator: launches backend + frontend
├── backend/
│   ├── .env              # HMI_DATA_PATH and test config
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

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/datasets` | GET | List all datasets |
| `/api/datasets/{id}` | GET | Get dataset metadata and capabilities |
| `/api/datasets/{id}/episodes` | GET | List episodes in a dataset |
| `/api/datasets/{id}/episodes/{idx}` | GET | Get episode data with trajectory and metadata |
| `/api/datasets/{id}/episodes/{idx}/trajectory` | GET | Get trajectory data only |
| `/api/datasets/{id}/episodes/{idx}/frames/{frame}` | GET | Get a single frame image |
| `/api/datasets/{id}/episodes/{idx}/cameras` | GET | List available camera views |
| `/api/datasets/{id}/episodes/{idx}/video/{camera}` | GET | Stream video for a camera |
| `http://localhost:8000/docs` | GET | Swagger UI documentation |

### Label Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/datasets/{id}/labels` | GET | Get all episode labels and available label options |
| `/api/datasets/{id}/labels/options` | GET | List available label options |
| `/api/datasets/{id}/labels/options` | POST | Add a new label option (`{"label": "NAME"}`) |
| `/api/datasets/{id}/episodes/{idx}/labels` | GET | Get labels for one episode |
| `/api/datasets/{id}/episodes/{idx}/labels` | PUT | Set labels for one episode (`{"labels": ["A", "B"]}`) |
| `/api/datasets/{id}/labels/save` | POST | Persist all labels to disk |

### Annotation Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/datasets/{id}/episodes/{idx}/annotations` | GET | Get structured annotations |
| `/api/datasets/{id}/episodes/{idx}/annotations` | PUT | Update structured annotations |
| `/api/datasets/{id}/episodes/{idx}/annotations` | DELETE | Remove annotations |
| `/api/datasets/{id}/episodes/{idx}/annotations/auto` | POST | Trigger auto-annotation |
| `/api/datasets/{id}/annotations/summary` | GET | Get annotation summary across episodes |

### Export and Analysis Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/datasets/{id}/export` | POST | Export dataset with filters |
| `/api/datasets/{id}/export/stream` | POST | Stream export |
| `/api/datasets/{id}/export/preview` | GET | Preview export configuration |
| `/api/datasets/{id}/episodes/{idx}/detect` | POST | Run object detection |
| `/api/analysis/trajectory-quality` | POST | Trajectory quality analysis |
| `/api/analysis/anomaly-detection` | POST | Anomaly detection |
| `/api/ai/suggest-annotation` | POST | AI-suggested annotations |

## Annotation Workflow

Annotation combines API calls for efficiency with Playwright UI interaction for verification. Use the API for bulk operations and the UI for visual review and spot-checking.

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

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | float | Time in seconds from episode start |
| `frame` | int | Frame index |
| `joint_positions` | list[float] | Joint positions for all robot joints |

The `meta` field of the episode response contains `index`, `length`, `task_index`, and `has_annotations`.

### Step 2 — Determine labels from trajectory

Analyze gripper and joint data at multiple time points to classify episodes. Check the midpoint first, then 25% and 75% for episodes where grasp actions happen earlier or later:

```python
# Example: check grip values at multiple points for robust classification
for pct in [25, 50, 75]:
    idx = int(len(traj) * pct / 100)
    jp = traj[idx]['joint_positions']
    right_grip = jp[7]   # Right arm gripper index
    left_grip = jp[15]   # Left arm gripper index
```

> [!IMPORTANT]
> Some episodes have late or early grasp actions, so checking only the midpoint may yield UNKNOWN results. Always check multiple time points (25%, 50%, 75%) and the minimum grip value across the full trajectory for robust classification.

### Step 3 — Apply labels via API

Use the PUT endpoint for each episode:

```bash
curl -s -X PUT "http://localhost:8000/api/datasets/{dataset_id}/episodes/{idx}/labels" \
  -H "Content-Type: application/json" \
  -d '{"labels": ["RIGHT", "SUCCESS"]}'
```

For bulk annotation, loop over episodes in a script:

```python
import json, urllib.request

def annotate(dataset_id, episode_idx, labels):
    data = json.dumps({"labels": labels}).encode()
    req = urllib.request.Request(
        f"http://localhost:8000/api/datasets/{dataset_id}/episodes/{episode_idx}/labels",
        data=data, method="PUT",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())
```

### Step 4 — Persist labels

After applying labels via the API, persist them to disk:

```bash
curl -s -X POST "http://localhost:8000/api/datasets/{dataset_id}/labels/save"
```

> [!WARNING]
> Labels applied via PUT are held in memory until saved. Always call the save endpoint after bulk annotation to avoid data loss.

#### Label storage on disk

The save endpoint writes labels to a JSON file inside the dataset's `meta/` directory:

```text
{HMI_DATA_PATH}/{dataset_id}/meta/episode_labels.json
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

To clear all labels for a fresh start, overwrite the file with an empty `episodes` object:

```json
{
  "dataset_id": "{dataset_id}",
  "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "LEFT", "RIGHT"],
  "episodes": {}
}
```

After editing the file on disk, restart the backend or reload the page for changes to take effect.

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
2. Scroll to the "Edit Tools" / "Episode Labels" section using `browser_evaluate` with `scrollIntoView`.
3. Toggle label buttons (SUCCESS, FAILURE, PARTIAL, or custom labels) — clicking a selected label removes it.
4. Click "Save All" to persist.

## Frontend UI Structure

The React app has these key areas for Playwright interaction:

| Area | Selector Pattern | Description |
|------|-----------------|-------------|
| Header | `header` | Contains title and dataset selector dropdown |
| Dataset selector | `header select` or `header input` | Dropdown (multi-dataset) or text input (single) |
| Episode sidebar | `aside` | Scrollable episode list with selection state |
| Episode item | `aside li button` | Clickable episode entry with index and metadata |
| Main workspace | `main` | Annotation workspace with frame viewer |
| Label filter | Label filter component in sidebar | Filter episodes by annotation labels |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Backend fails to start | Check `backend/.venv` exists; run `cd backend && uv venv --python 3.11 && source .venv/bin/activate && uv pip install -e ".[dev,analysis,export]"` |
| Frontend shows "Loading..." indefinitely | Verify backend is healthy: `curl http://localhost:8000/health` |
| No datasets visible | Check `HMI_DATA_PATH` in `backend/.env` points to a directory with dataset subdirectories |
| Port conflict | Set `BACKEND_PORT` or `FRONTEND_PORT` environment variables |
| CORS errors | Backend allows localhost ports 5173-5177; check the frontend port is in range |
| Labels not persisted after restart | Call `POST /api/datasets/{id}/labels/save` after API-based annotation |
| Playwright opens separate Chrome window | Ensure `--headless` is in the Playwright MCP args in `.vscode/mcp.json`; restart the MCP server after changing |
| Snapshot refs stale after navigation | Always take a fresh `browser_snapshot` before clicking; refs change on page updates |
| Slider not responding to Playwright | Use `browser_evaluate` with native input value setter and dispatch `input` + `change` events |
| Sidebar not scrolling | Scroll the `aside ul` element directly via `browser_evaluate` with `element.scrollTop = N` |

> Brought to you by physical-ai-toolchain
