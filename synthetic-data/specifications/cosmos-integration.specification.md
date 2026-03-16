# Cosmos Integration

Integration specification for NVIDIA Cosmos world foundation models within the Physical AI Toolchain SDG pipeline.

## Status

Planned — placeholder for future implementation.

## Models

| Model           | Version | Purpose                              |
|-----------------|---------|--------------------------------------|
| Cosmos Transfer | 2.5     | Sim-to-real image style transfer     |
| Cosmos Predict  | 2.5     | Future frame generation              |
| Cosmos Reason   | 2       | Data quality assessment and curation |

## Container Images

| Model               | Registry | Image | Tag |
|---------------------|----------|-------|-----|
| Cosmos Transfer 2.5 | nvcr.io  | TBD   | TBD |
| Cosmos Predict 2.5  | nvcr.io  | TBD   | TBD |
| Cosmos Reason 2     | nvcr.io  | TBD   | TBD |

## Environment Variables

All Cosmos containers require:

| Variable                     | Value | Description                |
|------------------------------|-------|----------------------------|
| `ACCEPT_EULA`                | `Y`   | NVIDIA EULA acceptance     |
| `PRIVACY_CONSENT`            | `Y`   | NVIDIA privacy consent     |
| `NVIDIA_DRIVER_CAPABILITIES` | `all` | Full GPU capability access |

## Integration Patterns

### OSMO Workflows

Cosmos workflows use OSMO dataset injection for input/output data. Workflow YAML uses Jinja templates (`{{ }}`) for parameterization.

### AzureML Jobs

Cosmos jobs use the `commandJob` schema with GPU instance types. Code snapshots and model artifacts follow the standard AzureML patterns.

## External References

| Resource               | URL                                            |
|------------------------|------------------------------------------------|
| NVIDIA Cosmos Platform | <https://developer.nvidia.com/cosmos>          |
| Cosmos Transfer 2.5    | <https://github.com/NVIDIA/Cosmos-Transfer2.5> |
| Cosmos Predict 2.5     | <https://github.com/NVIDIA/Cosmos-Predict2.5>  |
| Cosmos Cookbook        | <https://github.com/NVIDIA/Cosmos-Cookbook>    |
