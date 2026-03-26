# Cosmos Model Configurations

Model-specific configuration templates for NVIDIA Cosmos world foundation models. Each configuration defines model parameters, input/output formats, and GPU requirements.

## Planned Configurations

| Configuration     | Model               | Description                                       |
|-------------------|---------------------|---------------------------------------------------|
| Transfer defaults | Cosmos Transfer 2.5 | Style transfer parameters and resolution settings |
| Predict defaults  | Cosmos Predict 2.5  | Prediction horizon and conditioning parameters    |
| Reason defaults   | Cosmos Reason 2     | Quality thresholds and filtering criteria         |

## GPU Requirements

| Model               | Minimum GPU     | Recommended GPU |
|---------------------|-----------------|-----------------|
| Cosmos Transfer 2.5 | 1x A100 (40 GB) | 1x H100 (80 GB) |
| Cosmos Predict 2.5  | 1x A100 (40 GB) | 1x H100 (80 GB) |
| Cosmos Reason 2     | 1x A100 (40 GB) | 1x H100 (80 GB) |
