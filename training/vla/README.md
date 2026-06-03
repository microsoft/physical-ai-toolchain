# VLA Training

Vision-Language-Action (VLA) training for multi-modal transformer-based policies. VLA models combine visual perception with language understanding to generate robot actions from natural language task descriptions.

## 📁 Directory Structure

```text
vla/
├── configs/
│   └── groot/
│       └── examples/
│           ├── data_config.py                  # GR00T N1.5 example data config
│           ├── modality_config.py              # GR00T N1.7+ example modality config
│           └── README.md                       # How to adapt for a custom embodiment
├── scripts/
│   └── submit-osmo-lerobot-vla-fine-tuning.sh  # GR00T submission to OSMO
├── workflows/
│   └── osmo/
│       └── groot-train.yaml                     # GR00T fine-tuning OSMO workflow
└── README.md
```

## 🚀 GR00T-N1.5 Fine-Tuning

GR00T-N1.5-3B is NVIDIA's vision-language-action foundation model for robot manipulation. Fine-tuning is submitted via the VLA submission script:

```bash
training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --base-model nvidia/GR00T-N1.5-3B \
  --data-config example \
  --data-config-file training/vla/configs/groot/examples/data_config.py \
  --blob-url https://<account>.blob.core.windows.net/<container>/<path> \
  --azure-upload
```

See [configs/groot/examples/README.md](configs/groot/examples/README.md) for how to adapt the bundled example for a custom embodiment.

## 📋 Specifications

See [VLA Training Specification](../specifications/vla-training.specification.md) for additional VLA approaches and components.
