"""Frozen saved-edit composition: removals compose, every other operation blocks."""

from __future__ import annotations

import pytest
from physical_ai_curation.release.canonical import canonical_json_bytes
from physical_ai_curation.release.composition import (
    REMOVED_FRAMES_REASON,
    compose_frame_removals,
    encoding_window,
    media_reasons,
    subset_episode_inputs,
)
from physical_ai_curation.release.media import ReleasePlanError, SubsetEpisodeInput, build_subset_timeline
from physical_ai_curation.release.models import FrozenInput, SavedResource

EP0 = "dataset-a/episode_000000"
EP1 = "dataset-a/episode_000001"


def _resource(resource_id: str, payload: object) -> SavedResource:
    return SavedResource(
        resource_id=resource_id,
        revision="edit-rev-1",
        value_json=canonical_json_bytes(payload).decode("utf-8"),
    )


def _frozen(*resources: SavedResource) -> FrozenInput:
    return FrozenInput(selected_episode_ids=(EP0, EP1), saved_resources=resources)


def _sources() -> dict[str, SubsetEpisodeInput]:
    return {
        EP0: SubsetEpisodeInput(episode_id=EP0, original_episode_index=0, length=6, fps=10.0),
        EP1: SubsetEpisodeInput(episode_id=EP1, original_episode_index=1, length=4, fps=10.0),
    }


def test_removed_frames_compose_into_subset_and_encoding_inputs() -> None:
    frozen = _frozen(_resource("edits/episode_000000", {"operations": {"removedFrames": [1, 4]}}))

    composition = compose_frame_removals(frozen)
    episodes = subset_episode_inputs(frozen, _sources(), composition)

    assert composition.submittable
    assert episodes[0].removed_frames == frozenset({1, 4})
    assert episodes[1].removed_frames == frozenset()
    window = encoding_window(episodes[0], from_timestamp=0.0)
    assert window.kept_frames == (0, 2, 3, 5)
    assert window.source_frame_count == 6
    assert media_reasons(composition, EP0) == (REMOVED_FRAMES_REASON,)
    assert media_reasons(composition, EP1) == ()


def test_top_level_edit_value_is_accepted_without_operations_wrapper() -> None:
    frozen = _frozen(_resource("edits/episode_000001", {"removedFrames": [0]}))

    composition = compose_frame_removals(frozen)

    assert composition.removed_for(EP1) == frozenset({0})


def test_subset_inputs_follow_frozen_selection_order() -> None:
    frozen = FrozenInput(selected_episode_ids=(EP1, EP0))

    episodes = subset_episode_inputs(frozen, _sources(), compose_frame_removals(frozen))

    assert [episode.episode_id for episode in episodes] == [EP1, EP0]


@pytest.mark.parametrize(
    ("operations", "fragment"),
    [
        ({"insertedFrames": [{"afterFrameIndex": 1}]}, "insertedFrames"),
        ({"globalTransform": {"crop": {"x": 0, "y": 0, "width": 2, "height": 2}}}, "globalTransform"),
        ({"cameraTransforms": {"front": {"resize": {"width": 2, "height": 2}}}}, "cameraTransforms"),
        ({"trajectoryAdjustments": [{"frameIndex": 0, "channelDeltas": {"0": 0.1}}]}, "trajectoryAdjustments"),
        ({"subtasks": []}, "subtasks"),
    ],
)
def test_unhandled_operations_block_instead_of_dropping(operations: dict, fragment: str) -> None:
    frozen = _frozen(_resource("edits/episode_000000", {"operations": {**operations, "removedFrames": [1]}}))

    composition = compose_frame_removals(frozen)

    assert not composition.submittable
    assert any(fragment in reason for reason in composition.blocking)
    with pytest.raises(ReleasePlanError, match="blocked"):
        subset_episode_inputs(frozen, _sources(), composition)


def test_empty_or_null_operations_are_not_edits() -> None:
    frozen = _frozen(
        _resource(
            "edits/episode_000000",
            {
                "operations": {
                    "globalTransform": None,
                    "insertedFrames": [],
                    "trajectoryAdjustments": [],
                    "subtasks": None,
                }
            },
        )
    )

    composition = compose_frame_removals(frozen)

    assert composition.submittable
    assert composition.removals == ()


def test_edit_for_unselected_episode_blocks() -> None:
    frozen = _frozen(_resource("edits/episode_000009", {"removedFrames": [0]}))

    composition = compose_frame_removals(frozen)

    assert not composition.submittable
    assert "unselected episode" in composition.blocking[0]


def test_non_edit_resources_are_not_composed() -> None:
    frozen = _frozen(_resource("annotation/episode_000000", {"annotations": [1]}))

    composition = compose_frame_removals(frozen)

    assert composition.submittable
    assert composition.removals == ()


def test_removed_frames_must_be_a_list_of_nonnegative_integers() -> None:
    for bad in ("1,2", [True], [-1], [1.0]):
        frozen = _frozen(_resource("edits/episode_000000", {"removedFrames": bad}))
        with pytest.raises(ReleasePlanError, match="removedFrames"):
            compose_frame_removals(frozen)


def test_repeated_removed_frame_is_refused() -> None:
    frozen = _frozen(_resource("edits/episode_000000", {"removedFrames": [2, 2]}))

    with pytest.raises(ReleasePlanError, match="repeat"):
        compose_frame_removals(frozen)


def test_out_of_range_removal_is_refused_by_subset_inputs() -> None:
    frozen = _frozen(_resource("edits/episode_000001", {"removedFrames": [4]}))

    composition = compose_frame_removals(frozen)

    with pytest.raises(ReleasePlanError):
        build_subset_timeline(subset_episode_inputs(frozen, _sources(), composition))


def test_removing_every_frame_is_refused() -> None:
    frozen = _frozen(_resource("edits/episode_000001", {"removedFrames": [0, 1, 2, 3]}))

    with pytest.raises(ReleasePlanError, match="empty"):
        subset_episode_inputs(frozen, _sources(), compose_frame_removals(frozen))


def test_missing_source_descriptor_is_refused() -> None:
    frozen = _frozen()

    with pytest.raises(ReleasePlanError, match="source descriptor"):
        subset_episode_inputs(frozen, {EP0: _sources()[EP0]}, compose_frame_removals(frozen))


def test_changed_saved_removal_changes_composition() -> None:
    first = compose_frame_removals(_frozen(_resource("edits/episode_000000", {"removedFrames": [1]})))
    second = compose_frame_removals(_frozen(_resource("edits/episode_000000", {"removedFrames": [2]})))

    assert first != second


def test_given_saved_viewer_descriptor_when_compose_then_identity_is_not_an_edit() -> None:
    frozen = _frozen(
        _resource(
            "edits/episode_000001",
            {
                "dataset_id": "dataset-a",
                "episode_index": 1,
                "operations": {
                    "datasetId": "dataset-a",
                    "episodeIndex": 1,
                    "removedFrames": [2],
                    "globalTransform": None,
                    "cameraTransforms": None,
                    "insertedFrames": None,
                    "subtasks": None,
                    "trajectoryAdjustments": None,
                },
            },
        )
    )

    composition = compose_frame_removals(frozen)

    assert composition.submittable
    assert composition.removed_for(EP1) == frozenset({2})


def test_given_null_removals_when_compose_then_no_frames_removed() -> None:
    frozen = _frozen(_resource("edits/episode_000000", {"operations": {"removedFrames": None}}))

    assert compose_frame_removals(frozen).submittable


@pytest.mark.parametrize("value", [None, False, [], {}])
def test_given_unknown_operation_when_compose_then_blocks_even_if_empty(value: object) -> None:
    frozen = _frozen(_resource("edits/episode_000000", {"operations": {"futureOperation": value}}))

    assert not compose_frame_removals(frozen).submittable


def test_given_saved_camera_filter_when_compose_then_filter_is_retained() -> None:
    frozen = _frozen(
        _resource(
            "edits/episode_000001",
            {"operations": {"cameraTransforms": {"wrist": {"colorFilter": "invert"}}}},
        )
    )

    composition = compose_frame_removals(frozen)

    assert composition.submittable
    assert composition.filter_for(EP1, "wrist") == "invert"
    assert composition.filter_for(EP1, "front") == "none"


def _generation_case() -> tuple:
    from physical_ai_curation.release.canonical import serialize_model, sha256_hex
    from physical_ai_curation.release.media import MediaWindow, ObjectPlanRequest, StorageEndpoint
    from physical_ai_curation.release.models import SourceObject, VersionIdentity

    video = SourceObject(path="videos/front/chunk-000/file-000.mp4", revision="v1", length=100, sha256="a" * 64)
    frozen = FrozenInput(
        selected_episode_ids=(EP1,),
        source_objects=(video,),
        saved_resources=(_resource("edits/episode_000001", {"removedFrames": [2]}),),
    )
    local = StorageEndpoint(alias="local", kind="local", volume="same")
    request = ObjectPlanRequest(
        source=local,
        destination=local,
        scratch=local,
        target_format=VersionIdentity(name="lerobot", version="v3.0"),
        frozen_input_sha256=sha256_hex(serialize_model(frozen)),
        selected_episode_ids=(EP1,),
        windows=(MediaWindow(EP1, "front", video, 0.0, 0.4, 0.4),),
        tool=VersionIdentity(name="pyav-libx264", version="test"),
        source_objects=(video,),
    )
    return frozen, request, {EP1: _sources()[EP1]}


def test_given_frozen_removal_when_prepare_then_existing_object_plan_requires_encoding() -> None:
    from physical_ai_curation.release.composition import prepare_saved_generation

    frozen, request, sources = _generation_case()

    prepared = prepare_saved_generation(frozen, request, sources)

    assert prepared.plan.objects[0].action == "transcode"
    assert prepared.episodes[0].removed_frames == frozenset({2})
    assert prepared.encoding_windows(prepared.plan.objects[0])[0].kept_frames == (0, 1, 3)


def test_given_reversed_selection_when_shared_video_then_encoding_uses_output_order() -> None:
    from dataclasses import replace

    from physical_ai_curation.release.canonical import serialize_model, sha256_hex
    from physical_ai_curation.release.composition import prepare_saved_generation

    frozen, request, _ = _generation_case()
    frozen = frozen.model_copy(update={"selected_episode_ids": (EP1, EP0)})
    window = request.windows[0]
    request = replace(
        request,
        frozen_input_sha256=sha256_hex(serialize_model(frozen)),
        selected_episode_ids=frozen.selected_episode_ids,
        windows=(
            replace(window, episode_id=EP0, from_timestamp=0.0, to_timestamp=0.6, object_duration_s=1.0),
            replace(window, from_timestamp=0.6, to_timestamp=1.0, object_duration_s=1.0),
        ),
    )

    prepared = prepare_saved_generation(frozen, request, _sources())

    assert [window.episode_id for window in prepared.encoding_windows(prepared.plan.objects[0])] == [EP1, EP0]


@pytest.mark.parametrize("field", ["frozen_input_sha256", "selected_episode_ids", "source_objects"])
def test_given_mismatched_frozen_binding_when_prepare_then_refused(field: str) -> None:
    from dataclasses import replace

    from physical_ai_curation.release.composition import prepare_saved_generation

    frozen, request, sources = _generation_case()
    value = "0" * 64 if field == "frozen_input_sha256" else ()

    with pytest.raises(ReleasePlanError, match="match frozen"):
        prepare_saved_generation(frozen, replace(request, **{field: value}), sources)


def test_given_changed_encoder_settings_when_regenerated_then_confirmation_is_stale() -> None:
    from physical_ai_curation.release.composition import prepare_saved_generation
    from physical_ai_curation.release.encoding import EncodeOptions
    from physical_ai_curation.release.media import validate_processing_confirmation

    frozen, request, sources = _generation_case()
    original = prepare_saved_generation(frozen, request, sources)
    changed = prepare_saved_generation(frozen, request, sources, encode_options=EncodeOptions(crf=18))

    with pytest.raises(ReleasePlanError, match="stale"):
        validate_processing_confirmation(changed.plan, confirmed_digest=original.plan.digest)


@pytest.mark.parametrize(
    "payload",
    [
        {"dataset_id": "other", "operations": {}},
        {"episode_index": 0, "operations": {}},
        {"operations": {"datasetId": "other"}},
        {"operations": {"episodeIndex": 0}},
        {"operations": {"episodeIndex": True}},
    ],
)
def test_given_mismatched_saved_identity_when_compose_then_refused(payload: dict) -> None:
    frozen = _frozen(_resource("edits/episode_000001", payload))

    with pytest.raises(ReleasePlanError, match="identity"):
        compose_frame_removals(frozen)


def test_given_unknown_saved_camera_when_prepare_then_refused() -> None:
    from dataclasses import replace

    from physical_ai_curation.release.canonical import serialize_model, sha256_hex
    from physical_ai_curation.release.composition import prepare_saved_generation

    frozen, request, sources = _generation_case()
    frozen = frozen.model_copy(
        update={
            "saved_resources": (
                _resource("edits/episode_000001", {"cameraTransforms": {"missing": {"colorFilter": "invert"}}}),
            ),
        }
    )
    request = replace(request, frozen_input_sha256=sha256_hex(serialize_model(frozen)))

    with pytest.raises(ReleasePlanError, match="unknown camera"):
        prepare_saved_generation(frozen, request, sources)


def test_given_copy_camera_without_statistics_when_prepare_then_warned_and_submittable() -> None:
    from dataclasses import replace

    from physical_ai_curation.release.canonical import serialize_model, sha256_hex
    from physical_ai_curation.release.composition import prepare_saved_generation

    frozen, request, sources = _generation_case()
    frozen = frozen.model_copy(update={"saved_resources": ()})
    request = replace(request, frozen_input_sha256=sha256_hex(serialize_model(frozen)))

    prepared = prepare_saved_generation(frozen, request, sources)

    assert prepared.plan.submittable
    assert prepared.plan.blocking == ()
    assert any("statistics unavailable for front" in warning for warning in prepared.plan.warnings)
    assert all(obj.action == "copy" for obj in prepared.plan.objects)


def test_given_unconfirmed_generation_when_assemble_then_refused_before_optional_runtime() -> None:
    from physical_ai_curation.release.composition import prepare_saved_generation
    from physical_ai_curation.release.tables import generate_native_selection

    frozen, request, sources = _generation_case()
    prepared = prepare_saved_generation(frozen, request, sources)

    with pytest.raises(ReleasePlanError, match="confirmation"):
        generate_native_selection(prepared, {}, {}, None, {}, confirmed_digest="0" * 64)
