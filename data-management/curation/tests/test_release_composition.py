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

    episodes = subset_episode_inputs(frozen, _sources(), compose_frame_removals(frozen))

    with pytest.raises(ReleasePlanError, match="empty"):
        encoding_window(episodes[1], from_timestamp=0.0)


def test_missing_source_descriptor_is_refused() -> None:
    frozen = _frozen()

    with pytest.raises(ReleasePlanError, match="source descriptor"):
        subset_episode_inputs(frozen, {EP0: _sources()[EP0]}, compose_frame_removals(frozen))


def test_changed_saved_removal_changes_composition() -> None:
    first = compose_frame_removals(_frozen(_resource("edits/episode_000000", {"removedFrames": [1]})))
    second = compose_frame_removals(_frozen(_resource("edits/episode_000000", {"removedFrames": [2]})))

    assert first != second
