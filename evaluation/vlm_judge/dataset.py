"""LeRobot dataset adapter for VLM-as-judge evaluation.

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

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    @property
    def duration_s(self) -> float:
        if self.from_timestamp is not None and self.to_timestamp is not None:
            return float(self.to_timestamp - self.from_timestamp)
        return float(self.length) / float(self.fps)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Top-level dataset metadata loaded from ``meta/info.json``."""

    root: Path
    codebase_version: str
    fps: float
    total_episodes: int
    chunks_size: int
    video_keys: tuple[str, ...]
    data_path: str
    video_path: str | None
    per_view_video_paths: dict[str, str]


def load_dataset_spec(root: Path) -> DatasetSpec:
    info_path = root / "meta" / "info.json"
    info: dict[str, Any] = json.loads(info_path.read_text())
    features: dict[str, Any] = info.get("features", {})
    video_keys = tuple(k for k, v in features.items() if v.get("dtype") == "video")
    if not video_keys:
        raise ValueError(f"No video features found in {info_path}")
    per_view_paths = {
        k: str(features[k]["videos_path"]) for k in video_keys if isinstance(features[k].get("videos_path"), str)
    }
    return DatasetSpec(
        root=root,
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
    root: Path,
    *,
    views: tuple[str, ...] | None = None,
    limit: int | None = None,
    indices: list[int] | None = None,
) -> Iterator[EpisodeRecord]:
    """Yield ``EpisodeRecord`` entries for every episode in the dataset.

    ``views`` filters which video keys (e.g. ``observation.images.front``) are
    returned; ``None`` keeps every video stream. ``indices`` restricts the
    iteration to a specific list of episode indices. ``limit`` caps the count.
    """
    spec = load_dataset_spec(root)
    selected_views = _resolve_views(spec, views)

    if spec.codebase_version.startswith("v3"):
        # Some v3 datasets store metadata as parquet (chunk-packed videos), while
        # others use jsonl with one-mp4-per-episode layout. Detect by metadata
        # file presence.
        parquet_meta = sorted((spec.root / "meta" / "episodes").rglob("file-*.parquet"))
        records = _iter_v3(spec, selected_views) if parquet_meta else _iter_v3_jsonl(spec, selected_views)
    else:
        records = _iter_v21(spec, selected_views)

    yielded = 0
    for record in records:
        if indices is not None and record.episode_index not in indices:
            continue
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
    episodes_path = spec.root / "meta" / "episodes.jsonl"
    tasks_path = spec.root / "meta" / "tasks.jsonl"
    tasks_by_index = _load_tasks_jsonl(tasks_path)

    with episodes_path.open() as fh:
        for line in fh:
            payload = json.loads(line)
            ep_idx = int(payload["episode_index"])
            instruction = _resolve_instruction_v21(payload, tasks_by_index)
            length = int(payload.get("length", 0))
            chunk = ep_idx // spec.chunks_size
            video_paths = {
                v: spec.root
                / spec.video_path.format(
                    episode_chunk=chunk,
                    video_key=v,
                    episode_index=ep_idx,
                )
                for v in views
            }
            yield EpisodeRecord(
                episode_id=f"{spec.root.name}/episode_{ep_idx:06d}",
                episode_index=ep_idx,
                instruction=instruction,
                fps=spec.fps,
                length=length,
                video_paths=video_paths,
                from_timestamp=None,
                to_timestamp=None,
            )


def _load_tasks_jsonl(path: Path) -> dict[int, str]:
    tasks: dict[int, str] = {}
    if not path.exists():
        return tasks
    with path.open() as fh:
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

    tasks_by_index = _load_tasks_parquet(spec.root / "meta" / "tasks.parquet")
    episode_files = sorted((spec.root / "meta" / "episodes").rglob("file-*.parquet"))
    if not episode_files:
        raise FileNotFoundError(
            f"No v3 episode metadata found under {spec.root / 'meta' / 'episodes'}",
        )

    for ep_file in episode_files:
        df = pq.read_table(ep_file).to_pandas()
        for row in df.to_dict(orient="records"):
            ep_idx = int(row["episode_index"])
            length = int(row["length"])
            instruction = _resolve_instruction_v3(row, tasks_by_index)
            video_paths: dict[str, Path] = {}
            from_ts: float | None = None
            to_ts: float | None = None
            for view in views:
                v_chunk = int(row[f"videos/{view}/chunk_index"])
                v_file = int(row[f"videos/{view}/file_index"])
                from_ts = float(row[f"videos/{view}/from_timestamp"])
                to_ts = float(row[f"videos/{view}/to_timestamp"])
                video_paths[view] = spec.root / spec.video_path.format(
                    chunk_index=v_chunk,
                    file_index=v_file,
                    video_key=view,
                )
            yield EpisodeRecord(
                episode_id=f"{spec.root.name}/episode_{ep_idx:06d}",
                episode_index=ep_idx,
                instruction=instruction,
                fps=spec.fps,
                length=length,
                video_paths=video_paths,
                from_timestamp=from_ts,
                to_timestamp=to_ts,
            )


# -------------------------------------------------------------------------
# LeRobot v3.0 — jsonl metadata + one MP4 per episode (e.g. schaeffler_sim_avc1)
# -------------------------------------------------------------------------


def _iter_v3_jsonl(spec: DatasetSpec, views: tuple[str, ...]) -> Iterator[EpisodeRecord]:
    ep_meta_dir = spec.root / "meta" / "episodes"
    ep_files = sorted(ep_meta_dir.rglob("episode_*.jsonl"))
    if not ep_files:
        flat = spec.root / "meta" / "episodes.jsonl"
        ep_files = [flat] if flat.exists() else []
    if not ep_files:
        raise FileNotFoundError(
            f"No jsonl v3 episode metadata found under {ep_meta_dir}",
        )

    tasks_by_index = _load_tasks_jsonl(spec.root / "meta" / "tasks.jsonl")

    for ep_file in ep_files:
        with ep_file.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = _normalize_tasks_field(json.loads(line))
                ep_idx = int(payload["episode_index"])
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
                    candidate = spec.root / template.format(episode_index=ep_idx)
                    if not candidate.exists():
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
                    episode_id=f"{spec.root.name}/episode_{ep_idx:06d}",
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


def _load_tasks_parquet(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    import pyarrow.parquet as pq

    df = pq.read_table(path).to_pandas()
    # LeRobot v3 stores the task text as the (named) DataFrame index alongside a
    # "task_index" column, so ``to_dict(records)`` drops the text and a direct
    # ``row["task"]`` lookup raises KeyError. Restore index columns first.
    if "task" not in df.columns or "task_index" not in df.columns:
        df = df.reset_index()
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
