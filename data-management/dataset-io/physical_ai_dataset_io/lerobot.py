"""LeRobot metadata shared by curation and evaluation.

Resolves a LeRobot dataset directory into an iterable of ``EpisodeRecord``
entries, each carrying the per-view MP4 paths, the language instruction,
and (when applicable) the time window inside a chunked v3.0 video file.

Supports:
- LeRobot v2.1: one MP4 per episode under ``videos/chunk-NNN/<view>/episode_NNNNNN.mp4``.
- LeRobot v3.0: multiple episodes packed into a single MP4 per chunk under
  ``videos/<view>/chunk-NNN/file-NNN.mp4`` with per-episode
  ``from_timestamp`` / ``to_timestamp`` in ``meta/episodes/chunk-NNN/file-NNN.parquet``.
"""

from __future__ import annotations

import io
import json
import logging
import math
from bisect import bisect_left
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, TextIO

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """One episode resolved against the LeRobot dataset on disk."""

    episode_id: str
    episode_index: int
    instruction: str
    fps: float
    length: int
    video_paths: dict[str, Path]
    from_timestamp: float | None
    to_timestamp: float | None
    video_windows: dict[str, tuple[float, float]] = field(default_factory=dict)
    media_identity: dict[str, str] | None = None
    snapshot_id: str | None = None

    @property
    def duration_s(self) -> float:
        if self.from_timestamp is not None and self.to_timestamp is not None:
            return float(self.to_timestamp - self.from_timestamp)
        return float(self.length) / float(self.fps)


class LeRobotFiles(Protocol):
    """Read-only, dataset-relative access to one LeRobot dataset.

    Relative paths use forward slashes. ``path`` is the identity reported in
    ``EpisodeRecord.video_paths``: a real path for local directories, or a
    relative path for release stores that callers resolve through the same store.
    """

    name: str

    def read_bytes(self, relative: str) -> bytes: ...

    def open_text(self, relative: str) -> AbstractContextManager[TextIO]: ...

    def exists(self, relative: str) -> bool: ...

    def find(self, directory: str, pattern: str) -> list[str]:
        """Return sorted relative paths under ``directory`` whose basename matches ``pattern``."""

    def path(self, relative: str) -> Path: ...


class LocalLeRobotFiles:
    """Filesystem view of one dataset directory, used by trusted CLI callers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.name = root.name

    def read_bytes(self, relative: str) -> bytes:
        return (self.root / relative).read_bytes()

    def open_text(self, relative: str) -> AbstractContextManager[TextIO]:
        return (self.root / relative).open(encoding="utf-8")

    def exists(self, relative: str) -> bool:
        return (self.root / relative).exists()

    def find(self, directory: str, pattern: str) -> list[str]:
        base = self.root / directory
        return sorted(item.relative_to(self.root).as_posix() for item in base.rglob(pattern))

    def path(self, relative: str) -> Path:
        return self.root / relative


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Top-level dataset metadata loaded from ``meta/info.json``."""

    files: LeRobotFiles
    codebase_version: str
    fps: float
    total_episodes: int
    chunks_size: int
    video_keys: tuple[str, ...]
    data_path: str
    video_path: str | None
    per_view_video_paths: dict[str, str]


def remap_persistent_language_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_to_output: Mapping[int, int],
    source_timestamps: Sequence[float],
    output_timestamps: Sequence[float],
    timestamp_tolerance_s: float = 1e-4,
) -> list[dict[str, Any]]:
    """Retarget persistent language changes to their first retained output frame.

    Removed starts advance to the next retained source frame. Changes of one
    style, role and camera that collapse together retain the latest source time.
    """
    kept = sorted(source_to_output)
    placed = {}
    for row in rows:
        source_time = float(row["timestamp"])
        if not math.isfinite(source_time):
            raise ValueError("persistent language timestamps must be finite")
        frame = bisect_left(source_timestamps, source_time - timestamp_tolerance_s)
        index = bisect_left(kept, frame)
        if index == len(kept):
            continue
        key = (row.get("style"), row.get("role"), row.get("camera"), source_to_output[kept[index]])
        current = placed.get(key)
        if current is None or source_time > current[0]:
            placed[key] = (source_time, [row])
        elif source_time == current[0]:
            current[1].append(row)
    planned = [{**row, "timestamp": output_timestamps[key[-1]]} for key, (_, group) in placed.items() for row in group]
    return sorted(planned, key=lambda row: (float(row["timestamp"]), row.get("style") or "", row.get("role") or ""))


def _as_files(root: Path | LeRobotFiles) -> LeRobotFiles:
    return LocalLeRobotFiles(root) if isinstance(root, Path) else root


def load_dataset_spec(root: Path | LeRobotFiles) -> DatasetSpec:
    files = _as_files(root)
    info: dict[str, Any] = json.loads(files.read_bytes("meta/info.json"))
    features: dict[str, Any] = info.get("features", {})
    video_keys = tuple(k for k, v in features.items() if v.get("dtype") == "video")
    if not video_keys:
        raise ValueError("No video features found in meta/info.json")
    per_view_paths = {
        k: str(features[k]["videos_path"]) for k in video_keys if isinstance(features[k].get("videos_path"), str)
    }
    return DatasetSpec(
        files=files,
        codebase_version=str(info.get("codebase_version", "v2.1")),
        fps=float(info.get("fps", 30.0)),
        total_episodes=int(info["total_episodes"]),
        chunks_size=int(info.get("chunks_size", 1000)),
        video_keys=video_keys,
        data_path=str(info["data_path"]),
        video_path=str(info["video_path"]) if "video_path" in info else None,
        per_view_video_paths=per_view_paths,
    )


def iter_episodes(
    root: Path | LeRobotFiles,
    *,
    views: tuple[str, ...] | None = None,
    limit: int | None = None,
    indices: list[int] | None = None,
    video_validator: Callable[[Path], Path] | None = None,
) -> Iterator[EpisodeRecord]:
    """Yield ``EpisodeRecord`` entries for every episode in the dataset.

    ``views`` filters which video keys (e.g. ``observation.images.front``) are
    returned; ``None`` keeps every video stream. ``indices`` restricts the
    iteration to a specific list of episode indices. ``limit`` caps the count.
    HTTP callers supply ``video_validator`` to confine media before probes;
    trusted CLI callers retain the default filesystem behavior.
    """
    spec = load_dataset_spec(root)
    selected_views = _resolve_views(spec, views)

    if spec.codebase_version.startswith("v3"):
        # Some v3 datasets store metadata as parquet (chunk-packed videos), while
        # others use jsonl with one-mp4-per-episode layout. Detect by metadata
        # file presence.
        parquet_meta = spec.files.find("meta/episodes", "file-*.parquet")
        records = (
            _iter_v3(spec, selected_views)
            if parquet_meta
            else _iter_v3_jsonl(spec, selected_views, indices=indices, video_validator=video_validator)
        )
    else:
        records = _iter_v21(spec, selected_views)

    yielded = 0
    for record in records:
        if indices is not None and record.episode_index not in indices:
            continue
        if video_validator is not None:
            record = replace(
                record, video_paths={view: video_validator(path) for view, path in record.video_paths.items()}
            )
        yield record
        yielded += 1
        if limit is not None and yielded >= limit:
            return


def _resolve_views(spec: DatasetSpec, views: tuple[str, ...] | None) -> tuple[str, ...]:
    if views is None:
        return spec.video_keys
    missing = [v for v in views if v not in spec.video_keys]
    if missing:
        raise ValueError(
            f"Requested views not present in dataset: {missing}. Available: {spec.video_keys}",
        )
    return views


# -------------------------------------------------------------------------
# LeRobot v2.1 — one MP4 per episode
# -------------------------------------------------------------------------


def _iter_v21(spec: DatasetSpec, views: tuple[str, ...]) -> Iterator[EpisodeRecord]:
    tasks_by_index = _load_tasks_jsonl(spec.files, "meta/tasks.jsonl")

    with spec.files.open_text("meta/episodes.jsonl") as fh:
        for line in fh:
            payload = json.loads(line)
            ep_idx = int(payload["episode_index"])
            instruction = _resolve_instruction_v21(payload, tasks_by_index)
            length = int(payload.get("length", 0))
            chunk = ep_idx // spec.chunks_size
            video_paths = {
                v: spec.files.path(
                    spec.video_path.format(
                        episode_chunk=chunk,
                        video_key=v,
                        episode_index=ep_idx,
                    )
                )
                for v in views
            }
            yield EpisodeRecord(
                episode_id=f"{spec.files.name}/episode_{ep_idx:06d}",
                episode_index=ep_idx,
                instruction=instruction,
                fps=spec.fps,
                length=length,
                video_paths=video_paths,
                from_timestamp=None,
                to_timestamp=None,
            )


def _load_tasks_jsonl(files: LeRobotFiles, relative: str) -> dict[int, str]:
    tasks: dict[int, str] = {}
    if not files.exists(relative):
        return tasks
    with io.StringIO(files.read_bytes(relative).decode()) as fh:
        for line in fh:
            entry = json.loads(line)
            if "task_index" not in entry or "task" not in entry:
                continue
            tasks[int(entry["task_index"])] = str(entry["task"])
    return tasks


def _resolve_instruction_v21(
    payload: dict[str, Any],
    tasks_by_index: dict[int, str],
) -> str:
    if isinstance(payload.get("tasks"), list) and payload["tasks"]:
        return " | ".join(str(t) for t in payload["tasks"])
    if "task_index" in payload:
        return tasks_by_index.get(int(payload["task_index"]), "")
    return ""


# -------------------------------------------------------------------------
# LeRobot v3.0 — episodes packed into shared chunk MP4s
# -------------------------------------------------------------------------


def _iter_v3(spec: DatasetSpec, views: tuple[str, ...]) -> Iterator[EpisodeRecord]:
    import pyarrow.parquet as pq

    tasks_by_index = _load_tasks_parquet(spec.files, "meta/tasks.parquet")
    episode_files = spec.files.find("meta/episodes", "file-*.parquet")
    if not episode_files:
        raise FileNotFoundError("No v3 episode metadata found under meta/episodes")

    for ep_file in episode_files:
        df = pq.read_table(io.BytesIO(spec.files.read_bytes(ep_file))).to_pandas()
        for row in df.to_dict(orient="records"):
            ep_idx = int(row["episode_index"])
            length = int(row["length"])
            instruction = _resolve_instruction_v3(row, tasks_by_index)
            video_paths: dict[str, Path] = {}
            video_windows = {}
            from_ts: float | None = None
            to_ts: float | None = None
            for view in views:
                v_chunk = int(row[f"videos/{view}/chunk_index"])
                v_file = int(row[f"videos/{view}/file_index"])
                from_ts = float(row[f"videos/{view}/from_timestamp"])
                to_ts = float(row[f"videos/{view}/to_timestamp"])
                video_windows[view] = (from_ts, to_ts)
                video_paths[view] = spec.files.path(
                    spec.video_path.format(
                        chunk_index=v_chunk,
                        file_index=v_file,
                        video_key=view,
                    )
                )
            yield EpisodeRecord(
                episode_id=f"{spec.files.name}/episode_{ep_idx:06d}",
                episode_index=ep_idx,
                instruction=instruction,
                fps=spec.fps,
                length=length,
                video_paths=video_paths,
                from_timestamp=from_ts,
                to_timestamp=to_ts,
                video_windows=video_windows,
            )


# -------------------------------------------------------------------------
# LeRobot v3.0 — jsonl metadata + one MP4 per episode (e.g. schaeffler_sim_avc1)
# -------------------------------------------------------------------------


def _iter_v3_jsonl(
    spec: DatasetSpec,
    views: tuple[str, ...],
    *,
    indices: list[int] | None = None,
    video_validator: Callable[[Path], Path] | None = None,
) -> Iterator[EpisodeRecord]:
    ep_files = spec.files.find("meta/episodes", "episode_*.jsonl")
    if not ep_files and spec.files.exists("meta/episodes.jsonl"):
        ep_files = ["meta/episodes.jsonl"]
    if not ep_files:
        raise FileNotFoundError("No jsonl v3 episode metadata found under meta/episodes")

    tasks_by_index = _load_tasks_jsonl(spec.files, "meta/tasks.jsonl")

    for ep_file in ep_files:
        with spec.files.open_text(ep_file) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = _normalize_tasks_field(json.loads(line))
                ep_idx = int(payload["episode_index"])
                if indices is not None and ep_idx not in indices:
                    continue
                length = int(payload.get("length", 0))
                instruction = _resolve_instruction_v21(payload, tasks_by_index)
                video_paths: dict[str, Path] = {}
                missing_views: list[str] = []
                for view in views:
                    template = spec.per_view_video_paths.get(view)
                    if not template:
                        raise KeyError(
                            f"No videos_path template for view {view!r} in info.json features",
                        )
                    relative = template.format(episode_index=ep_idx)
                    candidate = spec.files.path(relative)
                    if video_validator is not None:
                        candidate = video_validator(candidate)
                    elif not spec.files.exists(relative):
                        missing_views.append(view)
                    video_paths[view] = candidate
                if missing_views:
                    _LOGGER.warning(
                        "episode %d skipped: missing videos for views=%s",
                        ep_idx,
                        missing_views,
                    )
                    continue
                yield EpisodeRecord(
                    episode_id=f"{spec.files.name}/episode_{ep_idx:06d}",
                    episode_index=ep_idx,
                    instruction=instruction,
                    fps=spec.fps,
                    length=length,
                    video_paths=video_paths,
                    from_timestamp=None,
                    to_timestamp=None,
                )


def _normalize_tasks_field(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``tasks`` when stored as a JSON-encoded string (schaeffler_sim_avc1)."""
    tasks_field = payload.get("tasks")
    if isinstance(tasks_field, str) and tasks_field.startswith("["):
        try:
            return {**payload, "tasks": json.loads(tasks_field)}
        except json.JSONDecodeError:
            pass
    return payload


def _resolve_instruction_v3(
    row: dict[str, Any],
    tasks_by_index: dict[int, str],
) -> str:
    # Newer v3 metadata embeds the instruction list per episode under "tasks";
    # older variants reference the global tasks.parquet via "task_index".
    tasks_field = row.get("tasks")
    if tasks_field is not None:
        if hasattr(tasks_field, "tolist"):
            tasks_field = tasks_field.tolist()
        if isinstance(tasks_field, (list, tuple)) and len(tasks_field) > 0:
            return " | ".join(str(t) for t in tasks_field)
        if isinstance(tasks_field, str) and tasks_field:
            return tasks_field
    if "task_index" in row:
        return tasks_by_index.get(int(row["task_index"]), "")
    return ""


def _load_tasks_parquet(files: LeRobotFiles, relative: str) -> dict[int, str]:
    if not files.exists(relative):
        return {}
    import pyarrow.parquet as pq

    df = pq.read_table(io.BytesIO(files.read_bytes(relative))).to_pandas()
    # LeRobot v3 stores the task text as the (named) DataFrame index alongside a
    # "task_index" column, so ``to_dict(records)`` drops the text and a direct
    # ``row["task"]`` lookup raises KeyError. Restore index columns first.
    if "task" not in df.columns or "task_index" not in df.columns:
        df = df.reset_index(drop=df.index.name is None and "task_index" in df.columns)
    text_col = "task" if "task" in df.columns else None
    if text_col is None:
        # Fall back to the first non-index text column (e.g. "task_description").
        candidates = [c for c in df.columns if c != "task_index"]
        text_col = candidates[0] if candidates else None
    tasks: dict[int, str] = {}
    for row in df.to_dict(orient="records"):
        if "task_index" not in row or text_col is None or text_col not in row:
            continue
        tasks[int(row["task_index"])] = str(row[text_col])
    return tasks
