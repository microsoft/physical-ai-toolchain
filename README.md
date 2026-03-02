# 🤖 Azure Robotics Reference Architecture with NVIDIA OSMO

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/badge)](https://scorecard.dev/viewer/?uri=github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture)

This reference architecture provides a production-ready framework for orchestrating robotics and AI workloads on [Microsoft Azure](https://azure.microsoft.com/) using NVIDIA technologies such as [Isaac Lab](https://developer.nvidia.com/isaac/lab), [Isaac Sim](https://developer.nvidia.com/isaac/sim), and [OSMO](https://developer.nvidia.com/osmo).
It demonstrates end-to-end reinforcement learning workflows, scalable training pipelines, and deployment processes with Azure-native authentication, storage, and ML services.

## 🚀 Key Features

OSMO handles workflow orchestration and job scheduling while Azure provides elastic GPU compute, persistent checkpointing, MLflow experiment tracking, and enterprise grade security.

- **Infrastructure as Code** - Terraform modules referencing [microsoft/edge-ai](https://github.com/microsoft/edge-ai) components for reproducible deployments
- **Containerized Workflows** - Docker-based Isaac Lab training with NVIDIA GPU support
- **CI/CD Integration** - Automated deployment pipelines with GitHub Actions
- **MLflow Integration** - Automatic experiment tracking and model versioning
  - Automatic metric logging from SKRL agents to Azure ML
  - Comprehensive tracking of episode statistics, losses, optimization metrics, and timing data
  - Configurable logging intervals and metric filtering
  - See [MLflow Integration Guide](docs/training/mlflow-integration.md) for details
- **Scalable Compute** - Auto-scaling GPU nodes based on workload demands
- **Cost Optimization** - Pay-per-use compute with automatic scaling
- **Enterprise Security** - Entra ID integration
- **Global Deployment** - Multi-region support for worldwide teams

## 🗼 Architecture Overview

This reference architecture integrates:

- **NVIDIA OSMO** - Workflow orchestration and job scheduling
- **Azure Machine Learning** - Experiment tracking and model management
- **Azure Kubernetes Service** - Software in the Loop (SIL) training
- **Azure Arc for Kubernetes** - Software in the Loop (SIL) and Hardware in the Loop (HIL) training
- **Azure Storage** - Persistent data and checkpoint storage
- **Azure Key Vault** - Secure credential management
- **Azure Monitor** - Comprehensive logging and metrics

<!-- INSERT ARCHITECTURE DIAGRAM HERE -->

## 🌍 Real World Examples

**OSMO orchestration** on Azure enables production-scale robotics training across industries. Some examples include:

- **Warehouse AMRs** - Train navigation policies with 1000+ parallel environments on auto-scaling AKS GPU nodes, checkpoint to Azure Storage, track experiments in Azure ML
- **Manufacturing Arms** - Develop manipulation strategies with physics-accurate simulation, leveraging Azure's global regions for distributed teams and pay-per-use GPU compute
- **Legged Robots** - Optimize locomotion policies with MLflow experiment tracking for sim-to-real transfer
- **Collaborative Robots** - Create safe interaction policies with Azure Monitor logging and metrics, enabling compliance auditing and performance diagnostics at scale

See [OSMO workflow examples](workflows/osmo/) for job configuration templates.

## 🧑🏽‍💻 Prerequisites and Requirements

### Required Tools

- [uv](https://docs.astral.sh/uv/) - Python package manager and environment tool
- Python 3.11 (required by Isaac Sim 5.X)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (v2.50+)
- [Terraform](https://developer.hashicorp.com/terraform/install) (v1.5+)
- [NVIDIA OSMO CLI](https://developer.nvidia.com/osmo) (latest)
- [Docker](https://docs.docker.com/get-docker/) with NVIDIA Container Toolkit
- [hve-core](https://github.com/microsoft/hve-core) - Copilot-assisted development workflows ([install guide](https://github.com/microsoft/hve-core/blob/main/docs/getting-started/install.md))

### Azure Requirements

- Azure subscription with contributor access
- Sufficient quota for GPU VMs (Standard_NC6s_v3 or higher)
- Azure Machine Learning workspace (or permissions to create one)

### NVIDIA Requirements

- NVIDIA Developer account with OSMO access
- NGC API key for container registry access

## 🏃‍➡️ Quick Start

```bash
./setup-dev.sh
```

The setup script installs Python 3.11 via [uv](https://docs.astral.sh/uv/), creates a virtual environment at `.venv/`, and installs training dependencies.

Install [hve-core](https://github.com/microsoft/hve-core/blob/main/docs/getting-started/install.md) for Copilot-assisted development workflows. The simplest method is the [VS Code Extension](https://marketplace.visualstudio.com/items?itemName=ise-hve-essentials.hve-core).

### VS Code Configuration

The workspace is configured with `python.analysis.extraPaths` pointing to `src/`, enabling imports like:

```python
from training.utils import AzureMLContext, bootstrap_azure_ml
```

Select the `.venv/bin/python` interpreter in VS Code for IntelliSense support

The workspace `.vscode/settings.json` also configures Copilot Chat to load instructions, prompts, and chat modes from hve-core:

| Setting                           | hve-core Paths                                                               |
|-----------------------------------|------------------------------------------------------------------------------|
| `chat.modeFilesLocations`         | `../hve-core/.github/chatmodes`, `../hve-core/copilot/beads/chatmodes`       |
| `chat.instructionsFilesLocations` | `../hve-core/.github/instructions`, `../hve-core/copilot/beads/instructions` |
| `chat.promptFilesLocations`       | `../hve-core/.github/prompts`, `../hve-core/copilot/beads/prompts`           |

These paths resolve when hve-core is installed as a peer directory or via the VS Code Extension. Without hve-core, Copilot still functions but shared conventions, prompts, and chat modes are unavailable.

## 🧪 Running Tests

Once a `tests/` directory exists, run the test suite:

```bash
uv run pytest tests/
```

Run tests selectively by category:

```bash
# Unit tests only (fast, no external dependencies)
uv run pytest tests/ -m "not slow and not gpu"
```

See the [Testing Requirements](CONTRIBUTING.md#testing-requirements) section in CONTRIBUTING.md for test organization, markers, and coverage targets.

## 🧹 Cleanup and Uninstall

Reverse the changes made by `setup-dev.sh` and tear down deployed infrastructure.

### Remove Development Environment

```bash
# Remove Python virtual environment
rm -rf .venv

# Remove cloned IsaacLab repository
rm -rf external/IsaacLab

# Remove Node.js linting dependencies (if installed separately via npm install)
rm -rf node_modules

# Remove uv cache (optional, frees disk space)
uv cache clean
```

### Destroy Azure Infrastructure

```bash
cd deploy/001-iac
terraform destroy -var-file=terraform.tfvars
```

> [!WARNING]
> `terraform destroy` permanently deletes all deployed Azure resources including storage, AKS clusters, and Key Vault. Ensure training data and model checkpoints are backed up before destroying infrastructure.

See [Cost Considerations](docs/contributing/cost-considerations.md) for details on resource costs and cleanup timing.

## 🧱 Repository Structure

```text
.
├── deploy/
│   ├── 000-prerequisites/              # Prerequisites validation and setup
│   ├── 001-iac/                        # Infrastructure as Code deployment
│   ├── 002-setup/                      # Post-infrastructure setup
│   ├── 003-data/                       # Data preparation and upload
│   └── 004-workflow/                   # Training workflow execution
│       ├── job-templates/              # Job configuration templates
│       └── osmo/                       # OSMO inline workflow submission (see osmo/README.md)
├── src/
│   ├── terraform/                      # Infrastructure as Code
│   │   └── modules/                    # Reusable Terraform modules
│   └── training/                       # Training code and tasks
│       ├── common/                     # Shared utilities
│       ├── scripts/                    # Framework-specific training scripts configured for Azure services
│       │   ├── rsl_rl/                 # RSL_RL training scripts
│       │   ├── skrl/                   # SKRL training scripts
│       └── tasks/                      # Placeholder for Isaac Lab training tasks
```

## 🪪 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## 🔒 Security

<!-- cspell:words deployers -->

Review security guidance before deploying this reference architecture:

- [SECURITY.md](SECURITY.md) - vulnerability reporting and security considerations for deployers
- [Security Documentation](docs/security/README.md) - threat model, security guide, and deployment responsibilities

## 🤝 Support

For issues and questions:

- Review [microsoft/edge-ai](https://github.com/microsoft/edge-ai) documentation

## 🗺️ Roadmap

See the [project roadmap](docs/contributing/ROADMAP.md) for priorities, timelines, and success metrics covering Q1 2026 through Q1 2027.

## 🙏 Acknowledgments

This reference architecture builds upon:

- [microsoft/edge-ai](https://github.com/microsoft/edge-ai) - Edge AI infrastructure components
- [NVIDIA Isaac Lab](https://github.com/isaac-sim/IsaacLab) - RL task framework
- [NVIDIA Isaac Sim](https://developer.nvidia.com/isaac-sim) - Physics simulation
- [NVIDIA OSMO](https://developer.nvidia.com/osmo) - Workflow orchestration
- [NVIDIA OSMO GitHub](https://github.com/NVIDIA/OSMO) - Workflow orchestration
