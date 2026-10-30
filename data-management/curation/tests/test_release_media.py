"""Deterministic release object planning, route refusal, capacity, and digest semantics."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
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
from physical_ai_curation.release.models import PreviewAction, SourceObject, VersionIdentity
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
