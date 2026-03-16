# Physical AI Toolchain

<!-- markdownlint-disable MD013 -->
[![CI Status](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/main.yml/badge.svg)](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/main.yml)
[![CodeQL](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/microsoft/physical-ai-toolchain/actions/workflows/codeql-analysis.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/microsoft/physical-ai-toolchain/badge)](https://scorecard.dev/viewer/?uri=github.com/microsoft/physical-ai-toolchain)
[![License](https://img.shields.io/github/license/microsoft/physical-ai-toolchain)](./LICENSE)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://microsoft.github.io/physical-ai-toolchain/)
<!-- markdownlint-enable MD013 -->

## Overview

Physical AI and robotics are moving from headlines and experimentation into real-world industrial deployment. The shift creates practical implications for how human-robot-AI collaboration becomes an operational capability in manufacturing, logistics, healthcare, and autonomous systems. Operationalizing physical intelligence at scale — across fleets and federations of intelligent systems — is a challenge no single OEM or software vendor can deliver alone.

Physical AI is the strategic inflection point for AI platforms, and robotics is the hero use case. It sits at the intersection of cloud, edge, data, and agentic AI.

**Physical AI Toolchain** is an open-source, production-ready framework that integrates [Microsoft Azure](https://azure.microsoft.com/) cloud services with [NVIDIA's](https://developer.nvidia.com/) physical AI stack, accelerating robotics and physical AI developers to automate and scale data curation, augmentation, and evaluation across perception, mobility, imitation learning, and reinforcement learning pipelines. It provides:

- **Accelerate physical AI innovation.** From edge data capture on NVIDIA Jetson devices through cloud-based training on GPU clusters to model deployment at the edge, every stage of the physical AI lifecycle is addressed with tested, repeatable automation.
- **Operationalize physical intelligence.** Built on Azure Machine Learning, Azure Kubernetes Service, Azure Arc, and Azure Storage with Entra ID authentication, managed identities, and Infrastructure as Code, so workloads meet the security, compliance, and governance requirements of production environments.
- **Scale through ecosystem collaboration.** Native support for NVIDIA Isaac Sim and Isaac Lab for simulation and reinforcement learning, NVIDIA OSMO for workflow orchestration, and the NVIDIA Jetson platform for edge inference — a hardware-accelerated path from research to deployment enabled by deep partnership across the ecosystem.
- **Human-robot-AI agent collaboration.** Agentic engineering lets teams move from isolated machines to coordinated, instruction-driven workflows. AI agents can turn high-level instructions into executed pipelines — but they are a convenience layer, not a requirement. Start with manual workflows, introduce agents when you are ready, and customize their behavior to match your team's trust boundaries.
- **Broad physical AI applicability.** While robotics is the hero use case, the architecture supports any physical AI workload that follows the simulate → train → evaluate → deploy pattern, including autonomous mobile robots, robotic manipulation, industrial inspection, and embodied AI research.

Whether you are evaluating Azure and NVIDIA as a platform for physical AI, planning a proof of concept, or scaling to production, this toolchain provides a tested solution and working code to accelerate your timeline.

### Who This Is For

- **Robotics researchers** moving from Isaac Sim prototypes to production-grade training and deployment pipelines
- **Platform engineers** standardizing physical AI pipelines across teams with Infrastructure as Code and repeatable workflows
- **Enterprise teams** piloting Jetson + Azure deployments and need security, compliance, and scalability from day one

> [!NOTE]
> **Who it's not for (yet):** This toolchain targets production and pre-production workloads. It is not currently designed for hobbyist projects, ROS beginners learning the basics, or single-robot desktop demos. We welcome contributions that broaden accessibility over time.

<!-- -->

> [!TIP]
> **Get started in under 2 hours.** By the end of the [Quickstart Guide](docs/getting-started/quickstart.md), you will have:
>
> - A pick-and-place RL policy trained in Isaac Lab on Azure GPU compute
> - Experiment metrics and checkpoints tracked in MLflow
> - A containerized model deployed to a Jetson device via GitOps

## What's Inside

![Physical AI Toolchain Architecture Diagram](docs/images/physical-ai-toolchain-architecture-diagram.png "Physical AI Toolchain Architecture Diagram")

| Capability                      | Description                                                                                                 |
|---------------------------------|-------------------------------------------------------------------------------------------------------------|
| **Simulation & Synthetic Data** | Isaac Sim and Isaac Lab environments for RL task training and synthetic data generation                     |
| **Edge Data Capture**           | ROS 2 demonstration recording on Jetson with chunking, compression, and cloud upload                        |
| **Cloud Data Pipeline**         | Automated ROS-to-LeRobot conversion, quality validation, and event-driven orchestration                     |
| **Training Infrastructure**     | OSMO + Azure ML integration for scalable RL and IL training with experiment tracking                        |
| **Model Evaluation**            | Offline replay evaluation, Isaac Sim validation, and evaluation dashboards                                  |
| **Model Deployment**            | ONNX/TensorRT conversion, container packaging, and GitOps-based edge deployment                             |
| **Agentic Workflows**           | Instruction-driven agents that orchestrate data collection, training, evaluation, and deployment end-to-end |
| **Hybrid Architecture**         | Azure Arc, air-gapped training support, and MQTT telemetry for connected and disconnected sites             |

## Key Features

- **Infrastructure as Code** — Terraform modules for reproducible Azure deployments
- **Containerized Workflows** — Docker-based Isaac Lab training with NVIDIA GPU support
- **MLflow Integration** — Automatic experiment tracking and model versioning
- **Scalable Compute** — Auto-scaling GPU nodes with pay-per-use cost optimization
- **Enterprise Security** — Entra ID integration with managed identities
- **CI/CD Integration** — Automated deployment pipelines with GitHub Actions
- **Edge-to-Cloud Data Pipeline** — Automated capture, upload, conversion, and validation
- **Multi-Modal Training** — Support for reinforcement learning and imitation learning workflows
- **Agentic Pipeline Orchestration** — Describe a task; agents handle data collection through policy deployment

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
| [Deployment](docs/infrastructure/README.md)       | Infrastructure provisioning and setup                     |
| [Training](docs/training/README.md)               | RL and IL training workflows, MLflow, and checkpointing   |
| [Security](docs/security/README.md)               | Threat model, security guide, deployment responsibilities |
| [Contributing](docs/contributing/README.md)       | Architecture, style guides, contribution workflow         |

## Architecture

This toolchain integrates:

- **NVIDIA OSMO** — Workflow orchestration and job scheduling
- **NVIDIA Isaac Sim & Isaac Lab** — Physics simulation and RL task environments
- **NVIDIA Jetson** — Edge inference and demonstration data capture
- **Azure Machine Learning** — Experiment tracking and model management
- **Azure Kubernetes Service** — Software in the Loop (SIL) training
- **Azure Arc for Kubernetes** — Hardware in the Loop (HIL) training and edge fleet management
- **Azure Storage** — Persistent data and checkpoint storage
- **Azure Event Grid & Fabric** — Event-driven data pipeline orchestration

See [Architecture Overview](docs/contributing/architecture.md) for the full design.

## Agentic Workflows

The toolchain includes agent-driven automation that collapses multi-stage physical AI pipelines into simple, instruction-level interactions.

**How it works:**

1. **Describe the objective.** Provide a natural-language instruction such as "collect 50 demonstrations of an inspection and sorting task and train an IL policy."
2. **Agent plans and executes.** The agent decomposes the objective into pipeline stages — data collection, conversion, training configuration, compute provisioning, and training launch — then executes each stage using the toolchain's APIs and infrastructure.
3. **Evaluate and iterate.** The agent runs evaluation (simulation replay, success-rate metrics) and presents results. If the policy does not meet acceptance criteria, the agent adjusts hyperparameters or collects additional data and re-trains.
4. **Deploy.** Once a policy passes evaluation, the agent packages it (ONNX/TensorRT), builds a container image, and triggers GitOps deployment to target edge devices.

**What agents can do today:**

| Capability             | Description                                                                        |
|------------------------|------------------------------------------------------------------------------------|
| Sample data collection | Configure Isaac Sim scenes and collect synthetic demonstration datasets            |
| RL pipeline execution  | Set up Isaac Lab tasks, launch OSMO training jobs, and track experiments in MLflow |
| IL pipeline execution  | Convert demonstration data to LeRobot format, run imitation learning training      |
| Policy evaluation      | Execute offline replay and simulation-based validation against success criteria    |
| Deployment promotion   | Convert checkpoints, package containers, and push to edge via GitOps               |

Agents operate within the same security boundaries, managed identities, and RBAC controls as manual workflows. All agent actions are logged and auditable.

### Guardrails and Control

| Question                                         | Answer                                                                                                                                                           |
|--------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Are agents required?                             | No. Every pipeline stage has a manual CLI and API path. Agents are opt-in.                                                                                       |
| Can I use agents for some stages but not others? | Yes. Agents are composable — use them for data collection but run training manually, or vice versa.                                                              |
| Are agents opinionated or customizable?          | Customizable. Agent behavior is driven by configuration files you control: which stages to automate, compute budgets, approval gates, and evaluation thresholds. |
| What happens if an agent makes a mistake?        | Agents request human approval before destructive actions (deploying to production, deleting data). All intermediate artifacts are versioned and recoverable.     |
| How are agent actions audited?                   | Every agent action is logged with the initiating instruction, parameters, and outcome. Logs integrate with Azure Monitor and MLflow.                             |

## For Developers

### Repository Structure

| Directory  | Purpose                                                            |
|------------|--------------------------------------------------------------------|
| `src/`     | Core Python modules — conversion, validation, training utilities   |
| `infra/`   | Terraform and Bicep templates for Azure resource provisioning      |
| `config/`  | YAML configuration schemas for recording, training, and deployment |
| `scripts/` | Setup, benchmarking, and operational helper scripts                |
| `tests/`   | Unit, integration, and end-to-end test suites                      |
| `docs/`    | All project documentation                                          |

### Development Environment

Prerequisites:

- Python 3.11+
- Docker with NVIDIA Container Toolkit
- Terraform 1.5+ (for infrastructure deployment)
- Azure CLI with an active subscription
- NVIDIA GPU (local development) or Azure GPU VM

Run the test suite:

```bash
pytest tests/ -v
```

See [prerequisites](docs/contributing/prerequisites.md) for the complete setup guide.

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

This toolchain builds upon:

- [NVIDIA Isaac Lab](https://github.com/isaac-sim/IsaacLab) — RL task framework
- [NVIDIA Isaac Sim](https://developer.nvidia.com/isaac-sim) — Physics simulation
- [NVIDIA OSMO](https://developer.nvidia.com/osmo) — Workflow orchestration
- [LeRobot](https://github.com/huggingface/lerobot) — Imitation learning dataset format

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
