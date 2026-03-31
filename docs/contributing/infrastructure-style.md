---
sidebar_position: 7
title: Infrastructure as Code Style Guide
description: Terraform conventions, shell script standards, and copyright headers for contributions
author: Microsoft Robotics-AI Team
ms.date: 2026-03-26
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

# Validate formatting and syntax across all deployment directories
npm run lint:tf:validate
```

### Variable Naming

* Use descriptive snake_case: `gpu_node_pool_vm_size` not `vm_sku`
* Prefix booleans with `should_`: `should_enable_private_endpoints`, `should_deploy_vpn`
* Group related variables with prefixes: `aks_cluster_name`, `aks_node_count`, `aks_version`

### Module Structure

Each Terraform module must include:

```text
modules/
  module-name/
    main.tf              # Resource definitions
    variables.tf         # Input variables with descriptions and types
    variables.core.tf    # Core variables (environment, resource_prefix, instance, resource_group)
    outputs.tf           # Output values
    versions.tf          # Provider version constraints
    tests/
      setup/
        main.tf          # Mock data generator with random prefix and typed outputs
      naming.tftest.hcl  # Resource naming convention assertions
      conditionals.tftest.hcl  # should_* boolean conditional tests
      outputs.tftest.hcl # Output structure and nullability tests
```

The root deployment directory (`infrastructure/terraform/`) also has integration tests:

```text
infrastructure/terraform/
  tests/
    setup/
      main.tf                # Core variables (no resource_group — root creates its own)
    integration.tftest.hcl   # Resource group conditionals, module instantiation
    outputs.tftest.hcl       # Output presence and nullability
```

### Terraform Testing

All modules use native `terraform test` with `mock_provider` for plan-time validation. Tests require no Azure credentials.

#### Running Tests

```bash
# Run all module tests via CI script
npm run test:tf

# Run tests for a specific module
cd infrastructure/terraform/modules/platform
terraform init -backend=false
terraform test

# Run a single test file
terraform test -filter=tests/naming.tftest.hcl
```

#### Setup Module Pattern

Each module's `tests/setup/main.tf` generates mock input values with internally consistent IDs derived from a random prefix:

```hcl
locals {
  subscription_id_part = "/subscriptions/00000000-0000-0000-0000-000000000000"
  resource_prefix      = "t${random_string.prefix.id}"
  environment          = "dev"
  instance             = "001"
  resource_group_name  = "rg-${local.resource_prefix}-${local.environment}-${local.instance}"
  resource_group_id    = "${local.subscription_id_part}/resourceGroups/${local.resource_group_name}"
}

output "resource_group" {
  value = {
    id       = local.resource_group_id
    name     = local.resource_group_name
    location = "westus3"
  }
}
```

Derive all Azure resource IDs from the random prefix using locals. Do not hardcode synthetic IDs.

#### Test File Conventions

Test files use `mock_provider` to intercept all provider calls and `command = plan` for assertions:

```hcl
mock_provider "azurerm" {}
mock_provider "azapi" {}

// Override data sources that generate invalid mock values
override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "verify_naming" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    resource_group  = run.setup.resource_group
  }

  assert {
    condition     = azurerm_key_vault.main.name == "kv${run.setup.resource_prefix}${run.setup.environment}${run.setup.instance}"
    error_message = "Key Vault name must follow kv{prefix}{env}{instance}"
  }
}
```

#### Mock Provider Constraints

| Constraint                                                                              | Resolution                                                                      |
|-----------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| `data.azurerm_client_config.current` generates random strings that fail UUID validation | Add `override_data` block with valid tenant_id                                  |
| `command = apply` generates invalid Azure resource IDs for role assignments             | Use `command = plan` and assert only on input-derived attributes                |
| Computed attributes (`.id`, `.fqdn`) are unknown at plan time                           | Assert on resource count, name, and configuration values instead                |
| `file()` built-in is not intercepted by mock providers                                  | Provide a real stub file (see automation module `tests/setup/scripts/stub.ps1`) |

#### Test Categories

| File                      | Purpose                                                          |
|---------------------------|------------------------------------------------------------------|
| `naming.tftest.hcl`       | Resource names follow `{abbreviation}-{prefix}-{env}-{instance}` |
| `conditionals.tftest.hcl` | `should_*` booleans control resource creation via `count`        |
| `defaults.tftest.hcl`     | Default variable values produce expected configuration           |
| `security.tftest.hcl`     | Security settings (RBAC, TLS, network ACLs)                      |
| `outputs.tftest.hcl`      | Output nullability when features are disabled                    |
| `validation.tftest.hcl`   | Variable validation rules via `expect_failures`                  |

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

## Documentation Generation

Terraform module documentation generates from source using [terraform-docs](https://terraform-docs.io/) v0.21.0. Each module and deployment directory contains a `TERRAFORM.md` file that terraform-docs produces automatically.

### Configuration

The repository-wide configuration lives in `.terraform-docs.yml` at the workspace root. This file controls output format, section ordering, and content templates.

### Generated Files

Generated `TERRAFORM.md` files exist in every Terraform module and deployment directory. These files are excluded from cspell and markdownlint because their content derives from HCL source code.

| Directory                                    | File           |
|----------------------------------------------|----------------|
| `infrastructure/terraform/`                  | `TERRAFORM.md` |
| `infrastructure/terraform/vpn/`              | `TERRAFORM.md` |
| `infrastructure/terraform/modules/platform/` | `TERRAFORM.md` |
| `infrastructure/terraform/modules/sil/`      | `TERRAFORM.md` |
| `infrastructure/terraform/modules/vpn/`      | `TERRAFORM.md` |

### Regenerating Documentation

Run terraform-docs against a specific directory:

```bash
terraform-docs markdown table --output-file TERRAFORM.md infrastructure/terraform/modules/platform/
```

Or regenerate all modules using the PowerShell helper:

```powershell
./scripts/Update-TerraformDocs.ps1
```

### Quality Standards

Variable descriptions serve as the primary documentation source. Write descriptions that:

* Use sentence case without trailing periods
* Explain purpose and expected values, not just the variable name restated
* Include examples for complex types (e.g., `object`, `map`)

## Related Documentation

* [Contributing Guide](README.md) - Prerequisites, workflow, commit messages
* [Deployment Validation](deployment-validation.md) - Validation levels and deployment testing
* [Security Review](security-review.md) - Security checklist and patterns
* [Shell Scripts Instructions](https://github.com/microsoft/physical-ai-toolchain/blob/main/.github/instructions/shell-scripts.instructions.md) - Detailed shell script guidance
* [Terraform Test Reference](https://developer.hashicorp.com/terraform/language/tests) - HashiCorp `terraform test` documentation
