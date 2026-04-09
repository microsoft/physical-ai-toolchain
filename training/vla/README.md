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
│   └── submit-osmo-twinvla-training.sh  # OSMO submission CLI
└── workflows/
    └── osmo/
        └── twinvla-train.yaml        # OSMO workflow template
```

## 🚀 Quick Start

### Train on RoboTwin 2.0 (RLDS format)

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

### Train on Tabletop-Sim (LeRobot format)

```bash
training/vla/scripts/submit-osmo-twinvla-training.sh \
    -d jellyho/aloha_handover_box \
    --dataset-format lerobot \
    --batch-size 8
```

## VLM Backbone Options

TwinVLA supports multiple VLM backbones via the `--model-type` flag:

| Backbone  | Params | `--model-type` value | VRAM (LoRA) |
| --------- | ------ | -------------------- | ----------- |
| SmolVLM2  | 256M   | `SmolVLM2VLA`        | ~16 GB      |
| Eagle2-1B | 1B     | `Eagle2_1BVLA`       | ~24 GB      |

## References

- [TwinVLA: Data-Efficient Bimanual Manipulation with Twin Single-Arm VLAs](https://arxiv.org/abs/2511.05275) (ICLR 2026)
- [RoboTwin 2.0: A Scalable Data Generator and Benchmark](https://arxiv.org/abs/2506.18088)
- [VLA Fundamentals](../../docs/foundations/vla-fundamentals.md)

