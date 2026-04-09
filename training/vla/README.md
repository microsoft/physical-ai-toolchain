# VLA Training

Vision-Language-Action (VLA) training for bimanual manipulation using pre-trained VLA models fine-tuned on task-specific demonstration data. VLA models combine visual perception with language understanding to generate robot actions from natural language task descriptions.

## Supported Models

| Model       | Params     | Action Head     | Bimanual Strategy                               | Source                                          |
| ----------- | ---------- | --------------- | ----------------------------------------------- | ----------------------------------------------- |
| **TwinVLA** | ~1B (twin) | DiT (diffusion) | Twin single-arm composition via Joint Attention | [ICLR 2026](https://jellyho.github.io/TwinVLA/) |

TwinVLA duplicates a pre-trained single-arm VLA backbone into two arm-specific branches linked via Joint Attention and MoE. This achieves bimanual coordination without any bimanual pre-training data — only single-arm pre-training + task-specific fine-tuning.

## Supported Datasets

| Dataset          | Format                  | Tasks                            | Episodes                   | Source                                                                                      |
| ---------------- | ----------------------- | -------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------- |
| **RoboTwin 2.0** | RLDS                    | 50 bimanual tasks, 5 embodiments | Variable (50-200 per task) | [robotwin-platform.github.io](https://robotwin-platform.github.io/)                         |
| **Tabletop-Sim** | RLDS / LeRobot          | 5 ALOHA bimanual tasks           | ~56 GB total               | [jellyho/tabletop-simulation-rlds](https://huggingface.co/jellyho/tabletop-simulation-rlds) |
| Custom LeRobot   | LeRobot (Parquet + MP4) | User-defined                     | User-defined               | Any LeRobot-compatible dataset                                                              |

### Action Space

All datasets use a standardized 20D end-effector pose action space:

```text
Left arm:  [x, y, z, r1, r2, r3, r4, r5, r6, gripper]  (10D)
Right arm: [x, y, z, r1, r2, r3, r4, r5, r6, gripper]  (10D)
```

Rotation uses the 6D continuous representation (Zhou et al., 2019).

## 📁 Directory Structure

```text
vla/
├── scripts/
│   ├── train_twinvla.py              # TwinVLA training orchestrator
│   ├── robotwin_config.py            # RoboTwin 2.0 task catalog and dataset config
│   ├── submit-osmo-twinvla-training.sh  # OSMO submission CLI
│   ├── setup-local-vla.sh           # Local environment setup (micromamba, deps, datasets, sims)
│   ├── train-local-twinvla.sh       # Local single-GPU training
│   └── eval-local-twinvla.sh        # Local simulation evaluation (RoboTwin / Tabletop-Sim)
└── workflows/
    └── osmo/
        └── twinvla-train.yaml        # OSMO workflow template
```

## 🚀 Quick Start

### Local Development (Single GPU)

The local workflow covers data annotation, training, and simulation evaluation on a single GPU (RTX 3090/4090/5090 with 24-32 GB VRAM).

#### Step 1: Environment Setup

```bash
# Set up micromamba env, clone TwinVLA, download RoboTwin dataset, install simulators
training/vla/scripts/setup-local-vla.sh -t open_laptop

# Preview what will be installed
training/vla/scripts/setup-local-vla.sh --config-preview

# Training-only setup (skip simulators)
training/vla/scripts/setup-local-vla.sh --skip-robotwin --skip-tabletop
```

#### Step 2: Annotate Data

Use the dataviewer to add language instructions to episodes:

```bash
cd data-management/viewer && npm run dev:backend
cd data-management/viewer && npm run dev:frontend
# Browse to http://localhost:5173, select episodes, add language annotations
```

The `LanguageInstructionAnnotation` model supports human, template, LLM-generated, and retroactive annotations with paraphrases and subtask decomposition.

#### Step 3: Train Locally

```bash
# Quick test (5K steps, ~2 hours on RTX 5090)
training/vla/scripts/train-local-twinvla.sh -t open_laptop -s 5000

# Full training (50K steps, ~20 hours)
training/vla/scripts/train-local-twinvla.sh -t open_laptop -s 50000 --wandb-project twinvla-dev

# LeRobot format from HuggingFace
training/vla/scripts/train-local-twinvla.sh \
    -t aloha_handover_box \
    --dataset-format lerobot \
    --lerobot-repo jellyho/aloha_handover_box

# Preview configuration
training/vla/scripts/train-local-twinvla.sh -t open_laptop --config-preview
```

#### Step 4: Evaluate in Simulation

```bash
# RoboTwin simulation (auto-detected for non-aloha tasks)
training/vla/scripts/eval-local-twinvla.sh \
    -c ./outputs/twinvla/checkpoint-10000 \
    -t open_laptop

# With domain randomization
training/vla/scripts/eval-local-twinvla.sh \
    -c ./outputs/twinvla/checkpoint-10000 \
    -t open_laptop \
    --task-config demo_randomized

# Tabletop-Sim (auto-detected for aloha_ tasks)
training/vla/scripts/eval-local-twinvla.sh \
    -c jellyho/TwinVLA-aloha_handover_box \
    -t aloha_handover_box

# Try a pre-trained HuggingFace checkpoint
training/vla/scripts/eval-local-twinvla.sh \
    -c jellyho/aloha_dish_drainer \
    -t aloha_dish_drainer
```

### Submit to OSMO (Multi-GPU Cloud)

```bash
# Submit to OSMO
training/vla/scripts/submit-osmo-twinvla-training.sh \
    -d jellyho/robotwin2_rlds \
    -t robotwin_open_laptop \
    --batch-size 4 \
    -g 2

# Preview configuration without submitting
training/vla/scripts/submit-osmo-twinvla-training.sh \
    -d jellyho/robotwin2_rlds \
    -t robotwin_open_laptop \
    --config-preview
```

### Train on Tabletop-Sim via OSMO (LeRobot format)

```bash
training/vla/scripts/submit-osmo-twinvla-training.sh \
    -d jellyho/aloha_handover_box \
    --dataset-format lerobot \
    --batch-size 8
```

## 🖥️ GPU Requirements

| Backbone | Params | `--model-type` value | Training VRAM (LoRA) | Inference VRAM |
| --- | --- | --- | --- | --- |
| SmolVLM2 | 256M | `SmolVLM2VLA` | ~16 GB | ~8 GB |
| Eagle2-1B | 1B | `Eagle2_1BVLA` | ~24 GB | ~12 GB |

| GPU | SmolVLM2 Training | Eagle2-1B Training | Inference + Sim |
| --- | --- | --- | --- |
| RTX 3090 (24 GB) | ✅ batch≤4 | ⚠️ batch=1 | ✅ |
| RTX 4090 (24 GB) | ✅ batch≤4 | ⚠️ batch=1 | ✅ |
| RTX 5090 (32 GB) | ✅ batch≤8 | ✅ batch≤4 | ✅ |
| A100 (80 GB) | ✅ batch≤32 | ✅ batch≤16 | ✅ |

## 📐 References

- [TwinVLA: Data-Efficient Bimanual Manipulation with Twin Single-Arm VLAs](https://arxiv.org/abs/2511.05275) (ICLR 2026)
- [RoboTwin 2.0: A Scalable Data Generator and Benchmark](https://arxiv.org/abs/2506.18088)
- [VLA Fundamentals](../../docs/foundations/vla-fundamentals.md)

