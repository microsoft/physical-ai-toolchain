---
description: 'Launch the dataviewer app with optional dataset path, open in Playwright browser'
agent: Dataviewer Developer
argument-hint: "[datasetPath=...] [backendPort=8000] [frontendPort=5173]"
---

# Start Dataviewer

## Inputs

* ${input:datasetPath}: (Optional) Absolute path to the datasets directory. Each subdirectory is a dataset. When provided, updates `backend/.env` before launch.
* ${input:backendPort:8000}: (Optional, defaults to 8000) Backend API port.
* ${input:frontendPort:5173}: (Optional, defaults to 5173) Frontend dev server port.

## Requirements

1. If datasetPath is provided, update `DATA_DIR` in `data-management/viewer/backend/.env` to the absolute path.
2. Start the dataviewer app using `data-management/viewer/start.sh` with configured ports.
3. Wait for the backend health check to pass.
4. Open `http://localhost:${frontendPort}` using `open_browser_page`. If Playwright MCP tools are available, take a snapshot instead.
5. Report the loaded datasets and episode counts.
