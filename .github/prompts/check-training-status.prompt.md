---
description: 'Check status, stream logs, and retrieve Azure ML metrics for a running or completed OSMO LeRobot training job'
agent: OSMO Training Manager
argument-hint: "[workflowId=...] [metrics] [summary]"
---

# Check Training Status

## Inputs

* ${input:workflowId}: (Optional) OSMO workflow ID. When omitted, uses the most recent workflow ID from session memory or lists recent workflows.
* ${input:metrics:false}: (Optional, defaults to false) Retrieve Azure ML training metrics in addition to OSMO status.
* ${input:summary:false}: (Optional, defaults to false) Generate a full training summary (implies metrics retrieval).

## Requirements

1. If no workflow ID is provided, check session memory for a stored workflow ID. If none found, run `osmo workflow list --status running --json` and `osmo workflow list --status completed --json` to find recent LeRobot training workflows.
2. Run `osmo workflow query <workflow-id>` to get current status.
3. Tail recent logs with `osmo workflow logs <workflow-id> -n 50`.
4. Parse logs for training progress: current step, loss values, checkpoint saves.
5. If `metrics` is true or the workflow is completed, retrieve Azure ML metrics using the Python SDK pattern from the skill reference.
6. If `summary` is true, generate a full training summary following Phase 4 of the agent protocol.
7. Report findings and offer next actions based on workflow state.
