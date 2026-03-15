# Synthetic Data Generation

SDG pipeline architecture for generating photorealistic training data from simulation using NVIDIA Cosmos world foundation models.

## Status

Planned — placeholder for future implementation.

## Components

| Component | Description |
|-----------|-------------|
| Cosmos Transfer 2.5 | Sim-to-real image transformation for photorealistic frame generation |
| Cosmos Predict 2.5 | Future frame prediction for temporal data augmentation |
| Cosmos Reason 2 | Quality assessment and curation of synthetic datasets |
| SDG Pipeline | End-to-end orchestration chaining Transfer, Predict, and Reason |

## Pipeline Architecture

| Stage | Input | Output | Model |
|-------|-------|--------|-------|
| Render | Isaac Sim scene | Simulation frames | Isaac Sim |
| Transfer | Simulation frames | Photorealistic frames | Cosmos Transfer 2.5 |
| Predict | Photorealistic frames | Future frame sequences | Cosmos Predict 2.5 |
| Reason | Generated frames | Curated training dataset | Cosmos Reason 2 |

## Orchestration

SDG workflows run on GPU-equipped AKS nodes via OSMO or AzureML. OSMO workflows use Jinja templates for parameterization. AzureML jobs follow the standard `commandJob` schema.

## Requirements

| Requirement | Value |
|-------------|-------|
| GPU | Minimum A100 (40 GB) per model stage |
| Container registry | nvcr.io (NVIDIA NGC) |
| EULA | `ACCEPT_EULA: "Y"` required for all NVIDIA containers |
| Storage | Azure Blob Storage for input/output datasets |
