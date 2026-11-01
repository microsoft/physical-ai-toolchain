#!/usr/bin/env python3
"""Run the CARLA + Cosmos Transfer tasks planned from an ODD output with Docker Compose."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parent
COMPOSE_FILE = COMPONENT_DIR / "compose.yaml"


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=COMPONENT_DIR,
        check=check,
        text=True,
    )


def image_exists(image: str) -> bool:
    result = run(["docker", "image", "inspect", image], check=False)
    return result.returncode == 0


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        ["docker", "compose", "--project-directory", str(COMPONENT_DIR), "-f", str(COMPOSE_FILE), *args],
        check=check,
    )


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Other options (--views, --seed, --seconds, --num-steps, ...) are passed to run_tasks.py.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="ODD wfm_generation_input.json path inside the pipeline container, under /workspace/data.",
    )
    parser.add_argument("--run-dir", default=None, help="Existing run directory in the container to resume.")
    parser.add_argument(
        "--rebuild-cosmos", action="store_true", help="Pull and rebuild the official Cosmos source image."
    )
    parser.add_argument(
        "--keep-services",
        action="store_true",
        help="Leave CARLA running after the run.",
    )
    return parser.parse_known_args()


def main() -> int:
    args, extra = parse_args()
    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    run(["docker", "compose", "version"])
    cosmos_image = os.environ.get("COSMOS_IMAGE", "cosmos-transfer2.5:local")
    pipeline_image = "carla-cosmos-pipeline:local"
    carla_image = "carla-cosmos-simulator:0.9.15"

    if (
        args.rebuild_cosmos
        or not image_exists(cosmos_image)
        or not image_exists(pipeline_image)
        or not image_exists(carla_image)
    ):
        build_script = COMPONENT_DIR / "build-cosmos-image.sh"
        run([str(build_script)])

    # Created here so Docker does not create the mount as root.
    (COMPONENT_DIR / "data").mkdir(exist_ok=True)
    command = ["run", "--rm", "-T", "pipeline", "--input", args.input]
    if args.run_dir:
        command.extend(["--run-dir", args.run_dir])
    command.extend(extra)
    try:
        compose("up", "--detach", "--wait", "--wait-timeout", "300", "carla")
        result = compose(*command, check=False)
    finally:
        if not args.keep_services:
            compose("down")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
