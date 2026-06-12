"""
Data loading and preprocessing configuration for LeRobot training.

Loads YAML config files, validates settings, and builds LeRobot CLI
arguments for dataset loading, image transforms, and normalization.

Consumed by the preprocess Component (preprocess.py) to validate user
configs at pipeline-input time, and by the train Component to translate
the merged config into ``lerobot-train`` CLI arguments.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "dataset": {
        "episodes": None,
    },
    "image_transforms": {
        "enable": False,
        "max_num_transforms": 3,
        "random_order": False,
        "brightness": {"weight": 1.0, "min_max": [0.8, 1.2]},
        "contrast": {"weight": 1.0, "min_max": [0.8, 1.2]},
        "saturation": {"weight": 1.0, "min_max": [0.8, 1.2]},
        "hue": {"weight": 0.5, "min_max": [-0.05, 0.05]},
        "sharpness": {"weight": 1.0, "min_max": [0.8, 1.2]},
    },
    "normalization": {
        "mapping": {
            "ACTION": "MEAN_STD",
            "STATE": "MEAN_STD",
            "VISUAL": "IDENTITY",
        },
    },
}

_VALID_NORMALIZATION_MODES = {"MEAN_STD", "MIN_MAX", "IDENTITY"}

_TRANSFORM_KEYS = ("brightness", "contrast", "saturation", "hue", "sharpness")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str | Path | None = None, project_root: Path | None = None) -> dict[str, Any]:
    """
    Load a preprocessing config YAML and merge it with defaults.

    Args:
        config_path: Path to a YAML config file. If ``None``, returns the
            built-in defaults.
        project_root: Optional project root for resolving relative paths.
            When running inside AzureML, the CWD may differ from the code
            snapshot root.

    Returns:
        Merged configuration dictionary.

    Raises:
        FileNotFoundError: If *config_path* does not exist.
        yaml.YAMLError: If the file is not valid YAML.

    """
    if config_path is None:
        return copy.deepcopy(_DEFAULT_CONFIG)

    path = Path(config_path)

    # If the path is a directory (e.g. AzureML uri_folder mount), look for
    # preprocessing.yaml inside it.
    if path.is_dir():
        path = path / "preprocessing.yaml"

    # If relative and not found, try resolving against the project root
    # (handles AzureML jobs where CWD != code snapshot root).
    if not path.is_absolute() and not path.exists() and project_root is not None:
        path = project_root / path

    if not path.exists():
        raise FileNotFoundError(f"Preprocessing config not found: {path}")

    with open(path) as fh:
        user_config: dict[str, Any] = yaml.safe_load(fh) or {}

    return _deep_merge(copy.deepcopy(_DEFAULT_CONFIG), user_config)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_config(config: dict[str, Any]) -> list[str]:
    """
    Validate a preprocessing config and return a list of error messages.

    Returns an empty list when the config is valid.
    """
    errors: list[str] = []

    # -- normalization modes --
    norm_map = config.get("normalization", {}).get("mapping", {})
    for key, mode in norm_map.items():
        if mode not in _VALID_NORMALIZATION_MODES:
            errors.append(f"normalization.mapping.{key}: unknown mode '{mode}' (valid: {_VALID_NORMALIZATION_MODES})")

    # -- image transforms --
    img = config.get("image_transforms", {})
    for tf_name in _TRANSFORM_KEYS:
        tf = img.get(tf_name, {})
        mm = tf.get("min_max")
        if mm is not None:
            if not (isinstance(mm, list) and len(mm) == 2):
                errors.append(f"image_transforms.{tf_name}.min_max must be a [min, max] list")
            elif mm[0] > mm[1]:
                errors.append(f"image_transforms.{tf_name}.min_max: min ({mm[0]}) > max ({mm[1]})")

    max_tf = img.get("max_num_transforms")
    if max_tf is not None and (not isinstance(max_tf, int) or max_tf < 0):
        errors.append("image_transforms.max_num_transforms must be a non-negative integer")

    # -- dataset episodes --
    eps = config.get("dataset", {}).get("episodes")
    if eps is not None and not isinstance(eps, list):
        errors.append("dataset.episodes must be a list of integers or null")

    return errors


# ---------------------------------------------------------------------------
# CLI argument building
# ---------------------------------------------------------------------------


def build_data_cli_args(
    config: dict[str, Any],
    *,
    dataset_root: str | None = None,
) -> list[str]:
    """
    Build LeRobot CLI arguments for data loading and preprocessing.

    Args:
        config: Merged preprocessing config (from :func:`load_config`).
        dataset_root: Optional local dataset root (``--dataset.root``).
            When set, LeRobot infers local-only mode automatically.

    Returns:
        List of CLI argument strings ready to append to the lerobot-train
        command.

    """
    args: list[str] = []
    ds_cfg = config.get("dataset", {})

    # -- dataset root --
    if dataset_root is not None:
        args.append(f"--dataset.root={dataset_root}")

    # -- episodes --
    episodes = ds_cfg.get("episodes")
    if episodes is not None and isinstance(episodes, list):
        args.append(f"--dataset.episodes={json.dumps(episodes)}")

    # -- image transforms --
    img_cfg = config.get("image_transforms", {})
    if img_cfg.get("enable"):
        args.append("--dataset.image_transforms.enable=true")
        max_tf = img_cfg.get("max_num_transforms")
        if max_tf is not None:
            args.append(f"--dataset.image_transforms.max_num_transforms={max_tf}")
        if img_cfg.get("random_order"):
            args.append("--dataset.image_transforms.random_order=true")

        # Build the tfs dict and pass as a single JSON arg
        tfs: dict[str, Any] = {}
        for tf_name in _TRANSFORM_KEYS:
            tf = img_cfg.get(tf_name, {})
            weight = tf.get("weight")
            mm = tf.get("min_max")
            if weight is not None and weight > 0 and mm is not None:
                tf_type = "SharpnessJitter" if tf_name == "sharpness" else "ColorJitter"
                tfs[tf_name] = {
                    "type": tf_type,
                    "weight": weight,
                    "kwargs": {tf_name: mm},
                }
        if tfs:
            args.append(f"--dataset.image_transforms.tfs={json.dumps(tfs)}")

    # -- normalization mapping --
    norm_map = config.get("normalization", {}).get("mapping", {})
    if norm_map:
        mapping_json = json.dumps(norm_map)
        args.append(f"--policy.normalization_mapping={mapping_json}")

    return args


def get_mlflow_params(config: dict[str, Any]) -> dict[str, str]:
    """
    Extract parameters from a preprocessing config for MLflow logging.

    Args:
        config: Merged preprocessing config.

    Returns:
        Flat dictionary of string key-value pairs suitable for
        ``mlflow.log_params()``.

    """
    params: dict[str, str] = {}

    ds = config.get("dataset", {})
    eps = ds.get("episodes")
    params["data/episodes"] = json.dumps(eps) if eps else "all"

    img = config.get("image_transforms", {})
    params["data/image_transforms_enable"] = str(img.get("enable", False))
    if img.get("enable"):
        params["data/max_num_transforms"] = str(img.get("max_num_transforms", 3))
        for tf_name in _TRANSFORM_KEYS:
            tf = img.get(tf_name, {})
            params[f"data/img_{tf_name}_weight"] = str(tf.get("weight", 0))

    norm = config.get("normalization", {}).get("mapping", {})
    for key, mode in norm.items():
        params[f"data/norm_{key}"] = mode

    return params
