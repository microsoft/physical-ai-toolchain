# Azure Infrastructure

Azure resource provisioning for the Physical AI Toolchain deployment.

## Components

| Component | Resource Type | Purpose |
|-----------|--------------|---------|
| Resource Group | `azurerm_resource_group` | Container for all deployed resources |
| Storage Account | `azurerm_storage_account` | Training data, checkpoints, model artifacts |
| Container Registry | `azurerm_container_registry` | Custom container images for training and inference |
| Key Vault | `azurerm_key_vault` | Secrets, certificates, encryption keys |
| Azure ML Workspace | `azurerm_machine_learning_workspace` | Experiment tracking, model registry, compute management |
| Log Analytics | `azurerm_log_analytics_workspace` | Centralized logging and diagnostics |

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `environment` | Deployment environment (`dev`, `test`, `prod`) | Required |
| `location` | Azure region | Required |
| `resource_prefix` | Prefix for resource naming | Required |
| `instance` | Instance identifier (e.g., `001`) | `001` |
| `should_create_resource_group` | Create or reference existing resource group | `true` |
| `should_enable_purge_protection` | Key Vault purge protection | `false` |

Resource naming follows the pattern: `{abbreviation}-{resource_prefix}-{environment}-{instance}`.

## Dependencies

This specification is the foundation for all other specifications:

- Kubernetes Setup depends on the resource group, VNet, and container registry
- Network Topology depends on the resource group and location
- Observability depends on Log Analytics workspace
- Identity and Access depends on Key Vault and managed identity infrastructure
