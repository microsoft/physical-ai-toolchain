"""Generate native LeRobot tables from explicitly selected, already admitted inputs.

Optional Arrow, NumPy and Pandas imports stay inside operations. These helpers
produce data, not a publication decision or a storage snapshot.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from physical_ai_dataset_io.lerobot import remap_persistent_language_rows

from physical_ai_curation.release.canonical import normalize_release_path, parse_strict_json
from physical_ai_curation.release.media import (
    ReleaseMediaLimits,
    ReleasePlanError,
    SubsetEpisodeInput,
    build_subset_timeline,
    validate_processing_confirmation,
)
from physical_ai_curation.release.statistics import (
    AGGREGATION_ALGORITHM,
    aggregate_feature_statistics,
    numeric_feature_statistics,
    validate_feature_statistics,
)

if TYPE_CHECKING:
    import pyarrow as pa

    from physical_ai_curation.release.composition import NativeGenerationPlan
    from physical_ai_curation.release.encoding import VideoOutput

_NUMERIC_DTYPES = frozenset(
    {f"{kind}{bits}" for kind in ("int", "uint") for bits in (8, 16, 32, 64)}
    | {"float16", "float32", "float64", "bool"}
)


def generate_episode_table(
    source: pa.Table,
    episode: SubsetEpisodeInput,
    *,
    output_episode_index: int,
    output_from_index: int,
    limits: ReleaseMediaLimits | None = None,
) -> pa.Table:
    """Retain selected rows and recorded columns with contiguous output coordinates.

    The input is exactly one source episode, not its shared shard. Saved operations
    beyond frame removal must be applied explicitly by the composing producer.
    """
    import numpy as np
    import pyarrow as pa

    for value in (output_episode_index, output_from_index):
        if type(value) is not int or value < 0:
            raise ReleasePlanError("output coordinates must be nonnegative integers")
    budget = (limits or ReleaseMediaLimits()).processing_memory_bytes
    if source.nbytes * 3 + episode.length * 512 > budget:
        raise ReleasePlanError("episode table exceeds its estimated processing memory budget")
    timeline = build_subset_timeline((episode,), limits=limits)
    if source.num_rows != episode.length:
        raise ReleasePlanError("source table length differs from the selected episode")
    required = {"episode_index", "frame_index", "index", "timestamp", "task_index"}
    if not required <= set(source.column_names) or len(set(source.column_names)) != source.num_columns:
        raise ReleasePlanError("source table requires unique native coordinate columns")
    for name in required - {"timestamp"}:
        if not pa.types.is_integer(source.schema.field(name).type):
            raise ReleasePlanError("native index columns must contain integers")
    if not pa.types.is_floating(source.schema.field("timestamp").type):
        raise ReleasePlanError("native timestamps must contain floating point seconds")
    if any(source.column(name).null_count for name in required):
        raise ReleasePlanError("native coordinates must not contain null values")
    if source.column("episode_index").to_pylist() != [episode.original_episode_index] * episode.length:
        raise ReleasePlanError("source episode identity differs from the selected episode")
    if source.column("frame_index").to_pylist() != list(range(episode.length)):
        raise ReleasePlanError("source frame indices must be contiguous from zero")
    timestamps = np.asarray(source.column("timestamp").to_pylist(), dtype=np.float64)
    if not np.allclose(timestamps, np.arange(episode.length) / episode.fps, rtol=0, atol=1e-4):
        raise ReleasePlanError("source timestamps do not match the episode frame rate")
    positions = [frame.original_frame_index for frame in timeline.frames]
    selected = source.take(pa.array(positions, type=pa.int64()))
    length = selected.num_rows
    replacement = {
        "episode_index": [output_episode_index] * length,
        "frame_index": list(range(length)),
        "index": list(range(output_from_index, output_from_index + length)),
        "timestamp": [frame.timestamp_s for frame in timeline.frames],
    }
    try:
        for name, values in replacement.items():
            position = selected.schema.get_field_index(name)
            selected = selected.set_column(
                position, selected.schema.field(name), pa.array(values, type=source[name].type)
            )
    except (pa.ArrowException, OverflowError) as error:
        raise ReleasePlanError("output coordinates do not fit the source feature types") from error
    if episode.removed_frames and "language_persistent" in source.column_names:
        recorded = source["language_persistent"].to_pylist()
        if any(rows != recorded[0] for rows in recorded):
            raise ReleasePlanError("persistent language rows must be shared across the source episode")
        try:
            rows = remap_persistent_language_rows(
                recorded[0] or [],
                source_to_output={frame.original_frame_index: frame.output_frame_index for frame in timeline.frames},
                source_timestamps=timestamps.tolist(),
                output_timestamps=replacement["timestamp"],
            )
            field = source.schema.field("language_persistent")
            selected = selected.set_column(
                selected.schema.get_field_index("language_persistent"),
                field,
                pa.array([rows] * length, type=field.type),
            )
        except (KeyError, TypeError, ValueError, pa.ArrowException) as error:
            raise ReleasePlanError("persistent language rows cannot be retimed") from error
    return selected


def generate_task_table(source: pa.Table) -> pa.Table:
    """Preserve task vocabulary in native index order with a named task index.

    The native reader selects task text using ``tasks.iloc[task_index].name``.
    A plain task column without Pandas index metadata is not equivalent.
    """
    import pyarrow as pa

    tasks = source.to_pandas()
    if tasks.index.name == "task":
        tasks = tasks.reset_index()
    if not {"task", "task_index"} <= set(tasks.columns) or tasks.empty:
        raise ReleasePlanError("task table requires names and indices")
    tasks = tasks.sort_values("task_index")
    indices = tasks["task_index"].tolist()
    names = tasks["task"].tolist()
    if any(type(index) is not int for index in indices) or indices != list(range(len(indices))):
        raise ReleasePlanError("task indices must be unique and contiguous from zero")
    if any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != len(names):
        raise ReleasePlanError("task names must be nonempty and unique")
    return pa.Table.from_pandas(tasks[["task", "task_index"]].set_index("task"), preserve_index=True)


@dataclass(frozen=True, slots=True)
class NativeVideoReference:
    path: str
    from_timestamp: float
    to_timestamp: float


@dataclass(frozen=True, slots=True)
class NativeMetadata:
    info: dict[str, object]
    episodes: pa.Table
    statistics: dict[str, object]
    statistics_algorithm: str
    statistics_approximation: str | None


@dataclass(frozen=True, slots=True)
class NativeSelection:
    """Generated in-memory payloads; the caller owns private writes and later publication."""

    data_tables: tuple[pa.Table, ...]
    tasks: pa.Table
    metadata: NativeMetadata


def generate_native_selection(
    prepared: NativeGenerationPlan,
    source_info: Mapping[str, object],
    source_tables: Mapping[str, pa.Table],
    source_tasks: pa.Table,
    encoded_media: Mapping[str, VideoOutput],
    *,
    confirmed_digest: str | None = None,
    preserved_dataset_statistics: Mapping[str, Mapping[str, object]] | None = None,
    limits: ReleaseMediaLimits | None = None,
) -> NativeSelection:
    """Assemble a confirmed saved selection without opening storage or unchanged media.

    The caller supplies condition-bound tables and actual results of the plan's binary
    encoders. Copied camera summaries were admitted and bound during preview. A storage
    coordinator revalidates frozen inputs before dispatch and owns all publication effects.
    """
    validate_processing_confirmation(prepared.plan, confirmed_digest=confirmed_digest)
    required = {obj.output_path for obj in prepared.plan.objects if obj.action == "transcode"}
    if set(encoded_media) != required:
        raise ReleasePlanError("encoded media results do not match planned transcode objects")
    info = copy.deepcopy(dict(source_info))
    features = info.get("features")
    if (
        not isinstance(features, dict)
        or not features
        or any(not isinstance(value, dict) for value in features.values())
    ):
        raise ReleasePlanError("native selection requires source feature descriptors")
    if any(episode.fps != info.get("fps") for episode in prepared.episodes):
        raise ReleasePlanError("selected episodes must match the native dataset frame rate")
    if any(episode.episode_id not in source_tables for episode in prepared.episodes):
        raise ReleasePlanError("selected episode has no condition-bound source table")
    budget = (limits or ReleaseMediaLimits()).processing_memory_bytes
    estimated = sum(
        source_tables[episode.episode_id].nbytes * 3 + episode.length * 512 for episode in prepared.episodes
    )
    if estimated > budget:
        raise ReleasePlanError("selected tables exceed the estimated processing memory budget")
    task_table = generate_task_table(source_tasks)
    task_names = task_table.to_pandas().index.tolist()
    preserved = parse_strict_json(prepared.camera_statistics_json.encode("utf-8"))
    observed = {}
    objects = {obj.source_path: obj for obj in prepared.plan.objects if obj.source_path is not None}
    for path in sorted(required):
        obj = next(obj for obj in prepared.plan.objects if obj.output_path == path)
        result = encoded_media[path]
        expected = prepared.encoding_windows(obj)
        if tuple(window.episode_id for window in result.windows) != tuple(window.episode_id for window in expected):
            raise ReleasePlanError("encoded media windows do not match frozen output order")
        offset = 0
        for actual, window in zip(result.windows, expected, strict=True):
            episode = next(episode for episode in prepared.episodes if episode.episode_id == window.episode_id)
            length = len(window.kept_frames)
            if (
                result.fps != episode.fps
                or actual.output_from_frame != offset
                or actual.output_to_frame != offset + length
                or not math.isclose(actual.output_from_s, offset / episode.fps, abs_tol=1e-6)
                or not math.isclose(actual.output_to_s, (offset + length) / episode.fps, abs_tol=1e-6)
            ):
                raise ReleasePlanError("encoded media coordinates do not match generated frames")
            offset += length
            observed[path, window.episode_id] = actual
        for camera in obj.affected_cameras:
            feature = features.get(camera, {})
            if feature.get("dtype") != "video" or feature.get("shape") != [result.height, result.width, 3]:
                raise ReleasePlanError("encoded geometry does not match the native video feature")
            descriptor = dict(feature.get("info") or {})
            descriptor.update(
                {
                    "video.codec": "h264" if result.codec == "libx264" else result.codec,
                    "video.pix_fmt": result.pix_fmt,
                    "video.fps": result.fps,
                    "video.width": result.width,
                    "video.height": result.height,
                    "video.channels": 3,
                    "has_audio": False,
                }
            )
            descriptor.pop("video.crf", None)
            descriptor.pop("video.g", None)
            feature["info"] = descriptor
    rows = []
    tables = []
    output_offset = 0
    cameras = {name for name, feature in features.items() if feature.get("dtype") == "video"}
    indexed_windows = {(item.window.episode_id, item.camera): item for item in prepared.windows}
    for output_episode_index, episode in enumerate(prepared.episodes):
        table = generate_episode_table(
            source_tables[episode.episode_id],
            episode,
            output_episode_index=output_episode_index,
            output_from_index=output_offset,
            limits=limits,
        )
        references = {}
        summaries = {}
        for camera in sorted(cameras):
            selected = indexed_windows.get((episode.episode_id, camera))
            if selected is None:
                raise ReleasePlanError("native video feature has no planned camera window")
            obj = objects[selected.source_path]
            if obj.action == "copy":
                start = selected.window.from_timestamp
                end = start + table.num_rows / episode.fps
                summary = preserved.get(episode.episode_id, {}).get(camera)
                original = source_info["features"][camera].get("info") or {}
                declared = features[camera].get("info") or {}
                if any(original.get(key) != declared.get(key) for key in ("video.codec", "video.pix_fmt")):
                    raise ReleasePlanError("mixed copied and encoded camera codecs must share one native descriptor")
            else:
                result = observed[obj.output_path, episode.episode_id]
                start, end, summary = result.output_from_s, result.output_to_s, result.statistics
            if summary is None:
                raise ReleasePlanError(f"statistics required: {camera}")
            references[camera] = NativeVideoReference(obj.output_path, start, end)
            summaries[camera] = summary
        rows.append(
            generate_episode_metadata(
                table,
                features,
                output_episode_index=output_episode_index,
                output_from_index=output_offset,
                fps=episode.fps,
                task_names=task_names,
                video_references=references,
                preserved_statistics=summaries,
            )
        )
        tables.append(table)
        output_offset += table.num_rows
    metadata = assemble_native_metadata(
        info,
        rows,
        task_count=len(task_names),
        preserved_dataset_statistics=preserved_dataset_statistics,
    )
    return NativeSelection(data_tables=tuple(tables), tasks=task_table, metadata=metadata)


def generate_episode_metadata(
    table: pa.Table,
    features: Mapping[str, Mapping[str, object]],
    *,
    output_episode_index: int,
    output_from_index: int,
    fps: float,
    task_names: Sequence[str],
    video_references: Mapping[str, NativeVideoReference],
    preserved_statistics: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Describe one generated episode using observed rows and admitted summaries.

    Preserved summaries must describe this exact unchanged feature selection.
    Video counts are sample counts, not necessarily episode frame counts.
    Missing camera summaries are unsupported here, never a request to decode.
    """
    if not table.num_rows or not math.isfinite(fps) or fps <= 0:
        raise ReleasePlanError("episode metadata requires frames and a positive finite frame rate")
    if table["episode_index"].to_pylist() != [output_episode_index] * table.num_rows:
        raise ReleasePlanError("output episode metadata does not match the table")
    if table["index"].to_pylist() != list(range(output_from_index, output_from_index + table.num_rows)):
        raise ReleasePlanError("output dataset offsets do not match the table")
    tasks = table["task_index"].to_pylist()
    if any(type(index) is not int or not 0 <= index < len(task_names) for index in tasks):
        raise ReleasePlanError("episode references an unknown task index")
    cameras = {name for name, feature in features.items() if feature.get("dtype") == "video"}
    if set(video_references) != cameras:
        raise ReleasePlanError("every video feature requires exactly one output reference")
    row = {
        "episode_index": output_episode_index,
        "tasks": list(dict.fromkeys(task_names[index] for index in tasks)),
        "length": table.num_rows,
        "data/chunk_index": output_episode_index // 1000,
        "data/file_index": output_episode_index % 1000,
        "dataset_from_index": output_from_index,
        "dataset_to_index": output_from_index + table.num_rows,
        "meta/episodes/chunk_index": 0,
        "meta/episodes/file_index": 0,
    }
    for camera in sorted(cameras):
        reference = video_references[camera]
        match = re.fullmatch(
            rf"videos/{re.escape(camera)}/chunk-(\d+)/file-(\d+)\.mp4", normalize_release_path(reference.path)
        )
        if match is None:
            raise ReleasePlanError("video reference must use the native v3 path layout")
        start, end = reference.from_timestamp, reference.to_timestamp
        if (
            not all(math.isfinite(value) for value in (start, end))
            or start < 0
            or not math.isclose(end - start, table.num_rows / fps, rel_tol=0, abs_tol=1e-6)
        ):
            raise ReleasePlanError("video window length does not match the generated episode")
        row.update(
            {
                f"videos/{camera}/chunk_index": int(match[1]),
                f"videos/{camera}/file_index": int(match[2]),
                f"videos/{camera}/from_timestamp": start,
                f"videos/{camera}/to_timestamp": end,
            }
        )
    for name, feature in features.items():
        dtype = feature.get("dtype")
        if name in preserved_statistics:
            summary = copy.deepcopy(dict(preserved_statistics[name]))
            validate_feature_statistics(summary, expected_count=None if name in cameras else table.num_rows)
        elif dtype in {"video", "image"}:
            raise ReleasePlanError(f"statistics required: {name}")
        elif dtype in _NUMERIC_DTYPES:
            if name not in table.column_names:
                raise ReleasePlanError("declared numeric feature is missing from the generated table")
            values = table[name].to_pylist()
            if dtype == "bool":
                values = [int(value) for value in values]
            summary = numeric_feature_statistics(values)
        else:
            continue
        for statistic, value in summary.items():
            row[f"stats/{name}/{statistic}"] = value
    return row


def assemble_native_metadata(
    source_info: Mapping[str, object],
    episode_rows: Sequence[Mapping[str, object]],
    *,
    task_count: int,
    preserved_dataset_statistics: Mapping[str, Mapping[str, object]] | None = None,
) -> NativeMetadata:
    """Build native v3 control tables and statistics without opening media.

    Episode rows must be in output order. The returned algorithm and approximation
    travel separately so the composing producer can bind them into provenance.
    """
    import pyarrow as pa

    if not episode_rows or type(task_count) is not int or task_count < 1:
        raise ReleasePlanError("native metadata requires episodes and a positive task count")
    if source_info.get("codebase_version") != "v3.0":
        raise ReleasePlanError("native metadata generation currently requires v3.0 input")
    features = source_info.get("features")
    if not isinstance(features, dict) or not features:
        raise ReleasePlanError("native metadata requires feature descriptors")
    if not all(isinstance(feature, Mapping) for feature in features.values()):
        raise ReleasePlanError("native features require descriptor mappings")
    required_statistics = {
        name for name, feature in features.items() if feature.get("dtype") in _NUMERIC_DTYPES | {"video", "image"}
    }
    offset = 0
    summaries = {}
    for index, row in enumerate(episode_rows):
        length = row.get("length")
        if (
            type(length) is not int
            or length < 1
            or row.get("episode_index") != index
            or row.get("dataset_from_index") != offset
            or row.get("dataset_to_index") != offset + length
        ):
            raise ReleasePlanError("episode metadata must use contiguous output coordinates")
        offset += length
        episode_summaries = {}
        for key, value in row.items():
            if key.startswith("stats/"):
                feature, statistic = key[6:].rsplit("/", 1)
                if feature not in features:
                    raise ReleasePlanError("episode statistics reference an undeclared feature")
                episode_summaries.setdefault(feature, {})[statistic] = value
        if not required_statistics <= set(episode_summaries):
            raise ReleasePlanError("statistics required for every declared numeric and media feature")
        if summaries and set(episode_summaries) != set(summaries):
            raise ReleasePlanError("episodes must share one statistic feature set")
        for feature, summary in episode_summaries.items():
            media = features[feature].get("dtype") in {"video", "image"}
            validate_feature_statistics(summary, expected_count=None if media else length)
            summaries.setdefault(feature, []).append(summary)
    if not summaries:
        raise ReleasePlanError("native metadata requires feature statistics")
    statistics = {}
    approximations = set()
    for feature, values in summaries.items():
        result = aggregate_feature_statistics(values, expected_counts=[value["count"][0] for value in values])
        statistics[feature] = result.values
        if result.approximation:
            approximations.add(result.approximation)
    algorithm = AGGREGATION_ALGORITHM
    if preserved_dataset_statistics is not None:
        if source_info.get("total_frames") != offset or source_info.get("total_episodes") != len(episode_rows):
            raise ReleasePlanError("source-wide statistics require the complete unchanged selection")
        statistics = _preserve_dataset_statistics(preserved_dataset_statistics, statistics)
        algorithm = "validated-full-source-statistics-v1"
        approximations.clear()
    info = copy.deepcopy(dict(source_info))
    for stale in ("total_chunks", "total_videos", "total_size", "download_size"):
        info.pop(stale, None)
    info.update(
        total_episodes=len(episode_rows),
        total_frames=offset,
        total_tasks=task_count,
        chunks_size=1000,
        splits={"train": f"0:{len(episode_rows)}"},
        data_path="data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    )
    return NativeMetadata(
        info=info,
        episodes=pa.Table.from_pylist([dict(row) for row in episode_rows]),
        statistics=statistics,
        statistics_algorithm=algorithm,
        statistics_approximation="; ".join(sorted(approximations)) or None,
    )


def _preserve_dataset_statistics(
    source: Mapping[str, Mapping[str, object]], generated: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    import numpy as np

    if set(source) != set(generated):
        raise ReleasePlanError("source-wide statistics must cover the generated feature set")
    for feature, values in source.items():
        expected = generated[feature]
        validate_feature_statistics(values, expected_count=int(expected["count"][0]))
        for statistic in ("min", "max", "mean", "std"):
            original = np.asarray(values[statistic], dtype=np.float64)
            observed = np.asarray(expected[statistic], dtype=np.float64)
            if original.shape != observed.shape or not np.allclose(original, observed, rtol=1e-5, atol=1e-8):
                raise ReleasePlanError("source-wide statistics differ from generated output moments")
    return copy.deepcopy({name: dict(values) for name, values in source.items()})
