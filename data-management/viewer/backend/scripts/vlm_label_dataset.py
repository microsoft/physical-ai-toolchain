"""Generic VLM labeling pass over any LeRobot dataset.

Loads a Qwen3-VL model once and, for every episode, tiles all (or selected)
camera views into a temporal filmstrip and asks the model for a structured
manipulation label: where the object is picked from, the target object, grasp
and place success, an overall movement-quality statement, and short notes.
Accepted work is persisted before execution. JSONL and CSV export saved results.
Use ``--resume --job-id ID`` to retry failed targets. Dataset execution requires
explicit sample approval; ``--single`` runs one judge-only episode.
Use ``--write-analysis`` for approved, contribution-aware application.

This is the reusable, dataset-agnostic version of the one-off SO-101 labeling
script: views are auto-detected from ``meta/info.json`` and every parameter is
a CLI flag, so it runs on any LeRobot v2.1/v3.0 dataset.

Example:
    python scripts/vlm_label_dataset.py \\
        --dataset-root /data/my-dataset \\
        --output-dir /data/my-dataset-vlm-labels \\
        --n-frames 16 --limit 1 --single
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from evaluation.vlm_judge.backend import GenerationConfig, Qwen3VLBackend
from evaluation.vlm_judge.curation_storage import LocalCurationStorage, apply_judge_result
from evaluation.vlm_judge.dataset import iter_episodes, load_dataset_spec
from evaluation.vlm_judge.frames import FrameWindow, extract_frames, tile_horizontally
from evaluation.vlm_judge.job_cli import add_job_arguments, execute_job_command
from evaluation.vlm_judge.job_storage import LocalJobStore
from evaluation.vlm_judge.jobs import JudgeJobs
from evaluation.vlm_judge.saved_input import (
    LocalDatasetResolver,
    SavedInputSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from evaluation.vlm_judge.dataset import EpisodeRecord

_LOGGER = logging.getLogger("vlm_label_dataset")

DEFAULT_MODEL_ID = "Qwen/Qwen3-VL-4B-Instruct"
DEFAULT_N_FRAMES = 16
DEFAULT_FRAME_SIZE = 512

ANALYSIS_FIELDS = (
    "pick_from",
    "object",
    "grasp_success",
    "place_success",
    "movement_quality",
    "notes",
)
CSV_FIELDS = [
    "episode_index",
    "pick_from",
    "object",
    "grasp_success",
    "place_success",
    "movement_quality",
    "notes",
    "duration_s",
    "error",
]

SYSTEM_PROMPT = (
    "You are a meticulous robotics data annotator reviewing remotely operated "
    "robot-arm manipulation episodes. You analyze multi-view camera frames and "
    "report precise, objective labels. You never guess wildly: when the "
    "evidence is ambiguous you say so. You always answer with a single strict "
    "JSON object and nothing else."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_user_prompt(
    *,
    n_frames: int,
    views: Sequence[str],
    instruction: str | None,
    scene_context: str | None = None,
) -> str:
    """Compose the per-episode user prompt, describing the tiled views in order."""
    if len(views) == 1:
        view_desc = f"Each image is a single camera view: {views[0]}."
    else:
        ordered = ", ".join(f"{i + 1}) {view}" for i, view in enumerate(views))
        view_desc = f"Each image tiles {len(views)} camera views side by side, left to right: {ordered}."
    task_line = (
        f'The task for this episode is: "{instruction}".'
        if instruction
        else "The task is a manipulation (pick-and-place) episode."
    )
    scene_line = f"\n{scene_context.strip()}\n" if scene_context else ""
    return f"""These {n_frames} images are frames sampled in temporal order (first = start of \
the episode, last = end) from ONE robot-arm episode.

{view_desc}

{task_line}
{scene_line}
Watch the whole sequence, then label this episode. Report:
1. pick_from: a short phrase for where the object is picked FROM (e.g. "front", \
"right", "left", "table", "bin", or "uncertain").
2. object: a short noun phrase naming the object being picked up (e.g. "red cube", \
"wooden block", "small toy"). Use "unclear" only if truly indeterminable.
3. grasp_success: true if the gripper closed on the object and lifted it clear of \
the source location; false otherwise.
4. place_success: true if the object was released and came to rest at the intended \
destination; false otherwise.
5. movement_quality: ONE concise sentence assessing the arm's motion (smoothness, \
hesitation, retries, collisions, or overall efficiency).
6. notes: at most one short sentence of supporting evidence (optional, may be "").

Respond with ONLY this JSON object, no markdown, no prose:
{{"pick_from": "...", "object": "...", "grasp_success": true, "place_success": true, \
"movement_quality": "...", "notes": "..."}}"""


def parse_label(text: str) -> dict[str, Any]:
    """Extract the JSON object from a model response, tolerating code fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    match = _JSON_RE.search(stripped)
    if not match:
        raise ValueError("No JSON object in model output")
    return json.loads(match.group(0))


def as_bool(value: Any) -> bool | None:
    """Coerce a model-provided value to a tri-state boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "y", "1"):
            return True
        if low in ("false", "no", "n", "0"):
            return False
    return None


def resolve_views(root: Path, requested: Sequence[str] | None) -> tuple[str, ...]:
    """Return the video views to tile: all dataset views, or a validated subset."""
    spec = load_dataset_spec(root)
    if not requested:
        return spec.video_keys
    missing = [view for view in requested if view not in spec.video_keys]
    if missing:
        raise ValueError(f"Requested views not in dataset: {missing}. Available: {list(spec.video_keys)}")
    return tuple(requested)


def build_filmstrip(
    record: EpisodeRecord,
    *,
    views: Sequence[str],
    n_frames: int,
    frame_size: int,
) -> list:
    """Tile the selected views into ``n_frames`` composite frames for the episode."""
    target = (frame_size, frame_size)
    per_view = []
    for view in views:
        start, end = record.video_windows.get(view, (record.from_timestamp, record.to_timestamp))
        window = FrameWindow(
            path=record.video_paths[view],
            from_s=start,
            to_s=end,
        )
        per_view.append(extract_frames(window, n_frames=n_frames, target_size=target))
    return tile_horizontally(per_view) if len(per_view) > 1 else per_view[0]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-episode rows into headline counts."""
    ok = [row for row in rows if row.get("error") is None]
    return {
        "labeled": len(ok),
        "total": len(rows),
        "errors": len(rows) - len(ok),
        "grasp_success": sum(1 for row in ok if row["grasp_success"] is True),
        "place_success": sum(1 for row in ok if row["place_success"] is True),
    }


def _row_from_label(label: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(label, dict):
        raise ValueError("Invalid task label result")
    for name in ("pick_from", "object", "movement_quality"):
        if not isinstance(label.get(name), str) or not label[name].strip():
            raise ValueError(f"Invalid task label result field: {name}")
    for name in ("grasp_success", "place_success"):
        if type(label.get(name)) is not bool:
            raise ValueError(f"Invalid task label result field: {name}")
    if not isinstance(label.get("notes", ""), str):
        raise ValueError("Invalid task label result field: notes")
    return {
        "pick_from": label["pick_from"].lower(),
        "object": label["object"],
        "grasp_success": label["grasp_success"],
        "place_success": label["place_success"],
        "movement_quality": label["movement_quality"],
        "notes": label.get("notes", ""),
        "error": None,
    }


def _empty_row(error: str) -> dict[str, Any]:
    return {field: None for field in ANALYSIS_FIELDS} | {"error": error}


class TaskExecutor:
    """Execute only the configured task-findings family with lazy model loading."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.backend = None
        self.lock = Lock()

    def _infer(self, record: EpisodeRecord, snapshot: SavedInputSnapshot) -> dict[str, Any]:
        with self.lock:
            if self.backend is None:
                self.backend = Qwen3VLBackend(
                    model_id=self.config["model_id"],
                    revision=self.config["model_revision"],
                    device_map=self.config["device_map"],
                    dtype=self.config["dtype"],
                )
        frames = build_filmstrip(
            record, views=self.config["views"], n_frames=self.config["n_frames"], frame_size=self.config["frame_size"]
        )
        raw = self.backend.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(
                n_frames=self.config["n_frames"],
                views=self.config["views"],
                instruction=snapshot.instruction,
                scene_context=self.config["scene_context"],
            ),
            images=frames,
            config=GenerationConfig(**self.config["generation"]),
        )
        row = _row_from_label(parse_label(raw))
        return {
            "episode_id": record.episode_id,
            "instruction": snapshot.instruction,
            "judge_model": self.config["model_id"],
            "prompt_version": self.config["prompt_version"],
            "findings": {field: row[field] for field in ANALYSIS_FIELDS},
        }

    async def __call__(
        self, record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, Any]
    ) -> dict[str, Any]:
        if {key: value for key, value in config.items() if key != "force"} != {
            key: value for key, value in self.config.items() if key != "force"
        }:
            raise ValueError("Saved task-finding runtime configuration is unavailable")
        execution = asyncio.create_task(asyncio.to_thread(self._infer, record, snapshot))
        try:
            return await asyncio.shield(execution)
        except asyncio.CancelledError:
            await execution
            raise


async def _durable_label_dataset(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.resume:
        if not args.job_id or args.operation not in {"submit", "retry"}:
            raise ValueError("Resume requires --job-id and the submit or retry operation")
        args.operation = "retry"
    root = args.dataset_root.resolve()
    dataset_id = args.dataset_id or root.name
    output = args.output_dir or root / "vlm-labels"
    config = {
        "backend": "qwen3-vl",
        "model_id": args.model_id,
        "model_revision": args.model_revision,
        "device_map": args.device_map,
        "dtype": args.dtype,
        "n_frames": args.n_frames,
        "frame_size": args.frame_size,
        "views": list(resolve_views(root, args.views)),
        "scene_context": args.scene_context,
        "prompt_version": "task-findings-v1",
        "generation": {"max_new_tokens": 512, "temperature": 0.0},
        "annotation_author_id": args.annotation_author_id,
        "force": args.force,
    }
    if args.write_analysis and args.operation == "submit":
        args.mode = "judge-and-label"
    resolver = LocalDatasetResolver({dataset_id: root})
    storage = LocalCurationStorage({dataset_id: root})

    async def apply_result(job: dict[str, Any], target: dict[str, Any]) -> bool:
        return await apply_judge_result(storage, job, target)

    jobs = JudgeJobs(
        LocalJobStore(args.job_dir or root.parent / ".curation" / "judge"),
        resolver,
        TaskExecutor(config),
        capacity=args.capacity,
        capacity_scope=args.capacity_scope,
        result_kind="task-findings",
        apply_result=apply_result,
        curation_storage=storage,
    )
    indices = []
    if args.operation == "submit":
        records = list(
            iter_episodes(
                root,
                views=config["views"],
                limit=args.limit,
                indices=[args.episode_index] if args.episode_index is not None else None,
            )
        )
        indices = [record.episode_index for record in records]
        output.mkdir(parents=True, exist_ok=True)
        (output / "labels.config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    code, result = await execute_job_command(
        jobs, args, dataset_id=dataset_id, actor=args.principal_scope_id, config=config, indices=indices
    )
    if args.operation in {"submit", "retry", "apply"} and not args.detach and "targets" in result:
        output.mkdir(parents=True, exist_ok=True)
        rows = []
        for target in result["targets"]:
            evidence = target["result"]
            row = {
                "episode_index": target["episode_index"],
                "instruction": target["input"]["instruction"],
                "snapshot_id": target["input"]["snapshot_id"],
                "input_key": target["input_key"],
                "run_id": result["id"],
                "result_id": target["result_id"],
                "source": config["model_id"],
            }
            row.update({**evidence["findings"], "error": None} if evidence else _empty_row(target["error"]))
            rows.append(row)
        with (output / "labels.jsonl").open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        with (output / "labels.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return code, result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_job_arguments(parser)
    parser.add_argument("--force", action="store_true", help="Recompute instead of reusing identical saved evidence")
    parser.add_argument("--dataset-root", type=Path, required=True, help="Path to the LeRobot dataset directory.")
    parser.add_argument(
        "--dataset-id",
        default=None,
        help="Canonical dataviewer ID written with --write-analysis (for example owner--dataset).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write labels.jsonl/labels.csv (default: <dataset-root>/vlm-labels).",
    )
    parser.add_argument(
        "--views",
        nargs="*",
        default=None,
        help="Video feature keys to tile (default: all views in the dataset).",
    )
    parser.add_argument("--n-frames", type=int, default=DEFAULT_N_FRAMES, help="Frames sampled per episode.")
    parser.add_argument("--frame-size", type=int, default=DEFAULT_FRAME_SIZE, help="Per-view letterbox size (px).")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="Hugging Face Qwen3-VL model id.")
    parser.add_argument("--model-revision", default=None, help="Immutable model revision.")
    parser.add_argument("--principal-scope-id", default="local", help="Saved edit author scope for local operation.")
    parser.add_argument("--annotation-author-id", default=None, help="Select one saved instruction author.")
    parser.add_argument("--device-map", default="auto", help="transformers device_map.")
    parser.add_argument("--dtype", default="bfloat16", help="Model dtype (e.g. bfloat16, float16).")
    parser.add_argument("--limit", type=int, default=None, help="Label only the first N episodes.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Retry an existing --job-id, retaining successful targets and validating saved inputs.",
    )
    parser.add_argument(
        "--write-analysis",
        action="store_true",
        help="Merge successful rows into <dataset-root>/meta/episode_labels.json for the dataviewer.",
    )
    parser.add_argument(
        "--scene-context",
        default=None,
        help="Optional sentence(s) describing the scene/layout, injected into the prompt "
        "(e.g. bin positions) to sharpen labels like pick_from.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    dataset_root: Path = args.dataset_root
    if not dataset_root.exists():
        _LOGGER.error("Dataset root does not exist: %s", dataset_root)
        return 2
    try:
        code, result = asyncio.run(_durable_label_dataset(args))
    except KeyboardInterrupt:
        _LOGGER.info("Client interrupted; accepted jobs remain available through status")
        return 130
    except (ValueError, OSError, KeyError) as error:
        _LOGGER.error("Task-label command failed category=%s", type(error).__name__)
        return 2
    sys.stdout.write(json.dumps(result) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
