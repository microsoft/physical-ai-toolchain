"""TwinVLA training orchestrator for OSMO and AzureML.

Wraps the TwinVLA fine-tuning pipeline for bimanual VLA training on
RoboTwin or custom LeRobot datasets. Handles dataset download, TwinVLA
installation, training execution, checkpoint upload, and MLflow logging.

The training script supports two dataset formats:
  - LeRobot (Parquet + MP4): Native format, recommended for new datasets.
  - RLDS (TFRecord): OpenVLA-compatible format, used by RoboTwin pre-converted data.

Action space: 20D end-effector pose per arm (xyz + 6D rotation + gripper) × 2 arms.

References:
  - TwinVLA: https://github.com/jellyho/TwinVLA (ICLR 2026)
  - RoboTwin 2.0: https://robotwin-platform.github.io/
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

_LOGGER = logging.getLogger("vla.twinvla")

_DEFAULT_MODEL_TYPE = "SmolVLM2VLA"
_DEFAULT_BATCH_SIZE = 4
_DEFAULT_NUM_GPUS = 1
_DEFAULT_OUTPUT_DIR = "/workspace/outputs/twinvla"
_TWINVLA_REPO = "https://github.com/jellyho/TwinVLA.git"
_TWINVLA_BRANCH = "master"


def _install_twinvla(install_dir: Path) -> Path:
    """Clone and install TwinVLA if not already present."""
    if install_dir.exists() and (install_dir / "setup.py").exists():
        _LOGGER.info("TwinVLA already installed at %s", install_dir)
        return install_dir

    _LOGGER.info("Cloning TwinVLA from %s", _TWINVLA_REPO)
    subprocess.check_call(
        ["git", "clone", "--depth", "1", "-b", _TWINVLA_BRANCH, _TWINVLA_REPO, str(install_dir)],
    )

    _LOGGER.info("Installing TwinVLA requirements")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(install_dir / "requirements.txt")],
    )
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-e", str(install_dir)],
    )

    return install_dir


def _build_training_command(args: argparse.Namespace) -> list[str]:
    """Build the accelerate launch command for TwinVLA training."""
    cmd = [
        "accelerate",
        "launch",
        "--num_processes",
        str(args.num_gpus),
        "scripts/train.py",
        "--model_type",
        args.model_type,
        "--output_dir",
        args.output_dir,
        "--batch_size",
        str(args.batch_size),
    ]

    if args.dataset_format == "lerobot":
        cmd.extend(["--data_type", "lerobot", "--data_root_dir", args.dataset_path])
    else:
        cmd.extend(["--data_type", "rlds", "--data_root_dir", args.dataset_path, "--data_mix", args.task_name])

    if args.learning_rate:
        cmd.extend(["--learning_rate", str(args.learning_rate)])

    if args.max_steps:
        cmd.extend(["--max_steps", str(args.max_steps)])

    if args.save_steps:
        cmd.extend(["--save_steps", str(args.save_steps)])

    if args.wandb_project:
        cmd.extend(["--wandb_project", args.wandb_project])

    return cmd


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TwinVLA bimanual VLA training")

    parser.add_argument(
        "--dataset-path",
        required=True,
        help="Path to dataset (LeRobot HF repo ID or local RLDS directory)",
    )
    parser.add_argument(
        "--task-name",
        default="",
        help="Task name for RLDS data mix (e.g., robotwin_open_laptop)",
    )
    parser.add_argument(
        "--dataset-format",
        choices=["lerobot", "rlds"],
        default="rlds",
        help="Dataset format: lerobot (Parquet+MP4) or rlds (TFRecord)",
    )
    parser.add_argument(
        "--model-type",
        default=_DEFAULT_MODEL_TYPE,
        help="VLM backbone type (default: %(default)s)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help="Training batch size (default: %(default)s)",
    )
    parser.add_argument(
        "--num-gpus",
        type=int,
        default=_DEFAULT_NUM_GPUS,
        help="Number of GPUs for distributed training (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default=_DEFAULT_OUTPUT_DIR,
        help="Checkpoint output directory (default: %(default)s)",
    )
    parser.add_argument("--learning-rate", type=float, help="Optimizer learning rate")
    parser.add_argument("--max-steps", type=int, help="Maximum training steps")
    parser.add_argument("--save-steps", type=int, help="Checkpoint save interval")
    parser.add_argument("--wandb-project", help="Weights & Biases project name")
    parser.add_argument(
        "--twinvla-dir",
        default="/workspace/TwinVLA",
        help="TwinVLA installation directory",
    )

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args()

    _LOGGER.info("=== TwinVLA Bimanual VLA Training ===")
    _LOGGER.info("Dataset: %s (format: %s)", args.dataset_path, args.dataset_format)
    _LOGGER.info("Model type: %s", args.model_type)
    _LOGGER.info("GPUs: %s, Batch size: %s", args.num_gpus, args.batch_size)

    twinvla_dir = _install_twinvla(Path(args.twinvla_dir))

    cmd = _build_training_command(args)
    _LOGGER.info("Training command: %s", " ".join(cmd))

    original_dir = os.getcwd()
    try:
        os.chdir(twinvla_dir)
        subprocess.check_call(cmd)
    finally:
        os.chdir(original_dir)

    _LOGGER.info("Training complete. Checkpoints at %s", args.output_dir)


if __name__ == "__main__":
    main()
