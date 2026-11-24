"""Validate Azure ML sweep tolerance of a failed sibling trial."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mlflow

SMOKE_METRIC = "sweep_smoke_score"


def create_parser() -> argparse.ArgumentParser:
    """Create the smoke-test argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("success", "fail"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    """Publish one selectable result or fail the trial deliberately."""
    if args.mode == "fail":
        raise RuntimeError("Deliberate sweep trial failure")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "result.json").write_text(
        json.dumps({"mode": args.mode, "selected": True}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    mlflow.log_metric(SMOKE_METRIC, 1)
    return 0


def main() -> int:
    """Run the sweep failure smoke trial."""
    return run(create_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
