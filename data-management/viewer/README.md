---
title: Dataset Analysis Tool
description: Run and configure the web application for analyzing and annotating episode-based robotics datasets
author: Microsoft
ms.date: 2026-10-01
ms.topic: overview
---

A full-stack application for analyzing and annotating robotic training data from episode-based datasets. Features include episode browsing, frame annotation, and export capabilities.

## 🏗️ Architecture

| Component | Stack                       | Default port |
|-----------|-----------------------------|--------------|
| Backend   | FastAPI and Python          | 8000         |
| Frontend  | React, Vite, and TypeScript | 5173         |

## 📋 Prerequisites

| Tool    | Version                |
|---------|------------------------|
| Python  | 3.12+                  |
| Node.js | 24+                    |
| npm     | Bundled with Node.js   |
| uv      | Current stable release |
| ffmpeg  | Current stable release |

The backend container image installs `ffmpeg`. When you run the backend outside it, put `ffmpeg` on `PATH`:

- HDF5 datasets need it, or OpenCV, to stream camera images as video. Without either, their camera video is unavailable.
- LeRobot datasets play without it, but each episode then loads its camera's whole video file instead of the episode's clip.

## 📦 Installation

### Backend Setup

```bash
cd backend

# Install locked dependencies (include 'azure' extra for blob storage support)
uv sync --frozen --python 3.12 --group dev --extra export --extra azure
```

### Frontend Setup

```bash
cd ../..
npm ci
```

The repository root lockfile is the source of truth for the frontend npm workspace.

From the repository root on Bash hosts, launch against a captured dataset parent without changing environment files:

```bash
bash data-management/viewer/start.sh --check
bash data-management/viewer/start.sh --config-preview --data-dir /path/to/datasets
bash data-management/viewer/start.sh --data-dir /path/to/datasets
```

The launcher resolves Vite from the npm workspace, binds both services to loopback,
and fails if either service exits or misses readiness. It requires the selected ports
to be available; it does not switch to a different frontend port. Optional VLM settings
may be omitted from `backend/.env`.

`--config-preview` prints resolved launch arguments without checking dependencies or
starting services. Set `NO_COLOR=1` to disable colored launcher output.

Use **Save & Next Episode** to save labels and continue reviewing. The last episode
shows **Save Episode** instead. A successful save updates the saved baseline without
restoring an older browser draft; later label edits remain unsaved until the next save.

### Dev Container

Open the repository in its VS Code devcontainer or GitHub Codespaces for a preconfigured Python, Node.js, npm, and uv environment. Ports 5173 and 8000 are forwarded for the frontend and backend.

Run the cross-platform development command after the container finishes setup:

```bash
npm run dataviewer:dev
```

## 📄 Accepted Dataset Contract

Local dataset exporters can write `accepted-dataset.json` beside
`capture-provenance.json` and `export-validation.json` in the dataset directory.
`GET /api/datasets/{id}/capabilities` returns its validated metadata as
`dataset_contract`; datasets without this optional descriptor remain supported.
Blob-only datasets do not fetch these sidecars automatically.

Version 1 has this structure. Replace the example identities and hash placeholders
with the exporter's values:

```json
{
  "schema_version": 1,
  "dataset_id": "sample-dataset",
  "output_adapter_id": "lerobot_v3",
  "output_adapter_version": "0.6.0",
  "viewer_adapter_id": "dataviewer_v1",
  "profile_id": "sample-profile",
  "profile_sha256": "<profile-sha256>",
  "capture_features": [
    {"feature_id": "state", "kind": "observation_state"}
  ],
  "sensors": [
    {"sensor_id": "front", "media_kind": "rgb"}
  ],
  "artifacts": {
    "capture_provenance": {
      "file": "capture-provenance.json",
      "sha256": "<sha256-of-capture-provenance.json>"
    },
    "export_validation": {
      "file": "export-validation.json",
      "sha256": "<sha256-of-export-validation.json>"
    }
  }
}
```

All identity fields are required strings. `capture_features` and `sensors` are
required arrays of objects, including when empty. `dataset_id` must match the
requested dataset ID; nested dataset IDs use the viewer's `--` separator.
The two artifact filenames are fixed, and each SHA-256 must match the file's exact
bytes, including whitespace.

The response follows the shared
[AcceptedDatasetContract model](backend/src/api/models/datasources.py).
It exposes the two verified digests as `capture_provenance_sha256` and
`export_validation_sha256` instead of returning the descriptor's `artifacts` map.
Matching hashes establish consistency with the descriptor, not authenticity or
semantic validity of an export. The producing adapter owns profile, feature,
sensor, and export validation.

A missing descriptor yields `dataset_contract: null`. Malformed JSON, missing or
mistyped fields, missing artifacts, and hash mismatches also yield `null` with a
backend warning rather than breaking the capabilities endpoint. Workflows that
require an accepted dataset must treat `null` as an unmet contract and stop before
annotation or training.

## ⚙️ Configuration

Copy the backend and frontend templates, then set values for your environment:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

The cross-platform npm launcher loads backend defaults from `backend/.env.example`, optional overrides from `backend/.env`, and existing shell environment variables with highest precedence.

### Local File Storage (default)

```env
STORAGE_BACKEND=local
DATA_DIR=/path/to/your/datasets
```

The backend lists every dataset folder under `DATA_DIR`, up to five levels deep, and joins nested folder names with `--` in the dataset ID. It skips folders whose names start with `.`, including an export's hidden staging folder.

### Azure Blob Storage

Use this mode when datasets live in Azure Blob Storage. Authentication uses
[DefaultAzureCredential](https://learn.microsoft.com/azure/developer/python/sdk/authentication-overview),
which supports managed identity, workload identity, and Azure CLI credentials
automatically — no SAS token required in AKS or Container Apps.

```env
STORAGE_BACKEND=azure
AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
AZURE_STORAGE_DATASET_CONTAINER=datasets
AZURE_STORAGE_ANNOTATION_CONTAINER=annotations
# Leave AZURE_STORAGE_SAS_TOKEN unset to use managed identity (MSI)
```

Expected blob structure:

```text
{dataset_id}/meta/info.json
{dataset_id}/meta/tasks.parquet
{dataset_id}/data/chunk-000/file-000.parquet
{dataset_id}/videos/{camera}/chunk-000/file-000.mp4
{dataset_id}/annotations/episodes/episode_000000.json
```

### Full Environment Variable Reference

| Variable                             | Default         | Description                                                    |
|--------------------------------------|-----------------|----------------------------------------------------------------|
| `STORAGE_BACKEND`                    | `local`         | Storage backend: `local` or `azure`                            |
| `DATA_DIR`                           | `./data`        | Local dataset directory (local mode)                           |
| `AZURE_STORAGE_ACCOUNT_NAME`         | —               | Azure Storage account name (azure mode)                        |
| `AZURE_STORAGE_DATASET_CONTAINER`    | —               | Blob container for dataset files                               |
| `AZURE_STORAGE_ANNOTATION_CONTAINER` | —               | Blob container for annotations (defaults to dataset container) |
| `AZURE_STORAGE_SAS_TOKEN`            | —               | SAS token (omit to use DefaultAzureCredential / MSI)           |
| `BACKEND_HOST`                       | `127.0.0.1`     | Bind address (`0.0.0.0` for containers)                        |
| `BACKEND_PORT`                       | `8000`          | API server port                                                |
| `LOG_LEVEL`                          | `info`          | Uvicorn log level                                              |
| `FRONTEND_PORT`                      | `5173`          | Dev server port                                                |
| `CORS_ORIGINS`                       | localhost ports | Comma-separated allowed CORS origins                           |

### VLM-as-Judge (experimental)

The viewer can score episodes with a vision-language-model (VLM) judge, reusing the
`evaluation.vlm_judge` harness. The router mounts only when `VLM_JUDGE_ENABLED=true`;
the frontend's JudgePanel auto-hides when the backend reports the judge is disabled.

#### What it does

The VLM-as-judge feature samples a fixed number of
still frames — `VLM_JUDGE_N_FRAMES` (default 12) — evenly spaced across the
episode's time window, decodes them with PyAV, letterboxes each to a fixed square
(default 448×448), and, for multi-camera datasets, tiles the per-view frames
side-by-side so every timestep becomes one composite image. The model therefore
reasons over an ordered sequence of `N` sampled frames.
Frame extraction and tiling live in [`evaluation/vlm_judge/frames.py`](../../evaluation/vlm_judge/frames.py)
and [`service.py`](../../evaluation/vlm_judge/service.py); the scoring chain lives
in [`judge.py`](../../evaluation/vlm_judge/judge.py) and [`agent.py`](../../evaluation/vlm_judge/agent.py).

From that frame sequence the judge produces four outputs, shown in the **VLM Judge**
card on the trajectory tab:

- **Outcome** — `SUCCESS`, `FAILURE`, or `Inconclusive`, with a confidence
  percentage. The same SUCCESS/FAILURE multiple-choice question is sampled several
  times (default 3, at temperature 0.6 / top-p 0.95) and decided by self-consistency
  majority vote: the episode is `SUCCESS` when at least half the valid votes say so.
  Confidence is the fraction of valid votes for the winning answer; responses that
  do not parse are discarded, and `Inconclusive` means no vote parsed (`judge.py`,
  `agent.py`).
- **Process reward** — a per-frame task-completion score (0–100%) plus a single
  `VOC` value. `VOC` (value–order correlation) is the Spearman rank correlation
  between the predicted per-frame progress and the true chronological order: `+1`
  when progress rises monotonically, `0` when unordered, negative when inverted
  (`value_order_correlation` in `judge.py`). The per-frame scores are produced by
  one of two strategies selected with `VLM_JUDGE_PROCESS_METHOD` (see below).
- **Milestones** — named sub-steps (e.g. *approach object*, *grasp object*) marked
  complete or incomplete, each with a frame range and a short justification.
  Milestone decomposition runs only when the outcome is uncertain (confidence below
  0.85), inconclusive, or a failure — not on every run — to limit visual-grounding
  hallucination (`agent.py`).
- **Failure mode** — only when the outcome is `FAILURE`: a short category describing
  what went wrong (e.g. *missed grasp*).

#### How to use it

1. Enable the judge and start the viewer with `./start.sh`. It installs the
  lightweight `physical-ai-vlm-judge` package into the backend environment and
  adds local model dependencies only when `VLM_JUDGE_BACKEND=qwen3-vl`:

   ```bash
   VLM_JUDGE_ENABLED=true VLM_JUDGE_BACKEND=qwen3-vl ./start.sh
   ```

2. Open a dataset, select an episode, and switch to the **Trajectory** tab.
3. Click **Run judge**. The first run invokes the model; results are cached per
   dataset under `annotations/vlm_judge/`, so re-opening the episode is instant.
   Use **Re-evaluate** / **Force fresh** to ignore the cache and run again.

> [!NOTE]
> The default `echo` backend returns deterministic placeholder judgments (no model
> is loaded). It exists to verify the wiring end-to-end and for tests — switch to
> `qwen3-vl` (local GPU) or `openai-compat` (a remote vLLM/NIM/Azure OpenAI server)
> for real scores.

#### Applying judgments as episode labels

The judge's outcome maps directly onto the viewer's episode label set
(`SUCCESS` / `FAILURE` / `PARTIAL`), so a judgment can be promoted to a saved label:

- **Apply label** writes the current episode's outcome as its label
  (`SUCCESS` → `SUCCESS`, `FAILURE` → `FAILURE`, `Inconclusive` → `PARTIAL`). It
  replaces any existing outcome label while preserving other custom labels on the
  episode, then persists via the labels API.

#### Scoring every episode

The **Whole dataset** controls run the judge across all episodes — sequentially,
one episode at a time, not as a single batched model call. Each episode reuses the
per-episode endpoint and its cache, so episodes already scored return instantly and
only those not yet scored invoke the model. A progress indicator shows
`done / total`, and **Cancel** stops the run after the in-flight episode.

- **Run all** scores every episode with the selected scoring technique but writes
  no labels. It uses each episode's saved instruction, or the instruction from
  dataset metadata when no saved annotation exists.
- **Label all** scores every episode and writes each outcome as that episode's
  label, using the same outcome → label mapping as **Apply label** above.

> [!NOTE]
> Sequential scoring with a local backend is slow: the model loads on the first
> episode and each subsequent episode runs a full judgment chain. Prefer a hosted
> `openai-compat` backend for whole-dataset runs, and leave results cached so reruns
> skip already-scored episodes.

#### "Run judge" vs. "Language instruction"

The judge scores the episode against a **task instruction** — the natural-language
goal for the episode, such as *"Grab orange and place into plate"*. **Run judge** uses
the instruction currently shown in the viewer's **Language Instruction** panel (your
saved or in-progress edit), so refining that text changes what the judge grades against.
In short:

- **Language Instruction** = *what the robot was asked to do* (the goal the
  judge grades against).
- **Run judge** = *grade this episode against that goal* and report the outcome,
  progress, milestones, and any failure mode.

When the Language Instruction is left empty, **Run judge** falls back to the task
instruction stored in the dataset's metadata. If neither is available, it returns an
error asking you to add or save a Language Instruction.

Whole-dataset actions do not reuse the current unsaved Language Instruction for every
episode. They score each episode against that episode's saved annotation text, falling
back to dataset metadata when needed.

#### Settings

Enable the judge via `start.sh`, or install the backend `vlm-judge` extra before
launching the backend manually. Install `vlm-judge-local` when using the
in-process `qwen3-vl` backend without `start.sh`.

| Variable                   | Default                     | Description                                                                           |
|----------------------------|-----------------------------|---------------------------------------------------------------------------------------|
| `VLM_JUDGE_ENABLED`        | `false`                     | Mount the `/judge` router                                                             |
| `VLM_JUDGE_BACKEND`        | `echo`                      | `qwen3-vl` (local HF), `openai-compat` (vLLM, NIM, Azure OpenAI), or `echo`           |
| `VLM_JUDGE_MODEL_ID`       | `Qwen/Qwen3-VL-4B-Instruct` | HF model id or remote model name                                                      |
| `VLM_JUDGE_BASE_URL`       | —                           | OpenAI-compatible server URL (`openai-compat` only)                                   |
| `VLM_JUDGE_API_KEY`        | —                           | Bearer token for the remote backend                                                   |
| `VLM_JUDGE_N_FRAMES`       | `12`                        | Frames sampled per episode                                                            |
| `VLM_JUDGE_PROCESS_METHOD` | `gvl`                       | Process-reward method: `gvl` (shuffle-and-rank) or `chronological`                    |
| `VLM_JUDGE_CACHE_DIR`      | —                           | Fallback judgment cache; the viewer caches per dataset under `annotations/vlm_judge/` |

> [!NOTE]
> **Process-reward method (`VLM_JUDGE_PROCESS_METHOD`).** The per-frame progress
> histogram comes from one of two strategies:
>
> - `gvl` (default) shows the frames **shuffled** and asks the model to rank each
>   by completion, then re-orders them ([GVL](https://arxiv.org/abs/2411.04549)).
>   Shuffling prevents the model from faking a monotonic ramp from frame position,
>   so it is the more rigorous signal — but it needs a capable VLM. Small local
>   models (e.g. `Qwen3-VL-4B`) often collapse to a flat/empty histogram under it.
> - `chronological` shows the frames in order and asks for the same per-frame
>   score. It yields fuller curves with small models, at the cost of being easier
>   to game positionally.
>
> Recommendation: keep `gvl` with an 8B+ or hosted model; switch to
> `chronological` when running a small local model and you want a populated
> histogram. Changing the method invalidates cached judgments automatically.

#### Request timeouts

The `/judge` request stays open until model loading and inference finish. Local
`qwen3-vl` runs can take minutes on the first request because the backend loads
the model in-process. Configure any reverse proxy, ingress, or browser-facing
gateway timeout above the expected first-run latency, or run the model through the
`openai-compat` shim so the dataviewer backend remains lightweight.

#### Local model via the openai-compat shim

`VLM_JUDGE_BACKEND=qwen3-vl` loads the model inside the dataviewer backend process.
To keep the backend environment lightweight, run the model in a separate process —
the bundled OpenAI-compatible shim — and point the dataviewer at it with
`VLM_JUDGE_BACKEND=openai-compat` and `VLM_JUDGE_BASE_URL=http://127.0.0.1:8001/v1`:

```bash
uv run --project ../../evaluation/vlm_judge --extra api --extra qwen3-vl \
  python -m evaluation.vlm_judge.openai_shim \
  --port 8001 \
  --model-id Qwen/Qwen3-VL-4B-Instruct
```

The shim reads these variables ([`evaluation/vlm_judge/openai_shim.py`](../../evaluation/vlm_judge/openai_shim.py)):

| Variable                          | Default                     | Description                                                           |
|-----------------------------------|-----------------------------|-----------------------------------------------------------------------|
| `VLM_SHIM_HOST`                   | `127.0.0.1`                 | Bind address; the shim has no auth, so keep it on loopback            |
| `VLM_SHIM_PORT`                   | `8001`                      | Listen port                                                           |
| `VLM_SHIM_MODEL_ID`               | `Qwen/Qwen3-VL-4B-Instruct` | Hugging Face model id to load                                         |
| `VLM_SHIM_DEVICE_MAP`             | `auto`                      | Transformers device map                                               |
| `VLM_SHIM_DTYPE`                  | `bfloat16`                  | Model dtype                                                           |
| `VLM_SHIM_ALLOW_REMOTE_IMAGES`    | `false`                     | Fetch `http(s)` image URLs server-side; off restricts to `data:` URIs |
| `VLM_SHIM_REMOTE_IMAGE_TIMEOUT_S` | `10`                        | Per-fetch timeout (seconds) when remote images are enabled            |

> [!WARNING]
> The shim exposes an unauthenticated `/v1/chat/completions` endpoint and runs
> model inference on untrusted input. Treat it as a trusted-network-only service:
>
> - Bind to loopback (`VLM_SHIM_HOST=127.0.0.1`, the default). Expose it only on a
>   private, trusted network — never a public interface — and front it with your own
>   auth/proxy if it must be reachable remotely.
> - Leave `VLM_SHIM_ALLOW_REMOTE_IMAGES=false`. The dataviewer sends frames as
>   `data:` URIs, so it never needs remote fetching. Enabling remote fetch lets the
>   shim issue server-side requests (a server-side request forgery surface) with no
>   host allowlist, so only enable it on a network you fully control.
> - When remote images are enabled, keep `VLM_SHIM_REMOTE_IMAGE_TIMEOUT_S` low to
>   bound request hangs.

## 🔒 Authentication with Entra ID

The application supports Microsoft Entra ID (Azure AD) authentication for public-facing deployments. When auth is disabled (the default for local development), all requests bypass authentication. When enabled, the frontend uses MSAL.js to acquire tokens via PKCE, and the backend validates JWT tokens against the Entra ID JWKS endpoint.

### Entra ID Prerequisites

1. An [Azure AD app registration](https://learn.microsoft.com/entra/identity-platform/quickstart-register-app) with:
   - **Single-page application** redirect URI set to your frontend URL (e.g., `http://localhost:5173` for local dev, `https://your-app.azurecontainerapps.io` for production)
   - An **API scope** named `access_as_user` under "Expose an API" (`api://<client-id>/access_as_user`)
   - Optional **App roles** defined for role-based access control (e.g., `Dataviewer.Viewer`, `Dataviewer.Annotator`, `Dataviewer.Admin`)

2. Note the **Application (client) ID** and **Directory (tenant) ID** from the app registration.

### Backend Configuration

Set these environment variables in `backend/.env` (or as container environment variables):

```env
DATAVIEWER_AUTH_DISABLED=false
DATAVIEWER_AUTH_PROVIDER=azure_ad
DATAVIEWER_AZURE_TENANT_ID=<your-tenant-id>
DATAVIEWER_AZURE_CLIENT_ID=<your-client-id>
DATAVIEWER_SECURE_COOKIES=true   # Set to true when behind HTTPS
```

The backend validates incoming `Authorization: Bearer <token>` headers using RS256 and the Entra ID JWKS endpoint. When `DATAVIEWER_AUTH_DISABLED=true` (default), all authentication checks are bypassed.

### Frontend Configuration

The frontend uses build-time environment variables to configure MSAL.js. Set these before building:

```env
VITE_AZURE_CLIENT_ID=<your-client-id>
VITE_AZURE_TENANT_ID=<your-tenant-id>
```

When `VITE_AZURE_CLIENT_ID` is set, the app wraps in an `MsalProvider` and attaches Bearer tokens to all API requests. When unset, MSAL is not initialized and the app runs without authentication (suitable for VPN-only access).

### Docker Compose with Auth

```bash
export DATAVIEWER_AUTH_DISABLED=false
export VITE_AZURE_CLIENT_ID=<your-client-id>
export VITE_AZURE_TENANT_ID=<your-tenant-id>
docker compose up --build
```

The frontend Dockerfile passes `VITE_AZURE_CLIENT_ID` and `VITE_AZURE_TENANT_ID` as build arguments. The backend receives `DATAVIEWER_AUTH_DISABLED` as a runtime environment variable.

### Auth Environment Variable Reference

| Variable                     | Location              | Description                                                |
|------------------------------|-----------------------|------------------------------------------------------------|
| `DATAVIEWER_AUTH_DISABLED`   | Backend               | Set to `false` to enable auth (`true` disables all checks) |
| `DATAVIEWER_AUTH_PROVIDER`   | Backend               | Auth provider: `apikey`, `azure_ad`, or `auth0`            |
| `DATAVIEWER_AZURE_TENANT_ID` | Backend               | Entra ID tenant ID (GUID)                                  |
| `DATAVIEWER_AZURE_CLIENT_ID` | Backend               | App registration client ID (GUID)                          |
| `DATAVIEWER_SECURE_COOKIES`  | Backend               | Set to `true` for HTTPS deployments                        |
| `VITE_AZURE_CLIENT_ID`       | Frontend (build-time) | Same client ID — enables MSAL.js when set                  |
| `VITE_AZURE_TENANT_ID`       | Frontend (build-time) | Same tenant ID — used for authority URL                    |

### Token Flow

```text
Browser → Entra ID (MSAL.js PKCE) → access_token
   ↓
   Bearer token → FastAPI backend (JWT validation)
   ↓
   Backend → Azure Storage (Managed Identity, not user token)
```

The backend accesses Azure Storage using managed identity, not the user's token. User authentication and storage authentication are independent.

## 🚀 Running the Application

### Cross-platform start (recommended)

From the repository root:

```bash
npm run dataviewer:dev
```

From `data-management/viewer/`:

```bash
npm run dev
```

The npm command starts the backend and frontend together on Windows, macOS, and Linux. Stop both processes with `Ctrl+C`.

### Health-checked Bash launcher

```bash
./start.sh
```

The Bash launcher starts the backend first, waits for its health endpoint, then starts the frontend. Use it on macOS, Linux, WSL, or a compatible shell when ordered startup and health checking are required.

Available options:

```bash
./start.sh --backend   # Start backend only
./start.sh --frontend  # Start frontend only
./start.sh --help      # Show all options
```

### Manual Start

#### Start Backend

```bash
cd backend
source .venv/bin/activate
uvicorn src.api.main:app --log-config logging.json --log-level "${LOG_LEVEL:-info}" --reload --port 8000
```

#### Start Frontend

```bash
cd frontend
npm run dev
```

The application will be available at `http://localhost:5173`.

### Bundle Analysis

Generate an interactive bundle map from the repository root:

```bash
npm run dataviewer:analyze
```

The report is written to `data-management/viewer/frontend/dist/stats.html`. The generated `dist/` directory is excluded from Git.

## 🏷️ Annotation Features

The annotation workspace exposes per-episode controls grouped by panel. Persisted state is stored alongside the dataset and surfaced through the REST API.

### Multi-camera viewing

Datasets that record multiple camera streams (e.g. `observation.images.front`, `observation.images.wrist`) drive a camera selector in the annotation workspace header. The selector lists every camera advertised by the episode's `cameras` array, falling back to the keys of `videoUrls` when the array is empty.

| Behavior          | Detail                                                                                          |
|-------------------|-------------------------------------------------------------------------------------------------|
| Default selection | First entry in `episode.cameras` (or `videoUrls`)                                               |
| Override          | User selection persists for the current episode                                                 |
| Stale fallback    | When the selected camera is missing on episode change, selection resets to the new `cameras[0]` |
| Frame extraction  | The chosen camera drives both video playback and `/frames/{idx}` thumbnail requests             |

### Language instruction (VLA annotation)

Each episode can carry a structured `LanguageInstructionAnnotation` for vision-language-action training. The widget appears in the annotation panel and writes through `PUT /api/datasets/{id}/episodes/{idx}/annotations`.

| Field                  | Purpose                                                                                               |
|------------------------|-------------------------------------------------------------------------------------------------------|
| `instruction`          | Primary natural-language task description (max 1000 characters)                                       |
| `source`               | Provenance: `human`, `template`, `llm-generated`, or `retroactive`                                    |
| `language`             | BCP-47 language tag, defaults to `en`                                                                 |
| `paraphrases`          | Alternative phrasings for data augmentation (up to 50 entries, 1000 characters each)                  |
| `subtask_instructions` | Ordered subtask decomposition for hierarchical conditioning (up to 100 entries, 1000 characters each) |

When a dataset task description is available, the widget seeds the instruction with `source = template` via the "Use as Instruction" button. Otherwise, "Add Instruction" creates a blank instruction with `source = human`. The source can be changed at any time through the dropdown.

A LeRobot export can include the saved instruction as LeRobot `task_aug` and `plan` rows; see [Exporting edited episodes](#exporting-edited-episodes).

### Episode Analyzer

The **Episode Analyzer** tab combines trajectory metrics, VLM judgments, persisted analysis, episode labels, and language instructions. The run panels and persisted record have distinct storage behavior.

| Surface              | Behavior                                                                                          |
|----------------------|---------------------------------------------------------------------------------------------------|
| Motion Analysis      | Computes metrics for the current trajectory and keeps the query result in browser memory          |
| VLM Judge            | Scores outcome and process progress, then caches the full judgment under `annotations/vlm_judge/` |
| Episode Analysis     | Reads the persisted `analysis` record for the episode from `meta/episode_labels.json`             |
| Episode Labels       | Imports supported persisted analysis fields into filterable labels                                |
| Language Instruction | Supplies the task goal used by the VLM judge                                                      |

Running Motion Analysis or VLM Judge does not write `meta/episode_labels.json`. Persist structured results through `PUT /api/datasets/{dataset_id}/episodes/{episode_idx}/analysis`, or use the dataset-labeling CLI with `--write-analysis`.

#### Motion metrics

| Metric                | Meaning                                                                                                |
|-----------------------|--------------------------------------------------------------------------------------------------------|
| Raw smoothness        | Reciprocal root-mean-square jerk; higher is smoother but degree-scale trajectories often approach zero |
| Normalized smoothness | Rescaled jerk score selected by the smoothness mode; higher is smoother                                |
| Efficiency            | Direct start-to-end distance divided by traveled path length; higher is more direct                    |
| Jitter                | Fraction of velocity-spectrum power above the configured frequency threshold; lower is better          |
| Hesitations           | Count of sustained near-zero velocity segments                                                         |
| Corrections           | Count of significant velocity-direction reversals                                                      |
| Overall motion score  | Composite score from 1 to 5 derived from smoothness, efficiency, jitter, hesitations, and corrections  |

The **Log-scaled** mode compresses the jerk range with `log10` and is the default for comparing degree-scale robot trajectories. The **Radian-based** mode converts degree-based jerk to radians before applying the reciprocal score.

#### Import analysis fields as labels

The label panel shows import buttons only for analysis fields present in the dataset. Enter an optional label prefix before importing. Enable **Replace existing imported labels** to remove stale labels in the same prefix namespace before applying current values. Unsaved label edits remain in the browser while the server response is reconciled.

Supported fields include object, pickup location, grasp outcome, place outcome, motion score, motion flags, and source. Free-text movement notes and instructions remain analysis data because importing them would create unbounded label sets.

### Exporting edited episodes

**Export** writes the current episode with its edits applied: removed and inserted frames, crop and resize, trajectory adjustments and subtasks. The source dataset is never modified, and the output path must be under the Dataviewer data directory.

| Source format | Export output                                                                                            |
|---------------|----------------------------------------------------------------------------------------------------------|
| LeRobot v3.0  | A new LeRobot v3.0 dataset at the output path, which must be new or empty and outside the source dataset |
| HDF5          | `episode_<index>.hdf5` files in the output directory, with `.meta.json` and `.subtasks.json` beside them |

A LeRobot export locks its output directory with a hidden `.dataviewer-export.lock` file and stages the dataset in a hidden `.dataviewer-export.partial` directory inside it, so an existing empty directory, such as a mounted volume, is kept. The dataset appears once the export completes, with `meta` moved in last. While the lock is held, a second export to the same directory fails.

If the backend stops partway through an export, or an export fails and can't remove what it moved, the next export to that directory cleans up first.
Before moving anything into place, every export records the identity of each file it staged. The cleanup removes the earlier export's staging and the moved files that still match that record, and it keeps a dataset whose move finished. Anything else in the directory stays, including files added inside the earlier export's folders, and exports there fail until you remove it.

LeRobot exports need a filesystem that supports file locking, such as a local disk or a Docker bind mount. On one that doesn't, the export fails and can leave an empty `.dataviewer-export.lock` behind. The export dialog reports every failure as "Export failed", and the backend log gives the reason.

A LeRobot export:

- keeps every recorded feature;
- re-encodes the videos with the source's recorded encoder settings, falling back to LeRobot's defaults for any setting the source doesn't record;
- recomputes the per-episode and dataset statistics;
- writes subtasks as LeRobot `subtask` annotations;
- can add saved language instructions as LeRobot `task_aug` and `plan` annotations.

Removing or inserting frames renumbers `frame_index` and sets `timestamp` to `frame_index / fps`. `dataviewer-export.json` maps each output frame to its source frame and records the edits and remapped subtasks.

A subtask that loses its first or last frames to the edits shrinks to the frames that remain, and one with no frames left is dropped.

In a LeRobot export, each subtask becomes a row in the `language_persistent` column, with the subtask label as its text and its first frame's `timestamp`. LeRobot treats a subtask as active until the next one starts, so frames in a gap between two subtasks read as the earlier subtask; `dataviewer-export.json` keeps the exact ranges. The label becomes the annotation text, so name subtasks the way training should read them.

Opening an exported dataset shows its subtasks in the subtask editor. LeRobot subtask rows run until the next one starts, as LeRobot reads them, and an HDF5 export's `.subtasks.json` keeps its exact ranges. When you export again, subtasks you left unchanged keep their recorded data, changed subtasks replace the recorded ones, and deleting every subtask removes them.
In `dataviewer-export.json`, `subtasks` is `null` when the export kept the recorded subtasks and an empty list when it removed them. A saved draft of the episode takes precedence over the recorded subtasks.

When the source already has LeRobot language annotations, the export keeps them and moves them with the edited frames, apart from rows your subtasks or language instructions replace. Clear **Include subtasks as LeRobot subtask annotations** to export without your subtask changes; recorded subtask rows stay.
For an HDF5 source the same option reads **Include subtask metadata**, and clearing it still carries a recorded `.subtasks.json` forward to the export.
A LeRobot export has language columns when its source has them or when an exported episode gets subtask or language-instruction rows, so clearing the subtask option alone doesn't leave them out.

For a LeRobot source, **Include language instructions as LeRobot task phrasings and plan** adds each episode's most recently saved [language instruction](#language-instruction-vla-annotation):

- the instruction and its paraphrases become `task_aug` rows, which LeRobot rotates `${task}` through during training;
- the subtask instructions become one numbered `plan` row at the first frame.

These rows replace the source's `task_aug` and `plan` rows for that episode, and episodes without a saved instruction keep theirs. The option is on by default in the dialog and in the export API, where `"includeLanguageInstructions": false` turns it off. `dataviewer-export.json` records whose instruction was used and when it was saved.

Trajectory adjustments never replace recorded joint positions:

- LeRobot exports add `adjusted.observation.state` and `adjusted.observation.state_mask` beside `observation.state`. The `adjusted.` prefix keeps them out of LeRobot policy inputs.
- HDF5 exports add `data/qpos_adjusted` and `data/qpos_adjusted_mask` beside `data/qpos`.

Each export is its own dataset. LeRobot merges datasets only when their features match, so a cropped or adjusted export doesn't merge with an unedited one.

### VLM dataset-labeling CLI

`backend/scripts/vlm_label_dataset.py` runs Qwen3-VL across a LeRobot v2.1 or v3.0 dataset. It writes full rows to `labels.jsonl` and a flat summary to `labels.csv`.

```bash
cd data-management/viewer/backend
uv run --extra vlm-judge --with-editable ../../../evaluation/vlm_judge \
  python scripts/vlm_label_dataset.py \
  --dataset-root /data/my-dataset \
  --dataset-id owner--my-dataset \
  --output-dir /data/my-dataset/vlm-labels \
  --n-frames 16 \
  --resume \
  --write-analysis
```

| Option             | Behavior                                                                                                                             |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `--dataset-id`     | Writes the canonical dataviewer ID into generated metadata; set it for nested IDs such as `owner--dataset`                           |
| `--resume`         | Loads existing `labels.jsonl`, skips successful episode indices, retries failed rows, appends attempts, and regenerates `labels.csv` |
| `--write-analysis` | Merges successful rows into `<dataset-root>/meta/episode_labels.json` without replacing labels or unrelated analysis fields          |
| `--views`          | Restricts the model input to selected video feature keys; all detected video views are used by default                               |
| `--limit`          | Processes only the first requested number of episodes                                                                                |
| `--scene-context`  | Adds dataset-specific scene context to the model prompt                                                                              |

Without `--write-analysis`, the JSONL and CSV files remain standalone exports and do not appear in the Episode Analysis card. Error rows remain in JSONL and CSV for inspection but are not merged into the dataset analysis map.

> [!WARNING]
> Stop other writers for the same dataset before using `--write-analysis`. The command replaces the metadata file atomically, but concurrent read-modify-write operations can still overwrite each other.

## 🚢 Container Deployment

### Docker Compose (local)

```bash
# Local storage mode without object detection: Docker Desktop for macOS or Windows
DATAVIEWER_HOST_DATA_DIR=/path/to/datasets docker compose up --build

# Rootful Docker Engine on Linux or directly inside WSL
DATAVIEWER_UID="$(id -u)" \
DATAVIEWER_GID="$(id -g)" \
DATAVIEWER_HOST_DATA_DIR=/path/to/datasets \
docker compose up --build

# Rootless Docker only
DATAVIEWER_UID=0 \
DATAVIEWER_GID=0 \
DATAVIEWER_HOST_DATA_DIR=/path/to/datasets \
docker compose up --build

# Azure Blob Storage mode
export STORAGE_BACKEND=azure
export AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
export AZURE_STORAGE_DATASET_CONTAINER=datasets
export AZURE_STORAGE_ANNOTATION_CONTAINER=annotations
docker compose up --build
```

Local storage requires write access to `DATAVIEWER_HOST_DATA_DIR` because annotations and labels are persisted atomically under each dataset directory. The backend validates create, flush, replace, and delete operations during startup and exits with the effective UID and GID when the mount is not writable.

| Environment                                           | Runtime identity                                                                             |
|-------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Docker Desktop for macOS or Windows                   | Uses the image-defined UID/GID 999                                                           |
| Rootful Docker Engine on Linux or directly inside WSL | Set `DATAVIEWER_UID` and `DATAVIEWER_GID` from `id -u` and `id -g`                           |
| Rootless Docker                                       | Set `DATAVIEWER_UID=0` and `DATAVIEWER_GID=0`; rootless UID 0 maps to the invoking host user |
| Docker daemon with user-namespace remapping           | Pre-arrange host directory ownership for the daemon's subordinate UID/GID mapping            |

> [!WARNING]
> Do not use the rootless UID/GID 0 override with a rootful Docker daemon. It runs the backend as host-capable container root.

On SELinux-enforcing hosts, set `DATAVIEWER_DATA_MOUNT_OPTIONS=rw,z` for a dataset shared with other containers or `rw,Z` for a private mount. These options relabel the host directory; do not apply them to system paths or directories whose existing labels must remain unchanged.

Object detection is optional. To enable it, stage reviewed model weights outside the repository before starting the services:

```bash
export DATAVIEWER_HOST_MODELS_DIR=/absolute/path/to/models
export DETECTION_MODEL_DIGESTS='{"yolo11n":"<sha256>","yolov8s-world":"<sha256>"}'
docker compose up --build
```

The backend mounts `DATAVIEWER_HOST_MODELS_DIR` read-only at `/models`. Each active `<model-identifier>.pt` file requires a matching reviewed SHA-256 value in `DETECTION_MODEL_DIGESTS`; missing or mismatched checkpoints return HTTP 503 before deserialization.

Run this preflight before deployment:

```bash
test -r "$DATAVIEWER_HOST_MODELS_DIR/yolo11n.pt"
test -r "$DATAVIEWER_HOST_MODELS_DIR/yolov8s-world.pt"
printf '%s  %s\n' \
  "$(shasum -a 256 "$DATAVIEWER_HOST_MODELS_DIR/yolo11n.pt" | cut -d' ' -f1)" \
  "$DATAVIEWER_HOST_MODELS_DIR/yolo11n.pt"
printf '%s  %s\n' \
  "$(shasum -a 256 "$DATAVIEWER_HOST_MODELS_DIR/yolov8s-world.pt" | cut -d' ' -f1)" \
  "$DATAVIEWER_HOST_MODELS_DIR/yolov8s-world.pt"
docker compose run --rm backend \
  python -c "from src.api.services.detection_service import get_detection_service; service = get_detection_service(); service._get_model('yolo11n'); service._get_model('yolov8s-world', labels=['robot'])"
```

Rollback by restoring the previous read-only model directory and its matching `DETECTION_MODEL_DIGESTS` value, then recreate the backend:

```bash
docker compose up -d --force-recreate backend
```

### Azure Kubernetes Service (AKS) / Container Apps

For AKS with workload identity or Container Apps with managed identity, set:

```env
STORAGE_BACKEND=azure
AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
AZURE_STORAGE_DATASET_CONTAINER=datasets
BACKEND_HOST=0.0.0.0
LOG_LEVEL=info
CORS_ORIGINS=https://your-frontend-url.example.com
DETECTION_MODELS_DIR=/models
DETECTION_MODEL_DIGESTS={"yolo11n":"<sha256>","yolov8s-world":"<sha256>"}
```

`AZURE_STORAGE_SAS_TOKEN` is **not** needed — `DefaultAzureCredential` automatically
uses the pod/container managed identity when running in Azure.

For an HTTPS backend, configure the frontend proxy with the certificate-valid backend FQDN:

```env
NGINX_BACKEND_SCHEME=https
NGINX_BACKEND_HOST=backend.example.com
```

NGINX verifies the backend certificate and hostname against `/etc/ssl/certs/ca-certificates.crt`. Install a custom backend CA in that frontend container trust bundle before deployment.

Mount the reviewed model directory read-only at `/models`. Update the mount and digest map together during rollout or rollback.

### Building Images

```bash
# Backend
docker build --file data-management/viewer/backend/Dockerfile --tag dataviewer-backend data-management/viewer/backend

# Frontend
docker build --file data-management/viewer/frontend/Dockerfile --tag dataviewer-frontend .
```

## 🧪 Development

### Backend Development

```bash
cd backend
source .venv/bin/activate

# Run tests
pytest

# Lint
ruff check src/

# Lint with auto-fix
ruff check src/ --fix
```

### Frontend Development

All frontend validation runs through npm scripts in `data-management/viewer/frontend/`.

```bash
cd frontend

# Full validation (type-check + lint + test)
npm run validate

# Individual checks
npm run type-check   # TypeScript compilation
npm run lint         # ESLint
npm run lint:fix     # ESLint with auto-fix
npm run test         # Vitest unit tests
npm run test:watch   # Vitest in watch mode
npm run format       # Prettier check
npm run format:fix   # Prettier auto-fix
npm run build        # Production build
```

## 🔍 Troubleshooting

Error messages in the viewer leave out server details; the backend log has them.

| Symptom                                                                                      | Cause and fix                                                                                                                                                                                                                                                   |
|----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| "Error loading episode: The episode's files couldn't be read; the backend log has the cause" | The dataset's loader couldn't read the episode's files, and the episode, detection, auto-analysis and export-preview endpoints return HTTP 500 with code `EPISODE_LOAD_FAILED`. Find the file and error in the backend log, then repair or replace the episode. |
| An export reports "Export failed"                                                            | The backend log names the cause. Another export may be writing to the same directory, the output directory may not be new or empty, or its filesystem may not support file locking.                                                                             |

## 📖 API Documentation

Once the backend is running, visit:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health check: `http://localhost:8000/health`
