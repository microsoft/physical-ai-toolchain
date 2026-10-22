# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Payload helpers that splice the ODD gap analysis between SDG curation and generation.

The external sdg-workflow DAG is never modified. Instead, ``sdg.sh pipeline`` runs the DAG
twice around the ODD container, and this module derives the DAG payloads for each phase:

* ``curation-payload``    curation + CDS ingest only (generation disabled)
* ``clips-dir``           host path of the curated clips written by that curation run
* ``generation-payloads`` one generation payload per ODD gap, prompted with the ODD paragraphs
* ``carla-plan``          validate views/GPUs and count the ODD gaps the CARLA backend can run
* ``carla-collect``       gather CARLA + Cosmos Transfer outputs and write ``carla_results.json``
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

DEFAULT_PROMPTS = Path(__file__).resolve().parents[1] / "configs" / "odd-prompts.json"
WFM_FILENAME = "wfm_generation_input.json"
PROMPTS_FILENAME = "generation_prompts.json"
RESULTS_FILENAME = "carla_results.json"
# The Cosmos3 (DAG) backend only generates these ODD attributes; the rest are not run there.
COSMOS3_ATTRIBUTES = ("weatherType", "timeOfDay")
CARLA_DEFAULT_DIR = Path(__file__).resolve().parents[1] / "carla-cosmos"
TASKS_FILENAME = "tasks.json"

# Six views: every camera except the narrow-FOV front tele. Still needs 8 Cosmos GPUs (see carla-plan).
SIX_VIEWS = ("front_wide", "cross_left", "cross_right", "rear_left", "rear_right", "rear")
ALL_VIEWS = (*SIX_VIEWS, "front_tele")


def log(message: str) -> None:
    print(f"[SDG-ODD] {message}", file=sys.stderr)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sanitize_run_id(run_id: str) -> str:
    """Mirror the DAG's join_output() so the run directory can be located on disk."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", run_id.strip("/"))


def curation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a payload that curates and indexes clips but skips retrieval and generation."""
    result = copy.deepcopy(payload)
    result["search_mode"] = "none"
    result.pop("query_video", None)
    result["cosmos"] = {"enabled": False}
    return result


def clips_dir(payload: dict[str, Any], run_id: str) -> Path:
    output_directory = str(payload.get("output_directory", "")).rstrip("/")
    if not output_directory or "://" in output_directory:
        raise ValueError(
            "The ODD step reads curated clips from disk, so output_directory must be a local "
            f"path (got {output_directory!r})"
        )
    return Path(output_directory) / sanitize_run_id(run_id) / "split" / "clips"


def resolve_prompt(prompts: dict[str, Any], attribute: str, value: str) -> str | None:
    template = prompts.get(attribute)
    if isinstance(template, dict):
        template = template.get(value)
    if not isinstance(template, str) or not template.strip():
        return None
    return template.replace("{value}", value).replace("{attribute}", attribute)


def generation_payloads(
    payload: dict[str, Any],
    wfm: dict[str, Any],
    prompts: dict[str, Any],
    max_runs: int = 0,
    odd_prompts: dict[tuple[str, str, str, str], str] | None = None,
    attributes: Collection[str] = COSMOS3_ATTRIBUTES,
) -> list[tuple[str, dict[str, Any]]]:
    """Build one generation payload per (ODD gap value, base video) of the given attributes.

    The ODD base video becomes the DAG's ``query_video``; the DAG retrieves similar clips
    with Cosmos Dataset Search and generates the missing condition on them. The prompt is
    the paragraph the ODD module wrote for that scene variation (``odd_prompts``, keyed by
    category, attribute, value, scene); otherwise the fixed template for the attribute.
    Gaps for other attributes are skipped.
    """
    odd_prompts = odd_prompts or {}
    results: list[tuple[str, dict[str, Any]]] = []
    ignored: set[str] = set()
    for category, gap_attributes in (wfm.get("generation_gaps") or {}).items():
        for attribute, info in gap_attributes.items():
            if attribute not in attributes:
                ignored.add(attribute)
                continue
            for value, spec in (info.get("values") or {}).items():
                template = resolve_prompt(prompts, attribute, value)
                videos = (spec.get("suitable_videos") or [])[: int(spec.get("required_count", 1))]
                for video in videos:
                    prompt = odd_prompts.get((category, attribute, value, video["scene_name"]), template)
                    if prompt is None:
                        log(f"No prompt for {attribute}={value} ({video['scene_name']}); skipping")
                        continue
                    run = copy.deepcopy(payload)
                    top_k = int(run.get("top_k", 10))
                    cosmos = dict(run.get("cosmos") or {})
                    cosmos.update(enabled=True, prompt=prompt)
                    if cosmos.get("num_clips") is not None:
                        cosmos["num_clips"] = min(int(cosmos["num_clips"]), top_k)
                    run.update(
                        search_mode="embedding",
                        query_video=video["video_path"],
                        cosmos=cosmos,
                    )
                    name = f"{attribute}_{value}_{video['scene_name']}"
                    results.append((re.sub(r"[^A-Za-z0-9._-]", "-", name), run))
    if ignored:
        log(f"Skipping gaps outside {', '.join(attributes)}: {', '.join(sorted(ignored))}")
    if max_runs > 0 and len(results) > max_runs:
        log(f"Limiting {len(results)} generation runs to {max_runs}")
        results = results[:max_runs]
    return results


def carla_gap_counts(wfm: dict[str, Any], carla_dir: Path = CARLA_DEFAULT_DIR) -> tuple[int, dict[str, int]]:
    """Return (CARLA task count, skipped task count per ``attribute=value``) for an ODD output."""
    plan = load_carla_module("pipeline/task_plan.py", carla_dir)
    tasks, skipped = plan.plan_tasks(wfm, plan.load_scenario_map(carla_dir / "scenario_map.json"))
    return len(tasks), skipped


def parse_views(raw: str) -> list[str]:
    tokens = raw.replace(",", " ").split()
    if not tokens or any(t.lower() == "all" for t in tokens):
        return list(ALL_VIEWS)
    unknown = [t for t in tokens if t not in ALL_VIEWS]
    if unknown:
        raise ValueError(f"Unknown view(s) {unknown}; use any of: all, {', '.join(ALL_VIEWS)}")
    return [view for view in ALL_VIEWS if view in tokens]


def load_carla_module(relative_path: str, carla_dir: Path) -> Any:
    """Import a dependency-free module of the CARLA pipeline by path, without its container-only packages."""
    path = carla_dir / "src" / relative_path
    spec = importlib.util.spec_from_file_location(f"carla_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def cp_size_for(carla_dir: Path, view_count: int) -> int:
    """Number of Cosmos GPU processes for a view count, from the pipeline's own rule."""
    return load_carla_module("utils/cosmos/gpu_utils.py", carla_dir).choose_context_parallel_size(view_count)


def installed_gpu_count() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return sum(1 for line in out.splitlines() if line.startswith("GPU "))


def cmd_carla_plan(args: argparse.Namespace) -> int:
    try:
        views = parse_views(args.views)
        needed = cp_size_for(Path(args.carla_dir), len(views))
    except (ValueError, OSError) as exc:
        log(str(exc))
        return 2
    ids = [g for g in args.gpu_ids.replace(" ", "").split(",") if g]
    if len(ids) < needed:
        log(
            f"{len(views)} view(s) need {needed} GPUs for Cosmos but COSMOS_GPU_IDS lists {len(ids)}; "
            "the context-parallel size must be at least the view count and divide the model's 16 heads"
        )
        return 2
    if len(ids) > needed:
        log(f"COSMOS_GPU_IDS lists {len(ids)} GPUs but {needed} are used; the rest stay idle")
    if args.carla_gpu in ids:
        log(f"CARLA GPU {args.carla_gpu} is shared with a Cosmos rank (CARLA needs about 6 GB)")
    installed = installed_gpu_count()
    wanted = max(int(g) for g in [*ids, args.carla_gpu])
    if installed is not None and wanted >= installed:
        log(f"GPU index {wanted} requested but only {installed} GPU(s) are installed")
        return 2
    if args.odd_output:
        wfm_path = Path(args.odd_output) / WFM_FILENAME
        if not wfm_path.is_file():
            log(f"ODD output not found: {wfm_path}")
            return 2
        runnable, skipped = carla_gap_counts(load_json(wfm_path), Path(args.carla_dir))
        for gap, count in skipped.items():
            log(f"CARLA has no scenario for {gap} ({count} variation(s)); skipping")
        if runnable == 0:
            log("None of the ODD gaps map to a CARLA scenario")
            return 2
        log(f"CARLA will run {runnable} task(s)")
    log(f"Views: {' '.join(views)}; Cosmos GPUs: {','.join(ids[:needed])}; CARLA GPU: {args.carla_gpu}")
    return 0


def collect_carla_run(run_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Copy each finished task's artifacts to ``out_dir`` and index them."""
    tasks: list[dict[str, Any]] = []
    for planned in load_json(run_dir / TASKS_FILENAME)["tasks"]:
        name = planned["task"]
        source = run_dir / "tasks" / name
        grid = source / "multiview_grid.mp4"
        entry: dict[str, Any] = {"task": name, "status": "incomplete"}
        if planned.get("error"):
            entry["error"] = planned["error"]
        if grid.is_file() and grid.stat().st_size > 0:
            target = out_dir / name
            target.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                if item.is_dir():
                    shutil.copytree(item, target / item.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target / item.name)
            entry.update(
                status="completed",
                multiview_grid=f"{name}/multiview_grid.mp4",
                caption=f"{name}/caption.txt" if (target / "caption.txt").is_file() else None,
                metadata=f"{name}/metadata.json" if (target / "metadata.json").is_file() else None,
                synthesized_views=sorted(
                    str(p.relative_to(out_dir)) for p in (target / "cosmos_synthesized").rglob("*.mp4")
                ),
            )
        tasks.append(entry)
    index = {
        "run_dir": str(run_dir),
        "completed": sum(1 for t in tasks if t["status"] == "completed"),
        "incomplete": sum(1 for t in tasks if t["status"] != "completed"),
        "tasks": tasks,
    }
    write_json(out_dir / RESULTS_FILENAME, index)
    return index


def cmd_carla_collect(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not (run_dir / TASKS_FILENAME).is_file():
        log(f"No CARLA run found in {run_dir}")
        return 2
    index = collect_carla_run(run_dir, Path(args.out_dir))
    log(f"CARLA run: {index['completed']} completed, {index['incomplete']} incomplete -> {args.out_dir}")
    for task in index["tasks"]:
        if task["status"] != "completed":
            log(f"Incomplete task: {task['task']}")
    return 0 if index["completed"] and not index["incomplete"] else 1


def cmd_curation_payload(args: argparse.Namespace) -> int:
    write_json(Path(args.out), curation_payload(load_json(Path(args.payload))))
    return 0


def cmd_clips_dir(args: argparse.Namespace) -> int:
    try:
        print(clips_dir(load_json(Path(args.payload)), args.run_id))
    except ValueError as exc:
        log(str(exc))
        return 2
    return 0


def load_odd_prompts(odd_output: Path) -> dict[tuple[str, str, str, str], str]:
    path = odd_output / PROMPTS_FILENAME
    if not path.is_file():
        log(f"{PROMPTS_FILENAME} not found in {odd_output}; using fixed prompt templates")
        return {}
    return {
        (e["category"], e["attribute"], e["value"], e["scene_name"]): e["prompt"]
        for e in load_json(path).get("prompts", [])
        if e.get("prompt")
    }


def cmd_generation_payloads(args: argparse.Namespace) -> int:
    wfm_path = Path(args.odd_output) / WFM_FILENAME
    if not wfm_path.is_file():
        log(f"ODD output not found: {wfm_path}")
        return 2
    runs = generation_payloads(
        load_json(Path(args.payload)),
        load_json(wfm_path),
        load_json(Path(args.prompts)),
        args.max_runs,
        load_odd_prompts(Path(args.odd_output)),
        [a for a in args.attributes.replace(",", " ").split() if a],
    )
    out_dir = Path(args.out_dir)
    for index, (name, run) in enumerate(runs, start=1):
        path = out_dir / f"{index:03d}_{name}.json"
        write_json(path, run)
        print(path)
    log(f"Wrote {len(runs)} generation payload(s) to {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cur = sub.add_parser("curation-payload", help="Derive the curation-only payload")
    cur.add_argument("--payload", required=True)
    cur.add_argument("--out", required=True)
    cur.set_defaults(func=cmd_curation_payload)

    clips = sub.add_parser("clips-dir", help="Print the curated clips directory of a run")
    clips.add_argument("--payload", required=True)
    clips.add_argument("--run-id", required=True)
    clips.set_defaults(func=cmd_clips_dir)

    gen = sub.add_parser("generation-payloads", help="Derive generation payloads from ODD output")
    gen.add_argument("--payload", required=True)
    gen.add_argument("--odd-output", required=True)
    gen.add_argument("--out-dir", required=True)
    gen.add_argument("--prompts", default=str(DEFAULT_PROMPTS))
    gen.add_argument("--max-runs", type=int, default=0, help="0 means no limit")
    gen.add_argument(
        "--attributes",
        default=",".join(COSMOS3_ATTRIBUTES),
        help="ODD attributes to generate (comma separated)",
    )
    gen.set_defaults(func=cmd_generation_payloads)

    plan = sub.add_parser("carla-plan", help="Validate CARLA views/GPUs and the ODD gaps it can run")
    plan.add_argument("--views", default=" ".join(SIX_VIEWS))
    plan.add_argument("--gpu-ids", required=True, help="Cosmos CUDA device IDs, comma separated")
    plan.add_argument("--carla-gpu", required=True)
    plan.add_argument("--carla-dir", default=str(CARLA_DEFAULT_DIR))
    plan.add_argument("--odd-output", default=None)
    plan.set_defaults(func=cmd_carla_plan)

    collect = sub.add_parser("carla-collect", help="Gather CARLA + Cosmos outputs of a run")
    collect.add_argument("--run-dir", required=True)
    collect.add_argument("--out-dir", required=True)
    collect.set_defaults(func=cmd_carla_collect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
