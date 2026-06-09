# VLM-as-Judge

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

| File / dir                          | Purpose                                                       |
|-------------------------------------|---------------------------------------------------------------|
| `dataset.py`                        | LeRobot v2.1 + v3.0 episode discovery and instruction lookup  |
| `frames.py`                         | PyAV frame extraction with multi-view tiling                  |
| `prompts.py`                        | Outcome MCQ + GVL + milestone + failure-mode templates        |
| `backend.py`                        | Qwen3-VL HF backend, OpenAI-compatible HTTP backend, echo     |
| `judge.py`                          | `score_episode` + `JudgeResult` + Spearman VOC                |
| `agent.py`                          | `JudgeAgent` multi-step controller                            |
| `cache.py`                          | SHA256-keyed JSON disk cache                                  |
| `service.py`                        | `JudgeService` — single integration surface                   |
| `api.py`                            | FastAPI router + standalone uvicorn app                       |
| `run.py`                            | CLI: judge a LeRobot dataset folder                           |
| `policy_eval.py`                    | CLI: judge a directory of policy-rollout MP4s                 |
| `scripts/evaluate-dataset.sh`       | Generic dataset wrapper                                       |
| `scripts/evaluate-cnc-lerobot.sh`   | Wrapper for `datasets/cnc_lerobot`                            |
| `scripts/evaluate-ur10e-episodes.sh`| Wrapper for `datasets/ur10e_episodes`                         |
| `scripts/evaluate-leisaac-pick-orange.sh` | Wrapper for `datasets/leisaac-pick-orange`              |
| `scripts/evaluate-policy-rollouts.sh` | Wrapper for policy rollout dirs (`leisaac-tests/*`)         |
| `scripts/serve-api.sh`              | Launch the HTTP API                                           |

## 🚀 Quick Start

A 24GB GPU (A10 / A100 / RTX A5000) fits `Qwen/Qwen3-VL-4B-Instruct` in bfloat16.

```sh
# 1. Smoke test (no model load, no GPU)
evaluation/vlm_judge/scripts/evaluate-leisaac-pick-orange.sh \
    --backend echo --dry-run --limit 2

# 2. Real evaluation on three episodes with Qwen3-VL-4B
evaluation/vlm_judge/scripts/evaluate-leisaac-pick-orange.sh --limit 3

# 3. Score policy rollouts under leisaac-tests/pickup-orange/
evaluation/vlm_judge/scripts/evaluate-policy-rollouts.sh

# 4. Serve the HTTP API (defaults to echo backend; see env vars in --help)
evaluation/vlm_judge/scripts/serve-api.sh --backend qwen3-vl --port 8088

# 5. Full dataset against a vLLM server hosting a larger model
VLM_JUDGE_BACKEND=openai-compat \
VLM_JUDGE_MODEL_ID=Qwen/Qwen3-VL-30B-A3B-Instruct \
evaluation/vlm_judge/scripts/evaluate-cnc-lerobot.sh \
    --base-url http://localhost:8000/v1
```

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
    "front": "/data/.../observation.images.front/episode_000007.mp4",
    "wrist": "/data/.../observation.images.wrist/episode_000007.mp4"
  },
  "from_s": null,
  "to_s": null,
  "force": false
}
```

Mount inside an existing FastAPI app (e.g., the dataviewer backend):

```python
from pathlib import Path
from evaluation.vlm_judge.api import build_router
from evaluation.vlm_judge.service import (
    BackendConfig, FrameConfig, JudgeService, ServiceConfig,
)

service = JudgeService(ServiceConfig(
    backend=BackendConfig(kind="qwen3-vl", model_id="Qwen/Qwen3-VL-4B-Instruct"),
    frames=FrameConfig(n_frames=12),
    cache_dir=Path("outputs/vlm-judge/cache"),
))
app.include_router(build_router(service), prefix="/api/vlm-judge")
```

The HTTP service is intentionally framework-thin — all stateful work
happens in `JudgeService` so the dataviewer backend, an Azure Container
App, or a Kubernetes sidecar all share one Python implementation.

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

| Backend          | Use case                                                     |
|------------------|--------------------------------------------------------------|
| `qwen3-vl`       | Local Hugging Face Qwen3-VL (default `Qwen3-VL-4B-Instruct`) |
| `openai-compat`  | vLLM, NVIDIA NIM, Azure OpenAI — set `--base-url`            |
| `echo`           | Deterministic stub for offline tests                         |

`Qwen3VLBackend` resolves the correct model class against the installed
`transformers` (4.57+ ships native Qwen3-VL classes). Adopt larger
variants (`Qwen3-VL-8B-Instruct`, `Qwen3-VL-30B-A3B-Instruct`) by passing
`--model-id`. For Cosmos-Reason1 (post-trained on Qwen2.5-VL with NVIDIA
Open License), point the OpenAI-compatible backend at a Cosmos NIM or
self-hosted vLLM.

## 💾 Cache

`JudgeCache` keys results on a stable SHA256 of:

- video paths + size + mtime (per view, sorted)
- instruction text
- `judge_model` (backend `name`)
- `prompt_version`
- serialised `AgentConfig`

Cache hits return immediately and skip all VLM inference, mirroring the
checkpoint-upload idempotency convention. Default location is
`outputs/vlm-judge/cache/`. Set `--cache-dir ""` to disable, or
`--force` to bypass on a single run.

## 🧪 Tests

```sh
cd evaluation && pytest tests/vlm_judge/ -q --no-header -o addopts=""
```

The tests use a `ScriptedBackend` (canned responses keyed by prompt
fragment) and a `StubService` to validate prompt parsers, cache
behaviour, the multi-step agent controller (success / low-confidence /
failure flows + format-violation tolerance), and the FastAPI router. No
GPU or network is required.

## 🗺️ Roadmap

- **Dataviewer integration.** Mount the HTTP router inside the dataviewer
  backend so reviewers see VLM annotations inline next to human labels.
- **MLflow logging.** Persist per-episode results into MLflow so the
  existing `evaluation/metrics/bootstrap_mlflow.py` flow picks them up.
- **Cosmos-Reason1 NIM deployment.** Swap the `openai-compat` backend
  base URL onto an Azure AI Foundry NIM endpoint once the trial is
  provisioned (see `.copilot-tracking/research/2026-04-30/`).
- **Pairwise A/B mode.** Add a `compare_episodes` agent step for
  RoboArena-style policy ranking when two rollouts share a task.
