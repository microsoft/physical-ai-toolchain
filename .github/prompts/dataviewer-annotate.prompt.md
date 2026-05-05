---
description: 'One-shot: launch the dataviewer (with VLM judge enabled) and run the multi-step VLM-as-judge pipeline across an entire dataset.'
agent: Dataviewer Developer
argument-hint: "<datasetId> [mode=vlm-judge] [backend=qwen3-vl|echo|openai-compat] [modelId=...] [baseUrl=...] [persistLabels=true|false] [limit=N]"
---

# Dataviewer Annotate

Launch the dataviewer, ensure the VLM-as-judge harness is enabled, and walk an entire dataset, scoring every episode with the chosen judge backend. Designed for fast operator triage — one chat command, no other plumbing.

## Inputs

* ${input:datasetId}: **Required.** Subdirectory name under `DATA_DIR`. Aliases accepted (`leisaac-pickup-orange` → `leisaac-pick-orange`).
* ${input:mode:vlm-judge}: Annotation mode. Currently only `vlm-judge` is supported. Reserved for future modes (e.g. `joint-trajectory`).
* ${input:backend:qwen3-vl}: One of `qwen3-vl`, `echo`, or `openai-compat`. With `qwen3-vl`, this prompt auto-launches the local OpenAI-compat shim so the dataviewer talks to the model via HTTP without colocating Torch in the dataviewer venv.
* ${input:modelId:Qwen/Qwen3-VL-4B-Instruct}: Hugging Face id (`qwen3-vl`) or remote model name (`openai-compat`).
* ${input:baseUrl}: OpenAI-compatible endpoint when `backend=openai-compat`; ignored otherwise.
* ${input:persistLabels:true}: When `true` and the model returns a binary outcome with confidence ≥ 0.7, set the dataviewer label to `SUCCESS` / `FAILURE` (or `PARTIAL` for mid-confidence). Skipped on `outcome_success: null`.
* ${input:limit:0}: Cap on number of episodes; `0` means the entire dataset.

## Requirements

Drive the **Dataviewer Developer** agent through Phase 5 ("VLM-as-Judge Evaluation") with these specific instructions. Re-use any running shim/dataviewer if their endpoints respond — do not relaunch unless required.

1. **Resolve aliases** for `datasetId`:
    * `leisaac-pickup-orange` → `leisaac-pick-orange`
    * `cnc` → `cnc_lerobot`
    * `ur10e` → `ur10e_episodes`
    * Otherwise pass through verbatim.
    Confirm the resolved id exists as `${DATA_DIR}/<id>` before continuing.

2. **Configure `data-management/viewer/backend/.env`** to enable the judge:

    ```env
    DATA_DIR=<absolute path to repo>/datasets
    DATAVIEWER_AUTH_DISABLED=true

    VLM_JUDGE_ENABLED=true
    VLM_JUDGE_BACKEND=${backend}
    VLM_JUDGE_MODEL_ID=${modelId}
    VLM_JUDGE_BASE_URL=<resolve below>
    VLM_JUDGE_API_KEY=EMPTY
    VLM_JUDGE_N_FRAMES=12
    VLM_JUDGE_CACHE_DIR=outputs/vlm-judge/cache
    ```

    Resolve `VLM_JUDGE_BASE_URL`:
    * `backend=qwen3-vl` → `http://127.0.0.1:8001/v1` (the shim) and rewrite `VLM_JUDGE_BACKEND=openai-compat` so the dataviewer's lightweight venv doesn't try to load Torch.
    * `backend=openai-compat` → `${baseUrl}` exactly as provided. Fail fast if missing.
    * `backend=echo` → leave unset.

3. **Launch the model server** when `backend=qwen3-vl`:
    * Probe `GET http://127.0.0.1:8001/health`. If 200, reuse it.
    * Otherwise start the shim in a background terminal from the **root** `.venv` (it has Torch + transformers):

        ```bash
        cd <repo> && source .venv/bin/activate && \
          PYTHONPATH="$PWD:$PWD/evaluation" \
          python -m evaluation.vlm_judge.openai_shim --port 8001 --model-id ${modelId}
        ```

    * Wait for `/health` to return `{"status":"ok"}` before continuing.

4. **Launch (or refresh) the dataviewer**:
    * Probe `GET http://localhost:8000/health`. If reachable AND `GET /api/datasets/<id>/episodes/0/judge` returns `enabled: true`, reuse it.
    * Otherwise: stop any stale uvicorn/vite, then run

        ```bash
        cd data-management/viewer && set -a && source backend/.env && set +a && ./start.sh
        ```

      `start.sh` already exports `PYTHONPATH` to include the `evaluation` package when `VLM_JUDGE_ENABLED=true`.

5. **Open the dataviewer UI** at `http://localhost:5173`:
    * Try `open_browser_page("http://localhost:5173")` first; if SimpleBrowser flakes, fall back to `mcp_playwright_browser_navigate`.
    * Switch to the resolved dataset via the header combobox (use the `[role="option"]` JS pattern documented in the dataviewer skill).

6. **Iterate the dataset**:
    * Fetch `/api/datasets/<id>/episodes?limit=1000` to enumerate. Apply `limit` if non-zero.
    * For each episode index `idx`:
       1. Probe `GET /api/datasets/<id>/episodes/<idx>/judge` (cache check).
       2. If `result == null`, `POST` it with `{"force": false}` and the dataviewer's CSRF cookie/header.
       3. When `persistLabels` is true and `result.outcome_success` is not `null`, derive the label:
          * `outcome_success == true` and `outcome_confidence >= 0.7` → `SUCCESS`
          * `outcome_success == false` and `outcome_confidence >= 0.7` → `FAILURE`
          * otherwise → `PARTIAL`
          Then `PUT /api/datasets/<id>/episodes/<idx>/labels` with the chosen label, followed by `POST /api/datasets/<id>/labels/save` once at the end.
    * Stream progress to the user every ~10 episodes (e.g. `15/60 done — 11 SUCCESS, 4 FAILURE, mean VOC 0.62`).

7. **Verify in the UI** by Playwright-navigating to a randomly chosen sample episode in the resolved dataset and asserting the **VLM Judge** panel renders an outcome badge.

8. **Report**:
    * Total episodes scored, success rate, mean VOC, mean inference latency.
    * Distribution of `failure_mode` values.
    * Output cache directory (`outputs/vlm-judge/cache/`) and how many entries it now holds.
    * Any `4xx`/`5xx` responses with the offending episode index.
    * URL to the running UI for hand-review.

## Recovery hints

* **502 "VLM backend error: 422 …"** from the dataviewer means the shim's request body model isn't being recognised — restart the shim; the fix is already in `evaluation/vlm_judge/openai_shim.py` (module-scope Pydantic models).
* **Process format violation** (model returns wrong-length array) is a known soft failure: the harness logs a warning and falls back to zeros for that episode. Continue.
* **Milestone format violation** for `{"milestones": []}` is cosmetic; the model legitimately found no milestones and the parser is intentionally strict.
* **No GPU memory** (`CUDA OOM`): retry with a smaller `modelId` (e.g. `Qwen/Qwen3-VL-2B-Instruct`) or offload to `openai-compat` against an external endpoint.
