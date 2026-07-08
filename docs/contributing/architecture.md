---
sidebar_position: 2
title: Repository Architecture
description: Tiered architecture for the Physical AI Toolchain, organized around the T0–T5 adoption ladder with the eight lifecycle domains presented as components adopted per tier.
ms.date: 2026-06-12
ms.topic: concept
---

## Overview

This repository provides Azure-integrated infrastructure and tooling for NVIDIA Isaac Lab-based robotics training, inference, and orchestration through NVIDIA OSMO and Azure Machine Learning. The architecture is organized as a **graduated adoption ladder** of six tiers (`T0`–`T5`) rather than a single all-or-nothing stack.

Each tier states the minimum edge and cloud infrastructure required to reach a concrete goal, and each tier is a legitimate stopping point. The eight lifecycle domains (Infrastructure, Data Pipeline, Data Management, Synthetic Data, Training, Evaluation, fleet delivery, fleet intelligence) are **components adopted per tier**, not prerequisites a user must stand up before doing anything useful.

> [!IMPORTANT]
> Tier IDs, stage names, boundaries, the autonomy ladder, and the fleet vocabulary rules are defined once in the canonical [Tier Model](../design/tier-model.md). This document cites those definitions rather than redefining them. If a tier boundary or name needs to change, change it there first.

**Reference goal (Goal: Full Training Lifecycle):** capture demonstrations on a robot, train an imitation policy, validate it, and run that policy back on the robot, the full loop for one task. Goal: Full Training Lifecycle is fully achievable at `T0`–`T2` with manual deployment and no Kubernetes, Arc, or fleet infrastructure.

## The Tier Ladder

`T0` is the documented **default** starting path. `T2` is the **recommended production** path. `T3`–`T5` are **advanced**. The pairing of stable ID and stage name (`T# — Name`) is canonical; see the [Tier Model](../design/tier-model.md#canonical-tier-table) for the authoritative table.

| T# | Stage name | Operator reach / scope                   | Edge infra              | Cloud infra                                        | Status                       |
|----|------------|------------------------------------------|-------------------------|----------------------------------------------------|------------------------------|
| T0 | Dev        | Laptop + 1 robot (default)               | ROS 2 + Docker only     | None                                               | Shipped (default)            |
| T1 | Lab        | One site, a few robots, shared GPU       | Shared disk (NFS/SMB)   | One Blob account (optional AzureML / MLflow)       | Shipped                      |
| T2 | Pilot      | One site, at scale, team (recommended)   | None beyond Docker      | AzureML + storage + model registry + MLflow        | Shipped (recommended)        |
| T3 | Production | Single site, declarative deployment      | Local k3s + FluxCD      | Same as T2 (no Arc)                                | Advanced                     |
| T4 | Scale      | Multiple sites you cannot directly reach | Arc + AKS/Flux + gating | T2 + cross-site connectivity / identity            | Advanced (fleet delivery)    |
| T5 | Operate    | Fleet-wide cognition (roadmap)           | + Azure IoT Operations  | + Fabric Real-Time Intelligence + drift/retraining | Roadmap (fleet intelligence) |

### Boundaries

- **Multi-site boundary (Arc):** falls between `T3` and `T4`. Arc becomes necessary only when robots span multiple sites you cannot reach from a single operator network.
- **Intelligence boundary:** falls between `T4` and `T5`. `T4` delivers and gates policies; it does not run drift detection, retraining, or aggregate analytics. Those are `T5`.

> [!NOTE]
> **Roadmap honesty.** `T5` (Operate / fleet intelligence) is on the roadmap and not yet available. The fleet-intelligence domain is currently specified, with implementation planned. The implemented center of gravity remains infrastructure provisioning plus training, data management, and evaluation tooling.

### Fleet Vocabulary

This document follows the canonical [vocabulary rules](../design/tier-model.md#vocabulary-rules):

- **"Fleet" means a fleet of robots only.** It never refers to Kubernetes clusters, nor to Azure Kubernetes Fleet Manager. Cluster-level concerns are written as "clusters" or "sites".
- **Fleet delivery (`T4`)** is the delivery and connectivity control plane: getting a validated policy onto robots across sites you cannot directly reach, with a safety gate before a policy swaps on a physical arm.
- **Fleet intelligence (`T5`)** is the cognition layer: drift detection, automated retraining triggers, and aggregate telemetry analytics. It is the roadmap/placeholder concern.

## Tiers in Detail

Each tier below adds a defined slice of edge and cloud infrastructure on top of the previous one, and pulls in additional lifecycle domains. Infra details are drawn from Section 5 of the [Tiered Architecture Proposal](../design/tiered-architecture-proposal.md).

### T0 — Dev

One robot, one laptop. No cloud, no Kubernetes. This is the honest floor for Goal: Full Training Lifecycle. This path exists in the code today: training detects available CUDA devices at runtime, evaluation has an explicit local path, and the dataviewer defaults to `local` mode.

| Concern      | Implementation                                                              |
|--------------|-----------------------------------------------------------------------------|
| Capture      | ROS 2 bag recording to local disk. No Arc, no ACSA, no PVC.                 |
| Move data    | `cp` or `rsync` from robot to laptop.                                       |
| Curate       | Dataviewer in `local` mode on the laptop.                                   |
| Train        | `lerobot-train` on the laptop, on CPU or a local GPU.                       |
| Track        | Training outputs written to local disk; hosted tracking enters at T2.       |
| Validate     | `run-local-lerobot-eval.py` / `play.py` locally.                            |
| Run on robot | The ACT inference node as a plain process or container. No Flux, no gating. |

| Surface | Infrastructure      |
|---------|---------------------|
| Edge    | ROS 2 + Docker only |
| Cloud   | None                |

**Domains active:** Data Pipeline (local capture), Data Management (local viewer), Training, Evaluation.

**Graduate when:** no local GPU; the task needs many training iterations as conditions vary; or a second person needs the data.

### T1 — Lab

One site, a few robots, a shared GPU box. The first cloud resource added is a single storage account.

| Concern      | Implementation                                                              | Delta from T0            |
|--------------|-----------------------------------------------------------------------------|--------------------------|
| Capture      | ROS 2 recording to shared NFS/SMB, or each robot `rsync`s up.               | shared disk              |
| Move data    | `azcopy` or `az storage blob upload-batch` to one Blob container.           | + Blob storage           |
| Curate       | Dataviewer in `azure` mode against that container (managed identity / SAS). | viewer → cloud           |
| Train        | Local shared GPU box, or first optional reach to AzureML on saturation.     | optional cloud GPU       |
| Track        | Local training outputs, optionally promoted to managed MLflow.              | optional hosted tracking |
| Run on robot | Plain container per robot; hand-update 2–3 robots via `docker pull`.        | unchanged                |

| Surface | Infrastructure                                          |
|---------|---------------------------------------------------------|
| Edge    | Shared disk (NFS/SMB)                                   |
| Cloud   | One Blob storage account; optionally AzureML and MLflow |

No Kubernetes, no Arc, no Flux.

**Domains active:** adds cloud-backed Data Management; Synthetic Data optional.

**Graduate when:** training scale or team size outgrows one GPU box; dataset governance and catalogs become necessary.

### T2 — Pilot

One site, several robots, real training scale and collaboration. This is the tier where cloud training genuinely becomes the default rather than an option, and it is the **recommended production** path.

| Concern      | Implementation                                                                  | Delta from T1      |
|--------------|---------------------------------------------------------------------------------|--------------------|
| Train        | AzureML or OSMO as default: multi-GPU, queued jobs, multiple people, VLA scale. | cloud GPU standard |
| Registry     | Model registry and versioning become load-bearing.                              | + registry         |
| Curate       | Dataviewer deployed as a shared web app rather than localhost.                  | hosted viewer      |
| Capture      | ACSA optional if disk pressure or unattended recording warrants it.             | optional ACSA      |
| Run on robot | Manual `docker pull` per robot; hand-updating reachable robots is tractable.    | unchanged          |

| Surface | Infrastructure                                     |
|---------|----------------------------------------------------|
| Edge    | None beyond Docker                                 |
| Cloud   | AzureML workspace, storage, model registry, MLflow |

Still no Kubernetes, no Arc, no fleet plane.

**Domains active:** full Training (RL / IL / VLA), Evaluation (SiL / HiL), Data Management, Synthetic Data.

**Graduate when:** the number of robots or the update cadence makes hand-updating each robot error-prone and version skew becomes a real problem, while all robots are still at one reachable site.

### T3 — Production

Local k3s + FluxCD, **no Arc**. Several robots at one site you control, updated often enough that manual `docker pull` causes version skew, but all reachable from a single operator network. This tier proves declarative, GitOps-style deployment does not require Azure Arc. Single-node k3s idles near zero, and the expensive part of the "fleet" stack (Arc enrollment, identity, policy) is paid only at the multi-site boundary.

| Concern          | Implementation                                                                    |
|------------------|-----------------------------------------------------------------------------------|
| Runtime          | A single local k3s node (a ~60 MB binary) at the site.                            |
| Delivery         | FluxCD reconciles robots to Git-declared desired state; rollback is a Git revert. |
| Version control  | Git becomes the single source of truth for which robot runs which policy.         |
| Safe swap        | Optional gating before a policy swaps on hardware.                                |
| Train and curate | Same as T2 (AzureML, registry, MLflow, hosted viewer).                            |

| Surface | Infrastructure                         |
|---------|----------------------------------------|
| Edge    | One local k3s node + FluxCD            |
| Cloud   | Same as T2 (no Arc, no IoT Operations) |

**Domains active:** adds `fleet-deployment` (GitOps + gating) at single-site scope.

**Graduate when:** robots span multiple sites, or sites become unreachable from a single operator network. That is the point at which a cross-site reachability and identity broker becomes genuinely necessary.

### T4 — Scale

Multi-site **fleet delivery** is the legitimate top of the necessary ladder. This is the delivery control plane: getting validated policies onto robots across sites you cannot directly reach, safely. The defining difference from `T3` is **multiple sites**, which is exactly what makes Arc necessary as the cross-site reachability and identity broker.

| Concern                   | Implementation                                                                |
|---------------------------|-------------------------------------------------------------------------------|
| Connectivity and identity | Azure Arc as the reachability and identity broker across sites.               |
| Runtime                   | AKS or Arc-enabled Kubernetes at each site.                                   |
| Delivery                  | FluxCD GitOps; per-site desired state recorded in Git.                        |
| Safe swap                 | Gating service approves deployment windows before a policy swaps on hardware. |

| Surface | Infrastructure                                   |
|---------|--------------------------------------------------|
| Edge    | Arc + AKS/Flux + gating                          |
| Cloud   | T2 + cross-site connectivity, identity, registry |

**Explicitly excluded at `T4`:** drift detection, automated retraining, aggregate telemetry analytics. This tier delivers and gates; it does not run fleet intelligence.

**Domains active:** `fleet-deployment` at multi-site scope (fleet delivery terminus).

**Graduate when:** the operator explicitly wants production signals to drive retraining and fleet-wide health analytics. This is a deliberate decision, not an automatic consequence of scale.

### T5 — Operate

> [!WARNING]
> **Roadmap / not shipped.** `T5` is **fleet intelligence**, the aspirational cognition layer. The fleet-intelligence domain is currently specified, with implementation planned. The components below are documented intent, not working capability. Treat this tier as a roadmap direction.

Drift detection, automated retraining triggers, aggregate telemetry, and health analytics over the robot fleet.

| Concern              | Implementation                                    | Status      |
|----------------------|---------------------------------------------------|-------------|
| Edge telemetry       | Azure IoT Operations MQTT aggregation             | Placeholder |
| Analytics            | Microsoft Fabric Real-Time Intelligence, Grafana  | Placeholder |
| Drift and retraining | Drift detection, retraining triggers, closed loop | Placeholder |

| Surface | Infrastructure                                               |
|---------|--------------------------------------------------------------|
| Edge    | + Azure IoT Operations                                       |
| Cloud   | + Microsoft Fabric Real-Time Intelligence + drift/retraining |

`T5` decomposes into an ordered **autonomy ladder** (`T5.0`–`T5.3`) rather than a single leap; autonomy is a different axis from infrastructure reach. See [The Autonomy Ladder](ROADMAP.md#the-autonomy-ladder-t50t53) in the roadmap. Human-in-the-loop gating is recommended over fully autonomous retraining.

**Domains active:** `fleet-intelligence` (roadmap/placeholder).

## Lifecycle Domains

The codebase reorganizes around eight lifecycle domains for robotics and physical AI, each built on current Azure services and NVIDIA's Physical AI Stack. The tiers above describe *which subset* of these domains a user adopts, and in what order; this section describes each domain in detail. Each domain maps to a root-level directory.

| Domain             | Directory             | First active tier | Scope                                                                          |
|--------------------|-----------------------|-------------------|--------------------------------------------------------------------------------|
| Infrastructure     | `infrastructure/`     | T1                | Shared Azure services: AKS, AzureML, networking, storage, observability        |
| Data Pipeline      | `data-pipeline/`      | T0                | Robot-to-cloud data capture via ROS 2 episodic recording (Arc from T4)         |
| Data Management    | `data-management/`    | T0                | Episodic data viewer, labeling, dataset curation, and job orchestration        |
| Synthetic Data     | `synthetic-data/`     | T1                | SDG pipelines leveraging NVIDIA Cosmos world foundation models                 |
| Training           | `training/`           | T0                | Policy training, packaging to TensorRT/ONNX, and model registration            |
| Evaluation         | `evaluation/`         | T0                | Software-in-the-loop and hardware-in-the-loop validation pipelines             |
| Fleet delivery     | `fleet-deployment/`   | T3                | Edge delivery via FluxCD GitOps; Arc-enabled multi-site delivery at T4         |
| Fleet intelligence | `fleet-intelligence/` | T5 (roadmap)      | Production telemetry, on-robot policy analytics, and drift detection (roadmap) |

### Infrastructure

Shared Azure services required from `T1` upward. Terraform modules provision AKS clusters with GPU node pools, AzureML workspaces, Azure Container Registry, Key Vault, managed identities, networking (VNet, subnets, NAT Gateway), and observability (Azure Monitor, DCGM metrics). Domain-specific infrastructure that stands alone (VPN, automation, DNS) deploys from subdirectories within each domain rather than the shared module. At `T0` no Azure infrastructure is required.

### Data Pipeline

Tooling and infrastructure for capturing real-world robot data and transmitting it to Azure. At `T0` this is purely local (ROS 2 bag recording plus `cp`/`rsync`); Azure Arc edge agents enter only at the multi-site `T4` boundary. This domain covers:

- Setup scripts for deploying Azure Arc, Arc-enabled Kubernetes, and data transfer components to edge devices (multi-site only)
- ROS 2 episodic data capture scripts for imitation learning (IL) training datasets
- Data transfer orchestration from edge storage to Azure Blob Storage
- Example programs demonstrating episodic recording from physical robot hardware

Episodic data follows the [LeRobot dataset format](https://huggingface.co/docs/lerobot) to maintain compatibility with the broader robotics ML ecosystem.

### Data Management

An episodic data viewer and curator built on top of LeRobot's visualization tooling. The viewer runs locally for development (`T0`) and can optionally be deployed to an Azure-hosted web app (`T2`) through the included setup scripts. Capabilities include:

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

> [!NOTE]
> Data augmentation is an optional axis (A0–A2) orthogonal to the `T0`–`T5` ladder, recommended only when data is scarce. It is **not** part of Goal: Full Training Lifecycle. See the augmentation axis in the [Tier Model](../design/tier-model.md#cross-cutting-augmentation-axis-reference).

### Training

End-to-end training pipeline from raw data to packaged, deployable model artifacts. The local path runs at `T0`; cloud GPU becomes the default at `T2`. Training code is organized by learning approach, with each approach containing its own source, workflows, and configuration:

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

Software-in-the-loop (SiL) and hardware-in-the-loop (HiL) evaluation pipelines for trained policies. SiL runs locally at `T0`; both approaches use Isaac Sim to emulate the target robot, with the trained policy controlling the simulation.

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

### Fleet delivery

Edge delivery of packaged policy containers to robots through GitOps, the `fleet-deployment` domain. At `T3` this is single-site (local k3s + FluxCD, no Arc); at `T4` it becomes multi-site fleet delivery with Azure Arc as the cross-site reachability and identity broker. This domain includes:

- [FluxCD](https://fluxcd.io/) GitOps manifests for local k3s (`T3`) or [Azure Arc-enabled Kubernetes](https://learn.microsoft.com/azure/azure-arc/kubernetes/overview) clusters (`T4`) co-located with robots
- Bootstrap scripts for configuring FluxCD on k3s or Arc-connected clusters
- A hot-loading workflow deployed alongside the GitOps configuration that pulls new policy container images and stages them for deployment
- A gating service (running on or near the robot) that explicitly approves staged policies before deployment, with configurable deployment windows

The delivery flow: AzureML model registry publishes a new container image, FluxCD detects the manifest update, the hot-loader pulls and stages the image, and the gating service approves deployment to the robot on the operator's schedule. Fleet delivery delivers and gates policies; it does **not** run drift detection, retraining, or aggregate analytics. Those are fleet intelligence (`T5`).

### Fleet intelligence

> [!WARNING]
> **Roadmap / not shipped.** The fleet-intelligence domain is currently specified, with implementation planned. The capabilities below are documented intent for `T5`, not working code.

Production monitoring, robotics telemetry, and on-robot policy performance analytics that close the data flywheel. This domain captures what happens after deployment and feeds insights back into Data Pipeline and Training. Planned capabilities include:

- On-robot policy performance tracking: success/failure rates per episode, grasp accuracy, navigation completion rates, and task-specific KPIs streamed from deployed robots
- Robotics telemetry ingestion into [Microsoft Fabric Real-Time Intelligence](https://learn.microsoft.com/fabric/real-time-intelligence/) for streaming analytics across robot fleets
- Fleet-wide health dashboards via [Azure Monitor](https://learn.microsoft.com/azure/azure-monitor/) and Grafana showing policy version distribution, error rates, latency percentiles, and hardware utilization
- Policy drift detection: automated comparison of production performance against evaluation baselines, triggering alerts when degradation exceeds thresholds
- Integration with [Azure IoT Operations](https://learn.microsoft.com/azure/iot-operations/) for edge telemetry aggregation, device management, and secure data routing from robots to Azure
- Automated retraining triggers that connect fleet telemetry back to the Data Pipeline domain, closing the feedback loop from production observations to new training data

This domain distinguishes itself from Evaluation (which validates policies in simulation before deployment) by focusing on real-world, production-time signals from physical robots. Its autonomy decomposes into the [autonomy ladder](ROADMAP.md#the-autonomy-ladder-t50t53) (`T5.0`–`T5.3`). Fully autonomous retraining is a foot-gun, and human-in-the-loop gating is recommended.

### Simulation Guidance

Simulation environment authoring, including robot asset import (USD, URDF, MJCF), scene configuration, domain randomization, and Isaac Lab task design, is a prerequisite for training and evaluation. NVIDIA provides comprehensive tooling and documentation for these workflows through [Isaac Sim](https://docs.isaacsim.omniverse.nvidia.com/latest/index.html) and the [Isaac Lab Reference Architecture](https://isaac-sim.github.io/IsaacLab/main/source/refs/reference_architecture/index.html).

This repository will not maintain a separate codebase domain for simulation. Instead, the `docs/` directory will provide guidance on:

- Setting up Isaac Sim and Isaac Lab environments for use with this reference architecture
- Importing custom robot assets and configuring scenes for training tasks
- Applying domain randomization to improve sim-to-real policy transfer
- Designing Isaac Lab environments using Manager-based and Direct workflows
- Connecting simulation outputs to the Synthetic Data and Training domains

## Source Code Organization

The `src/` directory currently contains three packages, being migrated into the per-domain layout described above:

| Package      | Contents                                                                                                |
|--------------|---------------------------------------------------------------------------------------------------------|
| `common/`    | Shared CLI argument parsing utilities                                                                   |
| `training/`  | Isaac Lab training scripts with skrl, RSL-RL, and LeRobot integration plus Azure MLflow metric tracking |
| `inference/` | Policy export, playback, and inference node scripts                                                     |

Training scripts and Python code act as integration shims between NVIDIA's open-source repositories and Azure connectivity features: MLflow from AzureML for tracking training metrics, logs, and checkpoints; the AzureML model registry for checkpoint versioning; and Azure Blob Storage for dataset access.

The root `pyproject.toml` serves local development dependency management, providing module availability for intellisense and verification. This setup is not intended for building publishable Python packages; the build target only packages `training/rl` into a wheel for in-container use.

## Agentic Tooling

This project uses [GitHub Copilot](https://code.visualstudio.com/docs/copilot/overview) agents, instructions, prompts, and skills to automate development workflows. Tooling comes from two sources: the HVE-Core extension (shared across Microsoft HVE projects) and project-specific artifacts defined in `.github/`.

### HVE-Core Extension

The [hve-core-all](https://marketplace.visualstudio.com/items?itemName=ise-hve-essentials.hve-core-all) VS Code extension provides shared agentic tooling:

| Artifact Type | Count | Examples                                                                         |
|---------------|-------|----------------------------------------------------------------------------------|
| Agents        | 33    | RPI workflow, backlog management, PR creation                                    |
| Instructions  | 24    | Coding standards (Bash, C#, Python, Terraform, Bicep), commit messages, markdown |
| Prompts       | 27    | ADO work items, GitHub issues, security planning, PR descriptions                |
| Skills        | 2     | PR reference generation, video-to-GIF conversion                                 |

HVE-Core artifacts are registered via the extension's `package.json` `contributes` section and loaded when the extension activates.

### Project Copilot Artifacts

This repository defines project-specific artifacts in `.github/` that extend HVE-Core with domain knowledge:

| Artifact Type | Count | Purpose                                                                          |
|---------------|-------|----------------------------------------------------------------------------------|
| Agents        | 2     | OSMO training manager, dataviewer developer                                      |
| Instructions  | 4     | Copilot instructions, dataviewer conventions, documentation style, shell scripts |
| Prompts       | 4     | OSMO training submission, LeRobot pipeline, dataviewer workflows                 |
| Skills        | 2     | Dataviewer interaction, OSMO LeRobot training                                    |

Project artifacts are auto-discovered by VS Code from the `.github/` directory without explicit registration.

Two workflow chains compose these artifacts:

- **OSMO Training Manager**: `osmo-training-manager` agent → `osmo-lerobot-training` skill → training submission prompts
- **Dataviewer Developer**: `dataviewer-developer` agent → `dataviewer` skill → dataviewer instruction conventions

### Artifact Types and Loading

Each artifact type uses YAML frontmatter to declare behavior:

| Artifact     | File Pattern        | Key Frontmatter                | Loading                                      |
|--------------|---------------------|--------------------------------|----------------------------------------------|
| Agents       | `*.agent.md`        | `mode`, `tools`, `description` | Auto-discovered from `.github/agents/`       |
| Instructions | `*.instructions.md` | `applyTo`, `description`       | Auto-discovered from `.github/instructions/` |
| Prompts      | `*.prompt.md`       | `mode`, `description`, `tools` | Auto-discovered from `.github/prompts/`      |
| Skills       | `SKILL.md`          | N/A (referenced by agents)     | Referenced via `copilot-skill:` URI          |

HVE-Core artifacts follow the same patterns but load through extension contribution points rather than workspace auto-discovery.

For the detailed per-artifact inventory and workflow chain diagrams, see [Copilot Artifacts](../reference/copilot-artifacts.md).

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
physical-ai-toolchain/
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
├── fleet-deployment/                      # Fleet delivery: edge delivery via GitOps
│   ├── gitops/                            # FluxCD manifests and bootstrap scripts
│   ├── gating/                            # Policy approval and scheduling service
│   ├── examples/                          # Fleet delivery workflow examples
│   └── specifications/                    # Fleet delivery specifications for Agent Skills
├── fleet-intelligence/                    # Fleet intelligence: telemetry and analytics (roadmap)
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
│   ├── fleet-deployment/                  # Fleet delivery guides
│   ├── fleet-intelligence/                # Fleet intelligence guides
│   ├── simulation/                        # Simulation setup and guidance
│   └── contributing/                      # Repository contributing and architecture design decisions
└── .github/                               # Agent Skills, instructions, and CI workflows
    └── skills/                            # Domain-linked Agent Skill definitions
```

## External References

| Resource                                                                                                                    | Relevance                                                           |
|-----------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [Tier Model](../design/tier-model.md)                                                                                       | Canonical reference for tier IDs, names, boundaries, and vocabulary |
| [Tiered Architecture Proposal](../design/tiered-architecture-proposal.md)                                                   | Rationale and per-tier detail behind the T0–T5 ladder               |
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
