---
name: submit-lerobot-training
description: 'Submit a LeRobot imitation learning training job to OSMO with configurable policy, dataset, and Azure ML logging. Use when the user runs /submit-lerobot-training.'
argument-hint: "dataset=... [policy={act|diffusion}] [steps=100000] [from-blob] [register=model-name]"
disable-model-invocation: true
---

# Submit LeRobot Training

This workflow follows Phase 1 ("Submit Training Job") of the [OSMO Training Manager agent](../../agents/osmo-training-manager.agent.md) and the [osmo-lerobot-training skill](../osmo-lerobot-training/SKILL.md). Run it from that agent when available; from another agent, read both files first.

## Arguments

Parse arguments from the request as `name=value` pairs; a bare `from-blob` sets `fromBlob=true`. Use the default when an argument is omitted.

* `dataset` (**required**): HuggingFace dataset repository ID (e.g., `lerobot/aloha_sim_insertion_human`) or local dataset name when using `--from-blob`.
* `policy` (optional, default `act`): Policy architecture — `act` or `diffusion`.
* `steps` (optional, default `100000`): Total training steps.
* `batchSize` (optional, default `32`): Training batch size.
* `learningRate` (optional, default `1e-4`): Optimizer learning rate.
* `fromBlob` (optional, default `false`): Use Azure Blob Storage as data source.
* `storageAccount` (optional): Azure Storage account name for blob data source.
* `blobPrefix` (optional): Blob path prefix for dataset files.
* `register` (optional): Model name for Azure ML checkpoint registration.
* `jobName` (optional): Custom job identifier.

## Requirements

1. Validate prerequisites: OSMO CLI authenticated, Azure CLI logged in, Terraform outputs accessible.
2. Submit the training job using `scripts/submit-osmo-lerobot-training.sh` with the provided parameters.
3. Capture and report the workflow ID from the submission output.
4. Keep the workflow ID, job name, experiment name, and model registration name in the conversation for monitoring. Later steps can recover them with `osmo workflow list`.
5. After submission, offer to monitor progress and stream logs with `/check-training-status`.
