# Recipes

Step-by-step guides that take you from a standing start to a working result. Each recipe is self-contained with prerequisites, runnable commands, and verification steps.

Recipes are organized two ways: by **tier** (how much infrastructure you run) and by **topic** (what task you are doing). New users should start with the tier ladder below; the [default path is T0 — Dev](tier-0-dev/README.md), which runs the full training lifecycle loop on one laptop with zero cloud and zero Kubernetes.

> [!NOTE]
> Only the cloud tiers (**T2+**) and the topic recipes under [Training](#training) and [Data Collection](#data-collection) assume deployed Azure infrastructure. [T0 — Dev](tier-0-dev/README.md) and the storage-only [T1 — Lab](tier-1-lab/README.md) do not. If a recipe needs cloud resources, complete the [Quickstart](../getting-started/quickstart.md) first. For the canonical tier definitions, see the [tier model](../design/tier-model.md).

## 🪜 Pick a Tier

Each tier states the minimum infrastructure it assumes. Start at the default (T0) and graduate only when a real constraint forces it.

| T# | Recipe                                         | Minimum infrastructure                            | Status          |
|----|------------------------------------------------|---------------------------------------------------|-----------------|
| T0 | [T0 — Dev](tier-0-dev/README.md)               | One laptop + one robot. ROS 2 + Docker. No cloud. | Default         |
| T1 | [T1 — Lab](tier-1-lab/README.md)               | T0 + one Azure Blob storage account.              | Authored        |
| T2 | [T2 — Pilot](tier-2-pilot/README.md)           | AzureML + storage + registry + MLflow. No k8s.    | Recommended     |
| T3 | [T3 — Production](tier-3-production/README.md) | T2 + single-site local k3s + FluxCD. No Arc.      | Advanced (stub) |
| T4 | [T4 — Scale](tier-4-scale/README.md)           | Multi-site Arc + AKS/Flux + gating.               | Advanced (stub) |
| T5 | [T5 — Operate](tier-5-operate/README.md)       | T4 + IoT Operations + Fabric RTI (roadmap).       | Roadmap (stub)  |

## 🗂️ Topic Recipes by Tier

The existing topic recipes assume the tier shown below. They are unchanged by the tier ladder. This table only classifies them.

| Topic recipe                                                                          | Assumes tier | Minimum infrastructure                             |
|---------------------------------------------------------------------------------------|--------------|----------------------------------------------------|
| [Configuring Edge Data Recording](data-collection/configuring-edge-data-recording.md) | T0 — Dev     | Jetson / robot, ROS 2. No cloud.                   |
| [Preparing Datasets for Training](data-collection/preparing-datasets-for-training.md) | T0–T1        | Local for T0; Azure CLI + Blob for cloud datasets. |
| [Your First LeRobot Training Job](training/your-first-lerobot-training-job.md)        | T2 — Pilot   | Deployed infrastructure, OSMO running.             |
| [Your First RL Training Job](training/your-first-rl-training-job.md)                  | T2 — Pilot   | Deployed infrastructure, OSMO running.             |
| [End-to-End LeRobot Pipeline](training/end-to-end-lerobot-pipeline.md)                | T2 — Pilot   | Deployed infrastructure, OSMO running.             |

## 🚀 Pick a Recipe

| Goal                                          | Recipe                                                                                | Time   |
|-----------------------------------------------|---------------------------------------------------------------------------------------|--------|
| Train an RL policy                            | [Your First RL Training Job](training/your-first-rl-training-job.md)                  | 30 min |
| Train a LeRobot policy                        | [Your First LeRobot Training Job](training/your-first-lerobot-training-job.md)        | 30 min |
| Run the full train → eval → register pipeline | [End-to-End LeRobot Pipeline](training/end-to-end-lerobot-pipeline.md)                | 60 min |
| Configure edge recording                      | [Configuring Edge Data Recording](data-collection/configuring-edge-data-recording.md) | 20 min |
| Prepare a dataset for training                | [Preparing Datasets for Training](data-collection/preparing-datasets-for-training.md) | 30 min |

## 📖 Recipe Catalog

### Training

| Recipe                                                                         | Description                                            | Prerequisites                                |
|--------------------------------------------------------------------------------|--------------------------------------------------------|----------------------------------------------|
| [Your First RL Training Job](training/your-first-rl-training-job.md)           | Submit an Isaac Lab RL training job on OSMO with SKRL  | Deployed infrastructure, OSMO running        |
| [Your First LeRobot Training Job](training/your-first-lerobot-training-job.md) | Submit a LeRobot behavioral cloning job on OSMO        | Deployed infrastructure, HuggingFace dataset |
| [End-to-End LeRobot Pipeline](training/end-to-end-lerobot-pipeline.md)         | Orchestrate train → evaluate → register in one command | Completed basic LeRobot recipe               |

### Data Collection

| Recipe                                                                                | Description                                                         | Prerequisites           |
|---------------------------------------------------------------------------------------|---------------------------------------------------------------------|-------------------------|
| [Configuring Edge Data Recording](data-collection/configuring-edge-data-recording.md) | Set up ROS 2 edge recording on Jetson with chunking and compression | Jetson device, ROS 2    |
| [Preparing Datasets for Training](data-collection/preparing-datasets-for-training.md) | Download, inspect, and validate datasets for LeRobot training       | Python 3.12+, Azure CLI |

## 🔗 Related Documentation

- [Tier model (canonical reference)](../design/tier-model.md): tier IDs, boundaries, and vocabulary
- [Getting Started](../getting-started/README.md): infrastructure deployment and first training job
- [Training Guide](../training/README.md): reference documentation for RL and IL workflows
- [Data Pipeline](../data-pipeline/README.md): edge recording configuration reference
- [Scripts Reference](../reference/scripts.md): CLI parameter tables for all submission scripts

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
