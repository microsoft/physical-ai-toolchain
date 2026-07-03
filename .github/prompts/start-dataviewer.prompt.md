---
description: 'Launch the dataviewer app with optional dataset path, open in Playwright browser, and optionally run the VLM judge'
agent: Dataviewer Developer
argument-hint: "[datasetPath=...] [backendPort=8000] [frontendPort=5173] [runVlmJudge=false] [vlmDatasets=...] [vlmBackend=echo] [vlmModelId=...] [vlmEpisodes=0,1,2]"
---

# Start Dataviewer

## Inputs

* ${input:datasetPath}: (Optional) Absolute path to the datasets directory. Each subdirectory is a dataset. When provided, updates `backend/.env` before launch.
* ${input:backendPort:8000}: (Optional, defaults to 8000) Backend API port.
* ${input:frontendPort:5173}: (Optional, defaults to 5173) Frontend dev server port.
* ${input:runVlmJudge:false}: (Optional) When `true`, enable the VLM-as-judge router and run a smoke pass after launch.
* ${input:vlmDatasets}: (Optional, comma-separated) Dataset ids to evaluate when `runVlmJudge=true` (default: all loaded datasets).
* ${input:vlmBackend:echo}: (Optional) `echo`, `qwen3-vl`, or `openai-compat`. Set the backend's `VLM_JUDGE_BACKEND` accordingly.
* ${input:vlmModelId}: (Optional) Model id for the chosen backend (default: `Qwen/Qwen3-VL-4B-Instruct`).
* ${input:vlmEpisodes:0,1,2}: (Optional, comma-separated) Episode indices to evaluate per dataset (defaults to first three).

## Requirements

1. If `datasetPath` is provided, update `DATA_DIR` in `data-management/viewer/backend/.env` to the absolute path.
2. If `runVlmJudge=true`, also update `backend/.env` with `VLM_JUDGE_ENABLED=true`, `VLM_JUDGE_BACKEND=${vlmBackend}`, and (when set) `VLM_JUDGE_MODEL_ID=${vlmModelId}`. Restart any running backend so env changes take effect.
3. Start the dataviewer app using `data-management/viewer/start.sh` with configured ports.
4. Wait for the backend health check to pass.
5. Open `http://localhost:${frontendPort}` using `open_browser_page`. If Playwright MCP tools are available, take a snapshot instead.
6. Report the loaded datasets and episode counts.
7. When `runVlmJudge=true`, follow Phase 5 of the Dataviewer Developer agent:
    * Probe `GET /api/datasets/{id}/episodes/0/judge` for each dataset in `vlmDatasets` (or all loaded datasets when omitted) to confirm the router is mounted.
    * For each `(dataset, episode)` pair in the cross-product of `vlmDatasets` and `vlmEpisodes`, run the judge via the CLI (`python -m evaluation.vlm_judge.run --dataset datasets/{id} --indices {idx} --backend ${vlmBackend} --output outputs/vlm-judge/{id}.jsonl`).
    * Verify one representative episode in the UI by navigating Playwright to the Trajectory tab and waiting for the outcome badge.
    * Summarize: success rate, mean VOC, any 4xx/5xx responses, and the JSONL output paths.
