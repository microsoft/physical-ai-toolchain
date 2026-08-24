# Retraining

Retraining pipeline triggered by drift detection signals, the loop-closure component of the
fleet-intelligence cognition layer (T5 — Operate).

## Status

Planned: placeholder for future implementation. Part of the roadmap **fleet intelligence** layer (T5);
this domain ships 0 Python files and design specs only.

## Autonomy Ladder Mapping

Loop closure here is **not** a single leap. It decomposes onto the
[autonomy ladder (T5.0–T5.3)](../../docs/design/tier-model.md#the-autonomy-ladder-t50t53), graded by how
much of the retraining decision a human delegates:

| Rung | Decision authority                                                 | Human role                 | Status       |
|------|--------------------------------------------------------------------|----------------------------|--------------|
| T5.0 | Gated retraining: system surfaces signals only.                    | Human triggers every cycle | Not built    |
| T5.1 | Human-in-the-loop / active learning: system proposes what/when.    | Human approves each cycle  | Ad-hoc (Hex) |
| T5.2 | Continual learning: system retrains on a schedule or trigger.      | Human reviews pre-deploy   | Not built    |
| T5.3 | Autonomous closed-loop: system detects, retrains, gates, deploys.  | None (fully autonomous)    | Not built    |

The "Trigger Criteria" below (manual override, scheduled, drift-driven) are the mechanisms that realize
these rungs; the rung in effect is determined by who holds the trigger authority, not by the mechanism.

> [!WARNING]
> Fully autonomous retraining on production data is a foot-gun: a legitimate distribution change can
> cause the loop to bake current degraded behavior into the next dataset, and drift detection needs
> statistical power that only exists at fleet scale. This pipeline should default to human-supervised
> stages (T5.0–T5.1), not the autonomous closed loop (T5.3). T5.3 stays a roadmap direction.

## Components

| Component         | Description                                                                    |
|-------------------|--------------------------------------------------------------------------------|
| Trigger Evaluator | Assesses drift signals against retraining thresholds                           |
| Pipeline Launcher | Submits training jobs to AzureML or OSMO                                       |
| Dataset Selector  | Identifies appropriate training data including recent episodes                 |
| Model Validator   | Runs SiL evaluation on retrained checkpoints before promotion                  |
| Deployment Gate   | Hands validated models to the T4 fleet-delivery pipeline for rollout to robots |

## Trigger Criteria

| Signal                | Threshold                                     | Action              |
|-----------------------|-----------------------------------------------|---------------------|
| Composite drift score | Above configurable limit for sustained period | Initiate retraining |
| Task success rate     | Below minimum acceptable threshold            | Initiate retraining |
| Manual override       | Operator request                              | Initiate retraining |
| Scheduled             | Periodic cadence regardless of drift          | Initiate retraining |
