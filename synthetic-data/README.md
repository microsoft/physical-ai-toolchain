# Synthetic Data

Synthetic data generation (SDG) pipelines leveraging NVIDIA Cosmos family of models.

The synthetic data generation skills are installed from
 [Nvidia Skills Catalog](https://github.com/NVIDIA/skills).

## 📂 Directory Structure

| Directory            | Purpose                                              |
|----------------------|------------------------------------------------------|
| `.claude/skills/`    | Skills for synthetic data generation scenarios       |
| `.agents/skills`     | Points to .agent/skills                              |
| `.codex/skills`      | Points to .agent/skills                              |
| `CLAUDE.md`          | Global agent instruction                             |
| `AGENTS.md`          | Points to CLAUDE.md                                  |

## Quick Start

This folder currently supports the following workflows directly mapped from
 [Nvidia Physical AI Data Factory bluepring](https://github.com/NVIDIA/physical-ai-data-factory):

1. **Defect Image Generation (DIG)**
2. **Video Data Augmentation (VDA)**

### Prerequisit

1. A OSMO 6.3 cluster on Azure with enough GPU to run the target workflow.

### Sample prompts to get oriented

```md
I'm interested in Video Data Augmentation,
- what kind of compute infra do I need?
- what do I need to provide as input data and config?
- what kind of augmentation can you do?
```

```md
I'm interested in Defect Image Generation,
- what kind of compute infra do I need?
- what do I need to provide as input data and config?
- what kind of image generation can you do?
```

### Sample prompts to start the workflows

```md
Here's my input video of a robot performing pick and place in a warehouse:
 /path/to/input or azureml://path/to/input.
Augment it with a warehouse background with clutters, people, and
direct sunlight from the ceiling.
```
