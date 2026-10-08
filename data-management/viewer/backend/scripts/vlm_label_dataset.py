"""Generic VLM labeling pass over any LeRobot dataset.

Loads a Qwen3-VL model once and, for every episode, tiles all (or selected)
camera views into a temporal filmstrip and asks the model for a structured
manipulation label: where the object is picked from, the target object, grasp
and place success, an overall movement-quality statement, and short notes.
Results are written as JSONL (full) and CSV (flat summary).
Use ``--resume`` to skip episode indices already present in JSONL. Use
``--write-analysis`` to merge successful rows into the dataset's
``meta/episode_labels.json`` analysis map for the dataviewer.

This is the reusable, dataset-agnostic version of the one-off SO-101 labeling
script: views are auto-detected from ``meta/info.json`` and every parameter is
a CLI flag, so it runs on any LeRobot v2.1/v3.0 dataset.

Example:
    python scripts/vlm_label_dataset.py \\
        --dataset-root /data/my-dataset \\
        --output-dir /data/my-dataset-vlm-labels \\
        --n-frames 16 --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from evaluation.vlm_judge.backend import GenerationConfig, Qwen3VLBackend
from evaluation.vlm_judge.cache import JudgeCache
from evaluation.vlm_judge.curation_storage import LocalCurationStorage, apply_judge_result
from evaluation.vlm_judge.dataset import iter_episodes, load_dataset_spec
from evaluation.vlm_judge.frames import FrameWindow, extract_frames, tile_horizontally
from evaluation.vlm_judge.job_cli import add_job_arguments, execute_job_command
from evaluation.vlm_judge.job_storage import LocalJobStore
from evaluation.vlm_judge.jobs import JudgeJobs
from evaluation.vlm_judge.saved_input import (
    LocalDatasetResolver,
    LocalSavedInputReader,
    SavedInputSnapshot,
    resolve_saved_input,
)

from src.api.storage.base import RevisionConflictError
from src.api.storage.local_revision import content_etag, write_conditional

# cspell:ignore extrasaction keepends

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


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Load prior rows, removing an incomplete final write when present."""
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    rows: list[dict[str, Any]] = []
    valid_length = 0
    for index, line in enumerate(lines):
        line_number = index + 1
        if not line.strip():
            valid_length += len(line)
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as err:
            is_incomplete_tail = index == len(lines) - 1 and not line.endswith(("\n", "\r"))
            if not is_incomplete_tail:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {err.msg}") from err
            _LOGGER.warning("Discarding incomplete final JSONL row at %s:%d", path, line_number)
            path.write_text(content[:valid_length], encoding="utf-8")
            break
        if not isinstance(row, dict) or "episode_index" not in row:
            raise ValueError(f"Invalid JSONL row at {path}:{line_number}: missing episode_index")
        rows.append(row)
        valid_length += len(line)

    if rows and path.stat().st_size > 0 and not path.read_bytes().endswith((b"\n", b"\r")):
        with path.open("a", encoding="utf-8") as jsonl_file:
            jsonl_file.write("\n")
    return rows


def _write_analysis_records(
    dataset_root: Path,
    rows: list[dict[str, Any]],
    source: str,
    dataset_id: str | None = None,
) -> int:
    """Merge successful labeling rows into the dataviewer analysis map."""
    labels_path = dataset_root / "meta" / "episode_labels.json"
    etag = None
    if labels_path.exists():
        try:
            content = labels_path.read_bytes()
            labels_file = json.loads(content)
            etag = content_etag(content)
        except json.JSONDecodeError as err:
            raise ValueError(f"Invalid labels file at {labels_path}: {err.msg}") from err
        if not isinstance(labels_file, dict):
            raise ValueError(f"Invalid labels file at {labels_path}: expected a JSON object")
    else:
        labels_file = {
            "dataset_id": dataset_id or dataset_root.name,
            "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
            "episodes": {},
            "analysis": {},
        }

    existing_dataset_id = labels_file.get("dataset_id")
    if dataset_id:
        labels_file["dataset_id"] = dataset_id
    elif not isinstance(existing_dataset_id, str) or not existing_dataset_id.strip():
        labels_file["dataset_id"] = dataset_root.name
    labels_file.setdefault("available_labels", ["SUCCESS", "FAILURE", "PARTIAL"])
    labels_file.setdefault("episodes", {})
    analysis = labels_file.setdefault("analysis", {})
    if not isinstance(analysis, dict):
        raise ValueError(f"Invalid labels file at {labels_path}: analysis must be a JSON object")

    updated = 0
    for row in rows:
        if row.get("error") is not None:
            continue
        key = str(int(row["episode_index"]))
        existing = analysis.get(key, {})
        if not isinstance(existing, dict):
            raise ValueError(f"Invalid analysis record for episode {key}: expected a JSON object")
        record = {field: row[field] for field in (*ANALYSIS_FIELDS, "instruction", "duration_s") if field in row}
        record["source"] = row.get("source") or source
        analysis[key] = {**existing, **record}
        updated += 1

    try:
        write_conditional(
            labels_path,
            json.dumps(labels_file, indent=2) + "\n",
            if_match=etag,
            if_none_match=etag is None,
        )
    except RevisionConflictError:
        _LOGGER.warning("Analysis merge revision conflict; retained existing labels and generated output")
        raise
    return updated


def label_dataset(
    *,
    dataset_root: Path,
    output_dir: Path,
    views: Sequence[str] | None,
    n_frames: int,
    frame_size: int,
    model_id: str,
    device_map: str,
    dtype: str,
    limit: int | None,
    scene_context: str | None = None,
    resume: bool = False,
    write_analysis: bool = False,
    dataset_id: str | None = None,
    model_revision: str | None = None,
    principal_scope_id: str = "local",
    annotation_author_id: str | None = None,
) -> dict[str, Any]:
    """Label every (or ``limit``) episode and write JSONL + CSV to ``output_dir``."""
    selected_views = resolve_views(dataset_root, views)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "labels.jsonl"
    csv_path = output_dir / "labels.csv"

    episodes = list(iter_episodes(dataset_root, views=selected_views, limit=limit))
    canonical_id = dataset_id or dataset_root.name
    reader = LocalSavedInputReader(dataset_root)

    async def saved_input(record: EpisodeRecord, expected: str | None = None) -> SavedInputSnapshot:
        source = await reader.source_revision(canonical_id, record.episode_index)
        return await resolve_saved_input(
            reader,
            canonical_id,
            record.episode_index,
            principal_scope_id=principal_scope_id,
            source=source,
            dataset_instruction=record.instruction,
            media_identity=record.media_identity,
            video_windows=record.video_windows,
            annotation_author_id=annotation_author_id,
            expected_snapshot_id=expected,
        )

    snapshots = [asyncio.run(saved_input(record)) for record in episodes]
    episodes = [
        replace(record, instruction=snapshot.instruction, snapshot_id=snapshot.snapshot_id)
        for record, snapshot in zip(episodes, snapshots, strict=True)
    ]
    declared_config = {
        "schema_version": 2,
        "backend": "qwen3-vl",
        "model_id": model_id,
        "model_revision": model_revision,
        "device_map": device_map,
        "dtype": dtype,
        "n_frames": n_frames,
        "frame_size": frame_size,
        "views": list(selected_views),
        "scene_context": scene_context,
        "prompt_version": "task-findings-v1",
        "generation": {"max_new_tokens": 512, "temperature": 0.0},
    }
    (output_dir / "labels.config.json").write_text(
        json.dumps(
            {**declared_config, "saved_inputs": [snapshot.model_dump(mode="json") for snapshot in snapshots]}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    cache = JudgeCache(None, execution_config=declared_config)
    input_keys = {
        record.episode_index: cache.key(
            episode_id=record.episode_id,
            snapshot_id=record.snapshot_id,
            video_paths=record.video_paths,
            instruction=record.instruction,
            judge_model=model_id,
            prompt_version="task-findings-v1",
            from_s=record.from_timestamp,
            to_s=record.to_timestamp,
            video_windows=record.video_windows,
            media_identity=record.media_identity,
        )
        for record in episodes
    }
    prior_rows = _load_jsonl_rows(jsonl_path) if resume else []
    matching_rows = {}
    for row in prior_rows:
        if row.get("input_key") == input_keys.get(int(row["episode_index"])) and row.get("error") is None:
            try:
                _row_from_label(row)
            except ValueError:
                continue
            matching_rows[int(row["episode_index"])] = row
    latest_rows = dict(matching_rows)
    completed_indices = set(matching_rows)
    pending_episodes = [record for record in episodes if record.episode_index not in completed_indices]
    _LOGGER.info(
        "Labeling %d episodes from %s (views: %s, skipped: %d)",
        len(pending_episodes),
        dataset_root.name,
        list(selected_views),
        len(episodes) - len(pending_episodes),
    )

    backend = None
    gen_cfg = None
    if pending_episodes:
        _LOGGER.info("Loading %s ...", model_id)
        backend = Qwen3VLBackend(model_id=model_id, revision=model_revision, device_map=device_map, dtype=dtype)
        gen_cfg = GenerationConfig(max_new_tokens=512, temperature=0.0)

    mode = "a" if resume else "w"
    with jsonl_path.open(mode, encoding="utf-8") as jf:
        for i, record in enumerate(pending_episodes):
            started = time.time()
            row: dict[str, Any] = {
                "episode_index": record.episode_index,
                "episode_id": record.episode_id,
                "instruction": record.instruction,
                "duration_s": round(record.duration_s, 2),
                "source": model_id,
                "snapshot_id": record.snapshot_id,
                "input_key": input_keys[record.episode_index],
            }
            try:
                if backend is None or gen_cfg is None:
                    raise RuntimeError("VLM backend was not initialized")
                frames = build_filmstrip(
                    record,
                    views=selected_views,
                    n_frames=n_frames,
                    frame_size=frame_size,
                )
                raw = backend.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=build_user_prompt(
                        n_frames=n_frames,
                        views=selected_views,
                        instruction=record.instruction,
                        scene_context=scene_context,
                    ),
                    images=frames,
                    config=gen_cfg,
                )
                row.update(_row_from_label(parse_label(raw)))
                asyncio.run(saved_input(record, record.snapshot_id))
            except Exception as err:
                row.update(_empty_row(type(err).__name__))

            elapsed = time.time() - started
            jf.write(json.dumps(row) + "\n")
            jf.flush()
            latest_rows[record.episode_index] = row
            status = row["error"] or "succeeded"
            _LOGGER.info(
                "[%2d/%d] ep%3d (%4.1fs) %s",
                i + 1,
                len(pending_episodes),
                record.episode_index,
                elapsed,
                status,
            )

    rows = list(latest_rows.values())
    with csv_path.open("w", newline="", encoding="utf-8") as cf:
        writer = csv.DictWriter(cf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if write_analysis:
        updated = _write_analysis_records(dataset_root, rows, model_id, dataset_id)
        _LOGGER.info("Merged %d analysis records into %s", updated, dataset_root / "meta" / "episode_labels.json")

    summary = summarize(rows)
    _LOGGER.info(
        "Labeled %d/%d episodes (%d errors) | grasp %d, place %d | JSONL %s | CSV %s",
        summary["labeled"],
        summary["total"],
        summary["errors"],
        summary["grasp_success"],
        summary["place_success"],
        jsonl_path,
        csv_path,
    )
    return summary


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
        help="Append attempts and reuse only successful results with the exact saved input and configuration.",
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
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    sys.exit(main())
