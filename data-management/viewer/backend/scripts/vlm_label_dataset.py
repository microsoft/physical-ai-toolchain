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
import csv
import json
import logging
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evaluation.vlm_judge.backend import GenerationConfig, Qwen3VLBackend
from evaluation.vlm_judge.dataset import iter_episodes, load_dataset_spec
from evaluation.vlm_judge.frames import FrameWindow, extract_frames, tile_horizontally

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
        raise ValueError(f"No JSON object in model output: {text!r}")
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
        window = FrameWindow(
            path=record.video_paths[view],
            from_s=record.from_timestamp,
            to_s=record.to_timestamp,
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
    return {
        "pick_from": (str(label.get("pick_from", "uncertain")).lower() or "uncertain"),
        "object": str(label.get("object", "unclear")),
        "grasp_success": as_bool(label.get("grasp_success")),
        "place_success": as_bool(label.get("place_success")),
        "movement_quality": str(label.get("movement_quality", "")),
        "notes": str(label.get("notes", "")),
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
    if labels_path.exists():
        try:
            labels_file = json.loads(labels_path.read_text(encoding="utf-8"))
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
        record = {field: row.get(field) for field in ANALYSIS_FIELDS}
        record.update(
            {
                "instruction": row.get("instruction"),
                "duration_s": row.get("duration_s"),
                "source": row.get("source") or source,
            }
        )
        analysis[key] = {**existing, **record}
        updated += 1

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=labels_path.parent,
            prefix=f".{labels_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(labels_file, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(labels_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
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
) -> dict[str, Any]:
    """Label every (or ``limit``) episode and write JSONL + CSV to ``output_dir``."""
    selected_views = resolve_views(dataset_root, views)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "labels.jsonl"
    csv_path = output_dir / "labels.csv"

    episodes = list(iter_episodes(dataset_root, views=selected_views, limit=limit))
    prior_rows = _load_jsonl_rows(jsonl_path) if resume else []
    latest_rows = {int(row["episode_index"]): row for row in prior_rows}
    completed_indices = {
        episode_index for episode_index, row in latest_rows.items() if row.get("error") is None
    }
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
        backend = Qwen3VLBackend(model_id=model_id, device_map=device_map, dtype=dtype)
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
            except Exception as err:
                row.update(_empty_row(f"{type(err).__name__}: {err}"))

            elapsed = time.time() - started
            jf.write(json.dumps(row) + "\n")
            jf.flush()
            latest_rows[record.episode_index] = row
            status = row["error"] or (
                f"pick={row['pick_from']!s:<10} object={row['object']!r:<20} "
                f"grasp={row['grasp_success']} place={row['place_success']}"
            )
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


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    parser.add_argument("--device-map", default="auto", help="transformers device_map.")
    parser.add_argument("--dtype", default="bfloat16", help="Model dtype (e.g. bfloat16, float16).")
    parser.add_argument("--limit", type=int, default=None, help="Label only the first N episodes.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append attempts, retry failed rows, and skip successful episode indices in labels.jsonl.",
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
    output_dir: Path = args.output_dir or (dataset_root / "vlm-labels")
    label_dataset(
        dataset_root=dataset_root,
        output_dir=output_dir,
        views=args.views,
        n_frames=args.n_frames,
        frame_size=args.frame_size,
        model_id=args.model_id,
        device_map=args.device_map,
        dtype=args.dtype,
        limit=args.limit,
        scene_context=args.scene_context,
        resume=args.resume,
        write_analysis=args.write_analysis,
        dataset_id=args.dataset_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
