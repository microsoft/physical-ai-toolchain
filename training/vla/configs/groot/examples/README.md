# GR00T Data and Modality Config Examples

Reference templates for fine-tuning NVIDIA Isaac-GR00T on a custom embodiment. The example covers a dual-arm setup (two 6-DoF arms, 12 DoF total) with four cameras (two exterior, two wrist) and language instructions.

## Files

| File                 | GR00T version | Purpose                                                                                         |
|----------------------|---------------|-------------------------------------------------------------------------------------------------|
| `data_config.py`     | N1.5          | `DATA_CONFIG_MAP` entry consumed by `scripts/gr00t_finetune.py --data-config <key>`             |
| `modality_config.py` | N1.7+         | `ModalityConfig` registered via `register_modality_config`, loaded via `--modality_config_path` |

## Usage

### Run the bundled example

```bash
training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --base-model nvidia/GR00T-N1.5-3B \
  --data-config example \
  --data-config-file training/vla/configs/groot/examples/data_config.py \
  --blob-url https://<account>.blob.core.windows.net/<container>/<dataset>
```

The submission script base64-encodes the file and the OSMO workflow appends it to `gr00t/experiment/data_config.py` at runtime.

### Adapt for your embodiment

1. Copy the example file into `training/vla/configs/groot/` as `<embodiment>_data_config.py` (and `<embodiment>_modality_config.py` for N1.7+).
2. Rename the class (`ExampleDataConfig` → `MyEmbodimentDataConfig`) and the registry key (`DATA_CONFIG_MAP["example"]` → `DATA_CONFIG_MAP["my_embodiment"]`).
3. Update `video_keys`, `state_keys`, `action_keys`, and `language_keys` to match your dataset's `meta/modality.json`.
4. Submit with `--data-config my_embodiment` — the submission script auto-resolves `<embodiment>_data_config.py` and `<embodiment>_modality_config.py` from `training/vla/configs/groot/`.

## Layout reference

The example assumes a dataset shaped like:

```text
state  : left_joint_positions (0:6) + right_joint_positions (6:12)   -- 12 DoF
action : same as state                                                -- 12 DoF
video  : exterior_image_1, exterior_image_2, left_wrist_image, right_wrist_image
lang   : annotation.language.language_instruction
```

See the [GR00T data and modality config docs](https://github.com/NVIDIA/Isaac-GR00T) for the full schema.
