"""
LeRobot dataset validation and path setup for AzureML pipelines.

This preprocessing component:
1. Validates that the input data is a valid LeRobot-format dataset
2. Resolves AzureML Named Asset URI mount paths into a layout LeRobot expects
3. Validates and merges the preprocessing config
4. Outputs the prepared dataset path and validated config for the training step

Usage (inside the AzureML pipeline):
    python preprocess.py \\
        --dataset_input /mnt/azureml/.../dataset \\
        --dataset_repo_id koch-pick-place-5-lego-random-pose \\
        --preprocessing_config training/il/configs/preprocessing/default.yaml \\
        --output_dataset /mnt/azureml/.../prepared \\
        --output_config /mnt/azureml/.../config
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# Relative import so the file works whether invoked via `python -m
# training.il.scripts.lerobot.preprocess` from the repo root or as
# `python preprocess.py` from inside the AzureML code snapshot.
try:
    from .data_config import load_config, validate_config
except ImportError:
    # Fallback when executed as a top-level script (no package context).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from data_config import load_config, validate_config  # type: ignore[no-redef]

# Required by load_config() to resolve relative YAML config paths when the
# AzureML container CWD differs from the code snapshot root.
# Repo layout: training/il/scripts/lerobot/preprocess.py -> parents[4] = repo root.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# LeRobot dataset format validation
# ---------------------------------------------------------------------------

# Required directory/file structure for a valid LeRobot v2 dataset.
_REQUIRED_META_FILES = ("info.json",)
_REQUIRED_DIRS = ("meta",)
_EXPECTED_DATA_DIRS = ("data",)


def validate_lerobot_dataset(dataset_path: Path) -> list[str]:
    """
    Validate that a directory contains a valid LeRobot-format dataset.

    Checks for required directory structure and metadata files.

    Args:
        dataset_path: Path to the dataset root directory.

    Returns:
        List of validation error messages (empty if valid).

    """
    errors: list[str] = []

    if not dataset_path.is_dir():
        return [f"Dataset path is not a directory: {dataset_path}"]

    # Check required directories
    for dir_name in _REQUIRED_DIRS:
        if not (dataset_path / dir_name).is_dir():
            errors.append(f"Missing required directory: {dir_name}/")

    # Check required metadata files
    meta_dir = dataset_path / "meta"
    if meta_dir.is_dir():
        for fname in _REQUIRED_META_FILES:
            if not (meta_dir / fname).exists():
                errors.append(f"Missing required metadata file: meta/{fname}")
        # Validate info.json content
        info_path = meta_dir / "info.json"
        if info_path.exists():
            try:
                with open(info_path) as fh:
                    info = json.load(fh)
                if "codebase_version" not in info:
                    errors.append("meta/info.json missing 'codebase_version' field")
                if "total_episodes" not in info:
                    errors.append("meta/info.json missing 'total_episodes' field")
            except (json.JSONDecodeError, OSError) as exc:
                errors.append(f"Cannot read meta/info.json: {exc}")

    # Check for data directory
    has_data = False
    for data_dir in _EXPECTED_DATA_DIRS:
        if (dataset_path / data_dir).is_dir():
            has_data = True
            break
    if not has_data:
        errors.append(f"Missing data directory (expected one of: {_EXPECTED_DATA_DIRS})")

    return errors


# ---------------------------------------------------------------------------
# Dataset path preparation
# ---------------------------------------------------------------------------


def prepare_dataset(
    dataset_input: Path,
    dataset_repo_id: str,
    output_dir: Path,
) -> Path:
    """
    Prepare dataset directory in LeRobot's expected layout.

    LeRobot expects datasets at ``{root}/{repo_id}/``. AzureML mounts Named
    Asset URIs to arbitrary paths, so this function copies the dataset
    into the expected structure. A deep copy (using hard links where
    possible) is used instead of symlinks because AzureML's output upload
    cannot follow symbolic links.

    Args:
        dataset_input: AzureML-mounted dataset directory.
        dataset_repo_id: Dataset identifier for LeRobot.
        output_dir: Output directory where the prepared layout is created.

    Returns:
        The ``root`` path to pass to LeRobot (i.e. the parent of the
        copied dataset directory).

    """
    import shutil

    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_dest = output_dir / dataset_repo_id

    if dataset_dest.exists():
        shutil.rmtree(dataset_dest)

    shutil.copytree(str(dataset_input), str(dataset_dest), copy_function=shutil.copy2)
    return output_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the preprocessing component."""
    parser = argparse.ArgumentParser(description="LeRobot dataset validation & path setup")
    parser.add_argument(
        "--dataset_input",
        type=str,
        required=True,
        help="Path to the AzureML-mounted dataset (from Named Asset URI)",
    )
    parser.add_argument(
        "--dataset_repo_id",
        type=str,
        required=True,
        help="Dataset identifier for LeRobot (e.g. koch-pick-place-5-lego-random-pose)",
    )
    parser.add_argument(
        "--preprocessing_config",
        type=str,
        default=None,
        help="Path to preprocessing config YAML (default: built-in defaults)",
    )
    parser.add_argument(
        "--output_dataset",
        type=str,
        required=True,
        help="Output directory for the prepared dataset layout",
    )
    parser.add_argument(
        "--output_config",
        type=str,
        required=True,
        help="Output directory for the validated preprocessing config",
    )
    args = parser.parse_args()

    dataset_input = Path(args.dataset_input)
    output_dataset = Path(args.output_dataset)
    output_config = Path(args.output_config)

    print("=== LeRobot Dataset Preprocessing ===")
    print(f"  dataset_input: {dataset_input}")
    print(f"  dataset_repo_id: {args.dataset_repo_id}")
    print(f"  preprocessing_config: {args.preprocessing_config or '(defaults)'}")

    # Step 1: Validate dataset format
    print("\n[1/4] Validating LeRobot dataset format...")
    ds_errors = validate_lerobot_dataset(dataset_input)
    if ds_errors:
        print("[ERROR] Dataset validation failed:")
        for err in ds_errors:
            print(f"  - {err}")
        sys.exit(1)
    print("[OK] Dataset format is valid")

    # Read dataset metadata for logging
    info_path = dataset_input / "meta" / "info.json"
    with open(info_path) as fh:
        dataset_info = json.load(fh)
    print(f"  codebase_version: {dataset_info.get('codebase_version', 'unknown')}")
    print(f"  total_episodes: {dataset_info.get('total_episodes', 'unknown')}")
    print(f"  total_frames: {dataset_info.get('total_frames', 'unknown')}")

    # Step 2: Load and validate preprocessing config
    print("\n[2/4] Loading preprocessing config...")
    config = load_config(args.preprocessing_config, project_root=_PROJECT_ROOT)
    cfg_errors = validate_config(config)
    if cfg_errors:
        print("[ERROR] Config validation failed:")
        for err in cfg_errors:
            print(f"  - {err}")
        sys.exit(1)
    print("[OK] Preprocessing config is valid")

    # Step 3: Prepare dataset layout
    print("\n[3/4] Preparing dataset layout for LeRobot...")
    lerobot_root = prepare_dataset(dataset_input, args.dataset_repo_id, output_dataset)
    print(f"[OK] Dataset prepared at: {lerobot_root}/{args.dataset_repo_id}")

    # Step 4: Write validated config and metadata to output
    print("\n[4/4] Writing validated config to output...")
    output_config.mkdir(parents=True, exist_ok=True)

    # Write the merged config
    config_out = output_config / "preprocessing.yaml"
    with open(config_out, "w") as fh:
        yaml.dump(config, fh, default_flow_style=False, sort_keys=False)

    # Write dataset metadata for downstream reference
    metadata = {
        "dataset_repo_id": args.dataset_repo_id,
        "dataset_root": str(lerobot_root),
        "dataset_info": {
            "codebase_version": dataset_info.get("codebase_version"),
            "total_episodes": dataset_info.get("total_episodes"),
            "total_frames": dataset_info.get("total_frames"),
        },
        "preprocessing_config_source": args.preprocessing_config or "built-in defaults",
    }
    meta_out = output_config / "metadata.json"
    with open(meta_out, "w") as fh:
        json.dump(metadata, fh, indent=2)

    print(f"[OK] Config written to: {config_out}")
    print(f"[OK] Metadata written to: {meta_out}")
    print("\n=== Preprocessing Complete ===")


if __name__ == "__main__":
    main()
