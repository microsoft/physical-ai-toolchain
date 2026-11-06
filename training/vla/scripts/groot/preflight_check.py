"""Preflight checks for GR00T fine-tuning jobs.

``gpus`` verifies that the job sees exactly the requested number of GPUs.
``dataset`` verifies the GR00T LeRobot dataset layout and imports the submitted
modality config, which registers it with GR00T, without starting training.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

REQUIRED_METADATA = ("info.json", "episodes.jsonl", "tasks.jsonl", "modality.json")


def check_gpu_count(expected: int, device_count: Callable[[], int]) -> str:
    """Return a status line, raising ``RuntimeError`` when the visible GPU count differs."""
    actual = device_count()
    if actual != expected:
        raise RuntimeError(f"expected {expected} visible GPUs, found {actual}")
    return f"[preflight] visible GPUs: {actual} (expected: {expected})"


def check_dataset(dataset: Path) -> None:
    """Raise ``FileNotFoundError`` when the dataset is missing metadata, episodes, or videos."""
    missing = [str(dataset / "meta" / name) for name in REQUIRED_METADATA if not (dataset / "meta" / name).is_file()]
    if missing:
        raise FileNotFoundError(f"dataset is missing required metadata: {missing}")
    if not any((dataset / "data").glob("**/*.parquet")):
        raise FileNotFoundError(f"dataset has no Parquet episodes: {dataset / 'data'}")
    if not any((dataset / "videos").glob("**/*.mp4")):
        raise FileNotFoundError(f"dataset has no MP4 videos: {dataset / 'videos'}")


def load_modality_config(path: Path) -> None:
    """Execute the modality config so it registers with GR00T."""
    spec = importlib.util.spec_from_file_location("submitted_modality_config", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load modality config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def _torch_device_count() -> int:
    import torch

    return torch.cuda.device_count()


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    gpus = commands.add_parser("gpus", help="Verify the visible GPU count")
    gpus.add_argument("--expected", type=_positive_int, required=True)
    dataset = commands.add_parser("dataset", help="Verify the dataset and modality config")
    dataset.add_argument("--dataset", type=Path, required=True)
    dataset.add_argument("--modality-config", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, device_count: Callable[[], int] = _torch_device_count) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "gpus":
            print(check_gpu_count(args.expected, device_count))
        else:
            check_dataset(args.dataset)
            load_modality_config(args.modality_config)
            print("[preflight] dataset metadata, GR00T import, and modality registration: PASS")
    except (OSError, ImportError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
