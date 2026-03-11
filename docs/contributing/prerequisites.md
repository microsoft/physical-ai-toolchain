---
title: Prerequisites and Build Validation
description: Required tools, Azure access, NGC credentials, and build validation commands for contributing
author: Microsoft Robotics-AI Team
ms.date: 2026-02-08
ms.topic: how-to
keywords:
  - prerequisites
  - azure
  - terraform
  - validation
  - contributing
---

> [!NOTE]
> This guide expands on the [Prerequisites](README.md#-prerequisites) section of the main contributing guide.

Tools, Azure access, and build validation requirements for contributing to the Physical AI Toolchain.

## Required Tools

Install these tools before contributing:

| Tool        | Minimum Version | Installation                                                          |
|-------------|-----------------|-----------------------------------------------------------------------|
| Terraform   | 1.9.8           | <https://developer.hashicorp.com/terraform/install>                   |
| Azure CLI   | 2.65.0          | <https://learn.microsoft.com/cli/azure/install-azure-cli>             |
| kubectl     | 1.31            | <https://kubernetes.io/docs/tasks/tools/>                             |
| Helm        | 3.16            | <https://helm.sh/docs/intro/install/>                                 |
| Node.js/npm | 20+ LTS         | <https://nodejs.org/>                                                 |
| Python      | 3.11+           | <https://www.python.org/downloads/>                                   |
| shellcheck  | 0.10+           | <https://www.shellcheck.net/>                                         |
| uv          | latest          | <https://docs.astral.sh/uv/>                                          |
| Docker      | latest          | <https://docs.docker.com/get-docker/> (with NVIDIA Container Toolkit) |
| OSMO CLI    | latest          | <https://developer.nvidia.com/osmo>                                   |
| hve-core    | latest          | <https://github.com/microsoft/hve-core>                               |

## Azure Access Requirements

Deploying this architecture requires Azure subscription access with specific permissions and quotas:

### Subscription Roles

* `Contributor` role for resource group creation and management
* `User Access Administrator` role for managed identity assignment

### GPU Quota

* Request GPU VM quota in your target region before deployment
* Architecture uses `Standard_NC24ads_A100_v4` (24 vCPU, 220 GB RAM, 1x A100 80GB GPU)
* Check quota: `az vm list-usage --location <region> --query "[?name.value=='standardNCadsA100v4Family']"`
* Request increase through Azure Portal → Quotas → Compute

### Regional Availability

* Verify GPU VM availability in target region: <https://azure.microsoft.com/global-infrastructure/services/?products=virtual-machines>
* Architecture validated in `eastus`, `westus2`, `westeurope` <!-- cspell:disable-line -->

## NVIDIA NGC Account

Training workflows use NVIDIA GPU Operator and Isaac Lab, which require NGC credentials:

* Create account: <https://ngc.nvidia.com/signup>
* Generate API key: NGC Console → Account Settings → Generate API Key
* Store API key in Azure Key Vault or Kubernetes secret (deployment scripts provide guidance)

## Cost Awareness

Full deployment validation incurs Azure costs. Understand cost structure before deploying:

### GPU Virtual Machines

* `Standard_NC24ads_A100_v4`: ~$3.06/hour per VM (pay-as-you-go)
* 8-hour validation session: ~$25
* 40-hour work week: ~$125

### Managed Services

* AKS control plane: ~$0.10/hour (~$73/month)
* Log Analytics workspace: ~$2.76/GB ingested
* Storage accounts: ~$0.02/GB (block blob, hot tier)
* Azure Container Registry: Basic tier ~$5/month

### Cost Optimization

* Use `terraform destroy` immediately after validation
* Automate cleanup with `-auto-approve` flag
* Monitor costs: Azure Portal → Cost Management + Billing
* Set budget alerts to prevent overruns

### Estimated Costs

* Quick validation (deploy + verify + destroy): ~$25-50
* Extended development session (8 hours): ~$50-100
* Monthly development (40 hours): ~$200-300

## Build and Validation Requirements

### Tool Version Verification

Verify tool versions before validating:

```bash
# Terraform
terraform version  # >= 1.9.8

# Azure CLI
az version  # >= 2.65.0

# kubectl
kubectl version --client  # >= 1.31

# Helm
helm version  # >= 3.16

# Node.js (for documentation linting)
node --version  # >= 20

# Python (for training scripts)
python --version  # >= 3.11

# shellcheck (for shell script validation)
shellcheck --version  # >= 0.10

# uv (Python package manager)
uv --version

# Docker with NVIDIA Container Toolkit
docker --version
nvidia-ctk --version

# OSMO CLI
osmo --version

# hve-core (VS Code extension — verify via extensions list)
code --list-extensions | grep -i hve-core
```

### Validation Commands

Run these commands before committing:

**Terraform:**

```bash
# Format check (required)
terraform fmt -check -recursive deploy/

# Initialize and validate (required for infrastructure changes)
cd deploy/001-iac/
terraform init
terraform validate
```

**Shell Scripts:**

```bash
# Lint all shell scripts (required)
shellcheck deploy/**/*.sh scripts/**/*.sh
```

**Documentation:**

```bash
# Install dependencies (first time only)
npm install

# Lint markdown (required for documentation changes)
npm run lint:md
```

## VS Code Configuration

The workspace is configured with `python.analysis.extraPaths` pointing to `src/`, enabling imports like:

```python
from training.utils import AzureMLContext, bootstrap_azure_ml
```

Select the `.venv/bin/python` interpreter in VS Code for IntelliSense support.

The workspace `.vscode/settings.json` also configures Copilot Chat to load instructions, prompts, and chat modes from hve-core:

| Setting                           | hve-core Paths                                                               |
|-----------------------------------|------------------------------------------------------------------------------|
| `chat.modeFilesLocations`         | `../hve-core/.github/chatmodes`, `../hve-core/copilot/beads/chatmodes`       |
| `chat.instructionsFilesLocations` | `../hve-core/.github/instructions`, `../hve-core/copilot/beads/instructions` |
| `chat.promptFilesLocations`       | `../hve-core/.github/prompts`, `../hve-core/copilot/beads/prompts`           |

These paths resolve when hve-core is installed as a peer directory or via the VS Code Extension. Without hve-core, Copilot still functions but shared conventions, prompts, and chat modes are unavailable.

## Related Documentation

* [Contributing Guide](README.md) - Main contributing guide with all sections
* [Deployment Validation](deployment-validation.md) - Validation levels and testing templates
* [Cost Considerations](cost-considerations.md) - Component costs, budgeting, regional pricing
