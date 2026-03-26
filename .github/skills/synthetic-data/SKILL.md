---
name: synthetic-data
description: 'Generate synthetic training data using NVIDIA Cosmos world foundation models for SDG pipelines'
---

# Synthetic Data Skill

Generate photorealistic training data using NVIDIA Cosmos world foundation models — Cosmos Transfer, Cosmos Predict, and Cosmos Reason.

## Overview

The Synthetic Data domain provides SDG pipelines that transform simulation-rendered frames into photorealistic training data, predict future environment states, and curate output for training quality.

## Pipeline Stages

| Stage | Model | Purpose |
|-------|-------|---------|
| Transfer | Cosmos Transfer 2.5 | Convert Isaac Sim renders to photorealistic images |
| Predict | Cosmos Predict 2.5 | Generate future frame sequences from observations |
| Reason | Cosmos Reason 2 | Assess data quality and filter training samples |

## Workflow Submission

SDG workflows can be submitted via OSMO or AzureML:

- **OSMO workflows**: `synthetic-data/workflows/osmo/`
- **AzureML jobs**: `synthetic-data/workflows/azureml/`

## Key Files

| File | Purpose |
|------|---------|
| `synthetic-data/README.md` | Domain overview and directory structure |
| `synthetic-data/workflows/osmo/sdg-pipeline.yaml` | End-to-end OSMO SDG pipeline |
| `synthetic-data/cosmos/configs/README.md` | Model configuration reference |
| `synthetic-data/specifications/synthetic-data.specification.md` | SDG pipeline specification |
| `synthetic-data/specifications/cosmos-integration.specification.md` | Cosmos model integration specification |

## Environment Requirements

All NVIDIA Cosmos containers require:

| Variable | Value |
|----------|-------|
| `ACCEPT_EULA` | `Y` |
| `PRIVACY_CONSENT` | `Y` |
| `NVIDIA_DRIVER_CAPABILITIES` | `all` |

## GPU Requirements

Each Cosmos model stage requires a minimum of 1x A100 (40 GB) GPU. H100 (80 GB) recommended for production workloads.
