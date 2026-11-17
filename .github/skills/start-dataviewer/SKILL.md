---
name: start-dataviewer
description: 'Launch the dataviewer app with an optional dataset path, open it in the browser, and optionally run a VLM judge smoke pass. Use when the user runs /start-dataviewer.'
argument-hint: "[datasetPath=...] [backendPort=8000] [frontendPort=5173] [runVlmJudge=false] [vlmDatasets=...] [vlmBackend=echo] [vlmModelId=...] [vlmEpisodes=0,1,2]"
disable-model-invocation: true
---

# Start Dataviewer

This workflow follows the phases of the [Dataviewer Developer agent](../../agents/dataviewer-developer.agent.md) and the [dataviewer skill](../dataviewer/SKILL.md). Run it from that agent when available; from another agent, read both files first.

## Arguments

Parse arguments from the request as `name=value` pairs. Use the default when an argument is omitted.

* `datasetPath` (optional): Absolute path to the datasets directory. Each subdirectory is a dataset. Pass it through `--data-dir` without changing persistent defaults.
* `backendPort` (optional, default `8000`): Backend API port.
* `frontendPort` (optional, default `5173`): Frontend dev server port.
* `runVlmJudge` (optional, default `false`): When `true`, enable the VLM-as-judge router and run a smoke pass after launch.
* `vlmDatasets` (optional, comma-separated): Dataset ids to evaluate when `runVlmJudge=true`. Defaults to all loaded datasets.
* `vlmBackend` (optional, default `echo`): `echo`, `qwen3-vl`, or `openai-compat`. Set the backend's `VLM_JUDGE_BACKEND` accordingly.
* `vlmModelId` (optional, default `Qwen/Qwen3-VL-4B-Instruct`): Model id for the chosen backend.
* `vlmEpisodes` (optional, comma-separated, default `0,1,2`): Episode indices to evaluate per dataset.

## Requirements

1. If `datasetPath` is provided, verify it and pass it to `data-management/viewer/start.sh` through `--data-dir`. Follow the dataviewer skill's accepted-dataset checks when a descriptor is present.
2. If `runVlmJudge=true`, pass `VLM_JUDGE_ENABLED=true`, `VLM_JUDGE_BACKEND=<vlmBackend>`, and any specified `VLM_JUDGE_MODEL_ID` to the launcher process. Persist settings in `backend/.env` only when the user explicitly requests defaults across restarts.
3. Start the app with the configured ports. Reuse an existing instance only when its dataset and launch settings match; do not stop a process this workflow does not own.
4. Wait for both backend and frontend readiness checks to pass.
5. Open `http://localhost:<frontendPort>` in the browser. Find browser tools with the host's tool search as described in the dataviewer skill's "Load browser automation tools" step: prefer the integrated browser (`open_browser_page`), and when only Playwright MCP tools are available, navigate and take a snapshot instead.
6. Report the loaded datasets and episode counts.
7. When `runVlmJudge=true`, follow Phase 5 of the Dataviewer Developer agent:
    * Probe `GET /api/datasets/{id}/capabilities` for each selected dataset and require `vlm_judge_enabled: true` before requesting judgments.
        * For each explicitly requested smoke episode, resolve the dataset root, principal scope and durable job root from the active viewer.
            Use `python -m physical_ai_curation.run --dataset <resolved-root> --dataset-id {id} --principal-scope-id <active-scope> --job-dir <active-job-root> --indices {idx} --single --backend <vlmBackend> --output outputs/vlm-judge/{id}.jsonl`, carrying the same configured model/revision and runtime settings.
            Follow the dataviewer skill's sample/approval workflow for dataset runs; do not fan out single requests to bypass approval.
    * Verify one representative episode in the UI by navigating the browser to the Trajectory tab and waiting for the outcome badge.
    * Summarize: success rate, mean VOC, any 4xx/5xx responses, and the JSONL output paths.
