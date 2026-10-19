---
title: VLM-as-Judge
description: Run local or hosted vision-language judgments over robotic episodes with confined HTTP media access
ms.date: 2026-10-15
---

Open-weight-first harness that scores LeRobot manipulation episodes and
policy-rollout videos with a vision-language model. Two consumption surfaces
share the same engine:

- **Dataset / rollout CLI** — `evaluation.vlm_judge.run` and
  `evaluation.vlm_judge.policy_eval`.
- **HTTP API** — `evaluation.vlm_judge.api` (FastAPI router) for the
  dataviewer and any other in-loop integrations.

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

| File / dir                                | Purpose                                                      |
|-------------------------------------------|--------------------------------------------------------------|
| `dataset.py`                              | LeRobot v2.1 + v3.0 episode discovery and instruction lookup |
| `frames.py`                               | PyAV frame extraction with multi-view tiling                 |
| `prompts.py`                              | Outcome MCQ + GVL + milestone + failure-mode templates       |
| `backend.py`                              | Qwen3-VL HF backend, OpenAI-compatible HTTP backend, echo    |
| `judge.py`                                | `score_episode` + `JudgeResult` + Spearman VOC               |
| `agent.py`                                | `JudgeAgent` multi-step controller                           |
| `cache.py`                                | SHA256-keyed JSON disk cache                                 |
| `service.py`                              | `JudgeService` — single integration surface                  |
| `api.py`                                  | FastAPI router + standalone uvicorn app                      |
| `run.py`                                  | CLI: judge a LeRobot dataset folder                          |
| `policy_eval.py`                          | CLI: judge a directory of policy-rollout MP4s                |
| `scripts/evaluate-dataset.sh`             | Generic dataset wrapper                                      |
| `scripts/evaluate-cnc-lerobot.sh`         | Wrapper for `datasets/cnc_lerobot`                           |
| `scripts/evaluate-ur10e-episodes.sh`      | Wrapper for `datasets/ur10e_episodes`                        |
| `scripts/evaluate-leisaac-pick-orange.sh` | Wrapper for `datasets/leisaac-pick-orange`                   |
| `scripts/evaluate-policy-rollouts.sh`     | Wrapper for policy rollout dirs (`leisaac-tests/*`)          |
| `scripts/serve-api.sh`                    | Launch the HTTP API                                          |

## 🚀 Quick Start

A 24GB GPU (A10 / A100 / RTX A5000) fits `Qwen/Qwen3-VL-4B-Instruct` in bfloat16.
CPU-only hosts use `echo` for security tests or `openai-compat` for hosted inference.
Run these commands from the repository root in an existing evaluation environment;
`--no-sync` does not install missing dependencies.

```sh
# 1. Smoke test (no model load, no GPU)
uv run --project evaluation --no-sync bash \
  evaluation/vlm_judge/scripts/evaluate-leisaac-pick-orange.sh \
  --backend echo --dry-run --limit 2

# 2. Evaluate three episodes with a pinned Qwen3-VL-4B on a GPU host
export VLM_JUDGE_MODEL_REVISION='<reviewed-full-40-hex-commit-sha>'
uv run --project evaluation --no-sync bash \
  evaluation/vlm_judge/scripts/evaluate-leisaac-pick-orange.sh \
  --backend qwen3-vl --limit 3 \
  -- --model-revision "$VLM_JUDGE_MODEL_REVISION"

# 3. Score policy rollouts using reviewed offline model artifacts
uv run --project evaluation --no-sync bash \
  evaluation/vlm_judge/scripts/evaluate-policy-rollouts.sh \
  --backend qwen3-vl --model-id /path/to/reviewed-qwen-model

# 4. Serve the HTTP API on loopback with the same pin (echo needs no GPU)
export VLM_JUDGE_ALLOW_UNAUTHENTICATED=true
export VLM_JUDGE_MEDIA_ROOT=/path/to/dataset
uv run --project evaluation --no-sync bash evaluation/vlm_judge/scripts/serve-api.sh \
  --backend qwen3-vl --port 8088

# 5. Full dataset against a vLLM server hosting a larger model
VLM_JUDGE_BACKEND=openai-compat \
VLM_JUDGE_MODEL_ID=Qwen/Qwen3-VL-30B-A3B-Instruct \
uv run --project evaluation --no-sync bash \
  evaluation/vlm_judge/scripts/evaluate-cnc-lerobot.sh \
  --base-url http://localhost:8000/v1
```

The wrappers retain their dataset and output defaults; use `--dataset`,
`--rollout-root` and `--output` for your own data. The dataset wrapper forwards
extra CLI options after `--`. For Azure OpenAI, use your deployment name as the
model ID and its OpenAI-compatible base URL. The dataset CLI reads
`OPENAI_API_KEY`; the server and viewer use `VLM_JUDGE_API_KEY`. Keep credentials
out of command arguments and shell history.

## 🌐 HTTP API

```text
GET  /health              -> {status, model_id, backend_kind, cache_enabled}
POST /judge               -> JudgeResponse
```

`POST /judge` body:

```json
{
  "episode_id": "leisaac-pick-orange/episode_000007",
  "instruction": "Grab orange and place into plate",
  "video_paths": {
    "front": "videos/front/episode_000007.mp4",
    "wrist": "videos/wrist/episode_000007.mp4"
  },
  "from_s": null,
  "to_s": null,
  "force": false
}
```

Paths are relative to the server-configured media root, or absolute paths inside
it. Every selected view must be a contained regular MP4 before cache access.
Traversal, escaping symlinks, missing files, directories and special files are rejected.
Suffix validation does not verify content safety; decoding remains in-process.

From another terminal on the same host:

```bash
curl --fail-with-body http://127.0.0.1:8088/judge \
  -H 'Content-Type: application/json' \
  -d '{"episode_id":"smoke","instruction":"Pick up the cube","video_paths":{"front":"videos/front/episode_000007.mp4"}}'
```

Mount the standalone local-only router in another FastAPI app:

```python
from pathlib import Path
from fastapi import FastAPI
from evaluation.vlm_judge.api import build_router
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
app = FastAPI()
app.include_router(
    build_router(service, media_root=Path("/path/to/dataset"), allow_unauthenticated=True),
    prefix="/api/vlm-judge",
)
```

This router retains its admission checks when mounted. The viewer uses its own
authenticated dataset router; its authentication and CSRF checks are unchanged.

### Local operating contract

Previously unrestricted HTTP requests now require
`VLM_JUDGE_ALLOW_UNAUTHENTICATED=true` and `VLM_JUDGE_MEDIA_ROOT` set to an existing
directory narrower than the filesystem root. Health and documentation boot without
a model or either setting. Requests require a literal loopback peer,
loopback/localhost Host and JSON content type. Browser Origin must match;
cross-site and forwarded-header requests are denied before body parsing.
Run direct ASGI deployments with `--host 127.0.0.1 --no-proxy-headers` as well.

| Status | Meaning                                                |
|--------|--------------------------------------------------------|
| 503    | Local judging configuration is disabled or unavailable |
| 403    | Request admission denied before body parsing           |
| 422    | Admitted JSON or schema validation failed              |
| 400    | Invalid media path, window or decoded content          |
| 502    | Backend failure                                        |

Errors do not reflect submitted paths or raw exceptions. Server logs retain diagnostics.
No Entra registration is required for trusted-local mode. It is not authentication:
shared hosts, LAN listeners, container port publishing, proxies, dev tunnels and port
forwarding are unsupported. A local forwarding process can appear to be a local caller.
Dataset writers must be trusted; file replacement races are not prevented.

## 📤 Output

Both the CLI and HTTP API produce records of this shape (one per
episode):

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
