"""Planning refuses ambiguous media dependencies and invalid resource observations."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from physical_ai_curation.release.media import (
    MetadataSpec,
    ReleaseExecutionError,
    ReleaseMediaLimits,
    ReleasePlanError,
    StorageEndpoint,
    execute_exact_copy,
    plan_release_objects,
)
from test_release_media import LOCAL_SCRATCH_SAME, VIDEO, _exact_copy_plan, _request, _source, _window


@pytest.mark.parametrize(
    "options",
    [
        {"alias": "", "kind": "local", "volume": "volume"},
        {"alias": "test", "kind": "unknown"},
        {"alias": "test", "kind": "blob"},
        {"alias": "test", "kind": "local"},
    ],
)
def test_storage_endpoint_requires_complete_route_identity(options: dict[str, object]) -> None:
    with pytest.raises(ReleasePlanError):
        StorageEndpoint(**options)


@pytest.mark.parametrize("field", ["processing_memory_bytes", "reserve_bytes", "transcode_output_percent"])
def test_media_limits_refuse_booleans_instead_of_treating_them_as_integers(field: str) -> None:
    with pytest.raises(ReleasePlanError, match="must be integers"):
        ReleaseMediaLimits(**{field: True})


@pytest.mark.parametrize("selection", [(), ("",), ("ep-0", "ep-0")])
def test_media_plan_requires_nonempty_unique_selection(selection: tuple[str, ...]) -> None:
    with pytest.raises(ReleasePlanError, match=r"selection|identifiers"):
        plan_release_objects(_request(selected_episode_ids=selection))


@pytest.mark.parametrize(
    ("window_changes", "message"),
    [
        ({"object_duration_s": 0.0}, "duration must be positive"),
        ({"object_duration_s": float("inf")}, "finite"),
        ({"processing_reasons": ("",)}, "reasons must be non-empty"),
        ({"camera": ""}, "camera must be non-empty"),
    ],
)
def test_media_plan_refuses_invalid_window_contract(window_changes: dict[str, object], message: str) -> None:
    with pytest.raises(ReleasePlanError, match=message):
        plan_release_objects(_request(windows=(replace(_window(), **window_changes),)))


def test_shared_object_must_have_one_duration_across_camera_windows() -> None:
    with pytest.raises(ReleasePlanError, match="contradictory source duration"):
        plan_release_objects(_request(windows=(_window(), _window(camera="wrist", duration=11.0))))


@pytest.mark.parametrize("damage", ["native_video_copy", "missing_copy", "negative_estimate", "bool_estimate"])
def test_media_plan_requires_explicit_condition_bound_dependencies(damage: str) -> None:
    if damage == "native_video_copy":
        request = _request(windows=(), copy_paths=(VIDEO,), source_objects=(_source(),))
    elif damage == "missing_copy":
        request = _request(windows=(), copy_paths=("data/missing.parquet",))
    else:
        request = _request(
            metadata=(MetadataSpec("meta/info.json", (), estimated_bytes=True if damage == "bool_estimate" else -1),)
        )

    with pytest.raises(ReleasePlanError, match=r"camera windows|known source|estimate"):
        plan_release_objects(request)


@pytest.mark.parametrize("field", ["destination_available_bytes", "scratch_available_bytes"])
@pytest.mark.parametrize("value", [True, -1, 0.5])
def test_capacity_observations_require_nonnegative_integer_bytes(field: str, value: object) -> None:
    with pytest.raises(ReleasePlanError, match="nonnegative integer"):
        plan_release_objects(_request(**{field: value}))


@pytest.mark.parametrize("mapping", [((VIDEO, "one.mp4"), (VIDEO, "two.mp4")), (("unplanned.mp4", "one.mp4"),)])
def test_media_output_mapping_must_be_unique_and_planned(mapping: tuple[tuple[str, str], ...]) -> None:
    with pytest.raises(ReleasePlanError, match=r"duplicate output mapping|unplanned source"):
        plan_release_objects(_request(output_paths=mapping))


def test_shared_volume_missing_capacity_is_disclosed_without_fabricating_shortage() -> None:
    plan = plan_release_objects(_request(windows=(_window(reasons=("invert",)),), scratch=LOCAL_SCRATCH_SAME))

    assert plan.submittable
    assert plan.capacity.shared_volume
    assert "shared-volume free space not observed" in plan.warnings


@pytest.mark.parametrize("chunk_size", [0, -1, True, 4 * 1024**2 + 1])
def test_exact_copy_validates_buffer_before_creating_output(tmp_path: Path, chunk_size: object) -> None:
    obj, _ = _exact_copy_plan(b"payload")
    destination = tmp_path / "output"

    with pytest.raises(ReleasePlanError, match="chunk size"):
        execute_exact_copy(obj, tmp_path / "missing", destination, chunk_size=chunk_size)

    assert not destination.exists()


def test_exact_copy_requires_full_content_identity_before_opening_source(tmp_path: Path) -> None:
    obj, _ = _exact_copy_plan(b"payload")
    obj = replace(obj, source_sha256=None)

    with pytest.raises(ReleasePlanError, match="bound source identity"):
        execute_exact_copy(obj, tmp_path / "missing", tmp_path / "output")


def test_exact_copy_refuses_missing_source_root_without_creating_output(tmp_path: Path) -> None:
    obj, _ = _exact_copy_plan(b"payload")

    with pytest.raises(ReleaseExecutionError, match="roots are unavailable"):
        execute_exact_copy(obj, tmp_path / "missing", tmp_path / "output")

    assert not (tmp_path / "output").exists()
