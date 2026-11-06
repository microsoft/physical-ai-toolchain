"""Deterministic release object planning, route refusal, capacity, and digest semantics."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from physical_ai_curation.release.canonical import canonical_json_bytes
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.media import (
    ENV_PROCESSING_MEMORY,
    ENV_RESERVE,
    ENV_TRANSCODE_PERCENT,
    MediaWindow,
    MetadataSpec,
    ObjectPlan,
    ObjectPlanRequest,
    ReleaseMediaLimits,
    ReleasePlanError,
    StorageEndpoint,
    SubsetEpisodeInput,
    UnsupportedRouteError,
    build_subset_timeline,
    execute_exact_copy,
    load_media_limits,
    plan_release_objects,
    resolve_route,
)
from physical_ai_curation.release.models import FrozenInput, PreviewAction, SavedResource, SourceObject, VersionIdentity
from physical_ai_curation.release.statistics import (
    MOMENT_KEYS,
    QUANTILE_KEYS,
    aggregate_feature_statistics,
    numeric_feature_statistics,
    validate_feature_statistics,
)
from physical_ai_curation.release.store import LocalReleaseStore
from pydantic import ValidationError

DIGEST = "b" * 64
FROZEN = "c" * 64
VIDEO = "videos/observation.images.front/chunk-000/file-000.mp4"
WRIST = "videos/observation.images.wrist/chunk-000/file-000.mp4"
INFO = "meta/info.json"
TARGET = VersionIdentity(name="lerobot", version="v3.0")
TOOL = VersionIdentity(name="pyav-h264", version="1.0.0")
ZERO_RESERVE = ReleaseMediaLimits(reserve_bytes=0)

LOCAL_SOURCE = StorageEndpoint(alias="dataset-root", kind="local", volume="vol-src")
LOCAL_DEST = StorageEndpoint(alias="release-local", kind="local", volume="vol-src", prefix="exports/run-1")
LOCAL_SCRATCH_SAME = StorageEndpoint(alias="scratch", kind="local", volume="vol-src")
LOCAL_SCRATCH_OTHER = StorageEndpoint(alias="scratch", kind="local", volume="vol-scratch")
BLOB_SOURCE = StorageEndpoint(alias="dataset-blob", kind="blob", account="acct-a")
BLOB_DEST = StorageEndpoint(alias="release-blob", kind="blob", account="acct-a", prefix="exports/run-1")
BLOB_OTHER_ACCOUNT = StorageEndpoint(alias="release-blob-b", kind="blob", account="acct-b")


def _source(path: str = VIDEO, length: int = 1000, sha: str = DIGEST, revision: str = "rev-1") -> SourceObject:
    return SourceObject(path=path, revision=revision, length=length, sha256=sha)


def _window(
    episode: str = "ep-0",
    camera: str = "front",
    source: SourceObject | None = None,
    start: float = 0.0,
    end: float = 10.0,
    duration: float = 10.0,
    reasons: tuple[str, ...] = (),
) -> MediaWindow:
    return MediaWindow(
        episode_id=episode,
        camera=camera,
        source=_source() if source is None else source,
        from_timestamp=start,
        to_timestamp=end,
        object_duration_s=duration,
        processing_reasons=reasons,
    )


def _request(**changes: object) -> ObjectPlanRequest:
    values: dict[str, object] = {
        "source": LOCAL_SOURCE,
        "destination": LOCAL_DEST,
        "scratch": None,
        "target_format": TARGET,
        "frozen_input_sha256": FROZEN,
        "selected_episode_ids": ("ep-0", "ep-1"),
        "windows": (_window(),),
        "tool": TOOL,
        "tool_settings_json": '{"crf":23}',
        "limits": ZERO_RESERVE,
    }
    values.update(changes)
    return ObjectPlanRequest(**values)


def _partial_request(**changes: object) -> ObjectPlanRequest:
    values: dict[str, object] = {"windows": (_window(end=5.0),), "scratch": LOCAL_SCRATCH_SAME}
    values.update(changes)
    return _request(**values)


def test_full_shared_object_is_exact_copy_without_confirmation() -> None:
    windows = (_window("ep-0", start=0.0, end=5.0), _window("ep-1", start=5.0, end=10.0))

    plan = plan_release_objects(_request(windows=windows))

    assert [obj.output_path for obj in plan.objects] == [VIDEO]
    obj = plan.objects[0]
    assert obj.action == "copy"
    assert obj.tool == "exact-copy"
    assert obj.confirmation_required is False
    assert obj.source_path == VIDEO
    assert obj.source_length == 1000
    assert obj.affected_cameras == ("front",)
    assert plan.capacity.copied_bytes == 1000
    assert plan.capacity.scratch_required_bytes is None


def test_partial_shared_object_requires_necessary_transcode() -> None:
    plan = plan_release_objects(_partial_request())

    obj = plan.objects[0]
    assert obj.action == "transcode"
    assert obj.reasons == ("partial_object_selection",)
    assert obj.confirmation_required is True
    assert obj.affected_cameras == ("front",)
    assert obj.tool == "pyav-h264@1.0.0"
    assert obj.estimated_output_bytes == 500
    assert obj.estimated_scratch_bytes == 1500


def test_missing_subset_statistics_warns_without_blocking_a_copy_only_preview() -> None:
    plan = plan_release_objects(_request(statistics_requirements=("observation.images.front",)))

    assert plan.submittable
    assert plan.blocking == ()
    assert "statistics unavailable for observation.images.front; published metadata omits that summary" in plan.warnings
    assert plan.objects[0].action == "copy"


def test_statistics_requirements_invalidate_preview_confirmation() -> None:
    ready = plan_release_objects(_request())
    missing = plan_release_objects(_request(statistics_requirements=("observation.state",)))

    assert ready.digest != missing.digest


def test_processing_confirmation_rejects_missing_or_stale_preview() -> None:
    from physical_ai_curation.release.media import validate_processing_confirmation

    request = _partial_request()
    preview = plan_release_objects(request)
    changed = plan_release_objects(replace(request, tool_settings_json='{"crf":18}'))

    for confirmation in (None, changed.digest):
        with pytest.raises(ReleasePlanError, match="confirmation"):
            validate_processing_confirmation(preview, confirmed_digest=confirmation)
    validate_processing_confirmation(preview, confirmed_digest=preview.digest)


def test_missing_statistics_do_not_block_processing_confirmation() -> None:
    from physical_ai_curation.release.media import validate_processing_confirmation

    plan = plan_release_objects(_partial_request(statistics_requirements=("observation.images.front",)))

    assert plan.blocking == ()
    validate_processing_confirmation(plan, confirmed_digest=plan.digest)


def test_generated_episode_metadata_omits_missing_video_summaries() -> None:
    import pyarrow as pa
    from physical_ai_curation.release.tables import NativeVideoReference, generate_episode_metadata

    table = pa.table({"episode_index": [0], "index": [0], "task_index": [0]})

    row = generate_episode_metadata(
        table,
        {"observation.images.front": {"dtype": "video"}},
        output_episode_index=0,
        output_from_index=0,
        fps=10,
        task_names=["pick"],
        video_references={"observation.images.front": NativeVideoReference(VIDEO, 0.0, 0.1)},
        preserved_statistics={},
    )

    assert "videos/observation.images.front/file_index" in row
    assert not any(key.startswith("stats/observation.images.front/") for key in row)


def test_constant_fraction_statistics_allow_float_roundoff() -> None:
    summary = numeric_feature_statistics([0.1] * 1000)

    assert summary["mean"] == pytest.approx([0.1])
    assert summary["std"][0] < 1e-15


def test_statistics_aggregation_rejects_nonfinite_output() -> None:
    positive = {"min": [1e300], "max": [1e300], "mean": [1e300], "std": [0.0], "count": [1]}
    negative = {"min": [-1e300], "max": [-1e300], "mean": [-1e300], "std": [0.0], "count": [1]}

    with pytest.raises(ReleasePlanError, match="finite"):
        aggregate_feature_statistics([positive, negative], expected_counts=[1, 1])


@pytest.mark.parametrize("damage", [None, "incomplete", "moments", "features"])
def test_full_dataset_metadata_preserves_valid_source_quantiles(damage: str | None) -> None:
    from physical_ai_curation.release.tables import assemble_native_metadata

    features = {"observation.state": {"dtype": "float32", "shape": [1]}}
    source_statistics = numeric_feature_statistics([0.0, 0.0, 0.5, 1.0])
    rows = []
    offset = 0
    for index, values in enumerate(([0.0, 0.0, 0.5], [1.0])):
        summary = numeric_feature_statistics(values)
        row = {
            "episode_index": index,
            "length": len(values),
            "dataset_from_index": offset,
            "dataset_to_index": offset + len(values),
        }
        row.update({f"stats/observation.state/{key}": value for key, value in summary.items()})
        rows.append(row)
        offset += len(values)

    info = {"codebase_version": "v3.0", "features": features, "total_episodes": 2, "total_frames": 4}
    preserved = {"observation.state": source_statistics}
    if damage is not None:
        if damage == "incomplete":
            info["total_frames"] = 5
        elif damage == "moments":
            source_statistics["mean"] = [1.0]
        else:
            preserved.clear()
        with pytest.raises(ReleasePlanError, match=r"complete|differ|cover"):
            assemble_native_metadata(info, rows, task_count=1, preserved_dataset_statistics=preserved)
        return
    result = assemble_native_metadata(
        info,
        rows,
        task_count=1,
        preserved_dataset_statistics=preserved,
    )

    assert result.statistics["observation.state"] == source_statistics
    assert result.statistics_approximation is None
    assert result.statistics_algorithm == "validated-full-source-statistics-v1"


def test_aggregate_metadata_rejects_missing_declared_feature_statistics() -> None:
    from physical_ai_curation.release.tables import assemble_native_metadata

    summary = numeric_feature_statistics([1.0, 2.0])
    row = {"episode_index": 0, "length": 2, "dataset_from_index": 0, "dataset_to_index": 2}
    row.update({f"stats/observation.state/{key}": value for key, value in summary.items()})
    info = {
        "codebase_version": "v3.0",
        "features": {"observation.state": {"dtype": "float32"}, "action": {"dtype": "float32"}},
    }

    with pytest.raises(ReleasePlanError, match="statistics required"):
        assemble_native_metadata(info, [row], task_count=1)


def _video_episode_rows(camera_episodes: tuple[bool, ...]) -> tuple[dict, list[dict]]:
    camera = "observation.images.front"
    info = {
        "codebase_version": "v3.0",
        "features": {"observation.state": {"dtype": "float32"}, camera: {"dtype": "video"}},
    }
    rows = []
    offset = 0
    for index, has_camera in enumerate(camera_episodes):
        row = {"episode_index": index, "length": 2, "dataset_from_index": offset, "dataset_to_index": offset + 2}
        row.update(
            {f"stats/observation.state/{key}": value for key, value in numeric_feature_statistics([1.0, 2.0]).items()}
        )
        if has_camera:
            row.update(
                {f"stats/{camera}/{key}": value for key, value in numeric_feature_statistics([0.1, 0.2]).items()}
            )
        rows.append(row)
        offset += 2
    return info, rows


@pytest.mark.parametrize("coverage", [(True, False), (False, True)])
def test_partial_video_statistics_are_omitted_from_dataset_metadata_and_disclosed(
    coverage: tuple[bool, ...],
) -> None:
    from physical_ai_curation.release.tables import assemble_native_metadata

    info, rows = _video_episode_rows(coverage)

    result = assemble_native_metadata(info, rows, task_count=1)

    assert "observation.images.front" not in result.statistics
    assert "observation.state" in result.statistics
    assert result.statistics_approximation is not None
    assert "observation.images.front" in result.statistics_approximation
    assert "1 of 2 episodes" in result.statistics_approximation
    output_rows = result.episodes.to_pylist()
    for index, present in enumerate(coverage):
        assert (output_rows[index].get("stats/observation.images.front/mean") is not None) == present


def test_complete_video_statistics_aggregate_into_dataset_metadata() -> None:
    from physical_ai_curation.release.tables import assemble_native_metadata

    info, rows = _video_episode_rows((True, True))

    result = assemble_native_metadata(info, rows, task_count=1)

    assert "observation.images.front" in result.statistics
    assert "partial coverage" not in (result.statistics_approximation or "")


def test_absent_video_statistics_are_omitted_without_refusal() -> None:
    from physical_ai_curation.release.tables import assemble_native_metadata

    info, rows = _video_episode_rows((False, False))

    result = assemble_native_metadata(info, rows, task_count=1)

    assert "observation.images.front" not in result.statistics
    assert "observation.state" in result.statistics


def test_malformed_partial_video_statistics_still_refused() -> None:
    from physical_ai_curation.release.tables import assemble_native_metadata

    info, rows = _video_episode_rows((True, False))
    rows[0]["stats/observation.images.front/min"] = "not-a-number"

    with pytest.raises(ReleasePlanError):
        assemble_native_metadata(info, rows, task_count=1)


def test_native_episode_table_rebuilds_indices_without_changing_recorded_values() -> None:
    import pyarrow as pa
    from physical_ai_curation.release.tables import generate_episode_table

    source = pa.table(
        {
            "observation.state": pa.array([[7.0, float(frame)] for frame in range(4)], type=pa.list_(pa.float32(), 2)),
            "instruction": ["first", "removed", "interior", "last"],
            "episode_index": pa.array([7] * 4, type=pa.int64()),
            "frame_index": pa.array(range(4), type=pa.int64()),
            "index": pa.array(range(30, 34), type=pa.int64()),
            "timestamp": pa.array([frame / 10 for frame in range(4)], type=pa.float32()),
            "task_index": pa.array([0] * 4, type=pa.int64()),
        }
    )
    episode = SubsetEpisodeInput("episode-7", 7, 4, 10.0, frozenset({1}))

    table = generate_episode_table(source, episode, output_episode_index=2, output_from_index=8)

    assert table.schema == source.schema
    assert table.column("observation.state").to_pylist() == [[7.0, 0.0], [7.0, 2.0], [7.0, 3.0]]
    assert table.column("instruction").to_pylist() == ["first", "interior", "last"]
    assert table.column("episode_index").to_pylist() == [2, 2, 2]
    assert table.column("frame_index").to_pylist() == [0, 1, 2]
    assert table.column("index").to_pylist() == [8, 9, 10]
    assert table.column("timestamp").to_pylist() == pytest.approx([0.0, 0.1, 0.2])


@pytest.mark.parametrize("layout", ["columns", "named-index", "unnamed-index"])
def test_native_task_table_has_named_task_index(layout: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from physical_ai_curation.release.tables import generate_task_table

    source = pa.table({"task_index": [0, 1], "task": ["pick the part", "place the part"]})
    if layout != "columns":
        frame = source.to_pandas().set_index("task")
        if layout == "unnamed-index":
            frame.index.name = None
        source = pa.Table.from_pandas(frame, preserve_index=True)

    tasks = generate_task_table(source)
    buffer = pa.BufferOutputStream()
    pq.write_table(tasks, buffer)
    reloaded = pq.read_table(pa.BufferReader(buffer.getvalue())).to_pandas()

    assert reloaded.index.name == "task"
    assert reloaded.index.tolist() == ["pick the part", "place the part"]
    assert reloaded["task_index"].tolist() == [0, 1]


def test_native_persistent_language_retimes_collapsed_changes_and_keeps_frame_events() -> None:
    import pyarrow as pa
    from physical_ai_curation.release.tables import generate_episode_table

    persistent = [
        {"role": "user", "style": "subtask", "content": content, "timestamp": timestamp}
        for content, timestamp in (("initial", 0.0), ("older", 0.1), ("newer", 0.2))
    ]
    source = pa.table(
        {
            "episode_index": [7] * 4,
            "frame_index": list(range(4)),
            "index": list(range(30, 34)),
            "timestamp": [frame / 10 for frame in range(4)],
            "task_index": [0] * 4,
            "language_persistent": [persistent] * 4,
            "language_events": [["start"], ["removed"], ["kept"], []],
        }
    )

    output = generate_episode_table(
        source,
        SubsetEpisodeInput("episode-7", 7, 4, 10.0, frozenset({1})),
        output_episode_index=0,
        output_from_index=0,
    )

    rows = output["language_persistent"].to_pylist()
    assert all([row["content"] for row in frame] == ["initial", "newer"] for frame in rows)
    assert all([row["timestamp"] for row in frame] == pytest.approx([0.0, 0.1]) for frame in rows)
    assert output["language_events"].to_pylist() == [["start"], ["kept"], []]


def test_non_media_objects_and_metadata_dependencies_bind_source_conditions() -> None:
    table = _source("data/chunk-000/file-000.parquet", length=2048)
    request = _request(
        source_objects=(table,),
        copy_paths=(table.path,),
        metadata=(MetadataSpec(output_path=INFO, depends_on=(table.path,), estimated_bytes=64),),
    )

    plan = plan_release_objects(request)
    changed = plan_release_objects(replace(request, source_objects=(table.model_copy(update={"revision": "rev-2"}),)))

    assert plan.capacity.copied_bytes == 3048
    assert table in plan.source_objects
    assert plan.digest != changed.digest


def test_metadata_only_dependencies_are_condition_bound() -> None:
    table = _source("data/chunk-000/file-000.parquet", length=2048)
    request = _request(
        source_objects=(table,),
        metadata=(MetadataSpec(output_path=INFO, depends_on=(table.path,), estimated_bytes=64),),
    )

    plan = plan_release_objects(request)
    changed = plan_release_objects(replace(request, source_objects=(table.model_copy(update={"revision": "rev-2"}),)))

    assert table in plan.source_objects
    assert plan.digest != changed.digest


@pytest.mark.parametrize("settings", ['{ "crf": 23 }', '{"limit":NaN}', '{"x":1,"x":2}'])
def test_invalid_generation_settings_are_rejected(settings: str) -> None:
    with pytest.raises(ReleasePlanError):
        plan_release_objects(_request(tool_settings_json=settings))


def test_empty_selection_is_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="selection"):
        plan_release_objects(_request(selected_episode_ids=(), windows=()))


def test_blob_scratch_is_not_a_processing_root() -> None:
    with pytest.raises(ReleasePlanError, match="scratch"):
        plan_release_objects(_partial_request(scratch=BLOB_DEST))


def test_processing_estimate_allows_output_growth() -> None:
    plan = plan_release_objects(
        _partial_request(limits=ReleaseMediaLimits(reserve_bytes=0, transcode_output_percent=250))
    )

    assert plan.objects[0].estimated_output_bytes == 1250


PROJECTION_DATASET = "lab/ds"


def _projection_episode(index: int, length: int = 10, removed: frozenset[int] = frozenset()) -> SubsetEpisodeInput:
    return SubsetEpisodeInput(f"{PROJECTION_DATASET}/episode_{index:06d}", index, length, 10.0, removed)


def _projection_frozen(*resources: tuple[str, object], episodes: tuple[int, ...] = (3, 7)) -> FrozenInput:
    return FrozenInput(
        selected_episode_ids=tuple(f"{PROJECTION_DATASET}/episode_{index:06d}" for index in episodes),
        saved_resources=tuple(
            SavedResource(resource_id=rid, revision="rev-1", value_json=canonical_json_bytes(value).decode())
            for rid, value in resources
        ),
    )


def _materialize_projections(frozen: FrozenInput, episodes: tuple[SubsetEpisodeInput, ...]) -> dict[str, bytes]:
    from physical_ai_curation.release.projections import materialize_saved_projections

    return materialize_saved_projections(frozen, episodes, release_dataset_id="released-dataset")


def test_saved_annotation_materializes_at_output_index_path() -> None:
    frozen = _projection_frozen(
        (
            "annotation/episode_000007",
            {"annotations": [{"annotator_id": "reviewer-a", "task_completeness": {"rating": "success"}}]},
        ),
    )

    files = _materialize_projections(frozen, (_projection_episode(3), _projection_episode(7)))

    assert set(files) == {"annotations/episodes/episode_000001.json"}
    payload = json.loads(files["annotations/episodes/episode_000001.json"])
    assert payload["dataset_id"] == "released-dataset"
    assert payload["output_episode_index"] == 1
    assert payload["source_episode_id"] == f"{PROJECTION_DATASET}/episode_000007"
    assert payload["annotations"][0]["task_completeness"] == {"rating": "success"}
    assert files["annotations/episodes/episode_000001.json"] == canonical_json_bytes(payload)


def test_labels_are_remapped_to_output_episode_keys_in_selection_order() -> None:
    labels = {
        "dataset_id": PROJECTION_DATASET,
        "episodes": {"3": ["failure"], "7": ["success"]},
        "analysis": {},
        "provenance": {},
    }
    frozen = _projection_frozen(("labels/episode_000007", labels), ("labels/episode_000003", labels), episodes=(7, 3))

    files = _materialize_projections(frozen, (_projection_episode(7), _projection_episode(3)))

    payload = json.loads(files["meta/episode_labels.json"])
    assert payload["episodes"] == {"0": ["success"], "1": ["failure"]}
    assert payload["dataset_id"] == "released-dataset"


def _machine_ledger(value: object) -> dict[str, object]:
    contribution = {
        "id": "c1",
        "field": "labels",
        "origin": "machine",
        "value": value,
        "sequence": 0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "machine": {
            "run_id": "run-1",
            "result_id": "result-1",
            "run_order": 0,
            "source_revision": "private-source-rev",
            "input_revision": "input-rev",
            "config_revision": "config-rev",
        },
    }
    return {"schema_version": "1.0.0", "contributions": [contribution]}


def test_labels_provenance_strips_private_source_revision_from_output() -> None:
    labels = {
        "dataset_id": PROJECTION_DATASET,
        "episodes": {"7": ["success"]},
        "provenance": {"7": _machine_ledger(["success"])},
    }
    frozen = _projection_frozen(("labels/episode_000007", labels), episodes=(7,))

    files = _materialize_projections(frozen, (_projection_episode(7),))

    raw = files["meta/episode_labels.json"]
    assert b"private-source-rev" not in raw
    machine = json.loads(raw)["provenance"]["0"]["contributions"][0]["machine"]
    assert machine == {
        "run_id": "run-1",
        "result_id": "result-1",
        "run_order": 0,
        "input_revision": "input-rev",
        "config_revision": "config-rev",
    }
    assert b"private-source-rev" in frozen.saved_resources[0].value_json.encode()


def test_labels_provenance_with_private_principal_field_is_refused() -> None:
    labels = {
        "dataset_id": PROJECTION_DATASET,
        "episodes": {"7": ["success"]},
        "provenance": {"7": _machine_ledger({"principal_scope_id": "tenant-a"})},
    }
    frozen = _projection_frozen(("labels/episode_000007", labels), episodes=(7,))

    with pytest.raises(ReleasePlanError, match="private principal"):
        _materialize_projections(frozen, (_projection_episode(7),))


def test_changed_episode_analysis_stays_frozen_not_output_valid() -> None:
    labels = {
        "dataset_id": PROJECTION_DATASET,
        "available_labels": ["custom-label"],
        "episodes": {"7": ["custom-label"]},
        "analysis": {"7": {"grasp_success": True, "duration_s": 1.0}},
    }
    frozen = _projection_frozen(("labels/episode_000007", labels), episodes=(7,))

    files = _materialize_projections(frozen, (_projection_episode(7, removed=frozenset({1})),))

    payload = json.loads(files["meta/episode_labels.json"])
    assert payload["analysis"] == {}
    assert "custom-label" in payload["available_labels"]
    assert json.loads(frozen.saved_resources[0].value_json)["analysis"]["7"]["grasp_success"] is True


def test_frame_range_spans_remap_inclusively_around_removed_frames() -> None:
    annotation = {
        "annotations": [
            {
                "annotator_id": "reviewer-a",
                "anomalies": {"anomalies": [{"id": "a1", "frame_range": [0, 2]}, {"id": "a2", "frame_range": [5, 9]}]},
                "object_detections": [{"frame_index": 9}],
            }
        ]
    }
    frozen = _projection_frozen(("annotation/episode_000007", annotation), episodes=(7,))

    files = _materialize_projections(frozen, (_projection_episode(7, removed=frozenset({1})),))

    entry = json.loads(files["annotations/episodes/episode_000000.json"])["annotations"][0]
    assert [item["frame_range"] for item in entry["anomalies"]["anomalies"]] == [[0, 0], [1, 1], [4, 8]]
    assert entry["object_detections"][0]["frame_index"] == 8


@pytest.mark.parametrize(
    "annotation",
    [
        {"annotations": [{"anomalies": {"anomalies": [{"frame_range": [8, 12]}]}}]},
        {"annotations": [{"anomalies": {"anomalies": [{"frame_range": [5, 2]}]}}]},
        {"annotations": [{"object_detections": [{"frame_index": 12}]}]},
    ],
    ids=["past-end", "inverted", "past-end-detection-frame"],
)
def test_unrepresentable_frame_references_are_rejected(annotation: dict[str, object]) -> None:
    frozen = _projection_frozen(("annotation/episode_000007", annotation), episodes=(7,))

    with pytest.raises(ReleasePlanError, match="frame"):
        _materialize_projections(frozen, (_projection_episode(7, removed=frozenset({1})),))


def test_projection_omits_excluded_annotations_and_preserves_source_claims() -> None:
    annotation = {
        "annotations": [
            {
                "task_completeness": {"rating": "success"},
                "anomalies": {"anomalies": [{"frame_range": [1, 1]}]},
                "data_quality": {"overall_quality": "good", "issues": [{"affected_frames": [0, 2]}]},
                "object_detections": [{"frame_index": 1}],
            }
        ]
    }
    frozen = _projection_frozen(("annotation/episode_000007", annotation), episodes=(7,))

    files = _materialize_projections(frozen, (_projection_episode(7, removed=frozenset({1})),))

    entry = json.loads(files["annotations/episodes/episode_000000.json"])["annotations"][0]
    assert entry["anomalies"]["anomalies"] == []
    assert entry["object_detections"] == []
    assert [issue["affected_frames"] for issue in entry["data_quality"]["issues"]] == [[0, 0], [1, 1]]
    assert entry["source_claims"]["task_completeness"] == {"rating": "success"}
    assert "task_completeness" not in entry


def test_annotation_entries_are_remapped_once_and_never_dropped() -> None:
    annotation = {
        "annotations": [
            {"annotator_id": "a", "anomalies": {"anomalies": [{"frame_range": [0, 2]}]}},
            {"annotator_id": "b", "anomalies": {"anomalies": [{"frame_range": [0, 2]}]}},
        ]
    }
    frozen = _projection_frozen(("annotation/episode_000007", annotation), episodes=(7,))

    files = _materialize_projections(frozen, (_projection_episode(7, removed=frozenset({1})),))

    entries = json.loads(files["annotations/episodes/episode_000000.json"])["annotations"]
    assert len(entries) == 2
    assert all(
        [item["frame_range"] for item in entry["anomalies"]["anomalies"]] == [[0, 0], [1, 1]] for entry in entries
    )


@pytest.mark.parametrize("private_key", ["principal_scope_id", "source_id", "api_key"])
def test_projection_refuses_private_principal_keys(private_key: str) -> None:
    annotation = {"annotations": [{"annotator_id": "a", private_key: "private-value"}]}
    frozen = _projection_frozen(("annotation/episode_000007", annotation), episodes=(7,))

    with pytest.raises(ReleasePlanError, match="private"):
        _materialize_projections(frozen, (_projection_episode(7),))


def test_missing_saved_values_emit_no_projection_files() -> None:
    frozen = _projection_frozen()

    assert _materialize_projections(frozen, (_projection_episode(3), _projection_episode(7))) == {}


@pytest.mark.parametrize("destination_available", [None, 10**12])
def test_shared_volume_uses_known_scratch_shortage(destination_available: int | None) -> None:
    plan = plan_release_objects(
        _partial_request(destination_available_bytes=destination_available, scratch_available_bytes=1)
    )

    assert not plan.submittable
    assert plan.capacity.shared_volume


def test_saved_media_edit_forces_transcode_even_for_full_object() -> None:
    plan = plan_release_objects(
        _partial_request(windows=(_window("ep-0", end=10.0, reasons=("frame_removal",)),)),
    )

    obj = plan.objects[0]
    assert obj.action == "transcode"
    assert obj.reasons == ("frame_removal",)
    assert obj.confirmation_required is True


def test_metadata_is_generated_with_sorted_dependencies() -> None:
    windows = (_window("ep-0", start=0.0, end=5.0), _window("ep-1", camera="wrist", source=_source(WRIST)))
    metadata = (MetadataSpec(output_path=INFO, depends_on=(WRIST, VIDEO), estimated_bytes=64),)

    plan = plan_release_objects(
        _request(windows=windows, metadata=metadata, scratch=LOCAL_SCRATCH_SAME, selected_episode_ids=("ep-0", "ep-1")),
    )

    by_path = {obj.output_path: obj for obj in plan.objects}
    info = by_path[INFO]
    assert info.action == "generate_metadata"
    assert info.dependencies == tuple(sorted((VIDEO, WRIST)))
    assert info.confirmation_required is False
    assert info.estimated_output_bytes == 64
    assert info.source_path is None


def test_no_remux_action_is_advertised() -> None:
    windows = (_window("ep-0", start=0.0, end=5.0), _window("ep-1", camera="wrist", source=_source(WRIST)))
    metadata = (MetadataSpec(output_path=INFO, depends_on=(VIDEO,)),)

    plan = plan_release_objects(_request(windows=windows, metadata=metadata, scratch=LOCAL_SCRATCH_SAME))

    actions = {obj.action for obj in plan.objects}
    assert actions <= {"copy", "transcode", "generate_metadata"}
    assert "remux" not in {action.action for action in plan.to_preview_actions()}


def test_plan_is_deterministic_for_reordered_windows() -> None:
    first = _window("ep-0", start=0.0, end=5.0)
    second = _window("ep-1", start=5.0, end=10.0)

    forward = plan_release_objects(_request(windows=(first, second)))
    reverse = plan_release_objects(_request(windows=(second, first)))

    assert forward.digest == reverse.digest
    assert forward.objects == reverse.objects


def test_digest_binds_selection_order_route_destination_and_tool() -> None:
    base = plan_release_objects(_partial_request())

    reordered = plan_release_objects(_partial_request(selected_episode_ids=("ep-1", "ep-0")))
    other_alias = plan_release_objects(
        _partial_request(destination=replace(LOCAL_DEST, alias="release-local-2")),
    )
    other_tool = plan_release_objects(_partial_request(tool=VersionIdentity(name="pyav-h264", version="1.0.1")))
    other_frozen = plan_release_objects(_partial_request(frozen_input_sha256="d" * 64))

    digests = {base.digest, reordered.digest, other_alias.digest, other_tool.digest, other_frozen.digest}
    assert len(digests) == 5


def test_digest_binds_affected_cameras() -> None:
    front = plan_release_objects(_partial_request())
    wrist = plan_release_objects(
        _partial_request(windows=(_window("ep-0", camera="wrist", source=_source(WRIST), end=5.0),))
    )

    assert front.digest != wrist.digest


def test_digest_excludes_free_space_observations() -> None:
    unobserved = plan_release_objects(_partial_request(destination_available_bytes=None))
    observed = plan_release_objects(_partial_request(destination_available_bytes=10**12))

    assert unobserved.digest == observed.digest


def test_contradictory_source_identity_is_rejected() -> None:
    windows = (_window("ep-0", start=0.0, end=5.0), _window("ep-1", start=5.0, end=10.0, source=_source(sha="d" * 64)))

    with pytest.raises(ReleasePlanError, match="contradictory"):
        plan_release_objects(_request(windows=windows))


def test_duplicate_output_destination_is_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="duplicate"):
        plan_release_objects(_request(metadata=(MetadataSpec(output_path=VIDEO, depends_on=()),)))


@pytest.mark.parametrize("path", ["release/manifest.json", "evidence/frozen-input.json"])
def test_reserved_namespace_metadata_is_rejected(path: str) -> None:
    with pytest.raises(ReleasePlanError, match="reserved"):
        plan_release_objects(_request(metadata=(MetadataSpec(output_path=path, depends_on=()),)))


def test_reserved_destination_prefix_is_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="reserved"):
        StorageEndpoint(alias="release-local", kind="local", volume="vol-src", prefix="release/out")


@pytest.mark.parametrize("path", ["videos/front.mp4?sig=abc", "https://account.example/videos/front.mp4"])
def test_credential_bearing_locator_is_rejected(path: str) -> None:
    with pytest.raises((ReleasePlanError, ValidationError)):
        plan_release_objects(_request(windows=(_window(source=_source(path=path)),)))


def test_unknown_metadata_dependency_is_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="dependency"):
        plan_release_objects(_request(metadata=(MetadataSpec(output_path=INFO, depends_on=("videos/missing.mp4",)),)))


def test_window_for_unselected_episode_is_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="unselected"):
        plan_release_objects(_request(windows=(_window("ep-9"),)))


@pytest.mark.parametrize(("start", "end"), [(5.0, 5.0), (6.0, 4.0), (0.0, 11.0)])
def test_invalid_window_timestamps_are_rejected(start: float, end: float) -> None:
    with pytest.raises(ReleasePlanError):
        plan_release_objects(_request(windows=(_window(start=start, end=end),)))


def test_supported_routes_are_accepted() -> None:
    assert resolve_route(LOCAL_SOURCE, LOCAL_DEST) == "local-to-local"
    assert resolve_route(BLOB_SOURCE, BLOB_DEST) == "blob-same-account"


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        (LOCAL_SOURCE, BLOB_DEST),
        (BLOB_SOURCE, LOCAL_DEST),
        (BLOB_SOURCE, BLOB_OTHER_ACCOUNT),
    ],
)
def test_unsupported_routes_are_refused_before_planning(source: StorageEndpoint, destination: StorageEndpoint) -> None:
    with pytest.raises(UnsupportedRouteError):
        resolve_route(source, destination)
    with pytest.raises(UnsupportedRouteError):
        plan_release_objects(_request(source=source, destination=destination))


def test_blob_same_account_copy_needs_no_local_destination_capacity() -> None:
    plan = plan_release_objects(_request(source=BLOB_SOURCE, destination=BLOB_DEST))

    assert plan.route == "blob-same-account"
    assert plan.capacity.destination_required_bytes is None
    assert plan.submittable is True


@pytest.mark.parametrize("version", ["v2.1", "v3.1"])
def test_only_lerobot_v3_targets_are_supported(version: str) -> None:
    with pytest.raises(UnsupportedRouteError):
        plan_release_objects(_request(target_format=VersionIdentity(name="lerobot", version=version)))


def test_transcode_estimate_scales_with_selected_fraction_and_percent() -> None:
    half = plan_release_objects(_partial_request())
    quarter = plan_release_objects(
        _partial_request(limits=ReleaseMediaLimits(reserve_bytes=0, transcode_output_percent=50)),
    )

    assert half.objects[0].estimated_output_bytes == 500
    assert quarter.objects[0].estimated_output_bytes == 250


def test_shared_volume_shortage_is_blocking_at_exact_boundary() -> None:
    short = plan_release_objects(_partial_request(destination_available_bytes=1999))
    exact = plan_release_objects(_partial_request(destination_available_bytes=2000))

    assert short.capacity.shared_volume is True
    assert short.capacity.combined_required_bytes == 2000
    assert any("shared_volume" in item for item in short.blocking)
    assert short.submittable is False
    assert exact.blocking == ()
    assert exact.submittable is True


def test_different_volume_scratch_shortage_is_blocking() -> None:
    short = plan_release_objects(
        _partial_request(
            scratch=LOCAL_SCRATCH_OTHER,
            destination_available_bytes=500,
            scratch_available_bytes=1499,
        ),
    )
    exact = plan_release_objects(
        _partial_request(
            scratch=LOCAL_SCRATCH_OTHER,
            destination_available_bytes=500,
            scratch_available_bytes=1500,
        ),
    )

    assert short.capacity.shared_volume is False
    assert short.capacity.destination_required_bytes == 500
    assert short.capacity.scratch_required_bytes == 1500
    assert any("scratch" in item for item in short.blocking)
    assert exact.blocking == ()


def test_warnings_never_clear_a_hard_shortage() -> None:
    plan = plan_release_objects(_partial_request(destination_available_bytes=1))

    assert plan.warnings
    assert plan.submittable is False


def test_unobserved_free_space_is_advisory_not_blocking() -> None:
    plan = plan_release_objects(_partial_request(destination_available_bytes=None))

    assert plan.submittable is True
    assert any("not observed" in item for item in plan.warnings)


def test_processing_without_scratch_root_is_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="scratch"):
        plan_release_objects(_partial_request(scratch=None))


def test_preview_actions_mirror_object_plans() -> None:
    plan = plan_release_objects(_partial_request())

    assert plan.to_preview_actions() == (
        PreviewAction(
            object_path=VIDEO,
            action="transcode",
            route="local-to-local",
            tool="pyav-h264@1.0.0",
            destination_alias="release-local",
            confirmation_required=True,
        ),
    )


def test_limits_default_when_environment_is_absent() -> None:
    assert load_media_limits({}) == ReleaseMediaLimits()


def test_limits_accept_documented_overrides() -> None:
    limits = load_media_limits(
        {ENV_PROCESSING_MEMORY: "4096", ENV_RESERVE: "0", ENV_TRANSCODE_PERCENT: "50"},
    )

    assert limits == ReleaseMediaLimits(processing_memory_bytes=4096, reserve_bytes=0, transcode_output_percent=50)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (ENV_PROCESSING_MEMORY, "0"),
        (ENV_RESERVE, "-1"),
        (ENV_TRANSCODE_PERCENT, "-1"),
        (ENV_TRANSCODE_PERCENT, "0"),
        (ENV_RESERVE, "abc"),
    ],
)
def test_invalid_limits_are_rejected(name: str, value: str) -> None:
    with pytest.raises(ReleasePlanError):
        load_media_limits({name: value})


def test_exact_copy_planning_imports_without_decoder_libraries() -> None:
    code = textwrap.dedent(
        """
        import sys
        for name in ("av", "numpy", "pyarrow", "cv2", "torch"):
            sys.modules[name] = None
        from physical_ai_curation.release.media import (
            MediaWindow, ObjectPlanRequest, StorageEndpoint, plan_release_objects,
        )
        from physical_ai_curation.release.models import SourceObject, VersionIdentity
        source = SourceObject(path="videos/front.mp4", revision="r", length=10, sha256="a" * 64)
        request = ObjectPlanRequest(
            source=StorageEndpoint(alias="s", kind="local", volume="v"),
            destination=StorageEndpoint(alias="d", kind="local", volume="v"),
            scratch=None,
            target_format=VersionIdentity(name="lerobot", version="v3.0"),
            frozen_input_sha256="b" * 64,
            selected_episode_ids=("ep-0",),
            windows=(MediaWindow("ep-0", "front", source, 0.0, 1.0, 1.0),),
            tool=VersionIdentity(name="t", version="1"),
            tool_settings_json="{}",
        )
        assert plan_release_objects(request).objects[0].action == "copy"
        print("ok")
        """
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_subset_timeline_renumbers_episodes_in_selection_order() -> None:
    timeline = build_subset_timeline(
        (
            SubsetEpisodeInput("ep-b", original_episode_index=7, length=3, fps=10.0),
            SubsetEpisodeInput("ep-a", original_episode_index=2, length=2, fps=10.0),
        )
    )

    assert timeline.episode_lengths == (3, 2)
    assert [(f.output_episode_index, f.output_frame_index, f.original_episode_index) for f in timeline.frames] == [
        (0, 0, 7),
        (0, 1, 7),
        (0, 2, 7),
        (1, 0, 2),
        (1, 1, 2),
    ]
    assert timeline.frames[3].timestamp_s == 0.0
    assert timeline.frames[4].timestamp_s == pytest.approx(0.1)


def test_subset_timeline_drops_removed_frames_and_restarts_timestamps() -> None:
    timeline = build_subset_timeline(
        (SubsetEpisodeInput("ep-0", original_episode_index=0, length=5, fps=10.0, removed_frames=frozenset({1, 2})),)
    )

    assert [f.original_frame_index for f in timeline.frames] == [0, 3, 4]
    assert [f.output_frame_index for f in timeline.frames] == [0, 1, 2]
    assert timeline.frames[1].timestamp_s == pytest.approx(0.1)
    assert timeline.episode_lengths == (3,)


@pytest.mark.parametrize(
    ("episode", "message"),
    [
        (SubsetEpisodeInput("ep-0", 0, length=5, fps=10.0, removed_frames=frozenset({5})), "removed frame"),
        (SubsetEpisodeInput("ep-0", 0, length=2, fps=10.0, removed_frames=frozenset({0, 1})), "empty"),
        (SubsetEpisodeInput("ep-0", 0, length=0, fps=10.0), "length"),
        (SubsetEpisodeInput("ep-0", 0, length=2, fps=0.0), "fps"),
    ],
)
def test_subset_timeline_rejects_invalid_episodes(episode: SubsetEpisodeInput, message: str) -> None:
    with pytest.raises(ReleasePlanError, match=message):
        build_subset_timeline((episode,))


def test_subset_timeline_rejects_duplicate_episode_identifiers() -> None:
    with pytest.raises(ReleasePlanError, match="unique"):
        build_subset_timeline(
            (
                SubsetEpisodeInput("ep-0", 0, length=2, fps=10.0),
                SubsetEpisodeInput("ep-0", 1, length=2, fps=10.0),
            )
        )


def _exact_copy_plan(payload: bytes) -> tuple[ObjectPlan, SourceObject]:
    source = SourceObject(
        path=VIDEO,
        revision="rev-1",
        length=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    request = ObjectPlanRequest(
        source=LOCAL_SOURCE,
        destination=LOCAL_DEST,
        scratch=None,
        target_format=TARGET,
        frozen_input_sha256=FROZEN,
        selected_episode_ids=("ep-0",),
        windows=(MediaWindow("ep-0", "front", source, 0.0, 10.0, 10.0),),
        tool=TOOL,
    )
    plan = plan_release_objects(request)
    return plan.objects[0], source


def _write_source(root: Path, payload: bytes) -> Path:
    path = root / VIDEO
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return root


def test_exact_copy_writes_byte_identical_output(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 37
    obj, _ = _exact_copy_plan(payload)
    source_root = _write_source(tmp_path / "source", payload)
    output_root = tmp_path / "out"

    written = execute_exact_copy(obj, source_root, output_root, chunk_size=7)

    assert written == len(payload)
    assert (output_root / VIDEO).read_bytes() == payload
    assert (output_root / VIDEO).stat().st_ino != (source_root / VIDEO).stat().st_ino


def test_exact_copy_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    payload = b"video-bytes"
    obj, _ = _exact_copy_plan(payload)
    source_root = _write_source(tmp_path / "source", payload)
    output_root = tmp_path / "out"
    (output_root / VIDEO).parent.mkdir(parents=True)
    (output_root / VIDEO).write_bytes(b"existing")

    with pytest.raises(ReleaseVerificationError):
        execute_exact_copy(obj, source_root, output_root)

    assert (output_root / VIDEO).read_bytes() == b"existing"


@pytest.mark.parametrize("relationship", ["equal", "output_inside_source", "source_inside_output"])
def test_exact_copy_rejects_overlapping_roots_without_mutation(tmp_path: Path, relationship: str) -> None:
    payload = b"source-bytes"
    obj, _ = _exact_copy_plan(payload)
    source = _write_source(tmp_path / "source", payload)
    output = {
        "equal": source,
        "output_inside_source": source / "derived",
        "source_inside_output": tmp_path,
    }[relationship]
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    with pytest.raises(ReleaseVerificationError, match="overlap"):
        execute_exact_copy(obj, source, output)

    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before
    assert (source / VIDEO).read_bytes() == payload


def test_exact_copy_detects_same_length_source_drift_and_publishes_nothing(tmp_path: Path) -> None:
    payload = b"A" * 64
    obj, _ = _exact_copy_plan(payload)
    source_root = _write_source(tmp_path / "source", b"B" * 64)
    output_root = tmp_path / "out"

    with pytest.raises(ReleaseVerificationError, match="drift"):
        execute_exact_copy(obj, source_root, output_root)

    assert not (output_root / VIDEO).exists()
    assert list((output_root / VIDEO).parent.glob("*")) == []


def test_exact_copy_rejects_symlinked_source(tmp_path: Path) -> None:
    payload = b"real-bytes"
    obj, _ = _exact_copy_plan(payload)
    real = tmp_path / "elsewhere.mp4"
    real.write_bytes(payload)
    link_parent = tmp_path / "source" / VIDEO
    link_parent.parent.mkdir(parents=True)
    os.symlink(real, link_parent)

    with pytest.raises(ReleaseVerificationError):
        execute_exact_copy(obj, tmp_path / "source", tmp_path / "out")

    assert not (tmp_path / "out" / VIDEO).exists()


def test_exact_copy_refuses_processing_objects(tmp_path: Path) -> None:
    payload = b"x" * 100
    source = SourceObject(path=VIDEO, revision="rev-1", length=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    request = ObjectPlanRequest(
        source=LOCAL_SOURCE,
        destination=LOCAL_DEST,
        scratch=LOCAL_SCRATCH_OTHER,
        target_format=TARGET,
        frozen_input_sha256=FROZEN,
        selected_episode_ids=("ep-0",),
        windows=(MediaWindow("ep-0", "front", source, 0.0, 4.0, 10.0),),
        tool=TOOL,
    )
    obj = plan_release_objects(request).objects[0]
    assert obj.action == "transcode"

    with pytest.raises(ReleasePlanError, match="copy"):
        execute_exact_copy(obj, _write_source(tmp_path / "source", payload), tmp_path / "out")


@pytest.mark.parametrize("field", ["source_path", "output_path"])
def test_exact_copy_revalidates_plan_paths(tmp_path: Path, field: str) -> None:
    payload = b"source-bytes"
    obj, _ = _exact_copy_plan(payload)
    source = _write_source(tmp_path / "source", payload)
    (tmp_path / "outside.mp4").write_bytes(payload)

    with pytest.raises(ReleaseVerificationError):
        path = "../outside.mp4" if field == "source_path" else "../escaped.mp4"
        execute_exact_copy(replace(obj, **{field: path}), source, tmp_path / "out")

    assert (tmp_path / "outside.mp4").read_bytes() == payload
    assert not (tmp_path / "escaped.mp4").exists()
    assert not (tmp_path / "out" / VIDEO).exists()


@pytest.mark.parametrize("linked_root", ["source", "output"])
def test_exact_copy_rejects_symlinked_roots(tmp_path: Path, linked_root: str) -> None:
    payload = b"source-bytes"
    obj, _ = _exact_copy_plan(payload)
    source = _write_source(tmp_path / "source", payload)
    output = tmp_path / "out"
    actual = source if linked_root == "source" else output
    actual.mkdir(exist_ok=True)
    link = tmp_path / "linked-root"
    link.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ReleaseVerificationError):
        execute_exact_copy(
            obj, link if linked_root == "source" else source, link if linked_root == "output" else output
        )

    assert not (output / VIDEO).exists()


def test_exact_copy_detects_destination_directory_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"source-bytes"
    obj, _ = _exact_copy_plan(payload)
    source = _write_source(tmp_path / "source", payload)
    output = tmp_path / "out"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    original = LocalReleaseStore.iter_bytes

    def replacing(self: LocalReleaseStore, path: str, *, chunk_size: int) -> Iterator[bytes]:
        yield from original(self, path, chunk_size=chunk_size)
        (output / "videos").rename(tmp_path / "detached-output")
        (output / "videos").symlink_to(foreign, target_is_directory=True)

    monkeypatch.setattr(LocalReleaseStore, "iter_bytes", replacing)
    with pytest.raises(ReleaseVerificationError):
        execute_exact_copy(obj, source, output)

    assert not list(foreign.rglob("*.mp4"))
    assert not list((tmp_path / "detached-output").rglob("*.partial"))
    assert not list((tmp_path / "detached-output").rglob("*.mp4"))


def test_subset_timeline_refuses_known_memory_shortage() -> None:
    with pytest.raises(ReleasePlanError, match="memory"):
        build_subset_timeline(
            (SubsetEpisodeInput("ep-0", 0, 100, 10.0),), limits=ReleaseMediaLimits(processing_memory_bytes=1)
        )


def test_subset_timeline_rejects_invalid_original_episode_index() -> None:
    with pytest.raises(ReleasePlanError, match="episode index"):
        build_subset_timeline((SubsetEpisodeInput("ep-0", -1, 2, 10.0),))


def _episode_summary() -> dict[str, object]:
    return numeric_feature_statistics([[1.0], [2.0], [3.0], [4.0]])


def test_known_numeric_columns_match_population_statistics() -> None:
    result = numeric_feature_statistics([[1.0, 10.0], [3.0, 20.0], [5.0, 60.0]])
    assert result["count"] == [3]
    assert result["min"] == [1.0, 10.0]
    assert result["max"] == [5.0, 60.0]
    assert result["mean"] == pytest.approx([3.0, 30.0])
    assert result["std"] == pytest.approx([(8 / 3) ** 0.5, (1400 / 3) ** 0.5])
    assert result["q50"] == pytest.approx([3.0, 20.0])
    assert numeric_feature_statistics([2.0, 4.0])["mean"] == [3.0]


def test_image_summaries_keep_native_channel_shape() -> None:
    result = numeric_feature_statistics([[[[0.0]], [[1.0]], [[2.0]]], [[[2.0]], [[3.0]], [[4.0]]]])
    assert result["mean"] == [[[1.0]], [[2.0]], [[3.0]]]
    assert result["std"] == [[[1.0]], [[1.0]], [[1.0]]]
    validate_feature_statistics(result, expected_count=2)
    with pytest.raises(ReleasePlanError, match="feature shape"):
        numeric_feature_statistics([[[[0.0], [1.0]]], [[[2.0], [3.0]]]])


def test_video_sample_count_is_explicit_and_never_equated_with_frames() -> None:
    frame = [[[0.0]], [[1.0]], [[2.0]]]
    result = numeric_feature_statistics([frame, frame, frame, frame])
    assert result["count"] == [4]
    assert result["mean"] == [[[0.0]], [[1.0]], [[2.0]]]
    validate_feature_statistics(result, expected_count=4)
    with pytest.raises(ReleasePlanError, match="does not match"):
        validate_feature_statistics(result, expected_count=120)


def test_unequal_episode_counts_pool_mean_and_population_variance() -> None:
    first = numeric_feature_statistics([[0.0], [2.0]])
    second = numeric_feature_statistics([[4.0]])
    result = aggregate_feature_statistics([first, second], expected_counts=[2, 1])
    assert result.values["count"] == [3]
    assert result.values["mean"] == pytest.approx([2.0])
    assert result.values["std"] == pytest.approx([(8 / 3) ** 0.5])
    assert result.values["min"] == pytest.approx([0.0])
    assert result.values["max"] == pytest.approx([4.0])


def test_single_summary_preserves_its_values() -> None:
    summary = _episode_summary()
    result = aggregate_feature_statistics([summary], expected_counts=[4])
    for key in (*MOMENT_KEYS[:-1], *QUANTILE_KEYS):
        assert result.values[key] == pytest.approx(summary[key])
    assert result.values["count"] == [4]


def test_quantiles_are_labelled_approximate_and_never_fabricated() -> None:
    summary = _episode_summary()
    labelled = aggregate_feature_statistics([summary, summary], expected_counts=[4, 4])
    assert labelled.approximation is not None
    moments_only = {key: summary[key] for key in MOMENT_KEYS}
    result = aggregate_feature_statistics([moments_only, moments_only], expected_counts=[4, 4])
    assert result.approximation is None
    assert not set(QUANTILE_KEYS) & set(result.values)


def test_episode_summaries_must_share_one_key_set() -> None:
    summary = _episode_summary()
    moments_only = {key: summary[key] for key in MOMENT_KEYS}
    with pytest.raises(ReleasePlanError, match="share one statistic key set"):
        aggregate_feature_statistics([summary, moments_only], expected_counts=[4, 4])


def test_aggregation_rejects_count_and_structure_mismatches() -> None:
    summary = _episode_summary()
    with pytest.raises(ReleasePlanError, match="does not match"):
        aggregate_feature_statistics([summary], expected_counts=[5])
    with pytest.raises(ReleasePlanError, match="one count per summary"):
        aggregate_feature_statistics([summary], expected_counts=[4, 4])
    with pytest.raises(ReleasePlanError, match="at least one"):
        aggregate_feature_statistics([], expected_counts=[])


def test_ragged_non_numeric_and_nonfinite_feature_values_are_rejected() -> None:
    with pytest.raises(ReleasePlanError, match="rectangular"):
        numeric_feature_statistics([[1.0], [2.0, 3.0]])
    with pytest.raises(ReleasePlanError, match="numeric"):
        numeric_feature_statistics([["a"], ["b"]])
    with pytest.raises(ReleasePlanError, match="finite"):
        numeric_feature_statistics([[1.0], [float("inf")]])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("count", [2.0], "integers"),
        ("count", [True], "integers"),
        ("count", [0], "positive integer"),
        ("mean", [float("nan")], "must be finite"),
        ("std", [-1.0], "std must be nonnegative"),
        ("mean", [5.0], "must be ordered"),
        ("q10", [3.0], "nondecreasing"),
        ("min", "text", "must contain numbers"),
        ("mean", [2.0, 3.0], "share one shape"),
    ],
)
def test_malformed_episode_summaries_are_rejected(field: str, value: object, message: str) -> None:
    summary = {**_episode_summary(), field: value}
    with pytest.raises(ReleasePlanError, match=message):
        validate_feature_statistics(summary, expected_count=4)


def test_missing_or_partial_statistic_keys_are_rejected() -> None:
    summary = _episode_summary()
    without_std = {key: value for key, value in summary.items() if key != "std"}
    partial_quantiles = {key: value for key, value in summary.items() if key != "q01"}
    with pytest.raises(ReleasePlanError, match="statistic keys"):
        validate_feature_statistics(without_std, expected_count=4)
    with pytest.raises(ReleasePlanError, match="statistic keys"):
        validate_feature_statistics(partial_quantiles, expected_count=4)


def _solid_frames(values: list[int]) -> list:
    np = pytest.importorskip("numpy")
    return [np.full((16, 16, 3), value, dtype=np.uint8) for value in values]


def _encode_source(frames: list, fps: int = 10) -> bytes:
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    from fractions import Fraction
    from io import BytesIO

    buffer = BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = int(frames[0].shape[1])
        stream.height = int(frames[0].shape[0])
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "10"}
        for index, pixels in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(pixels), format="rgb24")
            frame = frame.reformat(format="yuv420p")
            frame.pts = index
            frame.time_base = Fraction(1, fps)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


def _decoded_arrays(data: bytes) -> list:
    av = pytest.importorskip("av")
    from io import BytesIO

    with av.open(BytesIO(data)) as container:
        return [frame.reformat(format="rgb24").to_ndarray() for frame in container.decode(video=0)]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("ep-0", 0.0, 4, ()), "at least one"),
        (("ep-0", 0.0, 4, (2, 1)), "increasing"),
        (("ep-0", 0.0, 4, (4,)), "outside"),
        (("ep-0", -0.1, 4, (0,)), "nonnegative"),
        (("", 0.0, 4, (0,)), "non-empty"),
        (("ep-0", 0.0, 4, [0]), "tuple"),
    ],
)
def test_encoding_window_contracts_are_rejected(args: tuple, message: str) -> None:
    from physical_ai_curation.release.encoding import EncodingWindow

    with pytest.raises(ReleasePlanError, match=message):
        EncodingWindow(*args)


@pytest.mark.parametrize("fps", [0, -1.0, float("nan"), True])
def test_invalid_fps_is_rejected_before_opening_media(fps: object) -> None:
    from io import BytesIO

    from physical_ai_curation.release.encoding import EncodingWindow, encode_windows

    sink = BytesIO()
    window = EncodingWindow("ep-0", 0.0, 2, (0,))
    with pytest.raises(ReleasePlanError, match="fps"):
        encode_windows(BytesIO(), sink, (window,), fps=fps)
    assert sink.getvalue() == b""


def test_empty_or_duplicate_selection_is_rejected_before_opening_media() -> None:
    from io import BytesIO

    from physical_ai_curation.release.encoding import EncodingWindow, encode_windows

    sink = BytesIO()
    duplicate = (EncodingWindow("ep-0", 0.0, 2, (0,)), EncodingWindow("ep-0", 0.2, 2, (0,)))
    with pytest.raises(ReleasePlanError, match="unique"):
        encode_windows(BytesIO(), sink, duplicate, fps=10)
    with pytest.raises(ReleasePlanError, match="at least one"):
        encode_windows(BytesIO(), sink, (), fps=10)
    assert sink.getvalue() == b""


def test_prefix_and_interior_frames_are_selected_and_read_back_contiguously() -> None:
    from io import BytesIO

    from physical_ai_curation.release.encoding import EncodingWindow, encode_windows

    source = _encode_source(_solid_frames([10 + 20 * index for index in range(12)]))
    source_means = [float(frame.mean()) for frame in _decoded_arrays(source)]
    windows = (
        EncodingWindow("ep-0", 0.3, 4, (0, 2, 3)),
        EncodingWindow("ep-1", 0.8, 3, (0, 1, 2)),
    )
    sink = BytesIO()
    result = encode_windows(BytesIO(source), sink, windows, fps=10)
    readback = [float(frame.mean()) for frame in _decoded_arrays(sink.getvalue())]
    expected = [source_means[index] for index in (3, 5, 6, 8, 9, 10)]

    assert len(readback) == 6
    assert readback == pytest.approx(expected, abs=5.0)
    assert [(window.output_from_frame, window.output_to_frame) for window in result.windows] == [(0, 3), (3, 6)]
    assert result.windows[1].output_from_s == pytest.approx(0.3)
    assert result.windows[0].statistics["count"] == [3]
    assert result.codec == "libx264"
    assert result.pix_fmt == "yuv420p"
    assert result.fps == 10.0


def test_horizontal_flip_reverses_the_pixel_pattern() -> None:
    from io import BytesIO

    from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow, encode_windows

    np = pytest.importorskip("numpy")
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    frame[:, :8] = 255
    source = _encode_source([frame, frame])
    sink = BytesIO()
    encode_windows(
        BytesIO(source),
        sink,
        (EncodingWindow("ep-0", 0.0, 2, (0,)),),
        fps=10,
        options=EncodeOptions(flip_horizontal=True),
    )
    flipped = _decoded_arrays(sink.getvalue())[0].mean(axis=2)

    assert flipped[:, 8:].mean() > flipped[:, :8].mean() + 100


def test_wrong_fps_and_short_media_are_refused() -> None:
    from io import BytesIO

    from physical_ai_curation.release.encoding import EncodingWindow, encode_windows
    from physical_ai_curation.release.media import ReleaseExecutionError

    source = _encode_source(_solid_frames([50] * 12))
    with pytest.raises(ReleaseExecutionError, match="fps"):
        encode_windows(BytesIO(source), BytesIO(), (EncodingWindow("ep-0", 0.0, 4, (0,)),), fps=20)
    with pytest.raises(ReleaseExecutionError, match="source ended"):
        encode_windows(BytesIO(source), BytesIO(), (EncodingWindow("ep-0", 0.0, 20, (0,)),), fps=10)


def test_processing_memory_budget_is_checked_before_decoding() -> None:
    from io import BytesIO

    from physical_ai_curation.release.encoding import EncodingWindow, encode_windows

    source = _encode_source(_solid_frames([50] * 4))
    with pytest.raises(ReleasePlanError, match="memory"):
        encode_windows(
            BytesIO(source),
            BytesIO(),
            (EncodingWindow("ep-0", 0.0, 2, (0,)),),
            fps=10,
            limits=ReleaseMediaLimits(processing_memory_bytes=1),
        )


def test_encoding_module_imports_without_optional_media_packages() -> None:
    code = textwrap.dedent(
        """
        import sys
        for name in ("av", "numpy"):
            sys.modules[name] = None
        from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow
        EncodingWindow("ep-0", 0.0, 2, (0,))
        EncodeOptions()
        print("ok")
        """
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
