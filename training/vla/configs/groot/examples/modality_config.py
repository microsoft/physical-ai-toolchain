###############################################################################
# Example modality config for GR00T N1.7+.
#
# Reference template for a dual-arm embodiment (two 6-DoF arms, 12 DoF total)
# with four cameras (two exterior + two wrist). Copy this file into
# `training/vla/configs/groot/` as `<embodiment>_modality_config.py`, adapt
# the video/state/action/language keys to your dataset's `meta/modality.json`,
# and pass `--modality-config-file <path>` (or rely on the submission script's
# `${data_config}_modality_config.py` auto-resolution) when running with
# `--vla-version 1.7`.
#
# Loaded by `gr00t/experiment/launch_finetune.py` via `--modality_config_path`;
# registers a ModalityConfig under EmbodimentTag.NEW_EMBODIMENT.
###############################################################################

from __future__ import annotations

from gr00t.configs.data.embodiment_configs import register_modality_config
from gr00t.data.embodiment_tags import EmbodimentTag
from gr00t.data.types import ModalityConfig

_EXAMPLE_MODALITY_CONFIG = {
    "video": ModalityConfig(
        delta_indices=[0],
        modality_keys=[
            "exterior_image_1",
            "exterior_image_2",
            "left_wrist_image",
            "right_wrist_image",
        ],
    ),
    "state": ModalityConfig(
        delta_indices=[0],
        modality_keys=["left_joint_positions", "right_joint_positions"],
    ),
    "action": ModalityConfig(
        delta_indices=list(range(16)),
        modality_keys=["left_joint_positions", "right_joint_positions"],
    ),
    "language": ModalityConfig(
        delta_indices=[0],
        modality_keys=["annotation.language.language_instruction"],
    ),
}

register_modality_config(_EXAMPLE_MODALITY_CONFIG, EmbodimentTag.NEW_EMBODIMENT)
