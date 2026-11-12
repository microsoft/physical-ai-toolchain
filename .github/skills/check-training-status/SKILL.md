---
name: check-training-status
description: 'Check status, stream logs, and retrieve Azure ML metrics for a running or completed OSMO LeRobot training job. Use when the user runs /check-training-status.'
argument-hint: "[workflowId=...] [metrics] [summary]"
disable-model-invocation: true
---

# Check Training Status

This workflow follows Phases 2–4 of the [OSMO Training Manager agent](../../agents/osmo-training-manager.agent.md) and the [osmo-lerobot-training skill](../osmo-lerobot-training/SKILL.md). Run it from that agent when available; from another agent, read both files first.

## Arguments

Parse arguments from the request as `name=value` pairs; a bare `metrics` or `summary` sets that flag to `true`. Use the default when an argument is omitted.

* `workflowId` (optional): OSMO workflow ID. When omitted, uses the most recent workflow ID mentioned in the conversation or lists recent workflows.
* `metrics` (optional, default `false`): Retrieve Azure ML training metrics in addition to OSMO status.
* `summary` (optional, default `false`): Generate a full training summary (implies metrics retrieval).

## Requirements

1. If no workflow ID is provided, use the most recent one from the conversation. If none is found, run `osmo workflow list --status running --json` and `osmo workflow list --status completed --json` to find recent LeRobot training workflows.
2. Run `osmo workflow query <workflow-id>` to get current status.
3. Tail recent logs with `osmo workflow logs <workflow-id> -n 50`.
4. Parse logs for training progress: current step, loss values, checkpoint saves.
5. If `metrics` is true or the workflow is completed, retrieve Azure ML metrics using the Python SDK pattern from the skill reference.
6. If `summary` is true, generate a full training summary following Phase 4 ("Generate Training Summary") of the OSMO Training Manager agent.
7. Report findings and offer next actions based on workflow state.
