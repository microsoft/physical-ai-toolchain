---
sidebar_position: 6
title: Prerequisites and Build Validation
description: Required tools, Azure access, NGC credentials, and build validation commands for contributing
author: Microsoft Robotics-AI Team
ms.date: 2026-09-21
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

| Tool           | Minimum Version | Installation                                                                                                              |
|----------------|-----------------|---------------------------------------------------------------------------------------------------------------------------|
| Terraform      | 1.9.8           | <https://developer.hashicorp.com/terraform/install>                                                                       |
| TFLint         | 0.61.0          | <https://github.com/terraform-linters/tflint>                                                                             |
| Azure CLI      | 2.65.0          | <https://learn.microsoft.com/cli/azure/install-azure-cli>                                                                 |
| kubectl        | 1.31            | <https://kubernetes.io/docs/tasks/tools/>                                                                                 |
| Helm           | 4.2+            | <https://helm.sh/docs/intro/install/>                                                                                     |
| Node.js/npm    | 24+             | <https://nodejs.org/>                                                                                                     |
| Python         | 3.12+           | <https://www.python.org/downloads/>                                                                                       |
| shellcheck     | 0.10+           | <https://www.shellcheck.net/>                                                                                             |
| uv             | latest          | <https://docs.astral.sh/uv/>                                                                                              |
| Go             | 1.26+           | <https://go.dev/dl/>                                                                                                      |
| golangci-lint  | 2.11+           | <https://golangci-lint.run/welcome/install/>                                                                              |
| Docker         | latest          | <https://docs.docker.com/get-docker/> (with NVIDIA Container Toolkit)                                                     |
| OSMO CLI       | latest          | <https://developer.nvidia.com/osmo>                                                                                       |
| terraform-docs | 0.24.0          | <https://github.com/terraform-docs/terraform-docs/releases>                                                               |
| OSV-Scanner    | 2.3.8           | <https://github.com/google/osv-scanner/releases/tag/v2.3.8> (installed automatically by `setup-dev.sh` / `setup-dev.ps1`) |
| hve-core       | latest          | <https://github.com/microsoft/hve-core>                                                                                   |

> [!NOTE]
> GitHub Copilot Coding Agent runs in a separate cloud GitHub Actions environment provisioned by [.github/workflows/copilot-setup-steps.yml](../../.github/workflows/copilot-setup-steps.yml). When you bump a language runtime or test runner version locally (devcontainer or this list), update the matching pin in that workflow so cloud-agent sessions stay aligned.

## Dev Container GPU Runtime

The dev container requests a GPU only when Dev Containers detects one. Configure the host according to its platform:

| Host                          | Docker runtime configuration                                                                  |
|-------------------------------|-----------------------------------------------------------------------------------------------|
| x86_64 with an NVIDIA GPU     | Install NVIDIA Container Toolkit; automatic `--gpus all` handling normally requires no change |
| NVIDIA ARM64 host in CSV mode | Set `nvidia` as Docker's default runtime                                                      |
| Host without an NVIDIA GPU    | Retain the default `runc` runtime; the dev container starts without GPU devices               |

On NVIDIA ARM64 hosts, the NVIDIA Container Runtime can auto-detect CSV mode and reject the
`--gpus all` request unless Docker invokes the `nvidia` runtime. Confirm that Docker has registered the runtime:

```bash
docker info --format '{{json .Runtimes}}'
```

The output must include `nvidia`. Configure it as the default runtime, then reload Docker without stopping running
containers:

```bash
sudo nvidia-ctk runtime configure --runtime=docker --set-as-default
sudo systemctl reload docker
```

If the reload does not apply the change, restart Docker. A restart interrupts running containers:

```bash
sudo systemctl restart docker
```

Verify the default runtime and GPU attachment:

```bash
docker info --format 'Default runtime: {{.DefaultRuntime}}'
docker run --rm --runtime=nvidia --gpus all ubuntu:24.04 \
  sh -c 'test -e /dev/nvidia0 && echo "GPU attached"'
```

The first command must report `nvidia`, and the second must print `GPU attached`. Rebuild and reopen the dev
container after configuring the host.

This configuration resolves the following container startup error:

```text
invoking the NVIDIA Container Runtime Hook directly (e.g. specifying the docker --gpus flag) is not supported.
Please use the NVIDIA Container Runtime (e.g. specify the --runtime=nvidia flag) instead
```

### GPU Device Group Access

Attaching the GPU is not sufficient. CUDA also requires the container user to hold the host groups that own the GPU
device nodes. [.devcontainer/devcontainer.json](../../.devcontainer/devcontainer.json) grants them through `runArgs`:

| Device node         | Host group | Required for                               |
|---------------------|------------|--------------------------------------------|
| `/dev/nvmap`        | `video`    | Tegra memory manager on NVIDIA ARM64 hosts |
| `/dev/dri/renderD*` | `render`   | DRM render node that CUDA opens            |

The committed configuration assumes the host `render` group is GID `993`. Confirm the value on the host:

```bash
stat -c '%g %G' /dev/dri/renderD128
```

If the GID differs, update the matching `--group-add` entry and rebuild the dev container. Group names do not
transfer across the container boundary, so the numeric GID is required; inside the container it resolves to whichever
name holds that GID, commonly `systemd-resolve`.

Missing membership produces a misleading failure. `nvidia-smi` lists the GPU and `torch.cuda.device_count()` returns
`1`, but `torch.cuda.is_available()` returns `False`:

```text
CUDA initialization: Unexpected error from cudaGetDeviceCount(). Error 801: operation not supported
```

Verify device access and CUDA inside the dev container:

```bash
test -r /dev/nvmap && test -r /dev/dri/renderD128 && echo "device access OK"
python -c "import torch; print('cuda:', torch.cuda.is_available())"
```

Both commands must succeed, and the second must print `cuda: True`.

## Azure Access Requirements

Deploying this architecture requires Azure subscription access with specific permissions and quotas:

### Subscription Roles

* `Contributor` role for resource group creation and management
* `User Access Administrator` role for managed identity assignment

### GPU Quota

* Request GPU VM quota in your target region before deployment
* The checked-in `terraform.tfvars.example` uses `Standard_NV36ads_A10_v5`; request quota for the VM families selected in your `node_pools` map, including Spot quota when applicable
* Check regional quota with `az vm list-usage --location <region>` and inspect the selected VM family
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

The figures below are retained illustrative estimates, not current quotes for the default A10 deployment. Recalculate rates for your region and chosen SKUs using the [Cost Considerations](cost-considerations.md) guidance.

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

# TFLint (Terraform linter)
tflint --version  # >= 0.61.0

# Azure CLI
az version  # >= 2.65.0

# kubectl
kubectl version --client  # >= 1.31

# Helm
helm version  # >= 4.2

# Node.js (for documentation linting)
node --version  # >= 24

# Python (for training scripts)
python --version  # >= 3.12

# shellcheck (for shell script validation)
shellcheck --version  # >= 0.10

# uv (Python package manager)
uv --version

# Go
go version  # >= 1.26

# golangci-lint
golangci-lint version  # >= 2.11

# Docker with NVIDIA Container Toolkit
docker --version
nvidia-ctk --version

# OSMO CLI
osmo --version

# terraform-docs
terraform-docs --version  # >= 0.24.0

# OSV-Scanner (dependency vulnerability scanner)
osv-scanner --version  # == 2.3.8 (pinned; installed by setup-dev scripts)

# hve-core (VS Code extension — verify via extensions list)
code --list-extensions | grep -i hve-core
```

### TFLint Local Setup

Install TFLint v0.61.0 or newer before changing Terraform modules:

```bash
# macOS
brew install tflint

# Linux
curl -s https://raw.githubusercontent.com/terraform-linters/tflint/master/install_linux.sh | bash
```

```powershell
# Windows (Chocolatey)
choco install tflint

# Windows (Scoop)
scoop install tflint
```

Initialize the repository TFLint plugins once from the repository root. This downloads the Azure provider
ruleset declared in `.tflint.hcl`:

```bash
tflint --init
```

Then run the project wrapper before pushing Terraform changes:

```bash
npm run lint:tf
```

The wrapper runs TFLint recursively against `infrastructure/terraform/` with the shared `.tflint.hcl`
configuration. A VS Code TFLint extension is optional for inline diagnostics, but the CLI setup above remains
the required validation path.

### Validation Commands

Run these commands before committing:

Run package commands from the repository root after `npm ci`.

**Terraform:**

```bash
# Format check (required)
terraform fmt -check -recursive infrastructure/terraform/

# Initialize without a backend and validate each deployment directory
npm run lint:tf:validate

# Lint Terraform configurations (required for infrastructure changes)
tflint --init  # first time only, installs plugins from .tflint.hcl
npm run lint:tf
```

**Shell Scripts:**

```bash
# Lint all shell scripts (required)
npm run lint:sh
```

**Go:**

```bash
# Lint Go modules (required for Go changes)
npm run lint:go

# Test Go modules (required for Go changes)
npm run test:go

# Contract tests (validates Terraform outputs against Go struct — requires terraform-docs)
# Run after adding/removing/renaming Terraform outputs
./infrastructure/terraform/e2e/run-contract-tests.sh
```

**Documentation:**

```bash
# Restore dependencies from the committed lock
npm ci

# Lint markdown (required for documentation changes)
npm run lint:md
```

## VS Code Configuration

The `training` package lives at the repository root. Open the repository root as the workspace and select its Python environment for imports such as:

```python
from training.utils import AzureMLContext, bootstrap_azure_ml
```

Select `.venv/bin/python` on Linux/macOS or `.venv/Scripts/python.exe` on Windows. The current `python.analysis.extraPaths` includes Isaac Lab source paths and a legacy `src/` entry; that legacy entry is not the location of the `training` package.

The workspace recommends the hve-core extension in `.vscode/extensions.json`. Its settings reference `.github/instructions/commit-message.instructions.md` for Copilot commit messages; they do not configure peer-directory chat-mode, instruction, or prompt locations. Shared hve-core capabilities depend on your installed extension or plugin.

For a complete list of available agents, prompts, and skills, see [Copilot Artifacts](../reference/copilot-artifacts.md).

## Related Documentation

* [Contributing Guide](README.md) - Main contributing guide with all sections
* [Deployment Validation](deployment-validation.md) - Validation levels and testing templates
* [Cost Considerations](cost-considerations.md) - Component costs, budgeting, regional pricing
