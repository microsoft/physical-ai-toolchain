# Dataset Analysis Tool

A full-stack application for analyzing and annotating robotic training data from episode-based datasets. Features include episode browsing, frame annotation, and export capabilities.

## Architecture

- **Backend**: FastAPI (Python) - serves REST API on port 8000
- **Frontend**: React + Vite + TypeScript - runs on port 5173 with API proxy

## Prerequisites

- Python 3.12+
- Node.js 18+
- npm

## Installation

### Backend Setup

```bash
cd backend

# Create virtual environment (using uv)
uv venv --python 3.12
source .venv/bin/activate

# Install dependencies (include 'azure' extra for blob storage support)
uv pip install -e ".[dev,export,azure]"
```

### Frontend Setup

```bash
cd frontend
npm install
```

## Configuration

Copy `backend/.env.example` to `backend/.env` and set values for your environment.

### Local File Storage (default)

```env
STORAGE_BACKEND=local
DATA_DIR=/path/to/your/datasets
```

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
| `FRONTEND_PORT`                      | `5173`          | Dev server port                                                |
| `CORS_ORIGINS`                       | localhost ports | Comma-separated allowed CORS origins                           |

### VLM-as-Judge (optional)

The viewer can score episodes with a vision-language-model (VLM) judge, reusing the
`evaluation.vlm_judge` harness. The router mounts only when `VLM_JUDGE_ENABLED=true`;
the frontend's JudgePanel auto-hides when the backend reports the judge is disabled.

#### What it does

For the selected episode, the judge watches a handful of sampled video frames and
answers, in plain language, *"did the robot accomplish the task?"* It produces four
things, shown in the **VLM Judge** card on the trajectory tab:

- **Outcome** — `SUCCESS`, `FAILURE`, or `Inconclusive`, with a confidence percentage.
  The model is asked the same multiple-choice question several times and the answers
  are voted on, so confidence reflects how consistently it agreed with itself.
- **Process reward** — a per-frame "how far along is the task" progress bar (0–100%)
  plus a single `VOC` score summarizing how steadily progress increased over time.
- **Milestones** — named sub-steps (e.g. *approach object*, *grasp object*) marked
  complete or incomplete, each with the frame range and a short justification.
- **Failure mode** — when the outcome is `FAILURE`, a short category describing what
  went wrong (e.g. *missed grasp*).

#### How to use it

1. Enable the judge and start the viewer with `./start.sh` (it exports the
   `evaluation` package onto `PYTHONPATH` automatically):

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

#### "Run judge" vs. "Language instruction"

The judge scores the episode against a **task instruction** — the natural-language
goal for the episode, such as *"Grab orange and place into plate"*. **Run judge** uses
the instruction currently shown in the viewer's **Language Instruction** panel (your
saved or in-progress edit), so refining that text changes what the judge grades against.
In short:

- **Language Instruction** = *what the robot was asked to do* (the goal the judge grades against).
- **Run judge** = *grade this episode against that goal* and report the outcome, progress,
  milestones, and any failure mode.

When the Language Instruction is left empty, **Run judge** falls back to the task
instruction stored in the dataset's metadata. If neither is available, it returns an
error (HTTP 422) asking for one to be supplied.

#### Settings

Enable the judge via `start.sh`, or by exporting `PYTHONPATH` to the repository root
before launching the backend.

| Variable              | Default                     | Description                                                       |
|-----------------------|-----------------------------|-------------------------------------------------------------------|
| `VLM_JUDGE_ENABLED`   | `false`                     | Mount the `/judge` router                                         |
| `VLM_JUDGE_BACKEND`   | `echo`                      | `qwen3-vl` (local HF), `openai-compat` (vLLM, NIM, Azure OpenAI), or `echo` |
| `VLM_JUDGE_MODEL_ID`  | `Qwen/Qwen3-VL-4B-Instruct` | HF model id or remote model name                                  |
| `VLM_JUDGE_BASE_URL`  | —                           | OpenAI-compatible server URL (`openai-compat` only)              |
| `VLM_JUDGE_API_KEY`   | —                           | Bearer token for the remote backend                              |
| `VLM_JUDGE_N_FRAMES`  | `12`                        | Frames sampled per episode                                       |
| `VLM_JUDGE_CACHE_DIR` | —                           | Fallback judgment cache; the viewer caches per dataset under `annotations/vlm_judge/` |

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

## Running the Application

### Quick Start (Recommended)

```bash
./start.sh
```

This launches both backend and frontend in the correct order, with health checking and graceful shutdown.

**Options:**

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
uvicorn src.api.main:app --reload --port 8000
```

#### Start Frontend

```bash
cd frontend
npm run dev
```

The application will be available at `http://localhost:5173`.

## Annotation Features

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

## Container Deployment

### Docker Compose (local)

```bash
# Local storage mode (mount datasets directory)
DATAVIEWER_HOST_DATA_DIR=/path/to/datasets docker compose up --build

# Azure Blob Storage mode
export STORAGE_BACKEND=azure
export AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
export AZURE_STORAGE_DATASET_CONTAINER=datasets
export AZURE_STORAGE_ANNOTATION_CONTAINER=annotations
docker compose up --build
```

### Azure Kubernetes Service (AKS) / Container Apps

For AKS with workload identity or Container Apps with managed identity, set:

```env
STORAGE_BACKEND=azure
AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
AZURE_STORAGE_DATASET_CONTAINER=datasets
BACKEND_HOST=0.0.0.0
CORS_ORIGINS=https://your-frontend-url.example.com
```

`AZURE_STORAGE_SAS_TOKEN` is **not** needed — `DefaultAzureCredential` automatically
uses the pod/container managed identity when running in Azure.

### Building Images

```bash
# Backend
docker build -t dataviewer-backend ./backend

# Frontend
docker build -t dataviewer-frontend ./frontend
```

## Development

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

## API Documentation

Once the backend is running, visit:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health check: `http://localhost:8000/health`
