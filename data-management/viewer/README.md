---
title: Dataset Analysis Tool
description: Run and configure the web application for analyzing and annotating episode-based robotics datasets
author: Microsoft
ms.date: 2026-10-19
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

## 📊 AI Analysis

The [AI-analysis endpoints](backend/src/api/routes/ai_analysis.py) require nonempty, rectangular position and force matrices containing only finite numbers. Invalid matrices return HTTP 400. `POST /api/ai/cluster` requires at least two trajectories; an explicit `num_clusters` must be between 2 and 20 and cannot exceed the number of trajectories.

The [clustering service](backend/src/api/services/clustering.py) applies a shared scale to extreme coordinate magnitudes before extracting features when squared statistics or path lengths risk overflowing float64. Ordinary coordinate magnitudes retain their original feature calculation.

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

Dataset sources are exclusive: Azure mode reads only Blob datasets and ignores
`DATA_DIR`. Missing Azure configuration or dependencies fail explicitly; they do
not enable local discovery. Local mode reads only `DATA_DIR`.

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

Choose where inference runs:

| Backend         | Runs the model on              | Viewer needs a GPU?                     |
|-----------------|--------------------------------|-----------------------------------------|
| `qwen3-vl`      | The viewer host                | Yes, a suitable CUDA-capable NVIDIA GPU |
| `openai-compat` | A separate inference server    | No                                      |
| `echo`          | No model; returns test results | No                                      |

RTX is not required; Azure A10 is also a candidate. For local Qwen3-VL-4B, start
validation with a full 24 GB GPU and concurrency one. Actual memory use depends on
the model and frame settings. See [GPU and Azure deployment guidance](../../evaluation/vlm_judge/README.md).

1. Enable the selected backend and start the viewer. For local Qwen:

   ```bash
   VLM_JUDGE_ENABLED=true VLM_JUDGE_BACKEND=qwen3-vl \
     VLM_JUDGE_MODEL_REVISION='<reviewed-full-40-hex-commit-sha>' ./start.sh
   ```

2. Select an episode and save its changes. Resolve any unsaved-input or conflict warnings.
3. Open Episode Analysis on the Trajectory Viewer tab and run the judge.
4. Review progress and the saved result. Apply the result separately to update labels.

> [!NOTE]
> The launcher restores frozen dependencies; approve restoration and model downloads
> before first use. The default `echo` backend tests the workflow, not model quality.
> Reopening saved results does not run inference again. Force a new evaluation only
> when you intend to recompute.

#### Applying judgments as episode labels

Judging alone does not change labels. Explicit application saves a machine-owned
contribution linked to the result, while protecting human revisions and unrelated fields.

| Judge outcome  | Label application        |
|----------------|--------------------------|
| `SUCCESS`      | Can apply `SUCCESS`      |
| `FAILURE`      | Can apply `FAILURE`      |
| `Inconclusive` | Does not apply `PARTIAL` |

#### Scoring every episode

1. Save any episode-label or instruction edits. Select exactly one `SUCCESS`, `FAILURE` or
  `PARTIAL` label for each grounding episode; conflicting outcome labels cannot form a label baseline.
2. Open Dataset workspace and select grounding episodes. Each needs a saved instruction or a
  dataset task instruction. Saved task-completeness ratings are also supported.
3. Select the cameras and scoring method, then choose **Evaluate grounding episodes**.
4. Review the saved-baseline versus judge comparisons. Acknowledge disagreements or inconclusive
  results, then choose **Approve configuration**. Individual episode judge runs do not create approvals.
5. Select that entry in **Configuration approval** and choose target episodes. Leave the target
  field empty to select every episode not used for grounding, then submit judge-only or judge-and-label work.
6. Follow the saved job's progress. You can close the browser and reconnect later.

| Operation       | Effect                                                                                    |
|-----------------|-------------------------------------------------------------------------------------------|
| Judge-only      | Saves results without changing labels                                                     |
| Judge-and-label | Judges and conditionally applies results                                                  |
| Cancel          | Prevents late results from publishing; an active GPU call may continue                    |
| Retry           | Retries eligible failed work through the saved job                                        |
| Withdraw        | Previews and removes covered machine contributions; preserves human revisions and history |

Changing the configuration, source or samples invalidates the corresponding approval.

Instruction source is user-selected metadata, not an eligibility restriction. Saved template
instructions and dataset task instructions are accepted. Episode labels, including AI-applied
labels, provide baseline outcomes; they do not establish independent human validation. Conflicting
outcome labels cannot provide a label baseline. No Task Completeness pane is required for label-based
grounding. The Task Completeness widget is not displayed in the current workspace; use the visible
Episode Labels controls for label baselines. The approval selector shows **No approved configuration
yet** until a grounding evaluation is reviewed and approved. Saving labels or annotations refreshes
eligibility and approval state; unsaved drafts still block new work.

Episode labels, structured annotations and saved judge results are separate records. A SUCCESS
judge result does not change a label or annotation unless explicitly applied. An applied label can
be a baseline without being represented as human-authored evidence.

The reset-history read (`GET /api/judge/resets`) returns `200` with `null` when no label removal has
run. Do not restart a viewer while its job is active.

#### "Run judge" vs. "Language instruction"

| Action or input      | Meaning                                                                           |
|----------------------|-----------------------------------------------------------------------------------|
| Language Instruction | The goal against which the episode is judged; save edits before judging           |
| Save                 | Persists episode changes and stays on the episode                                 |
| Next                 | Navigates; it is not a save action                                                |
| Run judge            | Uses the selected saved instruction and supported saved edits, not browser drafts |
| Dataset job          | Uses each target's own saved inputs, not the active episode's text                |

Dataset metadata supplies the instruction when no saved instruction exists. Missing
or ambiguous inputs block judging. Unsupported media transformations also block
judging rather than silently substituting the original video.

#### Settings

The launcher selects the evaluator dependencies for the configured backend. For
container build profiles and Azure setup, see the
[deployment guide](../../evaluation/vlm_judge/README.md).

| Variable                   | Default                       | Description                                                                             |
|----------------------------|-------------------------------|-----------------------------------------------------------------------------------------|
| `VLM_JUDGE_ENABLED`        | `false`                       | Enable the viewer judge and durable `/api/judge` lifecycle routes                       |
| `VLM_JUDGE_BACKEND`        | `echo`                        | `qwen3-vl` (local HF), `openai-compat` (vLLM, NIM, Azure OpenAI), or `echo`             |
| `VLM_JUDGE_MODEL_ID`       | `Qwen/Qwen3-VL-4B-Instruct`   | HF model id or remote model name                                                        |
| `VLM_JUDGE_MODEL_REVISION` | Unset                         | Required full Hub commit SHA for native Qwen; omit for explicit local model directories |
| `VLM_JUDGE_BASE_URL`       | —                             | OpenAI-compatible server URL (`openai-compat` only)                                     |
| `VLM_JUDGE_API_KEY`        | —                             | API key for non-Azure endpoints; ignored for Azure endpoints                            |
| `VLM_JUDGE_N_FRAMES`       | `12`                          | Frames sampled per episode                                                              |
| `VLM_JUDGE_PROCESS_METHOD` | `gvl`                         | Process-reward method: `gvl` (shuffle-and-rank) or `chronological`                      |
| `VLM_JUDGE_CACHE_DIR`      | Unset                         | Disposable inference cache, not durable job authority                                   |
| `VLM_JUDGE_JOB_DIR`        | Derived local default         | Durable local job root; Compose uses `/state/judge`, Azure mode uses shared Blob state  |
| `VLM_JUDGE_CAPACITY`       | `1`                           | Shared maximum concurrent judgment work for a capacity scope                            |
| `VLM_JUDGE_CAPACITY_SCOPE` | `configured-inference-device` | Shared identity for workers using the same inference capacity                           |

Configure inference authentication for the target environment, independently of
Blob storage authentication. The current Azure inference setup is for development;
production managed-identity support is deferred. See the
[authentication guidance](../../evaluation/vlm_judge/README.md#azure-inference-authentication).

Native Hub loading requires a full SHA and never enables repository Python code.
For offline artifacts, set `VLM_JUDGE_MODEL_ID` to an absolute directory or a path
starting `./` or `../`, and omit `VLM_JUDGE_MODEL_REVISION`. Clear the judgment cache
after replacing local model contents. Echo and hosted `openai-compat` models need no
Hub pin. Existing viewer authentication and CSRF remain unchanged; standalone
`VLM_JUDGE_ALLOW_UNAUTHENTICATED`, `VLM_JUDGE_DATASETS` and `VLM_JUDGE_LOCAL_ACTOR`
do not govern this router.
Viewer media must remain inside its dataset directory, including metadata-derived paths.

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

Submission returns a job acknowledgment; the browser polls for completion. Model
loading does not hold that browser request open. The worker's inference request still
needs sufficient server/proxy timeouts. Keep at least one worker replica running while
jobs are pending; queued jobs cannot wake a scaled-to-zero service.

#### Local model via the openai-compat shim

`VLM_JUDGE_BACKEND=qwen3-vl` loads the model inside the dataviewer backend process.
To keep the backend environment lightweight, run the model in a separate process —
the bundled OpenAI-compatible shim — and point the dataviewer at it with
`VLM_JUDGE_BACKEND=openai-compat` and `VLM_JUDGE_BASE_URL=http://127.0.0.1:8001/v1`:

```bash
uv run --project ../../evaluation/vlm_judge --frozen --no-sync --extra api --extra qwen3-vl \
  python -m evaluation.vlm_judge.openai_shim \
  --port 8001 \
  --model-id Qwen/Qwen3-VL-4B-Instruct \
  --model-revision '<reviewed-full-40-hex-commit-sha>'
```

Run this from the viewer directory after an approved frozen restore of the evaluator's
`api` and `qwen3-vl` extras. Local Qwen still requires the GPU host and compatible
runtime; moving inference into a separate process does not remove that requirement.

The shim reads these variables ([`evaluation/vlm_judge/openai_shim.py`](../../evaluation/vlm_judge/openai_shim.py)):

| Variable                          | Default                     | Description                                                           |
|-----------------------------------|-----------------------------|-----------------------------------------------------------------------|
| `VLM_SHIM_HOST`                   | `127.0.0.1`                 | Bind address; the shim has no auth, so keep it on loopback            |
| `VLM_SHIM_PORT`                   | `8001`                      | Listen port                                                           |
| `VLM_SHIM_MODEL_ID`               | `Qwen/Qwen3-VL-4B-Instruct` | Hugging Face model id to load                                         |
| `VLM_SHIM_MODEL_REVISION`         | Unset                       | Full Hub commit SHA; omit for an explicit local model directory       |
| `VLM_SHIM_DEVICE_MAP`             | `auto`                      | Transformers device map                                               |
| `VLM_SHIM_DTYPE`                  | `bfloat16`                  | Model dtype                                                           |
| `VLM_SHIM_ALLOW_REMOTE_IMAGES`    | `false`                     | Fetch `http(s)` image URLs server-side; off restricts to `data:` URIs |
| `VLM_SHIM_REMOTE_IMAGE_TIMEOUT_S` | `10`                        | Per-fetch timeout (seconds) when remote images are enabled            |

> [!WARNING]
> The shim exposes an unauthenticated `/v1/chat/completions` endpoint and runs
> model inference on supplied input. Its protocol does not gain the standalone
> judge router's admission controls. Keep it on loopback on a trusted single-user
> host with `VLM_SHIM_ALLOW_REMOTE_IMAGES=false`; the viewer sends `data:` URIs.
> Do not expose it through LAN listeners, proxies, tunnels or port forwarding.
> Authentication and ingress hardening remain separate work.

## 🔒 Authentication with Entra ID

Azure Container Apps deployments use Easy Auth on the public frontend. Easy Auth authenticates the browser and injects `X-MS-CLIENT-PRINCIPAL`; the frontend nginx proxy forwards that principal with a Terraform-managed `X-Dataviewer-Proxy-Key`. The backend accepts the principal only when the proxy credential matches, preventing a direct caller from establishing identity by forging the platform header.

The frontend-to-backend proxy preserves native browser requests for images and video, including `HEAD` and HTTP Range requests. The browser does not acquire or attach a separate API token in this deployment mode.

### Entra ID Prerequisites

1. An [Azure AD app registration](https://learn.microsoft.com/entra/identity-platform/quickstart-register-app) with:
   - A web redirect URI at `https://<frontend-fqdn>/.auth/login/aad/callback`
   - A single-page application redirect URI at `http://localhost:5173/redirect.html` for local direct JWT development or `https://<frontend-host>/redirect.html` for production direct JWT deployments
   - API scope named `access_as_user` under "Expose an API" (`api://<client-id>/access_as_user`) for direct JWT deployments
   - Optional app roles for role-based access control, such as `Dataviewer.Viewer`, `Dataviewer.Annotator`, and `Dataviewer.Admin`

2. Note the **Application (client) ID** and **Directory (tenant) ID** from the app registration.

Terraform creates the app registration and enables frontend Easy Auth when authentication is enabled. The deployment script validates the Easy Auth web callback and reconciles the provider without changing redirect URIs.

The [Terraform module](../../infrastructure/terraform/modules/dataviewer/variables.tf) defaults to MSAL bridge URIs on local ports 5173 and 5174. Supply `dataviewer_redirect_uris` explicitly for production direct JWT origins or different development ports; caller-supplied lists are used unchanged.

### Azure Container Apps Configuration

Terraform creates one random proxy credential and installs it under the stable `dataviewer-proxy-key` secret name on both Container Apps. The backend bootstrap revision receives the secret reference and enforces proof validation. The public frontend bootstrap revision does not receive the reference; `deploy-dataviewer.sh` adds it only after verifying Easy Auth. The credential is not exposed as a Terraform output.

```env
DATAVIEWER_AUTH_DISABLED=false
DATAVIEWER_AUTH_PROVIDER=easy_auth
DATAVIEWER_PROXY_KEY=<shared-random-proxy-key>
DATAVIEWER_AZURE_TENANT_ID=<your-tenant-id>
DATAVIEWER_AZURE_CLIENT_ID=<your-client-id>
DATAVIEWER_SECURE_COOKIES=true
```

Do not put the proxy credential in source, command output, Terraform outputs, or deployment logs. [Container Apps secret updates](https://learn.microsoft.com/azure/container-apps/manage-secrets) do not create revisions or update values already loaded by running revisions.

Rotate the Terraform-owned password on both apps first, then close frontend ingress, restart both active revisions, verify proof enforcement and anonymous denial, and reopen ingress. Keep ingress closed after any partial restart or failed verification. The [deployment specification](../specifications/viewer-deployment.specification.md#credential-rotation) defines the complete sequence.

### Other Authentication Modes

The same images support API key, Auth0, and direct Entra JWT deployments. When `DATAVIEWER_PROXY_KEY` is absent, nginx removes any inbound proxy-proof header and preserves the browser's `Authorization` header. Direct Entra JWT deployments can configure the optional MSAL frontend at build time:

```env
VITE_AZURE_CLIENT_ID=<your-client-id>
VITE_AZURE_TENANT_ID=<your-tenant-id>
```

The frontend checks `/.auth/me` before starting MSAL. An active Easy Auth session takes precedence. Otherwise, when `VITE_AZURE_CLIENT_ID` is set, the app uses MSAL and attaches bearer tokens to API fetches for the `azure_ad` backend provider. When unset, MSAL is not initialized. Set `DATAVIEWER_AUTH_DISABLED=true` only for local development or private deployments that intentionally bypass application authentication.

Native images and videos cannot attach authorization headers. [`MediaAuthGate`](frontend/src/components/auth/MediaAuthGate.tsx) obtains a media cookie before mounting the authenticated workspace using the JWT- and CSRF-protected `POST /api/auth/media-session` endpoint. The gate renews at half the returned lifetime and displays an error on failure.

The backend sets a host-only `__Secure-dataviewer-media` cookie with `HttpOnly`, `Secure`, `SameSite=Strict`, and path `/api/datasets/`. Its browser lifetime is at most five minutes and never exceeds the JWT's remaining lifetime. Cookie authentication is restricted to exact read-only frame and video routes, including range requests, and requires `Sec-Fetch-Site: same-origin`. Clients that omit fetch metadata must use a bearer token.

Signature, issuer, audience, and expiration checks still apply; an invalid authorization header never falls back to a cookie. Dataset metadata, annotations, and mutations require ordinary authentication headers. Tokens are never placed in media URLs. Serve the frontend and API on the same HTTPS origin; Easy Auth continues to use its platform session.

Deploy the backend endpoint before the frontend gate. For acceptance, inspect a native `<video>` or `<img>` request with no authorization header, verify playback or image decoding, and confirm cookie-only general API access returns 401.

### Docker Compose with Auth

```bash
export DATAVIEWER_AUTH_DISABLED=false
export VITE_AZURE_CLIENT_ID=<your-client-id>
export VITE_AZURE_TENANT_ID=<your-tenant-id>
docker compose up --build
```

This example uses direct Entra JWT validation. Easy Auth is a Container Apps platform feature and is not provided by Docker Compose.

Supply the backend provider, tenant and client variables through container deployment environment settings as well; the Compose file forwards only the auth-disabled switch. Verify the effective environment with `docker compose config` before enabling authentication.

### Redirect Bridge and Deployment

MSAL v5 uses the standalone [redirect bridge](frontend/src/redirect.ts) for interactive redirects and silent iframe responses, independently of cross-origin isolation headers. Vite builds both `index.html` and `redirect.html`; the Docker image copies both documents and their bundled external modules. Token acquisition failures other than interaction-required errors reject API callers rather than sending requests without authentication headers.

The production [NGINX policy](frontend/nginx.conf.template) permits same-origin framing only on `/redirect.html`, with `frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`, and `Cache-Control: no-store`. Root, SPA, static, health and API responses retain framing denial. Keep bridge scripts on the same origin; do not add inline-script allowances or COOP/COEP/CORP headers. Vite development serves the bridge but does not enforce the production NGINX policy.

1. Add each exact `/redirect.html` URI to the Entra SPA registration before deploying an auth-enabled build. Retain the root SPA registration during the rollback window, and keep the Easy Auth web callback unchanged.
2. Deploy the frontend image with the intended client/tenant build variables, verify the bridge and headers, then complete live authentication acceptance below.
3. For rollback, restore the previous image and its matching configuration/registration. Do not loosen normal-page framing headers. Remove root SPA entries only after the rollback window closes.

### Authentication Smoke Tests

With an auth-disabled production Compose build running, execute from the repository root:

```bash
./scripts/security/Test-DataviewerSecurityHeaders.ps1
npm run test:auth --workspace robotic-training-data-tool
```

The verifier checks independent policy literals, rejecting weakened or duplicate headers. The browser suite uses the pinned SDK and built bridge without credentials: synthetic `login_required` must reach the caller as `InteractionRequiredAuthError`; blocked framing or a missing bridge must time out. It also checks cross-origin denial and visible, sanitized errors for malformed/missing responses.

These tests prove response delivery, not successful Entra authentication. Direct navigation to `/redirect.html` without an auth response must show an error.

| Runtime                       | Acceptance                                                                                                                                                                                                  |
|-------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Auth disabled                 | Build without `VITE_AZURE_CLIENT_ID`, run the backend with `DATAVIEWER_AUTH_DISABLED=true`, and verify normal dataset/API access without login. The bridge still exists for deterministic production tests  |
| Auth-enabled Vite development | Register the exact origin/port bridge URI, configure both frontend and backend auth, and verify sign-in and authorized API access. Use the production container for framing/header evidence                 |
| Auth-enabled production       | Register the HTTPS bridge URI, verify proxy headers and bridge asset delivery, then verify real sign-in, API access, silent renewal and the forced iframe fallback under the target tenant/browser policies |

Cached access tokens and successful refresh-token exchanges can bypass the iframe. In a controlled live test, use the pinned SDK's `CacheLookupPolicy.Skip` to force that path and verify its result or interactive recovery; never log codes, tokens or response hashes. Treat tenant consent, browser cookies, issued-token validation and refresh behavior as deployment acceptance, not outcomes of the synthetic suite.

### Auth Environment Variable Reference

| Variable                     | Location              | Description                                                           |
|------------------------------|-----------------------|-----------------------------------------------------------------------|
| `DATAVIEWER_AUTH_DISABLED`   | Backend               | Set to `false` to enable auth (`true` disables all checks)            |
| `DATAVIEWER_AUTH_PROVIDER`   | Backend               | Auth provider: `apikey`, `azure_ad`, `auth0`, or `easy_auth`          |
| `DATAVIEWER_PROXY_KEY`       | Frontend and backend  | Shared secret binding Easy Auth principal forwarding to trusted nginx |
| `DATAVIEWER_AZURE_TENANT_ID` | Backend               | Entra ID tenant ID                                                    |
| `DATAVIEWER_AZURE_CLIENT_ID` | Backend               | App registration client ID                                            |
| `DATAVIEWER_SECURE_COOKIES`  | Backend               | Set to `true` for HTTPS deployments                                   |
| `VITE_AZURE_CLIENT_ID`       | Frontend (build-time) | Enables the optional direct Entra JWT flow when set                   |
| `VITE_AZURE_TENANT_ID`       | Frontend (build-time) | Tenant used by the optional MSAL authority                            |

### Token Flow

```text
Browser → frontend Easy Auth → authenticated cookie session
Browser → frontend nginx → native /api, image, and video requests
Easy Auth principal + proxy credential → FastAPI identity validation
FastAPI → Azure Storage → managed identity
```

The backend accesses Azure Storage using managed identity, not the user's token. User authentication and storage authentication are independent.

### Deployment and Rollback

Run `data-management/setup/deploy-dataviewer.sh` interactively. For authenticated deployments, the script:

1. Verifies both Container Apps already contain the Terraform-owned `dataviewer-proxy-key` secret and the frontend contains the Terraform-owned Easy Auth client credential.
2. Captures the current images, authentication settings, and secret references and classifies whether they form a secure recovery pair.
3. Deploys the backend in `easy_auth` mode with its proxy-key secret reference.
4. Verifies frontend Easy Auth uses the Terraform-owned credential and denies an anonymous forged-principal probe.
5. Deploys the frontend with its proxy-key secret reference.
6. Repeats the negative probe, then requires one terminal browser and media verification gate before reporting success.

`--skip-backend` requires an existing backend already using `easy_auth` and `dataviewer-proxy-key`. `--skip-frontend` requires enabled frontend Easy Auth and an existing proxy-key reference. A failed rollout restores only a previously verified secure pair while Easy Auth remains enabled. If no secure pair exists or any restore step fails, the script disables frontend ingress and verifies the deny state. Terraform retains ownership of both credentials throughout deployment and recovery.

### Authentication Smoke Test

Open the deployed frontend in a private browser window and sign in through Easy Auth.

| Probe                                                      | Expected result                        |
|------------------------------------------------------------|----------------------------------------|
| `/api/auth/context` through the frontend                   | HTTP 200 with `auth_mode=easy_auth`    |
| Image and frame requests                                   | Render without a browser bearer header |
| Video `HEAD` request                                       | Successful native response             |
| Video Range request                                        | HTTP 206 with the requested byte range |
| Direct backend request with only a forged principal header | HTTP 401                               |
| Direct backend request with a missing or wrong proxy key   | HTTP 401                               |
| Credentialed cross-origin API request                      | Rejected                               |

The backend Container App remains internal-only. Inspect deployment output and application logs after the smoke test to confirm they contain neither the proxy credential nor decoded principal payloads.

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

The npm command starts the backend and frontend together on Windows, macOS, and Linux. Complete the dependency setup first: npm and VS Code tasks use the prepared backend environment without restoring or removing optional judge packages. Stop both processes with `Ctrl+C`.

### Health-checked Bash launcher

```bash
./start.sh
```

The Bash launcher starts the backend first, waits for its health endpoint, then starts the frontend. Use it on macOS, Linux, WSL, or a compatible shell when ordered startup and health checking are required.

When a dependency restore is needed, it restores the selected evaluator profile before the backend, then checks the combined environment. A dependency conflict stops startup. Existing services are not reconfigured by changing launcher source.

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

**Export** writes the current episode with its edits applied: removed and inserted frames, crop and resize, trajectory adjustments and subtasks. The source dataset is never modified, and the output path must be under the Dataviewer data directory. A relative output path is resolved against that directory, so the default, `<dataset>-export`, appears in the dataset list as a new dataset beside its source.

| Source format | Export output                                                                                            |
|---------------|----------------------------------------------------------------------------------------------------------|
| LeRobot v3.0  | A new LeRobot v3.0 dataset at the output path, which must be new or empty and outside the source dataset |
| HDF5          | `episode_<index>.hdf5` files in the output directory, with `.meta.json` and `.subtasks.json` beside them |

LeRobot export supports datasets whose camera features are non-depth videos. It refuses a dataset with image features or depth video.

A LeRobot export locks its output directory with a hidden `.dataviewer-export.lock` file and stages the dataset in a hidden `.dataviewer-export.partial` directory inside it, so an existing empty directory, such as a mounted volume, is kept. The dataset appears once the export completes, with `meta` moved in last. While the lock is held, a second export to the same directory fails.

If the backend stops partway through an export, or an export fails and can't remove what it moved, the next export to that directory cleans up first.
Before moving anything into place, every export records the identity of each file it staged. The cleanup removes the earlier export's staging and the moved files that still match that record, and it keeps a dataset whose move finished. Anything else in the directory stays, including files added inside the earlier export's folders, and exports there fail until you remove it.
The cleanup opens each folder without following links and checks it against the record, and it ignores a record whose paths don't describe a staged dataset, so it can't remove anything outside the output directory.
On Windows, which lacks the file operations that make this safe, the next export leaves the earlier export's files in place and fails until you remove them, including the hidden `.dataviewer-export.partial` directory.

LeRobot exports need a filesystem that supports file locking, such as a local disk or a Docker bind mount. On one that doesn't, the export fails and can leave an empty `.dataviewer-export.lock` behind. The export dialog reports every failure as "Export failed", and the backend log gives the reason.

A LeRobot export:

- keeps every supported recorded feature;
- re-encodes the videos with the source's recorded encoder settings, falling back to LeRobot's defaults for any setting the source doesn't record;
- recomputes the per-episode and dataset statistics;
- writes `meta/info.json` for its own files, without the `total_chunks` and `total_videos` counts a LeRobot v2.1 conversion can leave behind;
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
Both columns use the Arrow types LeRobot declares for them, with tool calls stored as JSON text.

For a LeRobot source, **Include language instructions as LeRobot task phrasings and plan** adds each episode's most recently saved [language instruction](#language-instruction-vla-annotation):

- the instruction and its paraphrases become `task_aug` rows, which LeRobot rotates `${task}` through during training;
- the subtask instructions become one numbered `plan` row at the first frame.

These rows replace the source's `task_aug` and `plan` rows for that episode, and episodes without a saved instruction keep theirs. The option is on by default in the dialog and in the export API, where `"includeLanguageInstructions": false` turns it off. `dataviewer-export.json` records whose instruction was used and when it was saved.

The trajectory editor adjusts the recorded frame under the playhead. After you remove or insert frames, it says which recorded frame that is. An inserted frame takes its values from the recorded frames on either side, so the editor doesn't adjust it directly.

Trajectory adjustments never replace recorded joint positions:

- LeRobot exports add `adjusted.observation.state` and `adjusted.observation.state_mask` beside `observation.state`. The `adjusted.` prefix keeps them out of LeRobot policy inputs.
- HDF5 exports add `data/qpos_adjusted` and `data/qpos_adjusted_mask` beside `data/qpos`.

Each export is its own dataset. LeRobot merges datasets only when their features match, so a cropped or adjusted export doesn't merge with an unedited one.

### VLM dataset-labeling CLI

`physical_ai_curation.label_dataset` runs Qwen3-VL across a LeRobot v2.1 or v3.0 dataset. It persists jobs before execution and exports full rows to `labels.jsonl` and a flat summary to `labels.csv`.

```bash
cd data-management/viewer/backend
uv run --frozen --extra vlm-judge \
  python -m physical_ai_curation.label_dataset \
  --dataset-root /data/my-dataset \
  --dataset-id owner--my-dataset \
  --output-dir /data/my-dataset/vlm-labels \
  --model-revision '<reviewed-full-40-hex-commit-sha>' \
  --n-frames 16 --limit 1 --single
```

| Option                 | Behavior                                                                                                   |
|------------------------|------------------------------------------------------------------------------------------------------------|
| `--dataset-id`         | Writes the canonical dataviewer ID into generated metadata; set it for nested IDs such as `owner--dataset` |
| `--resume --job-id ID` | Retries eligible targets in a saved job, preserving successful targets and revalidating saved inputs       |
| `--write-analysis`     | Requests approved dataset application without replacing human-owned labels or analysis                     |
| `--views`              | Restricts the model input to selected video feature keys; all detected video views are used by default     |
| `--limit`              | Processes only the first requested number of episodes                                                      |
| `--scene-context`      | Adds dataset-specific scene context to the model prompt                                                    |

Without `--write-analysis`, the JSONL and CSV files remain standalone exports and do not appear in the Episode Analysis card. Error rows remain in JSONL and CSV for inspection but are not merged into the dataset analysis map.

Dataset runs require reviewed samples and an approval for the same runtime configuration.
Use `--operation apply --job-id ID` for explicit application of an eligible saved job.
Application uses revision-conditional writes; resolve a reported conflict and retry instead of overwriting current metadata.
Management operations do not require the evaluator package. Direct inference remains available through `evaluation.vlm_judge.run`, without saved-input selection or durable lifecycle options.

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

### Local K3s Workstation

The [local OSMO profile](../../infrastructure/local/README.md#-dataviewer) deploys the dataviewer into a single-node K3s cluster with `infrastructure/local/04-deploy-dataviewer.sh`. It builds both images into the workstation's loopback registry, runs the backend on the local datasets directory as that directory's owner with `DATAVIEWER_AUTH_DISABLED=true`, and exposes only ClusterIP Services.

Open it through `kubectl proxy`, `kubectl port-forward`, or the pinned ClusterIP. The frontend resolves its assets and API calls against the page's own URL, so it also works under the `kubectl proxy` service path.

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

The backend image includes the frozen `azure`, `analysis`, `export`, `auth`, and `yolo` extras. Its build stage installs dependencies; the runtime stage copies the virtual environment with the application user's ownership. Installation caches remain in the build stage.

Build from the repository root so the allowlisted context includes curation, dataset-I/O and evaluator sources.

```bash
# Backend
docker build --file data-management/viewer/backend/Dockerfile --tag dataviewer-backend .

# Frontend
docker build --file data-management/viewer/frontend/Dockerfile --tag dataviewer-frontend .
```

For model-free echo execution, set `--build-arg BACKEND_EXTRAS=azure,analysis,export,auth` to omit YOLO dependencies. Add `--build-arg JUDGE_EXTRAS=openai` for a remote inference client, or `JUDGE_EXTRAS=qwen3-vl` for a separately validated local GPU runtime. Build profiles select installed dependencies; runtime settings still control whether judging is enabled and which backend it uses.

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
