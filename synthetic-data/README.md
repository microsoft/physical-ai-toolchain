# Synthetic Data

Synthetic data generation (SDG) pipelines leveraging NVIDIA Cosmos world foundation models. Transforms simulation outputs into photorealistic training data, predicts future environment states, and reasons over generated data for quality curation.

## 📂 Directory Structure

| Directory          | Purpose                                               |
|--------------------|-------------------------------------------------------|
| `workflows/osmo/`  | OSMO workflow definitions for Cosmos SDG jobs         |
| `workflows/azureml/` | AzureML job definitions for Cosmos SDG jobs         |
| `cosmos/transfer/` | Cosmos Transfer 2.5 sim-to-real image transformation  |
| `cosmos/predict/`  | Cosmos Predict 2.5 future frame prediction            |
| `cosmos/reason/`   | Cosmos Reason 2 data curation and quality assessment  |
| `cosmos/configs/`  | Model-specific configuration templates                |
| `examples/`        | SDG pipeline examples and reference configurations    |
| `specifications/`  | Domain specifications for agent skills                |

## Quick Start

> This domain is under active development. Placeholder files define the planned structure and integration points.

The SDG pipeline chains three NVIDIA Cosmos capabilities:

1. **Cosmos Transfer** — Convert simulation-rendered frames into photorealistic images
2. **Cosmos Predict** — Generate plausible future frames from current observations
3. **Cosmos Reason** — Assess and curate generated data for training quality

## External References

| Resource | URL |
|----------|-----|
| NVIDIA Cosmos Platform | <https://developer.nvidia.com/cosmos> |
| Cosmos Transfer 2.5 | <https://github.com/NVIDIA/Cosmos-Transfer2.5> |
| Cosmos Predict 2.5 | <https://github.com/NVIDIA/Cosmos-Predict2.5> |
| Cosmos Cookbook | <https://github.com/NVIDIA/Cosmos-Cookbook> |
