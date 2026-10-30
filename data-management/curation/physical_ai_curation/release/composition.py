"""Compose frozen saved frame removals into native subset and encoding inputs.

Only removed frames are composed here. Every other saved operation blocks the release
with a named reason; it is never dropped silently. Inputs come from a frozen selection,
so later saved edits cannot change the result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from physical_ai_curation.release.canonical import parse_strict_json
from physical_ai_curation.release.encoding import EncodingWindow
from physical_ai_curation.release.media import ReleasePlanError, SubsetEpisodeInput
from physical_ai_curation.release.models import FrozenInput

EDIT_RESOURCE = re.compile(r"^edits/(episode_\d{6})$")
REMOVED_FRAMES_REASON = "removed_frames"
_COMPOSED_KEYS = frozenset({"removedFrames"})


@dataclass(frozen=True, slots=True)
class FrameRemoval:
    episode_id: str
    removed_frames: frozenset[int]


@dataclass(frozen=True, slots=True)
class EditComposition:
    removals: tuple[FrameRemoval, ...]
    blocking: tuple[str, ...]

    @property
    def submittable(self) -> bool:
        return not self.blocking

    def removed_for(self, episode_id: str) -> frozenset[int]:
        for removal in self.removals:
            if removal.episode_id == episode_id:
                return removal.removed_frames
        return frozenset()


def _episode_key(episode_id: str) -> str:
    return episode_id.rsplit("/", 1)[-1]


def _parse_removed(value: Any, episode: str) -> frozenset[int]:
    if not isinstance(value, list):
        raise ReleasePlanError(f"{episode}: removedFrames must be a list of frame indices")
    frames = []
    for frame in value:
        if type(frame) is not int or frame < 0:
            raise ReleasePlanError(f"{episode}: removedFrames must contain nonnegative integers")
        frames.append(frame)
    if len(set(frames)) != len(frames):
        raise ReleasePlanError(f"{episode}: removedFrames must not repeat a frame index")
    return frozenset(frames)


def _operations(value: Any, episode: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleasePlanError(f"{episode}: saved edit value must be a JSON object")
    operations = value.get("operations", value)
    if not isinstance(operations, dict):
        raise ReleasePlanError(f"{episode}: saved edit operations must be a JSON object")
    return operations


def _unsupported(key: str, value: Any) -> bool:
    """Return True when a saved operation changes output that this composer does not handle.

    The viewer treats null, false, and empty values as no edit. Subtask lists are the
    exception: an empty list replaces recorded subtasks, so any non-null value counts.
    """
    if key in _COMPOSED_KEYS:
        return False
    if key == "subtasks":
        return value is not None
    return not (value is None or value is False or value == [] or value == {})


def compose_frame_removals(frozen: FrozenInput) -> EditComposition:
    """Read frozen edit resources and report every operation this release cannot compose."""
    selected = {_episode_key(episode_id): episode_id for episode_id in frozen.selected_episode_ids}
    removals: list[FrameRemoval] = []
    blocking: list[str] = []
    for resource in frozen.saved_resources:
        match = EDIT_RESOURCE.fullmatch(resource.resource_id)
        if match is None:
            continue
        key = match.group(1)
        if key not in selected:
            blocking.append(f"{key}: saved edits target an unselected episode")
            continue
        episode_id = selected[key]
        operations = _operations(parse_strict_json(resource.value_json.encode("utf-8")), key)
        for name in sorted(operations):
            if _unsupported(name, operations[name]):
                blocking.append(f"{key}: saved {name} edits are not composed for release")
        if "removedFrames" in operations:
            frames = _parse_removed(operations["removedFrames"], key)
            if frames:
                removals.append(FrameRemoval(episode_id=episode_id, removed_frames=frames))
    return EditComposition(removals=tuple(removals), blocking=tuple(blocking))


def subset_episode_inputs(
    frozen: FrozenInput,
    sources: Mapping[str, SubsetEpisodeInput],
    composition: EditComposition,
) -> tuple[SubsetEpisodeInput, ...]:
    """Return one subset input per frozen episode, in frozen selection order.

    The source descriptors carry original index, length and fps; their removal sets are
    replaced by the frozen removals. Blocked compositions are never converted.
    """
    if not composition.submittable:
        raise ReleasePlanError("release composition is blocked: " + "; ".join(composition.blocking))
    episodes = []
    for episode_id in frozen.selected_episode_ids:
        source = sources.get(episode_id)
        if source is None:
            raise ReleasePlanError("selected episode has no source descriptor")
        episodes.append(replace(source, episode_id=episode_id, removed_frames=composition.removed_for(episode_id)))
    return tuple(episodes)


def encoding_window(episode: SubsetEpisodeInput, from_timestamp: float) -> EncodingWindow:
    """Describe one episode's source frames with the removed frames excluded from encoding."""
    removed = episode.removed_frames
    kept = tuple(index for index in range(episode.length) if index not in removed)
    if not kept:
        raise ReleasePlanError("episode would be empty after frame removal")
    return EncodingWindow(
        episode_id=episode.episode_id,
        from_timestamp=from_timestamp,
        source_frame_count=episode.length,
        kept_frames=kept,
    )


def media_reasons(composition: EditComposition, episode_id: str) -> tuple[str, ...]:
    """Processing reasons a camera window of this episode must carry in the object plan."""
    return (REMOVED_FRAMES_REASON,) if composition.removed_for(episode_id) else ()
