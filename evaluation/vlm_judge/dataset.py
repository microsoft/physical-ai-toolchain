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
    video_path: str


def load_dataset_spec(root: Path) -> DatasetSpec:
    info_path = root / "meta" / "info.json"
    info: dict[str, Any] = json.loads(info_path.read_text())
    features: dict[str, Any] = info.get("features", {})
    video_keys = tuple(k for k, v in features.items() if v.get("dtype") == "video")
    if not video_keys:
        raise ValueError(f"No video features found in {info_path}")
    return DatasetSpec(
        root=root,
        codebase_version=str(info.get("codebase_version", "v2.1")),
        fps=float(info.get("fps", 30.0)),
        total_episodes=int(info["total_episodes"]),
        chunks_size=int(info.get("chunks_size", 1000)),
        video_keys=video_keys,
        data_path=str(info["data_path"]),
        video_path=str(info["video_path"]),
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
        records = _iter_v3(spec, selected_views)
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
            task_idx = int(row["task_index"])
            length = int(row["length"])
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
                instruction=tasks_by_index.get(task_idx, ""),
                fps=spec.fps,
                length=length,
                video_paths=video_paths,
                from_timestamp=from_ts,
                to_timestamp=to_ts,
            )


def _load_tasks_parquet(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    import pyarrow.parquet as pq

    df = pq.read_table(path).to_pandas()
    return {int(r["task_index"]): str(r["task"]) for r in df.to_dict(orient="records")}
