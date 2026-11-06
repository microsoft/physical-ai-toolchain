"""Resolve LeRobot source layouts for bounded release planning.

LeRobot v2.1 stores one parquet file and one MP4 per episode, described by info.json
templates and JSONL metadata. LeRobot v3.0 stores shared chunked files described by
parquet metadata. Both normalize to the same planning rows; output is always v3.0.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from io import BytesIO
from string import Formatter

import pyarrow as pa

from physical_ai_curation.release.canonical import normalize_release_path, parse_strict_json
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.inputs import ReleaseInputError, SourceMetadataError

INFO_PATH = "meta/info.json"
V3_TASKS_PATH = "meta/tasks.parquet"
V3_EPISODES_PREFIX = "meta/episodes/"
V21_TASKS_PATH = "meta/tasks.jsonl"
V21_EPISODES_PATH = "meta/episodes.jsonl"
V21_EPISODE_STATS_PATH = "meta/episodes_stats.jsonl"
SUPPORTED_SOURCE_VERSIONS = ("v2.1", "v3.0")
_CAMERA_STATISTICS = ("min", "max", "mean", "std", "count")


def source_version(info: Mapping[str, object]) -> str:
    version = info.get("codebase_version")
    if version not in SUPPORTED_SOURCE_VERSIONS:
        raise ReleaseInputError("only native LeRobot v2.1 and v3.0 sources are supported")
    return str(version)


def control_paths(info: Mapping[str, object], inventory_paths: tuple[str, ...]) -> set[str]:
    """Return the metadata objects required to plan a source of this layout."""
    if source_version(info) == "v2.1":
        paths = {V21_TASKS_PATH, V21_EPISODES_PATH}
        if V21_EPISODE_STATS_PATH in inventory_paths:
            paths.add(V21_EPISODE_STATS_PATH)
        return paths
    return {V3_TASKS_PATH, *(path for path in inventory_paths if _is_v3_episode_metadata(path))}


def _is_v3_episode_metadata(path: str) -> bool:
    return path.startswith(V3_EPISODES_PREFIX) and path.endswith(".parquet")


def object_path(info: Mapping[str, object], row: Mapping[str, object], prefix: str) -> str:
    """Return the source object for ``data`` or ``videos/<camera>`` for one episode row."""
    if source_version(info) == "v2.1":
        return _v21_object_path(info, row, prefix)
    chunk, file = row.get(f"{prefix}/chunk_index"), row.get(f"{prefix}/file_index")
    if type(chunk) is not int or chunk < 0 or type(file) is not int or file < 0:
        raise SourceMetadataError("source_object_reference_invalid", "native object index is invalid")
    extension = "parquet" if prefix == "data" else "mp4"
    return f"{prefix}/chunk-{chunk:03d}/file-{file:03d}.{extension}"


def _v21_object_path(info: Mapping[str, object], row: Mapping[str, object], prefix: str) -> str:
    episode, chunks_size = row.get("episode_index"), info.get("chunks_size")
    if type(episode) is not int or episode < 0 or type(chunks_size) is not int or chunks_size < 1:
        raise SourceMetadataError("source_object_reference_invalid", "v2.1 episode index is invalid")
    chunk = episode // chunks_size
    if prefix == "data":
        template, values = info.get("data_path"), {"episode_chunk": chunk, "episode_index": episode}
    else:
        template = info.get("video_path")
        values = {"episode_chunk": chunk, "episode_index": episode, "video_key": prefix.removeprefix("videos/")}
    if not isinstance(template, str):
        raise SourceMetadataError("source_metadata_invalid", "v2.1 path template is missing")
    if len(template) > 1024:
        raise SourceMetadataError("source_metadata_invalid", "v2.1 path template exceeds its bound")
    try:
        for _literal, field, spec, conversion in Formatter().parse(template):
            if field is None:
                continue
            if field not in values or conversion is not None:
                raise ValueError("unsupported template field")
            if (field == "video_key" and spec) or (field != "video_key" and not re.fullmatch(r"(?:0?[1-9])?d?", spec)):
                raise ValueError("unsupported template format")
        path = template.format(**values)
    except (KeyError, IndexError, ValueError) as error:
        raise SourceMetadataError("source_metadata_invalid", "v2.1 path template is invalid") from error
    return normalize_release_path(path)


def v21_tasks_table(read: Callable[[str], bytes], *, max_rows: int = 10000) -> pa.Table:
    content = read(V21_TASKS_PATH)
    try:
        rows = _jsonl(content, max_rows=max_rows)
    except ReleaseVerificationError as error:
        raise SourceMetadataError("source_task_metadata_invalid", "v2.1 task metadata is invalid") from error
    indices = [row.get("task_index") for row in rows]
    names = [row.get("task") for row in rows]
    if not rows or any(type(index) is not int for index in indices) or any(not isinstance(n, str) for n in names):
        raise SourceMetadataError("source_task_metadata_invalid", "v2.1 task metadata is invalid")
    return pa.table({"task_index": pa.array(indices, pa.int64()), "task": pa.array(names, pa.string())})


def v21_episode_rows(
    info: Mapping[str, object],
    read: Callable[[str], bytes],
    *,
    max_rows: int = 10000,
    has_statistics: bool = True,
) -> list[dict[str, object]]:
    """Normalize v2.1 metadata without inventing statistics; absent summaries stay absent."""
    fps = info.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise SourceMetadataError("source_metadata_invalid", "v2.1 frame rate is invalid")
    stats_by_episode = (
        _stats_by_episode(_jsonl(read(V21_EPISODE_STATS_PATH), max_rows=max_rows)) if has_statistics else {}
    )
    cameras = sorted(name for name, feature in info["features"].items() if feature.get("dtype") == "video")
    rows = []
    seen = set()
    for item in _jsonl(read(V21_EPISODES_PATH), max_rows=max_rows):
        index, length = item.get("episode_index"), item.get("length")
        if type(index) is not int or index < 0 or type(length) is not int or length < 1:
            raise SourceMetadataError("source_metadata_invalid", "v2.1 episode metadata is invalid")
        if index in seen:
            raise SourceMetadataError("source_metadata_invalid", "v2.1 episode metadata is ambiguous")
        seen.add(index)
        stats = stats_by_episode.get(index, {})
        row = {"episode_index": index, "length": length}
        for camera in cameras:
            row[f"videos/{camera}/from_timestamp"] = 0.0
            row[f"videos/{camera}/to_timestamp"] = length / float(fps)
            summary = stats.get(camera)
            if summary is None:
                continue
            if not isinstance(summary, Mapping):
                raise SourceMetadataError("source_metadata_invalid", f"v2.1 video statistics are invalid: {camera}")
            for name in _CAMERA_STATISTICS:
                if name in summary:
                    row[f"stats/{camera}/{name}"] = summary[name]
        rows.append(row)
    if not rows:
        raise SourceMetadataError("source_metadata_invalid", "v2.1 episode metadata is empty")
    return rows


def _stats_by_episode(rows: list[dict[str, object]]) -> dict[int, Mapping[str, object]]:
    by_episode = {}
    for row in rows:
        index, stats = row.get("episode_index"), row.get("stats")
        if type(index) is not int or not isinstance(stats, Mapping) or index in by_episode:
            raise ReleaseInputError("v2.1 episode statistics are ambiguous")
        by_episode[index] = stats
    return by_episode


def _jsonl(content: bytes, *, max_rows: int) -> list[dict[str, object]]:
    rows = []
    for line in BytesIO(content):
        if not line.strip():
            continue
        if len(rows) >= max_rows:
            raise ReleaseInputError("v2.1 metadata exceeds its row budget")
        value = parse_strict_json(line)
        if not isinstance(value, dict):
            raise ReleaseInputError("v2.1 metadata lines must be objects")
        rows.append(value)
    return rows
