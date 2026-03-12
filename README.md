# Physical AI Toolchain

<!-- markdownlint-disable MD013 -->
[![CI Status](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/main.yml/badge.svg)](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/main.yml)
[![CodeQL](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/codeql-analysis.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/microsoft/physical-ai-toolchain/badge)](https://scorecard.dev/viewer/?uri=github.com/microsoft/physical-ai-toolchain)
[![License](https://img.shields.io/github/license/microsoft/physical-ai-toolchain)](./LICENSE)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://microsoft.github.io/physical-ai-toolchain/)
<!-- markdownlint-enable MD013 -->

Production-ready framework for orchestrating robotics and AI workloads on [Microsoft Azure](https://azure.microsoft.com/) using NVIDIA [Isaac Lab](https://developer.nvidia.com/isaac/lab), [Isaac Sim](https://developer.nvidia.com/isaac/sim), and [OSMO](https://developer.nvidia.com/osmo).

> [!TIP]
> Get started in under 2 hours — follow the [Quickstart Guide](docs/getting-started/quickstart.md).

## Overview

This reference architecture demonstrates end-to-end reinforcement learning workflows, scalable training pipelines, and deployment processes with Azure-native authentication, storage, and ML services. OSMO handles workflow orchestration and job scheduling while Azure provides elastic GPU compute, persistent checkpointing, MLflow experiment tracking, and enterprise-grade security.

## Key Features

- **Infrastructure as Code** — Terraform modules for reproducible Azure deployments
- **Containerized Workflows** — Docker-based Isaac Lab training with NVIDIA GPU support
- **MLflow Integration** — Automatic experiment tracking and model versioning
- **Scalable Compute** — Auto-scaling GPU nodes with pay-per-use cost optimization
- **Enterprise Security** — Entra ID integration with managed identities
- **CI/CD Integration** — Automated deployment pipelines with GitHub Actions

## Quick Start

```bash
./setup-dev.sh
```

The setup script installs Python 3.11 via [uv](https://docs.astral.sh/uv/), creates a virtual environment, and installs training dependencies. Follow the [Quickstart Guide](docs/getting-started/quickstart.md) for the full deployment walkthrough.

## Documentation

Full documentation is available in the [docs/](docs/README.md) directory.

| Guide                                             | Description                                               |
|---------------------------------------------------|-----------------------------------------------------------|
| [Getting Started](docs/getting-started/README.md) | Prerequisites, quickstart, and first training job         |
| [Deployment](docs/deploy/README.md)               | Infrastructure provisioning and setup                     |
| [Training](docs/training/README.md)               | RL training workflows, MLflow, and checkpointing          |
| [Security](docs/security/README.md)               | Threat model, security guide, deployment responsibilities |
| [Contributing](docs/contributing/README.md)       | Architecture, style guides, contribution workflow         |

## Architecture

This reference architecture integrates:

- **NVIDIA OSMO** — Workflow orchestration and job scheduling
- **Azure Machine Learning** — Experiment tracking and model management
- **Azure Kubernetes Service** — Software in the Loop (SIL) training
- **Azure Arc for Kubernetes** — Hardware in the Loop (HIL) training
- **Azure Storage** — Persistent data and checkpoint storage

See [Architecture Overview](docs/contributing/architecture.md) for the full design.

## Contributing

Contributions are welcome. Whether fixing documentation or adding new training tasks:

1. Read the [Contributing Guide](CONTRIBUTING.md)
2. Review [open issues](https://github.com/microsoft/physical-ai-toolchain/issues)
3. See the [prerequisites](docs/contributing/prerequisites.md) for required tools

## Verifying Git Tags

All release tags are signed. Verify a release tag before using it in production workflows:

```bash
git fetch --tags
git tag -v v1.0.0
```

This repository uses Sigstore `gitsign` keyless signing for release tags. For tag signing policy and maintainer guidance, see [CONTRIBUTING.md](CONTRIBUTING.md#release-tag-signing).

## Roadmap

See the [project roadmap](docs/contributing/ROADMAP.md) for priorities, timelines, and success metrics.

## Acknowledgments

This reference architecture builds upon:

- [NVIDIA Isaac Lab](https://github.com/isaac-sim/IsaacLab) — RL task framework
- [NVIDIA Isaac Sim](https://developer.nvidia.com/isaac-sim) — Physics simulation
- [NVIDIA OSMO](https://developer.nvidia.com/osmo) — Workflow orchestration

## 🤖 Responsible AI

Microsoft encourages customers to review its Responsible AI Standard when developing AI-enabled systems to ensure ethical, safe, and inclusive AI practices. Learn more at [Microsoft's Responsible AI](https://www.microsoft.com/ai/responsible-ai).

## ⚠️ Deprecations

No interfaces are currently deprecated. When deprecations are announced, they appear here with migration guidance and removal timelines.

See the [Deprecation Policy](docs/deprecation-policy.md) for how interface changes are communicated and managed.

## Legal

This project is licensed under the [MIT License](./LICENSE).

See [SECURITY.md](./SECURITY.md) for the security policy and vulnerability reporting.

See [GOVERNANCE.md](./GOVERNANCE.md) for the project governance model.

See [SUPPORT.md](./SUPPORT.md) for support options and issue reporting.

## Trademark Notice

> This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
> trademarks or logos is subject to and must follow Microsoft's Trademark & Brand Guidelines. Use of Microsoft
> trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
> Any use of third-party trademarks or logos are subject to those third-party's policies.

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
