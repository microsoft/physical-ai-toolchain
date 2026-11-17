"""Native table admission rejects inconsistent coordinates before publication."""

from __future__ import annotations

import copy
from dataclasses import replace

import pandas as pd
import pyarrow as pa
import pytest
from physical_ai_curation.release.composition import NativeGenerationPlan, prepare_saved_generation
from physical_ai_curation.release.encoding import EncodedWindow, VideoOutput
from physical_ai_curation.release.media import ReleaseMediaLimits, ReleasePlanError, SubsetEpisodeInput
from physical_ai_curation.release.statistics import numeric_feature_statistics
from physical_ai_curation.release.tables import (
    NativeVideoReference,
    assemble_native_metadata,
    generate_episode_metadata,
    generate_episode_table,
    generate_native_selection,
    generate_task_table,
)
from test_release_composition import EP1, _generation_case


@pytest.fixture
def source_table() -> pa.Table:
    return pa.table(
        {
            "episode_index": [7, 7, 7],
            "frame_index": [0, 1, 2],
            "index": [20, 21, 22],
            "timestamp": [0.0, 0.1, 0.2],
            "task_index": [0, 0, 0],
            "observation.state": [1.0, 2.0, 3.0],
        }
    )


def _episode() -> SubsetEpisodeInput:
    return SubsetEpisodeInput("source/episode_000007", 7, 3, 10.0, frozenset({1}))


@pytest.mark.parametrize(
    ("column", "values", "message"),
    [
        ("episode_index", [7, 8, 7], "episode identity"),
        ("frame_index", [0, 2, 1], "contiguous"),
        ("index", [20.0, 21.0, 22.0], "integers"),
        ("task_index", [0, None, 0], "null"),
        ("timestamp", [0, 1, 2], "floating point"),
        ("timestamp", [0.0, 0.2, 0.3], "frame rate"),
        ("timestamp", [0.0, float("nan"), 0.2], "frame rate"),
    ],
)
def test_episode_table_rejects_invalid_native_coordinates(
    source_table: pa.Table, column: str, values: list[object], message: str
) -> None:
    damaged = source_table.set_column(source_table.schema.get_field_index(column), column, pa.array(values))

    with pytest.raises(ReleasePlanError, match=message):
        generate_episode_table(damaged, _episode(), output_episode_index=0, output_from_index=0)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "length"])
def test_episode_table_rejects_ambiguous_or_incomplete_rows(source_table: pa.Table, damage: str) -> None:
    if damage == "missing":
        source_table = source_table.drop(["index"])
    elif damage == "duplicate":
        source_table = source_table.append_column("index", source_table["index"])
    else:
        source_table = source_table.slice(0, 2)

    with pytest.raises(ReleasePlanError, match=r"columns|length"):
        generate_episode_table(source_table, _episode(), output_episode_index=0, output_from_index=0)


@pytest.mark.parametrize("coordinate", [-1, True, 1.5])
@pytest.mark.parametrize("field", ["output_episode_index", "output_from_index"])
def test_episode_table_rejects_invalid_output_coordinates(
    source_table: pa.Table, field: str, coordinate: object
) -> None:
    options = {"output_episode_index": 0, "output_from_index": 0, field: coordinate}

    with pytest.raises(ReleasePlanError, match="nonnegative integers"):
        generate_episode_table(source_table, _episode(), **options)


def test_episode_table_refuses_memory_estimate_before_materializing_rows(source_table: pa.Table) -> None:
    with pytest.raises(ReleasePlanError, match="memory budget"):
        generate_episode_table(
            source_table,
            _episode(),
            output_episode_index=0,
            output_from_index=0,
            limits=replace(ReleaseMediaLimits(), processing_memory_bytes=1),
        )


def test_episode_table_refuses_output_index_overflow(source_table: pa.Table) -> None:
    narrowed = source_table.set_column(0, "episode_index", pa.array([7, 7, 7], type=pa.int8()))

    with pytest.raises(ReleasePlanError, match="do not fit"):
        generate_episode_table(narrowed, _episode(), output_episode_index=128, output_from_index=0)


def test_persistent_language_must_be_shared_across_source_rows(source_table: pa.Table) -> None:
    source_table = source_table.append_column("language_persistent", pa.array([["pick"], ["place"], ["pick"]]))

    with pytest.raises(ReleasePlanError, match="must be shared"):
        generate_episode_table(source_table, _episode(), output_episode_index=0, output_from_index=0)


def test_invalid_persistent_language_is_not_silently_discarded(source_table: pa.Table) -> None:
    source_table = source_table.append_column("language_persistent", pa.array([["invalid"]] * 3))

    with pytest.raises(ReleasePlanError, match="cannot be retimed"):
        generate_episode_table(source_table, _episode(), output_episode_index=0, output_from_index=0)


@pytest.mark.parametrize(
    ("names", "indices", "message"),
    [
        ([], [], "names and indices"),
        (["pick", "place"], [0, 0], "contiguous"),
        (["pick", "place"], [1, 2], "contiguous"),
        (["pick", "place"], [0.0, 1.0], "contiguous"),
        (["pick", "pick"], [0, 1], "nonempty and unique"),
        (["pick", " "], [0, 1], "nonempty and unique"),
        (["pick", None], [0, 1], "nonempty and unique"),
    ],
)
def test_task_table_rejects_invalid_vocabulary(names: list[object], indices: list[object], message: str) -> None:
    with pytest.raises(ReleasePlanError, match=message):
        generate_task_table(pa.table({"task": names, "task_index": indices}))


def test_task_table_preserves_unnamed_string_index_in_native_order() -> None:
    source = pa.Table.from_pandas(pd.DataFrame({"task_index": [1, 0]}, index=["place", "pick"]))

    result = generate_task_table(source).to_pandas()

    assert result.index.name == "task"
    assert result.index.tolist() == ["pick", "place"]
    assert result["task_index"].tolist() == [0, 1]


def _output_table(source_table: pa.Table) -> pa.Table:
    return generate_episode_table(source_table, _episode(), output_episode_index=0, output_from_index=0)


def _metadata(table: pa.Table, **overrides: object) -> dict[str, object]:
    options = {
        "output_episode_index": 0,
        "output_from_index": 0,
        "fps": 10.0,
        "task_names": ["pick"],
        "video_references": {},
        "preserved_statistics": {},
        **overrides,
    }
    return generate_episode_metadata(table, {"observation.state": {"dtype": "float32"}}, **options)


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")])
def test_episode_metadata_refuses_invalid_frame_rate(source_table: pa.Table, fps: float) -> None:
    with pytest.raises(ReleasePlanError, match="positive finite"):
        _metadata(_output_table(source_table), fps=fps)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("empty", "requires frames"),
        ("episode", "episode metadata"),
        ("offset", "dataset offsets"),
        ("task", "unknown task"),
        ("missing_numeric", "numeric feature is missing"),
    ],
)
def test_episode_metadata_rejects_inconsistent_native_rows(source_table: pa.Table, damage: str, message: str) -> None:
    table = _output_table(source_table)
    if damage == "empty":
        table = table.slice(0, 0)
    elif damage == "episode":
        table = table.set_column(0, "episode_index", pa.array([1, 1]))
    elif damage == "offset":
        table = table.set_column(2, "index", pa.array([10, 11]))
    elif damage == "task":
        table = table.set_column(4, "task_index", pa.array([0, 1]))
    else:
        table = table.drop(["observation.state"])

    with pytest.raises(ReleasePlanError, match=message):
        _metadata(table)


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (None, "exactly one"),
        (NativeVideoReference("other/front.mp4", 0.0, 0.2), "native v3 path"),
        (NativeVideoReference("videos/front/chunk-000/file-001.mp4", -0.1, 0.1), "window length"),
        (NativeVideoReference("videos/front/chunk-000/file-001.mp4", 0.0, 0.3), "window length"),
        (NativeVideoReference("videos/front/chunk-000/file-001.mp4", 0.0, float("inf")), "window length"),
    ],
)
def test_episode_metadata_requires_exact_camera_layout_and_duration(
    source_table: pa.Table, reference: NativeVideoReference | None, message: str
) -> None:
    with pytest.raises(ReleasePlanError, match=message):
        generate_episode_metadata(
            _output_table(source_table),
            {"front": {"dtype": "video"}},
            output_episode_index=0,
            output_from_index=0,
            fps=10.0,
            task_names=["pick"],
            video_references={} if reference is None else {"front": reference},
            preserved_statistics={},
        )


def test_episode_metadata_boolean_features_have_numeric_statistics(source_table: pa.Table) -> None:
    table = _output_table(source_table).append_column("done", pa.array([False, True]))

    row = generate_episode_metadata(
        table,
        {"done": {"dtype": "bool"}, "image": {"dtype": "image"}, "note": {"dtype": "string"}},
        output_episode_index=0,
        output_from_index=0,
        fps=10.0,
        task_names=["pick"],
        video_references={},
        preserved_statistics={},
    )

    assert row["stats/done/mean"] == [0.5]
    assert row["stats/done/count"] == [2]
    assert not any(key.startswith(("stats/image/", "stats/note/")) for key in row)


def _episode_row() -> dict[str, object]:
    summary = numeric_feature_statistics([1.0, 3.0])
    return {
        "episode_index": 0,
        "length": 2,
        "dataset_from_index": 0,
        "dataset_to_index": 2,
        **{f"stats/state/{key}": value for key, value in summary.items()},
    }


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("no_episodes", "requires episodes"),
        ("no_tasks", "positive task count"),
        ("bool_tasks", "positive task count"),
        ("version", "v2.1 or v3.0"),
        ("features", "feature descriptors"),
        ("descriptor", "descriptor mappings"),
        ("length", "contiguous"),
        ("episode_index", "contiguous"),
        ("dataset_from_index", "contiguous"),
        ("dataset_to_index", "contiguous"),
        ("foreign_statistics", "undeclared feature"),
        ("missing_statistics", "every declared numeric"),
        ("no_statistics", "requires feature statistics"),
    ],
)
def test_native_metadata_refuses_invalid_schema_and_statistics(damage: str, message: str) -> None:
    info = {"codebase_version": "v3.0", "features": {"state": {"dtype": "float32"}}}
    row = _episode_row()
    rows = [row]
    task_count = 1
    if damage == "no_episodes":
        rows = []
    elif damage == "no_tasks":
        task_count = 0
    elif damage == "bool_tasks":
        task_count = True
    elif damage == "version":
        info["codebase_version"] = "v1.0"
    elif damage == "features":
        info["features"] = {}
    elif damage == "descriptor":
        info["features"] = {"state": "float32"}
    elif damage in {"length", "episode_index", "dataset_from_index", "dataset_to_index"}:
        row[damage] = -1
    elif damage == "foreign_statistics":
        row["stats/unknown/mean"] = [0.0]
    elif damage == "missing_statistics":
        row = {key: value for key, value in row.items() if not key.startswith("stats/")}
        rows = [row]
    else:
        info["features"] = {"state": {"dtype": "string"}}
        rows = [{key: value for key, value in row.items() if not key.startswith("stats/")}]

    with pytest.raises(ReleasePlanError, match=message):
        assemble_native_metadata(info, rows, task_count=task_count)


@pytest.mark.parametrize("damage", ["incomplete_selection", "missing_feature", "shape", "moment"])
def test_source_wide_statistics_require_complete_matching_selection(damage: str) -> None:
    info = {
        "codebase_version": "v3.0",
        "features": {"state": {"dtype": "float32"}},
        "total_frames": 2,
        "total_episodes": 1,
    }
    source = copy.deepcopy(numeric_feature_statistics([1.0, 3.0]))
    preserved = {"state": source}
    if damage == "incomplete_selection":
        info["total_frames"] = 3
    elif damage == "missing_feature":
        preserved = {}
    elif damage == "shape":
        for key in source.keys() - {"count"}:
            source[key] = source[key] * 2
    else:
        source["mean"] = [2.5]

    with pytest.raises(ReleasePlanError, match=r"complete unchanged|cover the generated|moments"):
        assemble_native_metadata(info, [_episode_row()], task_count=1, preserved_dataset_statistics=preserved)


def _native_case() -> tuple[NativeGenerationPlan, dict[str, object], dict[str, pa.Table], dict[str, VideoOutput]]:
    frozen, request, sources = _generation_case()
    prepared = prepare_saved_generation(frozen, request, sources)
    info = {
        "codebase_version": "v3.0",
        "fps": 10.0,
        "features": {
            "observation.state": {"dtype": "float32"},
            "front": {"dtype": "video", "shape": [2, 4, 3]},
        },
    }
    table = pa.table(
        {
            "episode_index": [1] * 4,
            "frame_index": list(range(4)),
            "index": list(range(4)),
            "timestamp": [0.0, 0.1, 0.2, 0.3],
            "task_index": [0] * 4,
            "observation.state": [1.0, 2.0, 3.0, 4.0],
        }
    )
    output = VideoOutput(
        windows=(EncodedWindow(EP1, 0, 3, 0.0, 0.3, numeric_feature_statistics([[0.5, 0.5, 0.5]])),),
        codec="libx264",
        pix_fmt="yuv420p",
        fps=10.0,
        width=4,
        height=2,
        statistic_algorithm="sampled",
        sampling_recipe="one frame",
    )
    return prepared, info, {EP1: table}, {prepared.plan.objects[0].output_path: output}


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("extra_output", "transcode objects"),
        ("features", "feature descriptors"),
        ("descriptor", "feature descriptors"),
        ("fps", "dataset frame rate"),
        ("table", "condition-bound"),
        ("memory", "memory budget"),
        ("order", "output order"),
        ("encoded_fps", "coordinates"),
        ("from_frame", "coordinates"),
        ("to_frame", "coordinates"),
        ("from_timestamp", "coordinates"),
        ("to_timestamp", "coordinates"),
        ("geometry", "encoded geometry"),
        ("missing_camera", "no planned camera"),
    ],
)
def test_native_selection_refuses_mismatched_frozen_encoder_results(damage: str, message: str) -> None:
    prepared, info, tables, outputs = _native_case()
    path = prepared.plan.objects[0].output_path
    output = outputs[path]
    limits = None
    if damage == "extra_output":
        outputs["videos/extra/chunk-000/file-000.mp4"] = output
    elif damage == "features":
        info["features"] = {}
    elif damage == "descriptor":
        info["features"]["front"] = "video"
    elif damage == "fps":
        info["fps"] = 20.0
    elif damage == "table":
        tables = {}
    elif damage == "memory":
        limits = replace(ReleaseMediaLimits(), processing_memory_bytes=1)
    elif damage == "order":
        outputs[path] = replace(output, windows=(replace(output.windows[0], episode_id="wrong"),))
    elif damage == "encoded_fps":
        outputs[path] = replace(output, fps=20.0)
    elif damage in {"from_frame", "to_frame", "from_timestamp", "to_timestamp"}:
        field = {
            "from_frame": "output_from_frame",
            "to_frame": "output_to_frame",
            "from_timestamp": "output_from_s",
            "to_timestamp": "output_to_s",
        }[damage]
        outputs[path] = replace(output, windows=(replace(output.windows[0], **{field: 10}),))
    elif damage == "geometry":
        outputs[path] = replace(output, width=8)
    else:
        info["features"]["other_camera"] = {"dtype": "video", "shape": [2, 4, 3]}

    with pytest.raises(ReleasePlanError, match=message):
        generate_native_selection(
            prepared,
            info,
            tables,
            pa.table({"task": ["pick"], "task_index": [0]}),
            outputs,
            confirmed_digest=prepared.plan.digest,
            limits=limits,
        )


def test_native_selection_uses_observed_encoder_geometry_without_mutating_source() -> None:
    prepared, info, tables, outputs = _native_case()
    original = copy.deepcopy(info)

    result = generate_native_selection(
        prepared,
        info,
        tables,
        pa.table({"task": ["pick"], "task_index": [0]}),
        outputs,
        confirmed_digest=prepared.plan.digest,
    )

    assert result.data_tables[0]["observation.state"].to_pylist() == [1.0, 2.0, 4.0]
    assert result.metadata.info["total_frames"] == 3
    assert result.metadata.info["features"]["front"]["info"]["video.codec"] == "h264"
    assert result.metadata.info["features"]["front"]["info"]["has_audio"] is False
    assert result.metadata.episodes["videos/front/to_timestamp"].to_pylist() == [0.3]
    assert info == original
