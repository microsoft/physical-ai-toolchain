---
title: Repository Architecture
description: Current state analysis and target architecture for the Azure NVIDIA Robotics Reference Architecture, organized around physical AI lifecycle domains with Agent Skill specifications.
ms.date: 2026-02-24
ms.topic: concept
---

## Current and Past Architecture

This repository provides Azure-integrated infrastructure and tooling for NVIDIA Isaac Lab-based robotics training, inference, and orchestration through NVIDIA OSMO and Azure Machine Learning.

### Repository Structure

| Directory    | Purpose                                                                                                       |
|--------------|---------------------------------------------------------------------------------------------------------------|
| `deploy/`    | Ordered Terraform IaC and shell scripts for Azure infrastructure provisioning and Kubernetes cluster setup.   |
| `src/`       | Python code for training and inference, acting as a shim between NVIDIA OSS libraries and Azure services.     |
| `workflows/` | OSMO workflow and AzureML job YAML definitions for training, inference, and validation.                       |
| `scripts/`   | Convenience scripts for submitting OSMO workflows and AzureML jobs, plus CI linting and security tooling.     |
| `external/`  | Cloned NVIDIA Isaac Lab repository for local development intellisense and reference.                          |
| `docs/`      | Documentation covering GPU configuration, MLflow integration, deployment validation, and contributing guides. |

### Source Code Organization

The `src/` directory contains three packages:

| Package      | Contents                                                                                                |
|--------------|---------------------------------------------------------------------------------------------------------|
| `common/`    | Shared CLI argument parsing utilities                                                                   |
| `training/`  | Isaac Lab training scripts with skrl, RSL-RL, and LeRobot integration plus Azure MLflow metric tracking |
| `inference/` | Policy export, playback, and inference node scripts                                                     |

Training scripts and Python code act as integration shims between NVIDIA's open-source repositories and Azure connectivity features: MLflow from AzureML for tracking training metrics, logs, and checkpoints; the AzureML model registry for checkpoint versioning; and Azure Blob Storage for dataset access.

### Python Dependencies

The root `pyproject.toml` serves local development dependency management:

| Context           | Usage                                                           |
|-------------------|-----------------------------------------------------------------|
| Local development | Providing module availability for intellisense and verification |

This setup is not intended for building publishable Python packages. The `pyproject.toml` build target only packages `src/training` into a wheel for in-container use.

## Future and Ongoing Architecture

This codebase will reorganize around eight lifecycle domains for robotics and physical AI, each built on current Azure services and NVIDIA's Physical AI Stack. Each domain represents a distinct functional concern in the physical AI lifecycle.

### Domain Overview

Each domain maps to a root-level directory in this repository. Domains that require Azure infrastructure beyond what `infrastructure/` provides maintain their own IaC subdirectories.

| Domain             | Directory             | Scope                                                                       |
|--------------------|-----------------------|-----------------------------------------------------------------------------|
| Infrastructure     | `infrastructure/`     | Shared Azure services: AKS, AzureML, networking, storage, observability     |
| Data Pipeline      | `data-pipeline/`      | Robot-to-cloud data capture via Azure Arc and ROS 2 episodic recording      |
| Data Management    | `data-management/`    | Episodic data viewer, labeling, dataset curation, and job orchestration     |
| Synthetic Data     | `synthetic-data/`     | SDG pipelines leveraging NVIDIA Cosmos world foundation models              |
| Training           | `training/`           | Policy training, packaging to TensorRT/ONNX, and model registration         |
| Evaluation         | `evaluation/`         | Software-in-the-loop and hardware-in-the-loop validation pipelines          |
| Fleet Deployment   | `fleet-deployment/`   | Edge deployment via FluxCD GitOps on Azure Arc-enabled Kubernetes           |
| Fleet Intelligence | `fleet-intelligence/` | Production telemetry, on-robot policy analytics, and fleet health reporting |

### Infrastructure

Shared Azure services required across all domains. Terraform modules provision AKS clusters with GPU node pools, AzureML workspaces, Azure Container Registry, Key Vault, managed identities, networking (VNet, subnets, NAT Gateway), and observability (Azure Monitor, DCGM metrics). Domain-specific infrastructure that stands alone (VPN, automation, DNS) deploys from subdirectories within each domain rather than the shared module.

### Data Pipeline

Tooling and infrastructure for capturing real-world robot data and transmitting it to Azure. This domain covers:

- Setup scripts for deploying Azure Arc, Arc-enabled Kubernetes, and data transfer components to edge devices
- Azure Arc and Azure Arc-enabled Kubernetes configuration on edge devices co-located with robots
- ROS 2 episodic data capture scripts for imitation learning (IL) training datasets
- Data transfer orchestration from edge storage to Azure Blob Storage
- Example programs demonstrating episodic recording from physical robot hardware

Episodic data follows the [LeRobot dataset format](https://huggingface.co/docs/lerobot) to maintain compatibility with the broader robotics ML ecosystem.

### Data Management

An episodic data viewer and curator built on top of LeRobot's visualization tooling. The viewer runs locally for development and can optionally be deployed to an Azure-hosted web app through the included setup scripts. Capabilities include:

- Browsing, labeling, and categorizing episodic datasets across Blob Storage containers
- Assigning datasets to training workflows and triggering OSMO or AzureML jobs directly from the viewer
- Evaluating synthetic data generation outputs for quality and diversity
- Reviewing playback video captured during policy evaluation runs
- Curating datasets by filtering, splitting, and merging episode collections

### Synthetic Data

Pipelines for synthetic data generation (SDG) through OSMO workflows and AzureML jobs. This domain will incorporate NVIDIA Cosmos world foundation models for generating photorealistic training data:

- [Cosmos-Transfer](https://github.com/nvidia-cosmos/cosmos-transfer2.5) for converting simulated environments into photorealistic video, bridging the sim-to-real gap for policy training
- [Cosmos-Predict](https://github.com/nvidia-cosmos/cosmos-predict2.5) for generating novel future frames from initial conditions, expanding dataset diversity
- [Cosmos-Reason](https://github.com/nvidia-cosmos/cosmos-reason2) for data curation through physical common-sense reasoning against video clips
- Full example SDG workflows that chain Isaac Sim scene generation with Cosmos Transfer post-processing

The [Cosmos Cookbook](https://github.com/nvidia-cosmos/cosmos-cookbook) provides post-training scripts and recipes that this domain's workflows will reference for model customization.

### Training

End-to-end training pipeline from raw data to packaged, deployable model artifacts. Training code is organized by learning approach, with each approach containing its own source, workflows, and configuration:

| Approach | Directory       | Scope                                                               |
|----------|-----------------|---------------------------------------------------------------------|
| RL       | `training/rl/`  | Reinforcement learning with Isaac Lab (skrl, RSL-RL)                |
| IL       | `training/il/`  | Imitation learning with LeRobot and demonstration datasets          |
| VLA      | `training/vla/` | Vision-language-action model training for generalist robot policies |

Cross-cutting concerns shared across approaches:

- Multi-GPU and multi-node training orchestration through OSMO workflows and AzureML jobs
- Policy export and packaging into TensorRT and ONNX formats for edge inference
- Container image creation with packaged policies, versioned and pushed to the AzureML model registry
- Model distribution through [Azure AI Foundry](https://learn.microsoft.com/azure/ai-foundry/) for centralized model management and deployment
- Setup scripts for deploying training pipelines to OSMO and AzureML compute targets
- Full end-to-end pipelines that chain training, export, packaging, and registration into a single orchestrated workflow

A complete example pipeline demonstrates the full path from trained checkpoint to containerized inference image registered in AzureML.

### Evaluation

Software-in-the-loop (SiL) and hardware-in-the-loop (HiL) validation pipelines for trained policies. Both approaches use Isaac Sim to emulate the target robot, with the trained policy controlling the simulation.

| Approach | Infrastructure                                                                                         | Policy Host                     |
|----------|--------------------------------------------------------------------------------------------------------|---------------------------------|
| SiL      | Any available compute that can serve the policy as an inference endpoint                               | AzureML managed endpoint or AKS |
| HiL      | Target deployment hardware (typically NVIDIA Jetson) running the containerized TensorRT or ONNX policy | Edge device matching production |

Evaluation metrics capture to:

- AzureML experiment tracking for per-episode performance metrics
- Azure Monitor for operational dashboards and Grafana visualization
- Microsoft Fabric Real-Time Intelligence for streaming telemetry analysis

Setup scripts deploy evaluation pipelines to OSMO and AzureML compute targets. Full end-to-end evaluation pipelines orchestrate policy loading, simulation execution, metric collection, and result publishing as a single workflow.

Isaac Sim connects to the deployed policy endpoint, generating control signals and receiving observations to produce evaluation episodes that the Data Management domain can review.

### Fleet Deployment

Edge deployment of packaged policy containers to robots through GitOps. This domain includes:

- [FluxCD](https://fluxcd.io/) GitOps manifests for [Azure Arc-enabled Kubernetes](https://learn.microsoft.com/azure/azure-arc/kubernetes/overview) clusters co-located with robots
- Bootstrap scripts for configuring FluxCD on Arc-connected clusters
- A hot-loading workflow deployed alongside the GitOps configuration that pulls new policy container images and stages them for deployment
- A gating service (running on or near the robot) that explicitly approves staged policies before deployment, with configurable deployment windows

The deployment flow: AzureML model registry publishes a new container image, FluxCD detects the manifest update, the hot-loader pulls and stages the image on the Arc cluster, and the gating service approves deployment to the robot on the operator's schedule.

### Fleet Intelligence

Production monitoring, robotics telemetry, and on-robot policy performance analytics that close the data flywheel. The deployment lifecycle does not end when a policy reaches a robot. This domain captures what happens after deployment and feeds insights back into Data Pipeline and Training.

Capabilities include:

- On-robot policy performance tracking: success/failure rates per episode, grasp accuracy, navigation completion rates, and task-specific KPIs streamed from deployed robots
- Robotics telemetry ingestion into [Microsoft Fabric Real-Time Intelligence](https://learn.microsoft.com/fabric/real-time-intelligence/) for streaming analytics across robot fleets
- Fleet-wide health dashboards via [Azure Monitor](https://learn.microsoft.com/azure/azure-monitor/) and Grafana showing policy version distribution, error rates, latency percentiles, and hardware utilization
- Policy drift detection: automated comparison of production performance against evaluation baselines, triggering alerts when degradation exceeds thresholds
- Integration with [Azure IoT Operations](https://learn.microsoft.com/azure/iot-operations/) for edge telemetry aggregation, device management, and secure data routing from robots to Azure
- Automated retraining triggers that connect fleet telemetry back to the Data Pipeline domain, closing the feedback loop from production observations to new training data
- Setup scripts for deploying Azure IoT Operations to Arc-enabled Kubernetes clusters, configuring telemetry pipelines, and provisioning dashboards and alerting rules

This domain distinguishes itself from Evaluation (which validates policies in simulation before deployment) by focusing on real-world, production-time signals from physical robots operating in uncontrolled environments.

### Simulation Guidance

Simulation environment authoring, including robot asset import (USD, URDF, MJCF), scene configuration, domain randomization, and Isaac Lab task design, is a prerequisite for training and evaluation. NVIDIA provides comprehensive tooling and documentation for these workflows through [Isaac Sim](https://docs.isaacsim.omniverse.nvidia.com/latest/index.html) and the [Isaac Lab Reference Architecture](https://isaac-sim.github.io/IsaacLab/main/source/refs/reference_architecture/index.html).

This repository will not maintain a separate codebase domain for simulation. Instead, the `docs/` directory will provide guidance on:

- Setting up Isaac Sim and Isaac Lab environments for use with this reference architecture
- Importing custom robot assets and configuring scenes for training tasks
- Applying domain randomization to improve sim-to-real policy transfer
- Designing Isaac Lab environments using Manager-based and Direct workflows
- Connecting simulation outputs to the Synthetic Data and Training domains

## Agent Skills and Specification Documents

Each domain will contain specification documents alongside working examples. These specifications serve as structured inputs for [GitHub Copilot Agent Skills](https://code.visualstudio.com/docs/copilot/chat/chat-agent-mode), enabling customers to adapt this reference architecture to their own codebase and infrastructure.

### Specification Structure

Every domain directory will include:

| Artifact                            | Purpose                                                                                    |
|-------------------------------------|--------------------------------------------------------------------------------------------|
| `README.md`                         | Domain overview, quick start, and usage guide                                              |
| `examples/`                         | Complete, runnable examples with code, scripts, and configurations                         |
| `specifications/`                   | Domain specifications describing capabilities, inputs, outputs, and contracts              |
| `specifications/*.specification.md` | Individual specifications that Agent Skills consume to generate customized implementations |
| `.github/skills/`                   | Agent Skill definitions referencing the domain's specifications                            |

Domain documentation lives under the root `docs/` directory rather than inside each domain folder. Each domain has a corresponding subdirectory at `docs/<domain>/` containing detailed guidance, architecture decisions, and tutorials.

### How Agent Skills Use Specifications

Specifications define the contracts and patterns for each domain so that Agent Skills can generate customized implementations:

1. A customer identifies which domains apply to their robotics use case.
2. Agent Skills read the domain specifications to understand available capabilities, required Azure resources, integration points, and configuration options.
3. The customer describes their specific requirements (robot platform, sensor configuration, training framework preferences, deployment topology).
4. Agent Skills generate tailored infrastructure, code, and configuration files that integrate with the customer's existing codebase while following the patterns proven in this reference architecture.

Each customer may have different hardware configurations, Azure subscription topologies, network constraints, and compliance requirements. Specifications capture the variability points so Agent Skills can produce implementations that fit, rather than requiring manual adaptation of generic examples.

### Example Specification Content

A training domain specification might define:

- Supported RL frameworks and their configuration schemas
- Required Azure resources (AzureML workspace, compute targets, storage accounts)
- Container image build patterns for different GPU architectures
- MLflow experiment tracking integration contracts
- Policy export format requirements (TensorRT version, ONNX opset)
- OSMO workflow template parameters and their valid ranges

## Proposed Directory Structure

```text
azure-nvidia-robotics-reference-architecture/
├── infrastructure/                        # Shared Azure IaC and cluster setup
│   ├── terraform/                         # Terraform modules and root configurations
│   ├── setup/                             # Post-IaC Kubernetes and OSMO setup scripts
│   └── specifications/                   # Infrastructure specifications for Agent Skills
├── data-pipeline/                         # Robot-to-cloud data capture
│   ├── setup/                             # Deploy Arc, edge agents, and transfer components
│   ├── arc/                               # Azure Arc configuration and scripts
│   ├── capture/                           # ROS 2 episodic data recording
│   ├── examples/                          # End-to-end data pipeline examples
│   └── specifications/                    # Data pipeline specifications for Agent Skills
├── data-management/                       # Episodic data viewer and curation
│   ├── setup/                             # Deploy viewer to Azure web app
│   ├── viewer/                            # Data viewer application (runs locally or hosted)
│   ├── tools/                             # CLI tools for dataset operations
│   ├── examples/                          # Data management workflow examples
│   └── specifications/                    # Data management specifications for Agent Skills
├── synthetic-data/                        # Synthetic data generation pipelines
│   ├── workflows/                         # OSMO and AzureML SDG job definitions
│   ├── cosmos/                            # Cosmos model integration and configs
│   ├── examples/                          # SDG pipeline examples
│   └── specifications/                    # SDG specifications for Agent Skills
├── training/                              # Policy training and model packaging
│   ├── setup/                             # Deploy training pipelines to OSMO and AzureML
│   ├── rl/                                # Reinforcement learning (skrl, RSL-RL)
│   ├── il/                                # Imitation learning (LeRobot)
│   ├── vla/                               # Vision-language-action model training
│   ├── pipelines/                         # End-to-end train, export, package, register
│   ├── packaging/                         # TensorRT/ONNX export and containerization
│   ├── examples/                          # Training pipeline examples
│   └── specifications/                    # Training specifications for Agent Skills
├── evaluation/                            # SiL and HiL validation
│   ├── setup/                             # Deploy evaluation pipelines to OSMO and AzureML
│   ├── sil/                               # Software-in-the-loop pipelines
│   ├── hil/                               # Hardware-in-the-loop pipelines
│   ├── pipelines/                         # End-to-end evaluation workflows
│   ├── metrics/                           # Metric collection and reporting
│   ├── examples/                          # Evaluation pipeline examples
│   └── specifications/                    # Evaluation specifications for Agent Skills
├── fleet-deployment/                      # Edge deployment via GitOps
│   ├── gitops/                            # FluxCD manifests and bootstrap scripts
│   ├── gating/                            # Policy approval and scheduling service
│   ├── examples/                          # Fleet deployment workflow examples
│   └── specifications/                    # Fleet deployment specifications for Agent Skills
├── fleet-intelligence/                    # Production telemetry and policy analytics
│   ├── setup/                             # Deploy IoT Operations, telemetry, and dashboards
│   ├── telemetry/                         # On-robot telemetry capture and routing
│   ├── dashboards/                        # Grafana and Azure Monitor configurations
│   ├── drift/                             # Policy drift detection and alerting
│   ├── examples/                          # Fleet intelligence pipeline examples
│   └── specifications/                    # Fleet intelligence specifications for Agent Skills
├── scripts/                               # Cross-domain CI, linting, and security tooling
├── external/                              # Cloned external repositories for reference
├── docs/                                  # All domain and cross-domain documentation
│   ├── infrastructure/                    # Infrastructure architecture and guides
│   ├── data-pipeline/                     # Data pipeline guides
│   ├── data-management/                   # Data management guides
│   ├── synthetic-data/                    # SDG guides
│   ├── training/                          # Training guides
│   ├── evaluation/                        # Evaluation guides
│   ├── fleet-deployment/                  # Fleet deployment guides
│   ├── fleet-intelligence/                # Fleet intelligence guides
│   ├── simulation/                        # Simulation setup and guidance
│   └── contributing/                      # Repository contributing and architecture design decisions
└── .github/                               # Agent Skills, instructions, and CI workflows
    └── skills/                            # Domain-linked Agent Skill definitions
```

## External References

| Resource                                                                                                                    | Relevance                                                           |
|-----------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [NVIDIA Isaac Lab](https://isaac-sim.github.io/IsaacLab/main/index.html)                                                    | Robot learning framework for simulation-based RL and IL training    |
| [NVIDIA Isaac Sim](https://docs.isaacsim.omniverse.nvidia.com/latest/index.html)                                            | Physics simulation platform underlying Isaac Lab                    |
| [Isaac Lab Reference Architecture](https://isaac-sim.github.io/IsaacLab/main/source/refs/reference_architecture/index.html) | End-to-end robot learning workflow from asset import to deployment  |
| [NVIDIA Cosmos Platform](https://github.com/nvidia-cosmos)                                                                  | World foundation models for physical AI (Predict, Transfer, Reason) |
| [Cosmos-Transfer2.5](https://github.com/nvidia-cosmos/cosmos-transfer2.5)                                                   | Sim-to-real photorealistic video generation from simulation         |
| [Cosmos-Predict2.5](https://github.com/nvidia-cosmos/cosmos-predict2.5)                                                     | Future state prediction and world simulation                        |
| [Cosmos Cookbook](https://github.com/nvidia-cosmos/cosmos-cookbook)                                                         | Post-training recipes for Cosmos model customization                |
| [NVIDIA OSMO](https://developer.nvidia.com/osmo)                                                                            | Cloud-native orchestration for AI simulation and training           |
| [LeRobot](https://huggingface.co/docs/lerobot)                                                                              | Hugging Face robotics ML library for imitation learning             |
| [Azure Machine Learning](https://learn.microsoft.com/azure/machine-learning/)                                               | ML model training, registry, and deployment on Azure                |
| [Azure AI Foundry](https://learn.microsoft.com/azure/ai-foundry/)                                                           | Centralized model management and deployment platform                |
| [Azure Arc-enabled Kubernetes](https://learn.microsoft.com/azure/azure-arc/kubernetes/overview)                             | Kubernetes management for edge clusters connected to Azure          |
| [Azure IoT Operations](https://learn.microsoft.com/azure/iot-operations/)                                                   | Edge telemetry aggregation and device management for robotics       |
| [FluxCD](https://fluxcd.io/)                                                                                                | GitOps toolkit for Kubernetes continuous delivery                   |
| [Azure Monitor](https://learn.microsoft.com/azure/azure-monitor/)                                                           | Observability and metrics for Azure and hybrid workloads            |
| [Microsoft Fabric RTI](https://learn.microsoft.com/fabric/real-time-intelligence/)                                          | Streaming telemetry analysis for fleet intelligence                 |
