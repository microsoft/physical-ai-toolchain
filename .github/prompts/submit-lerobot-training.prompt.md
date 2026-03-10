---
description: 'Submit a LeRobot imitation learning training job to OSMO with configurable policy, dataset, and Azure ML logging'
agent: OSMO Training Manager
argument-hint: "dataset=... [policy={act|diffusion}] [steps=100000] [from-blob] [register=model-name]"
---

# Submit LeRobot Training

## Inputs

* ${input:dataset}: (Required) HuggingFace dataset repository ID (e.g., `lerobot/aloha_sim_insertion_human`) or local dataset name when using `--from-blob`.
* ${input:policy:act}: (Optional, defaults to `act`) Policy architecture — `act` or `diffusion`.
* ${input:steps:100000}: (Optional, defaults to 100000) Total training steps.
* ${input:batchSize:32}: (Optional, defaults to 32) Training batch size.
* ${input:learningRate:1e-4}: (Optional, defaults to 1e-4) Optimizer learning rate.
* ${input:fromBlob:false}: (Optional, defaults to false) Use Azure Blob Storage as data source.
* ${input:storageAccount}: (Optional) Azure Storage account name for blob data source.
* ${input:blobPrefix}: (Optional) Blob path prefix for dataset files.
* ${input:register}: (Optional) Model name for Azure ML checkpoint registration.
* ${input:jobName}: (Optional) Custom job identifier.

## Requirements

1. Validate prerequisites: OSMO CLI authenticated, Azure CLI logged in, Terraform outputs accessible.
2. Submit the training job using `scripts/submit-osmo-lerobot-training.sh` with the provided parameters.
3. Capture and report the workflow ID from the submission output.
4. Store the workflow ID in session memory for monitoring.
5. After submission, offer to monitor progress and stream logs.
