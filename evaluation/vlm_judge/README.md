---
title: VLM-as-Judge
description: Run vision-language evaluations with saved inputs, approval-gated jobs and confined HTTP media access
ms.date: 2026-10-19
---

Open-weight-first harness that scores LeRobot manipulation episodes and
policy-rollout videos with a vision-language model. Dataset and rollout CLIs
and the HTTP API share the same engine.

Curation clients resolve saved instructions and supported edit descriptors before
execution. Malformed model output is an execution error; an inconclusive judgment
does not become a human `PARTIAL` label. Cache identity includes the saved snapshot,
model revision or deployment digest, selected cameras, frame settings and process
configuration. An unpinned model or mutable remote deployment is not an immutable
reproducibility guarantee.

Local dataset storage does not imply local inference. The `openai-compat` backend
sends selected camera frames and the effective instruction to its configured
endpoint. Confirm that endpoint and its data-handling policy before running it.

The entry points are:

| Entry point                          | Responsibility                                                              |
|--------------------------------------|-----------------------------------------------------------------------------|
| `physical_ai_curation.run`           | Saved-input judging, durable jobs, approval, application and withdrawal     |
| `physical_ai_curation.label_dataset` | Durable task-findings workflow and JSONL/CSV exports                        |
| `physical_ai_curation.api`           | Curation HTTP routes used by the viewer and standalone app                  |
| `evaluation.vlm_judge.run`           | Direct dataset-metadata inference without saved annotations or durable jobs |
| `evaluation.vlm_judge.policy_eval`   | Direct policy-rollout inference                                             |

Curation launchers live in [data-management/curation/scripts](../../data-management/curation/scripts).
There are no old-command aliases. Direct inference does not accept lifecycle options.
Management operations remain available without the optional evaluator; scoring requires it.
See [data-management/curation/README.md](../../data-management/curation/README.md)
for install profiles, command migration and the current evidence contract.

## 🧠 Agentic Harness

`JudgeAgent` orchestrates a multi-step judgment chain on top of any
`JudgeBackend`:

1. **Outcome MCQ** with N-sample self-consistency voting (always runs).
   Cosmos-Reason1-style `<think>...</think><answer>A|B</answer>` format.
2. **GVL process reward** — shuffle-and-rank dense per-frame progress,
   anchored at frame 0; produces a Value-Order Correlation (VOC) plus the
   per-frame 0-100 array (always runs, independent of outcome).
3. **Milestone decomposition** — runs when outcome confidence is below
   `milestone_threshold` (default 0.85) **or** the outcome is FAILURE.
   Returns 3-5 atomic milestones with frame-range citations and an
   evidence sentence each. Frame-range citations mitigate the
   over-criticism bias documented in *Behavior Critic* (arXiv:2402.04210).
4. **Failure-mode attribution** — only on FAILURE outcomes. Picks one of
   `missed_grasp`, `wrong_object`, `dropped`, `target_not_reached`,
   `collision_or_unsafe`, `early_termination`, `other`.

`JudgeService` wraps the agent with frame extraction, multi-view tiling,
lazy backend init, and a SHA256-keyed disk cache so re-runs are free. Both
the CLI and the HTTP API import the service — no parallel paths.

## 📂 Layout

| File / dir                            | Purpose                                                   |
|---------------------------------------|-----------------------------------------------------------|
| `physical_ai_dataset_io.lerobot`      | Shared LeRobot v2.1 + v3.0 discovery and metadata parsing |
| `frames.py`                           | PyAV frame extraction with multi-view tiling              |
| `prompts.py`                          | Outcome MCQ + GVL + milestone + failure-mode templates    |
| `backend.py`                          | Qwen3-VL HF backend, OpenAI-compatible HTTP backend, echo |
| `judge.py`                            | `score_episode` + `JudgeResult` + Spearman VOC            |
| `agent.py`                            | `JudgeAgent` multi-step controller                        |
| `cache.py`                            | SHA256-keyed JSON disk cache                              |
| `service.py`                          | `JudgeService` — single integration surface               |
| `physical_ai_curation.api`            | FastAPI router + standalone curation app                  |
| `run.py`                              | CLI: direct dataset inference without curation            |
| `policy_eval.py`                      | CLI: judge a directory of policy-rollout MP4s             |
| `data-management/curation/scripts/`   | Dataset recipes and standalone curation HTTP launcher     |
| `scripts/evaluate-policy-rollouts.sh` | Wrapper for policy rollout dirs (`leisaac-tests/*`)       |
| `scripts/submit-osmo-judge.sh`        | Submit the judge as an OSMO workflow                      |
| `scripts/osmo-judge-entry.sh`         | OSMO task entry: locked runtime, judge, verdict check     |
| `workflows/osmo/judge.yaml`           | OSMO workflow template                                    |

## 🚀 Quick Start

| Backend         | Hardware required on this host                           |
|-----------------|----------------------------------------------------------|
| `qwen3-vl`      | CUDA-capable NVIDIA GPU; RTX or Azure A10 are candidates |
| `openai-compat` | CPU client; the remote endpoint hosts the model          |
| `echo`          | CPU only; no model execution                             |

For Qwen3-VL-4B in bfloat16, start validation with a full 24 GB GPU and concurrency
one. This is not a guaranteed minimum: model, frame and generation settings affect VRAM.
Run curation commands from the repository root in an existing viewer backend
environment with the required judge profile. Policy rollouts use the evaluator environment;
`--no-sync` does not install missing dependencies.

```sh
# 1. Smoke test (no model load, no GPU)
uv run --project data-management/viewer/backend --no-sync bash \
  data-management/curation/scripts/evaluate-leisaac-pick-orange.sh \
  --backend echo --dry-run --limit 2

# 2. Evaluate one episode with a pinned Qwen3-VL-4B on a GPU host
export VLM_JUDGE_MODEL_REVISION='<reviewed-full-40-hex-commit-sha>'
uv run --project data-management/viewer/backend --no-sync bash \
  data-management/curation/scripts/evaluate-leisaac-pick-orange.sh \
  --backend qwen3-vl --limit 1 \
  -- --single --model-revision "$VLM_JUDGE_MODEL_REVISION"

# 3. Score policy rollouts using reviewed offline model artifacts
uv run --project evaluation --no-sync bash \
  evaluation/vlm_judge/scripts/evaluate-policy-rollouts.sh \
  --backend qwen3-vl --model-id /path/to/reviewed-qwen-model

# 4. Serve the HTTP API on loopback with the same pin (echo needs no GPU)
export VLM_JUDGE_ALLOW_UNAUTHENTICATED=true
export VLM_JUDGE_DATASETS='{"example-dataset":"/path/to/dataset"}'
export VLM_JUDGE_LOCAL_ACTOR=local-operator
export VLM_JUDGE_JOB_DIR=outputs/vlm-judge/jobs
uv run --project data-management/viewer/backend --no-sync bash data-management/curation/scripts/serve-api.sh \
  --backend qwen3-vl --port 8088

# 5. Approved dataset against a vLLM server hosting a larger model
VLM_JUDGE_BACKEND=openai-compat \
VLM_JUDGE_MODEL_ID=Qwen/Qwen3-VL-30B-A3B-Instruct \
uv run --project data-management/viewer/backend --no-sync bash \
  data-management/curation/scripts/evaluate-dataset.sh \
  --dataset /path/to/approved-dataset --base-url http://localhost:8000/v1 \
  -- --approval-id "$APPROVAL_ID" --request-id "$REQUEST_ID"
```

The dataset example requires an approval from reviewed samples using the same
dataset, principal, job store and runtime configuration. Single-episode smoke
commands do not replace that approval workflow.

The wrappers retain their dataset and output defaults; use `--dataset`,
`--rollout-root` and `--output` for your own data. The dataset wrapper forwards
extra CLI options after `--`. For Azure OpenAI, use your deployment name as the
model ID and its OpenAI-compatible base URL. Configure authentication for the
target environment. For non-Azure endpoints, the dataset CLI reads
`OPENAI_API_KEY`; the server and viewer use `VLM_JUDGE_API_KEY`. Keep credentials
out of command arguments and shell history.

## ☁️ Azure Deployment

### Azure Inference Authentication

Use an identity with inference permission on the target deployment. The current
Azure inference authentication supports local development; production
managed-identity integration is deferred. Do not treat a developer login as
production configuration. Keep credentials out of source, `.env` files and logs.
Blob storage authentication is configured separately.

### Deployment Layout

Keep the existing CPU Container Apps deployment. Add a separate private GPU inference
service, starting with an existing suitable A10 VM.

| Component               | Host              | Responsibility                                           |
|-------------------------|-------------------|----------------------------------------------------------|
| Viewer frontend         | CPU Container App | UI                                                       |
| Viewer backend          | CPU Container App | Saved inputs, frame extraction, durable jobs and results |
| Qwen model server       | GPU host          | Inference through `/v1/chat/completions`                 |
| Dataset and job storage | Azure Blob        | Source data, annotations and shared durable job state    |

The viewer sends selected frames and saved instructions to the model server. Approve
the endpoint's data-handling policy before use. The model server does not own judge jobs.

> [!IMPORTANT]
> The current viewer Terraform and Compose configurations do not deploy a GPU server.
> Installing Qwen dependencies or setting `VLM_JUDGE_BACKEND=qwen3-vl` does not attach
> a GPU. GPU serving must be deployed and validated separately.

### Select the GPU Host

| Host                                           | When to choose it                         | What to verify                                                  |
|------------------------------------------------|-------------------------------------------|-----------------------------------------------------------------|
| Existing A10 VM                                | Initial validation and single-GPU serving | Driver, VRAM, process supervision, private ingress and patching |
| Existing AKS GPU pool                          | Shared, sustained inference               | GPU scheduling, ingress and scaling                             |
| Managed endpoint or Container Apps GPU profile | Managed operations or burst demand        | Region, quota, VRAM, model/API/auth support and cold starts     |

For a full A10, evaluate `Standard_NV36ads_A10_v5`. Smaller A10 sizes may expose
only fractional GPU memory. Check `nvidia-smi`, regional quota and cost before use;
see [Azure A10 sizes](https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series)
and [GPU infrastructure guidance](../../infrastructure/README.md).

### Configure the Viewer

1. Deploy and validate the GPU endpoint with authenticated private TLS ingress.
2. Build the CPU viewer image from the repository root:

   ```bash
   docker build -f data-management/viewer/backend/Dockerfile \
     --build-arg BACKEND_EXTRAS=azure,analysis,export,auth \
     --build-arg JUDGE_EXTRAS=openai \
     -t dataviewer-backend:validation .
   ```

3. Configure the following runtime settings and deploy the viewer image.
4. Test endpoint access from the viewer network before enabling the judge.

| Setting                    | Remote GPU deployment                                         |
|----------------------------|---------------------------------------------------------------|
| `STORAGE_BACKEND`          | `azure` with the configured dataset and annotation containers |
| `VLM_JUDGE_ENABLED`        | `false` during setup; `true` after endpoint validation        |
| `VLM_JUDGE_BACKEND`        | `openai-compat`                                               |
| `VLM_JUDGE_BASE_URL`       | Private inference base URL, including `/v1` where required    |
| `VLM_JUDGE_MODEL_ID`       | Model name accepted by the server                             |
| `VLM_JUDGE_MODEL_REVISION` | Immutable identity matching the deployed weights              |
| `VLM_JUDGE_API_KEY`        | Platform secret reference for endpoint authentication         |
| `VLM_JUDGE_CAPACITY`       | `1` initially                                                 |
| `VLM_JUDGE_CAPACITY_SCOPE` | Same value for workers sharing inference capacity             |
| `VLM_JUDGE_CACHE_DIR`      | Writable disposable scratch                                   |

The build extra is `openai`; the runtime backend is `openai-compat`. For inference
inside the viewer process, use `JUDGE_EXTRAS=qwen3-vl` on a GPU-equipped host with
NVIDIA device passthrough and a compatible driver/CUDA runtime. Do not combine these
profiles with the backend's `vlm-judge` extra, which pins a different Transformers version.

### Deployment Boundaries

| Area                   | Requirement or limitation                                                                     |
|------------------------|-----------------------------------------------------------------------------------------------|
| GPU endpoint           | Deploy separately; the viewer deployment script only builds and rolls out viewer images       |
| Existing Container App | Roll out environment and secret changes explicitly; Terraform ignores later template changes  |
| Shared jobs            | Workers must share Blob job storage, capacity scope and capacity limit                        |
| Availability           | Keep a worker replica running while jobs are pending; no job-triggered scale-from-zero exists |
| GPU capacity           | Limit other inference clients separately; drain work before deallocating the GPU              |
| Model identity         | Pin weights on the model server; the viewer's revision setting does not pin them remotely     |
| Credentials            | Validate authentication in the target runtime; inference managed-identity support is deferred |

> [!WARNING]
> The bundled [shim](openai_shim.py) has no authentication. Keep it on loopback
> behind an authenticated proxy for cross-host access; never expose it publicly.
> Leave remote image fetching disabled. Setting an API key on the client does not
> secure a server that ignores it.

### Validate Before Enabling

Use disposable data and record results separately from production configuration.

| Check           | Required evidence                                                                                       |
|-----------------|---------------------------------------------------------------------------------------------------------|
| GPU runtime     | Allocated VRAM; compatible pinned Python, NumPy, Torch, torchvision, CUDA, cuDNN, Transformers and PyAV |
| Qwen inference  | One episode, then realistic camera/frame settings; peak VRAM, latency and valid results                 |
| Installed image | Imports and echo execution outside the checkout                                                         |
| Remote endpoint | Authenticated image-and-text requests from the viewer network                                           |
| Job lifecycle   | Submit, status, cancel, retry, restart, sample approval, application and withdrawal                     |
| Persistence     | Job recovery after worker replacement and disposable scratch loss                                       |
| Browser         | Saved-state recovery, keyboard/focus, responsive layout and assistive technology                        |

Restore frozen dependencies and download weights only with operator approval. Keep
endpoint values, resource identifiers and validation evidence outside Git. Echo and
unit tests do not establish GPU compatibility or model quality.

## 🌐 HTTP API

```text
GET  /health              -> {status, model_id, backend_kind, cache_enabled}
POST /judge               -> 202 job summary, Location and Retry-After headers
GET  /jobs/{job_id}        -> Durable job status and paginated targets
GET  /results             -> Episode evidence (dataset_id and episode_index query)
```

`POST /judge` body:

```json
{
  "dataset_id": "example-dataset",
  "episode_index": 7,
  "views": ["observation.images.front", "observation.images.wrist"],
  "force": false
}
```

The operator maps dataset IDs to local roots with `VLM_JUDGE_DATASETS`. Requests
select saved episodes and views, not filesystem paths or inline instructions.
Every selected view must be a regular MP4 contained within its dataset root before cache access.
Traversal, escaping symlinks, missing files, directories and special files are rejected.
Suffix validation does not verify content safety; decoding remains in-process.

From another terminal on the same host:

```bash
curl --fail-with-body --include http://127.0.0.1:8088/judge \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-dataset-episode-7-run-1' \
  -d '{"dataset_id":"example-dataset","episode_index":7,"views":["observation.images.front"]}'
```

Poll the returned `Location` until the job completes, then read its results. Reuse
the same idempotency key when retrying submission of the same request, and use a new
key for a new run. A successful submission is not a completed judgment or label write.

Mount the standalone local-only router in another FastAPI app:

```python
from pathlib import Path
from fastapi import FastAPI
from physical_ai_curation.api import build_router
from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
from evaluation.vlm_judge.service import (
    BackendConfig,
    FrameConfig,
    JudgeService,
    ServiceConfig,
)

service = JudgeService(
    ServiceConfig(
        backend=BackendConfig(kind="echo"),
        frames=FrameConfig(n_frames=12),
        cache_dir=Path("outputs/vlm-judge/cache"),
    )
)
resolver = LocalDatasetResolver({"example-dataset": Path("/data/example-dataset")})
app = FastAPI()
app.include_router(
    build_router(
        service,
        resolver=resolver,
        local_actor="local-operator",
        job_store=LocalJobStore(Path("outputs/vlm-judge/jobs")),
        allow_unauthenticated=True,
    ),
    prefix="/api/vlm-judge",
)
```

Core request models and router factories remain importable without the optional HTTP dependencies.
Install the API extra in the owning environment before constructing or serving the ASGI app.
Dataset grounding accepts saved instructions of any source, including templates and dataset tasks,
with one saved outcome label or a saved task-completeness rating. Applied labels are comparison
baselines, not independent human validation. Review sample results and explicitly approve the
configuration before dataset execution; changes to grounding inputs invalidate that approval.
Reading reset history before any withdrawal returns `200` with `null`, not a missing-route error.

The example explicitly enables an unauthenticated local actor. Bind local-only
services to loopback. Authenticated applications supply `principal_dependency`;
authentication failures never fall back to that local actor. Standalone startup
reads an operator-controlled `VLM_JUDGE_DATASETS` JSON map and requires
`VLM_JUDGE_ALLOW_UNAUTHENTICATED=true`, `VLM_JUDGE_LOCAL_ACTOR` and a persistent
`VLM_JUDGE_JOB_DIR` for local mode.
Requests cannot supply server paths or unsaved instructions.

`JudgeService` performs inference; `JudgeJobs` manages durable execution and explicit
result application. Router lifespan starts workers; the host application must run
its lifespan to process queued jobs.

This router retains its admission checks when mounted. The viewer uses its own
authenticated dataset router; its authentication and CSRF checks are unchanged.

### Local operating contract

Previously unrestricted HTTP requests now require the saved-dataset configuration
and explicit local opt-in described above. Dataset roots must be existing directories
narrower than the filesystem root. `VLM_JUDGE_MEDIA_ROOT` no longer configures this API.
Health and documentation boot without loading a model or configuring local judging.
Requests require a literal loopback peer and loopback/localhost Host; body-bearing
requests require JSON content type. Browser Origin must match;
cross-site and forwarded-header requests are denied before body parsing.
Run direct ASGI deployments with `--host 127.0.0.1 --no-proxy-headers` as well.

| Status | Meaning                                                   |
|--------|-----------------------------------------------------------|
| 202    | Work accepted; poll the returned status URL               |
| 503    | Local judging configuration is disabled or unavailable    |
| 403    | Request admission denied before body parsing              |
| 422    | Invalid request, missing instruction or unsupported edits |
| 404    | Dataset, episode or actor-scoped job is unavailable       |
| 409    | Saved inputs, configuration or idempotency conflict       |

Inference and decoder failures after acceptance are recorded in job state, not returned
as a synchronous judgment response. Admission and request-validation errors do not
reflect submitted paths or raw exceptions. Server logs retain diagnostics.
No Entra registration is required for trusted-local mode. It is not authentication:
shared hosts, LAN listeners, container port publishing, proxies, dev tunnels and port
forwarding are unsupported. A local forwarding process can appear to be a local caller.
Dataset writers must be trusted; file replacement races are not prevented.

## 📡 OSMO Workflow

`scripts/submit-osmo-judge.sh` runs the judge as an OSMO workflow on one GPU. The task installs the judge's locked runtime, judges its input, checks that every verdict has an outcome, a confidence, and per-frame progress, and writes `judgments.jsonl` to the task output. `--output-dataset NAME` also publishes the verdicts as a version of an OSMO dataset.

The judge writes its verdicts to `judgments.partial.jsonl`, and the task renames that file to `judgments.jsonl` only after every selected episode or video was judged and every verdict passed the check. A failed, interrupted, or killed run leaves at most `judgments.partial.jsonl`, and a failed task still logs a summary of the verdicts it wrote.

An inconclusive verdict, where the outcome votes tie, has a `null` outcome; it counts as a complete verdict, and the task log reports how many verdicts were inconclusive.

The input decides what the judge scores. A LeRobot dataset brings its episodes and instructions. Any other directory is judged as rollout videos, which need `--instruction`.

| Option            | Input                                                                                                                |
|-------------------|----------------------------------------------------------------------------------------------------------------------|
| `--local-dataset` | A dataset in the workstation's datasets directory, on the [local OSMO profile](../../infrastructure/local/README.md) |
| `--input-dataset` | An OSMO dataset, such as the replay videos that `submit-osmo-eval.sh --output-dataset` publishes                     |
| `--input-url`     | An object-store URL that OSMO downloads                                                                              |

The default model is `Qwen/Qwen3-VL-4B-Instruct` at a pinned commit. Another Hub model needs `--model-revision`, and a model directory inside the task, given as an absolute path, loads offline without one. On the local profile, the model downloads once into the job cache and later runs reuse it.

```bash
# Judge the episodes of a local dataset
evaluation/vlm_judge/scripts/submit-osmo-judge.sh --profile local --local-dataset so101-learner

# Judge the replay videos of an Isaac Lab evaluation
evaluation/vlm_judge/scripts/submit-osmo-judge.sh --profile local \
  --input-dataset cartpole-eval --instruction "Keep the pole balanced upright"
```

## 📤 Output

Completed inference results contain the following fields (one result per episode).
HTTP submission returns a job summary first; retrieve completed target results
through the job lifecycle routes.

```json
{
  "episode_id": "leisaac-pick-orange/episode_000007",
  "instruction": "Grab orange and place into plate",
  "judge_model": "Qwen/Qwen3-VL-4B-Instruct",
  "prompt_version": "outcome-mcq-v1+gvl-process-v1+milestones-v1+failuremode-v1",
  "n_frames": 12,
  "outcome_success": false,
  "outcome_confidence": 0.667,
  "outcome_n_valid_votes": 3,
  "progress_per_frame": [0, 8, 22, 31, 47, 60, 60, 55, 50, 48, 45, 42],
  "voc": 0.62,
  "milestones": [
    {"name": "approach_object", "completed": true,  "frame_range": "0-3", "evidence": "arm extends toward orange"},
    {"name": "grasp_object",    "completed": false, "frame_range": "3-6", "evidence": "fingers close on empty space"},
    {"name": "lift_clear",      "completed": false, "frame_range": "6-8", "evidence": "object remains on table"}
  ],
  "failure_mode": "missed_grasp"
}
```

## 🔌 Backends

| Backend         | Use case                                                     |
|-----------------|--------------------------------------------------------------|
| `qwen3-vl`      | Local Hugging Face Qwen3-VL (default `Qwen3-VL-4B-Instruct`) |
| `openai-compat` | vLLM, NVIDIA NIM, Azure OpenAI — set `--base-url`            |
| `echo`          | Deterministic stub for offline tests                         |

`Qwen3VLBackend` resolves the correct model class against the installed
`transformers` (4.57+ ships native Qwen3-VL classes). Adopt larger
variants (`Qwen3-VL-8B-Instruct`, `Qwen3-VL-30B-A3B-Instruct`) by passing
`--model-id`. For Cosmos-Reason1 (post-trained on Qwen2.5-VL with NVIDIA
Open License), point the OpenAI-compatible backend at a Cosmos NIM or
self-hosted vLLM.

### Native model migration and offline use

Previously unpinned Hub loads now require a full 40-hex commit SHA, supplied through
CLI `--model-revision` or server/viewer `VLM_JUDGE_MODEL_REVISION`. Branches, tags,
blank revisions and `trust_remote_code=True` fail before model imports or downloads.
Processor and all model fallback classes use native Transformers implementations.

To obtain the full SHA for a Hub model:

1. Open the model's commit history, for example
  [Qwen3-VL-4B-Instruct commits](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct/commits/main).
2. Select the revision you intend to use and copy its full 40-character commit
  hash, not the abbreviated hash or a branch name such as `main`.
3. Set `VLM_JUDGE_MODEL_REVISION` to that value for the server/viewer, or pass it
  through `--model-revision` for the CLI. Use the matching model repository ID.

The SHA pins an exact repository version; it does not establish that the artifacts
are trusted or compatible with your runtime. Review the selected revision and
validate it in your target environment before relying on it.

An absolute directory or a path starting `./` or `../` selects local-only loading.
Omit the revision for local directories; missing directories never fall back to the
Hub. A bare `owner/model` remains a Hub ID; a matching local path is rejected as
ambiguous. Use explicit `./owner/model` for local artifacts or change working directory.
`HF_HUB_OFFLINE=1` also permits loading an already-cached full Hub SHA. Echo and
OpenAI-compatible clients, including Azure hosted deployments, need no Hub revision.

## 💾 Cache

`JudgeCache` keys results on a stable SHA256 of:

- video paths + size + mtime (per view, sorted)
- instruction text
- `judge_model` (backend `name`)
- backend kind and normalized model revision, in a versioned payload
- `prompt_version`
- serialised `AgentConfig`

Cache hits return immediately and skip all VLM inference, mirroring the
checkpoint-upload idempotency convention. Default location is
`outputs/vlm-judge/cache/`. Set `--cache-dir ""` to disable, or
`--force` to bypass on a single run.

Old cache payloads are invalidated. Changing backend kind or Hub SHA creates a new
key; displayed model IDs do not encode the pin. Local directory identity is only a
canonical locator. Before replacing local artifacts, stop every service that has
loaded them (the standalone judge, viewer backend, or local OpenAI shim). After
replacement is complete, restart those services and clear the associated result
caches or force fresh judgments.
Clearing the result cache or using `--force` does not reload model weights already
held in memory. No cryptographic content identity is claimed.

## 🧪 Tests

```sh
uv run --project evaluation --no-sync pytest -c evaluation/pyproject.toml \
  --no-cov evaluation/tests/vlm_judge -q
```

The tests use a `ScriptedBackend` (canned responses keyed by prompt
fragment) and a `StubService` to validate prompt parsers, cache
behaviour, the multi-step agent controller (success / low-confidence /
failure flows + format-violation tolerance), and the FastAPI router. No
GPU or network is required.

## � Dataviewer integration (experimental)

The dataviewer mounts this harness as a per-episode **JudgePanel**: reviewers
run the judge inline next to human labels, and results are cached per dataset
under `<dataset>/annotations/vlm_judge/`. The backend reuses the same
`JudgeService` shown above via its own router and service factory. See the
dataviewer's [VLM-as-Judge setup guidance](../../data-management/viewer/README.md#vlm-as-judge-experimental)
for enabling it (`VLM_JUDGE_ENABLED=true`), backend options, and usage.

## 🗺️ Roadmap

- **MLflow logging.** Persist per-episode results into MLflow so the
  existing `evaluation/metrics/bootstrap_mlflow.py` flow picks them up.
- **Cosmos-Reason1 NIM deployment.** Swap the `openai-compat` backend
  base URL onto an Azure AI Foundry NIM endpoint once the trial is
  provisioned (see `.copilot-tracking/research/2026-04-30/`).
- **Pairwise A/B mode.** Add a `compare_episodes` agent step for
  RoboArena-style policy ranking when two rollouts share a task.
