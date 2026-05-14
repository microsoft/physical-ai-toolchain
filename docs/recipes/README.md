# Recipes

Step-by-step guides that take you from a standing start to a working result. Each recipe is self-contained with prerequisites, runnable commands, and verification steps.

> [!NOTE]
> Recipes assume deployed infrastructure. Complete the [Quickstart](../getting-started/quickstart.md) first if you have not provisioned Azure resources.

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

- [Getting Started](../getting-started/README.md) — infrastructure deployment and first training job
- [Training Guide](../training/README.md) — reference documentation for RL and IL workflows
- [Data Pipeline](../data-pipeline/README.md) — edge recording configuration reference
- [Scripts Reference](../reference/scripts.md) — CLI parameter tables for all submission scripts

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
