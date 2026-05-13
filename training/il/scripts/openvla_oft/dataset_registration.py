"""Register our RLDS dataset with the OpenVLA-OFT (`prismatic`) data pipeline.

The OFT fine-tuning script reads RLDS datasets through the OXE wrapper, which
requires a dataset to be present in three sibling registries plus a constants
file:

1. `prismatic/vla/datasets/rlds/oxe/configs.py`     -> `OXE_DATASET_CONFIGS`
2. `prismatic/vla/datasets/rlds/oxe/transforms.py`  -> standardization fn + `OXE_STANDARDIZATION_TRANSFORMS`
3. `prismatic/vla/datasets/rlds/oxe/mixtures.py`    -> `OXE_NAMED_MIXTURES`
4. `prismatic/vla/constants.py`                     -> `ACTION_DIM`, `PROPRIO_DIM`, `NUM_ACTIONS_CHUNK`

This module performs idempotent edits via marker-fenced blocks so re-runs in
AzureML are safe.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_MARKER_START = "# >>> physical-ai-toolchain auto-registered dataset >>>"
_MARKER_END = "# <<< physical-ai-toolchain auto-registered dataset <<<"

# OFT's JOINT_POS_BIMANUAL action encoding hardcodes absolute_action_mask =
# [True] * 14 (ALOHA-style 6 joints + 1 gripper per arm). Our UR5e bimanual
# robot has 6 joints per arm and no gripper, so we pad the 12-D action with
# two placeholder gripper dimensions to reach the 14-D model interface.
_BIMANUAL_GRIPPER_PAD = 2


def _strip_existing_block(text: str) -> str:
    pattern = re.compile(
        rf"\n?{re.escape(_MARKER_START)}.*?{re.escape(_MARKER_END)}\n?",
        flags=re.DOTALL,
    )
    return pattern.sub("\n", text)


def _append_block(path: Path, block: str) -> None:
    text = _strip_existing_block(path.read_text())
    if not text.endswith("\n"):
        text += "\n"
    text += f"\n{_MARKER_START}\n{block.rstrip()}\n{_MARKER_END}\n"
    path.write_text(text)


def _patch_configs(configs_path: Path, dataset_name: str, image_size: int) -> None:
    block = f"""
from prismatic.vla.datasets.rlds.oxe.configs import OXE_DATASET_CONFIGS  # type: ignore  # re-export
from prismatic.vla.datasets.rlds.oxe.configs import ActionEncoding, StateEncoding  # type: ignore

OXE_DATASET_CONFIGS["{dataset_name}"] = {{
    "image_obs_keys": {{
        "primary": "image",
        "secondary": None,
        "wrist": "wrist_image_right",
    }},
    "depth_obs_keys": {{"primary": None, "secondary": None, "wrist": None}},
    "state_obs_keys": ["state"],
    "state_encoding": StateEncoding.JOINT_BIMANUAL,
    "action_encoding": ActionEncoding.JOINT_POS_BIMANUAL,
}}
"""
    _append_block(configs_path, block)


def _patch_transforms(transforms_path: Path, dataset_name: str) -> None:
    fn_name = f"{dataset_name}_dataset_transform"
    block = f"""
from typing import Any, Dict
import tensorflow as tf

def {fn_name}(trajectory: Dict[str, Any]) -> Dict[str, Any]:
    # Absolute joint-angle actions for a 12-DOF bimanual UR5e (6+6, no grippers).
    # OFT's JOINT_POS_BIMANUAL encoding expects ALOHA layout (14-D: 6+1 per arm),
    # so pad with {_BIMANUAL_GRIPPER_PAD} placeholder gripper dimensions.
    trajectory["action"] = tf.concat(
        (
            trajectory["action"],
            tf.zeros_like(trajectory["action"][:, :{_BIMANUAL_GRIPPER_PAD}]),
        ),
        axis=-1,
    )
    trajectory["language_instruction"] = trajectory["language_instruction"]
    return trajectory

from prismatic.vla.datasets.rlds.oxe.transforms import OXE_STANDARDIZATION_TRANSFORMS  # type: ignore
OXE_STANDARDIZATION_TRANSFORMS["{dataset_name}"] = {fn_name}
"""
    _append_block(transforms_path, block)


def _patch_mixtures(mixtures_path: Path, dataset_name: str) -> None:
    block = f"""
from prismatic.vla.datasets.rlds.oxe.mixtures import OXE_NAMED_MIXTURES  # type: ignore

OXE_NAMED_MIXTURES["{dataset_name}"] = [("{dataset_name}", 1.0)]
"""
    _append_block(mixtures_path, block)


def _patch_constants(constants_path: Path, action_dim: int, proprio_dim: int, num_actions_chunk: int) -> None:
    """Edit ALOHA_CONSTANTS in prismatic/vla/constants.py to match our robot.

    OFT keys off the dataset-config StateEncoding/ActionEncoding to pick which
    constants block applies; for `JOINT_BIMANUAL` the ALOHA_CONSTANTS block is
    used. We rewrite the values inline rather than appending a new block so
    downstream code that imports the module-level names sees the updated dims.
    """
    text = constants_path.read_text()
    # ACTION_DIM in constants.py drives model-head dimensions, which see the
    # action AFTER our transform pads it to ALOHA bimanual layout.
    model_action_dim = action_dim + _BIMANUAL_GRIPPER_PAD
    updates = {
        "NUM_ACTIONS_CHUNK": num_actions_chunk,
        "ACTION_DIM": model_action_dim,
        "PROPRIO_DIM": proprio_dim,
    }
    for name, value in updates.items():
        pattern = re.compile(rf'^(\s*"{name}"\s*:\s*)\d+', flags=re.MULTILINE)
        new_text, count = pattern.subn(rf"\g<1>{value}", text)
        if count == 0:
            _LOGGER.warning("Could not patch %s in %s (no match found)", name, constants_path)
            continue
        text = new_text
    constants_path.write_text(text)


def patch_oft_repo(
    oft_root: Path,
    dataset_name: str,
    action_dim: int,
    proprio_dim: int,
    num_actions_chunk: int,
    image_size: int = 256,
) -> None:
    """Apply all four patches inside an `openvla-oft` repo clone."""
    prismatic = oft_root / "prismatic"
    rlds_oxe = prismatic / "vla" / "datasets" / "rlds" / "oxe"
    if not rlds_oxe.is_dir():
        raise FileNotFoundError(f"OFT repo layout missing: {rlds_oxe}")

    _patch_configs(rlds_oxe / "configs.py", dataset_name, image_size)
    _patch_transforms(rlds_oxe / "transforms.py", dataset_name)
    _patch_mixtures(rlds_oxe / "mixtures.py", dataset_name)
    _patch_constants(prismatic / "vla" / "constants.py", action_dim, proprio_dim, num_actions_chunk)
    _LOGGER.info("Patched OFT repo at %s for dataset %r", oft_root, dataset_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--oft-root", required=True, type=Path, help="Path to a cloned moojink/openvla-oft repo")
    parser.add_argument("--dataset-name", required=True, help="RLDS dataset name (matches TFDS builder name)")
    parser.add_argument("--action-dim", type=int, required=True, help="Action vector dimensionality")
    parser.add_argument("--proprio-dim", type=int, required=True, help="Proprio (state) vector dimensionality")
    parser.add_argument(
        "--num-actions-chunk",
        type=int,
        required=True,
        help="Action chunk length (e.g. 25 for 30 Hz to span ~0.83 s)",
    )
    parser.add_argument("--image-size", type=int, default=256)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    patch_oft_repo(
        args.oft_root,
        args.dataset_name,
        action_dim=args.action_dim,
        proprio_dim=args.proprio_dim,
        num_actions_chunk=args.num_actions_chunk,
        image_size=args.image_size,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
