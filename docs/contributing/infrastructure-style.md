---
sidebar_position: 7
title: Infrastructure as Code Style Guide
description: Terraform conventions, shell script standards, and copyright headers for contributions
author: Microsoft Robotics-AI Team
ms.date: 2026-02-03
ms.topic: reference
---

> [!NOTE]
> This guide expands on the [Infrastructure as Code Style](README.md#infrastructure-as-code-style) section of the main contributing guide.

Infrastructure code follows strict conventions for consistency, security, and maintainability.

## Terraform Conventions

### Formatting

```bash
# Format all Terraform files before committing
terraform fmt -recursive infrastructure/terraform/

# Validate syntax
terraform validate infrastructure/terraform/
```

### Variable Naming

* Use descriptive snake_case: `gpu_node_pool_vm_size` not `vm_sku`
* Prefix booleans with `enable_` or `is_`: `enable_private_endpoints`, `is_production`
* Group related variables with prefixes: `aks_cluster_name`, `aks_node_count`, `aks_version`

### Module Structure

Each Terraform module must include:

```text
modules/
  module-name/
    main.tf          # Resource definitions
    variables.tf     # Input variables with descriptions and types
    outputs.tf       # Output values
    versions.tf      # Provider version constraints
    README.md        # Module documentation
```

### Resource Tagging

All Azure resources must include standard tags:

```hcl
tags = merge(
  var.common_tags,
  {
    environment = var.environment
    workload    = "robotics-ml"
    managed_by  = "terraform"
    cost_center = var.cost_center
  }
)
```

### Security Patterns

* Prefer managed identities over service principals
* Use workload identity for Kubernetes pod authentication
* Enable private endpoints for production network mode
* Store secrets in Azure Key Vault, never in code or `.tfvars` files
* Apply minimum RBAC roles (avoid `Owner` unless required)

### Example

```hcl
resource "azurerm_kubernetes_cluster" "aks" {
  name                = "aks-${var.environment}-${var.location}"
  location            = var.location
  resource_group_name = var.resource_group_name

  default_node_pool {
    name       = "system"
    node_count = var.system_node_count
    vm_size    = "Standard_D4s_v5"
  }

  identity {
    type = "SystemAssigned"
  }

  private_cluster_enabled = var.network_mode == "private"

  tags = merge(
    var.common_tags,
    {
      component = "aks-cluster"
    }
  )
}
```

## Shell Script Conventions

### Shebang and Error Handling

Every shell script must begin with:

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
```

### Script Documentation

Include header documentation:

```bash
#!/usr/bin/env bash
# Deploy OSMO backend operator to AKS cluster
#
# Prerequisites:
#   - AKS cluster with GPU node pool deployed
#   - OSMO control plane installed (03-deploy-osmo-control-plane.sh)
#   - kubectl configured with AKS credentials
#
# Environment Variables:
#   RESOURCE_GROUP_NAME: Azure resource group name (required)
#   AKS_CLUSTER_NAME: AKS cluster name (required)
#   OSMO_VERSION: OSMO version to deploy (default: 6.0.0)
#
# Usage:
#   export RESOURCE_GROUP_NAME="rg-robotics-prod"
#   export AKS_CLUSTER_NAME="aks-robotics-prod"
#   ./04-deploy-osmo-backend.sh
```

### Validation

```bash
# Lint all shell scripts before committing
shellcheck deploy/**/*.sh scripts/**/*.sh

# Check specific script
shellcheck -x infrastructure/setup/01-deploy-robotics-charts.sh
```

### Configuration Management

* Use configuration files (`.conf`, `.env`) for environment-specific values
* Validate required environment variables at script start:

```bash
: "${RESOURCE_GROUP_NAME:?Environment variable RESOURCE_GROUP_NAME must be set}"
: "${AKS_CLUSTER_NAME:?Environment variable AKS_CLUSTER_NAME must be set}"
```

* Provide sensible defaults for optional variables:

```bash
OSMO_VERSION="${OSMO_VERSION:-6.0.0}"
LOG_LEVEL="${LOG_LEVEL:-info}"
```

For complete shell script guidance, see [shell-scripts.instructions.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/.github/instructions/shell-scripts.instructions.md).

## Copyright Headers

All new source files must include the Microsoft copyright header.

### Format

```text
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
```

### Language-Specific Examples

**Python (.py):**

```python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Module docstring."""

import os
```

**Terraform (.tf):**

```hcl
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

terraform {
  required_version = ">= 1.9.8"
}
```

**Shell Script (.sh):**

```bash
#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

set -euo pipefail
```

**YAML (.yaml, .yml):**

```yaml
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

apiVersion: v1
kind: ConfigMap
```

### Placement

* Place immediately after shebang line in executable scripts
* Place at the top of the file for other file types
* Include blank line between copyright header and code

## Related Documentation

* [Contributing Guide](README.md) - Prerequisites, workflow, commit messages
* [Deployment Validation](deployment-validation.md) - Validation levels and testing
* [Security Review](security-review.md) - Security checklist and patterns
* [Shell Scripts Instructions](https://github.com/microsoft/physical-ai-toolchain/blob/main/.github/instructions/shell-scripts.instructions.md) - Detailed shell script guidance
