###############################################################################
# Example data config for GR00T N1.5.
#
# Reference template for a dual-arm embodiment (two 6-DoF arms, 12 DoF total)
# with four cameras (two exterior + two wrist). Copy this file into
# `training/vla/configs/groot/` as `<embodiment>_data_config.py`, adapt the
# video/state/action keys to your dataset's `meta/modality.json`, and pass
# `--data-config <embodiment>` to the submission script.
#
# Loaded by the OSMO workflow: when DATA_CONFIG_B64 is set the file body is
# appended to `gr00t/experiment/data_config.py` inside the training container,
# registering `DATA_CONFIG_MAP["<embodiment>"]` for
# `scripts/gr00t_finetune.py --data-config <embodiment>`.
#
# N1.7 ignores this file (no --data-config flag); the N1.7 path consumes the
# matching modality_config.py example instead.
###############################################################################


class ExampleDataConfig(So100DataConfig):
    video_keys = [
        "video.exterior_image_1",
        "video.exterior_image_2",
        "video.left_wrist_image",
        "video.right_wrist_image",
    ]
    state_keys = ["state.left_joint_positions", "state.right_joint_positions"]
    action_keys = ["action.left_joint_positions", "action.right_joint_positions"]
    language_keys = ["annotation.language.language_instruction"]
    observation_indices = [0]
    action_indices = list(range(16))


DATA_CONFIG_MAP["example"] = ExampleDataConfig()
