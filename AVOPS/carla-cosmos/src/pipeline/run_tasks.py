# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Run the CARLA + Cosmos Transfer tasks planned from an ODD ``wfm_generation_input.json``.

Each task is captured in CARLA, converted to Cosmos control videos and rendered with Cosmos Transfer
multi-view inference. Results are written under ``<run-dir>/tasks/<task>/`` and indexed in
``<run-dir>/tasks.json``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

from . import task_plan, task_stages

PROMPTS_DIR = task_plan.COMPONENT_DIR / "prompts"
SCENARIOS_DIR = task_plan.COMPONENT_DIR / "scenarios"
MIN_GRID_BYTES = 1000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="ODD wfm_generation_input.json")
    parser.add_argument("--run-dir", type=Path, default=None, help="Run directory; finished tasks in it are skipped")
    parser.add_argument("--scenario-map", type=Path, default=task_plan.DEFAULT_SCENARIO_MAP)
    parser.add_argument("--views", default=None, help="Comma or space separated views (default: CARLA_COSMOS_VIEWS)")
    parser.add_argument("--seed", type=int, default=None, help="Seed for prompt selection")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--num-steps", type=int, default=35)
    parser.add_argument("--guidance", type=int, default=5)
    parser.add_argument("--control-weight", type=float, default=1.0)
    return parser.parse_args(argv)


def select_views(raw: str | None) -> list[str]:
    if not raw:
        return task_stages.default_views()
    tokens = raw.replace(",", " ").split()
    unknown = [t for t in tokens if t not in task_stages.ALL_VIEWS]
    if unknown:
        raise ValueError(f"Unknown view(s) {unknown}; use any of: {', '.join(task_stages.ALL_VIEWS)}")
    return [v for v in task_stages.ALL_VIEWS if v in tokens]


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    (run_dir / "tasks.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def grid_done(task_dir: Path) -> bool:
    grid = task_dir / "multiview_grid.mp4"
    return grid.is_file() and grid.stat().st_size > MIN_GRID_BYTES


def run_one(
    task: task_plan.Task, entry: dict[str, Any], task_dir: Path, args: argparse.Namespace, views: list[str]
) -> None:
    carla_params = {**task.params, **task_plan.scenario_params(task.scenario, SCENARIOS_DIR)}
    started = time.monotonic()
    task_stages.run_task(
        task_stages.TaskParams(
            out_dir=task_dir,
            prompt=entry["prompt"],
            views=views,
            carla_params=carla_params,
            seconds=args.seconds,
            num_steps=args.num_steps,
            guidance=args.guidance,
            control_weight=args.control_weight,
        )
    )
    (task_dir / "caption.txt").write_text(entry["prompt"] + "\n", encoding="utf-8")
    metadata = {
        **entry,
        "views": views,
        "carla": carla_params,
        "cosmos": {"num_steps": args.num_steps, "guidance": args.guidance, "control_weight": args.control_weight},
        "seconds": args.seconds,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    (task_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    views = select_views(args.views)
    tasks, skipped = task_plan.plan_tasks(
        json.loads(args.input.read_text(encoding="utf-8")), task_plan.load_scenario_map(args.scenario_map)
    )
    for gap, count in skipped.items():
        print(f"No CARLA scenario for {gap} ({count} variation(s)); skipping")
    if not tasks:
        print("None of the ODD gaps map to a CARLA scenario", file=sys.stderr)
        return 2

    # The Cosmos image runs Python 3.10, which has no datetime.UTC.
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    run_dir = args.run_dir or task_stages.DATA_ROOT / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    entries: list[dict[str, Any]] = []
    for task in tasks:
        prompt_file = PROMPTS_DIR / task.prompts
        seed = rng.randrange(2**31)
        entries.append(
            {
                "task": task.name,
                "attribute": task.attribute,
                "value": task.value,
                "scenario": task.scenario,
                "prompt_file": task.prompts,
                "seed": seed,
                "prompt": task_plan.pick_prompt(prompt_file, random.Random(seed)),
                "status": "pending",
            }
        )
    manifest = {"input": str(args.input), "views": views, "tasks": entries}
    write_manifest(run_dir, manifest)

    failures = 0
    for task, entry in zip(tasks, entries, strict=True):
        task_dir = run_dir / "tasks" / task.name
        if grid_done(task_dir):
            print(f"Skipping finished task: {task.name}")
            entry["status"] = "completed"
            continue
        print(f"Task {task.name}: {task.attribute}={task.value} ({task.scenario})")
        try:
            run_one(task, entry, task_dir, args, views)
            entry["status"] = "completed"
        except Exception as exc:  # one failed task must not stop the others
            failures += 1
            entry.update(status="failed", error=str(exc)[-2000:])
            print(f"[TASK ERROR] {task.name}: {exc}", file=sys.stderr)
        write_manifest(run_dir, manifest)

    write_manifest(run_dir, manifest)
    print(f"{len(tasks) - failures} of {len(tasks)} task(s) completed in {run_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
