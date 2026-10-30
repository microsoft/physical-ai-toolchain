"""Compose frozen saved removals and color filters into the existing object planner.

Unsupported saved effects block preview. This module reads no storage and publishes nothing;
execution must revalidate frozen inputs before using a regenerated confirmed plan.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json, serialize_model, sha256_hex
from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow
from physical_ai_curation.release.media import (
    ObjectPlan,
    ObjectPlanRequest,
    ReleaseObjectPlan,
    ReleasePlanError,
    SubsetEpisodeInput,
    plan_release_objects,
    subset_episode_lengths,
)
from physical_ai_curation.release.models import FrozenInput
from physical_ai_curation.release.statistics import validate_feature_statistics

EDIT_RESOURCE = re.compile(r"^edits/(episode_\d{6})$")
REMOVED_FRAMES_REASON = "removed_frames"
_COMPOSED_KEYS = frozenset({"removedFrames", "globalTransform", "cameraTransforms"})
_IDENTITY_KEYS = frozenset({"datasetId", "episodeIndex"})
_UNSUPPORTED_KEYS = frozenset(
    {"globalTransform", "cameraTransforms", "insertedFrames", "subtasks", "trajectoryAdjustments"}
)


@dataclass(frozen=True, slots=True)
class FrameRemoval:
    episode_id: str
    removed_frames: frozenset[int]


@dataclass(frozen=True, slots=True)
class CameraFilter:
    episode_id: str
    camera: str | None
    kind: str


@dataclass(frozen=True, slots=True)
class EditComposition:
    removals: tuple[FrameRemoval, ...]
    blocking: tuple[str, ...]
    filters: tuple[CameraFilter, ...] = ()

    @property
    def submittable(self) -> bool:
        return not self.blocking

    def removed_for(self, episode_id: str) -> frozenset[int]:
        for removal in self.removals:
            if removal.episode_id == episode_id:
                return removal.removed_frames
        return frozenset()

    def filter_for(self, episode_id: str, camera: str) -> str:
        choices = {item.camera: item.kind for item in self.filters if item.episode_id == episode_id}
        return choices.get(camera, choices.get(None, "none"))


def _filters(operations: dict[str, Any], episode_id: str) -> tuple[list[CameraFilter], list[str]]:
    result = []
    blocking = []
    cameras = operations.get("cameraTransforms") or {}
    if not isinstance(cameras, dict):
        raise ReleasePlanError("cameraTransforms must be a mapping")
    global_transform = operations.get("globalTransform")
    if global_transform and cameras:
        return [], ["combined globalTransform and cameraTransforms are not supported"]
    transforms = [(None, global_transform), *cameras.items()]
    for camera, transform in transforms:
        if transform is None:
            continue
        if not isinstance(transform, dict) or (camera is not None and (not isinstance(camera, str) or not camera)):
            raise ReleasePlanError("saved image transform must be a camera-keyed object")
        name = "globalTransform" if camera is None else "cameraTransforms"
        if set(transform) - {"crop", "resize", "colorAdjustment", "colorFilter"} or any(
            transform.get(key) is not None for key in ("crop", "resize", "colorAdjustment")
        ):
            blocking.append(f"{name}: only saved colorFilter transforms are supported")
            continue
        kind = transform.get("colorFilter") or "none"
        if kind not in ("none", "invert", "grayscale"):
            blocking.append(f"{name}: unsupported saved colorFilter")
            continue
        result.append(CameraFilter(episode_id=episode_id, camera=camera, kind=kind))
    return result, blocking


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

    Empty subtask lists replace recorded subtasks and cannot be ignored.
    """
    if key in _COMPOSED_KEYS | _IDENTITY_KEYS:
        return False
    if key not in _UNSUPPORTED_KEYS:
        return True
    if key == "subtasks":
        return value is not None
    return not (value is None or value is False or value == [] or value == {})


def compose_frame_removals(frozen: FrozenInput) -> EditComposition:
    """Read frozen edit resources and report every operation this release cannot compose."""
    selected = {_episode_key(episode_id): episode_id for episode_id in frozen.selected_episode_ids}
    if len(selected) != len(frozen.selected_episode_ids):
        raise ReleasePlanError("selected episode keys must be unambiguous")
    removals = []
    blocking = []
    filters = []
    for resource in frozen.saved_resources:
        match = EDIT_RESOURCE.fullmatch(resource.resource_id)
        if match is None:
            if resource.resource_id.startswith("edits/"):
                blocking.append("saved edit resource has an unsupported identity")
            continue
        key = match.group(1)
        if key not in selected:
            blocking.append(f"{key}: saved edits target an unselected episode")
            continue
        episode_id = selected[key]
        payload = parse_strict_json(resource.value_json.encode("utf-8"))
        operations = _operations(payload, key)
        dataset_id = episode_id.rsplit("/", 1)[0]
        episode_index = int(key.removeprefix("episode_"))
        for values, dataset_field, episode_field in (
            (payload, "dataset_id", "episode_index"),
            (operations, "datasetId", "episodeIndex"),
        ):
            if dataset_field in values and values[dataset_field] != dataset_id:
                raise ReleasePlanError("saved edit dataset identity does not match selection")
            if episode_field in values and (
                type(values[episode_field]) is not int or values[episode_field] != episode_index
            ):
                raise ReleasePlanError("saved edit episode identity does not match selection")
        for name in sorted(operations):
            if _unsupported(name, operations[name]):
                blocking.append(f"{key}: saved {name} edits are not composed for release")
        selected_filters, unsupported_filters = _filters(operations, episode_id)
        filters.extend(selected_filters)
        blocking.extend(f"{key}: {reason}" for reason in unsupported_filters)
        if operations.get("removedFrames") is not None:
            frames = _parse_removed(operations["removedFrames"], key)
            if frames:
                removals.append(FrameRemoval(episode_id=episode_id, removed_frames=frames))
    return EditComposition(removals=tuple(removals), blocking=tuple(blocking), filters=tuple(filters))


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
    if composition != compose_frame_removals(frozen):
        raise ReleasePlanError("release composition does not match frozen saved inputs")
    episodes = []
    for episode_id in frozen.selected_episode_ids:
        source = sources.get(episode_id)
        if source is None:
            raise ReleasePlanError("selected episode has no source descriptor")
        if (
            source.episode_id != episode_id
            or _episode_key(episode_id) != f"episode_{source.original_episode_index:06d}"
        ):
            raise ReleasePlanError("source descriptor episode identity does not match selection")
        if source.removed_frames:
            raise ReleasePlanError("source descriptor must describe unedited recorded frames")
        episodes.append(replace(source, episode_id=episode_id, removed_frames=composition.removed_for(episode_id)))
    subset_episode_lengths(episodes)
    return tuple(episodes)


def encoding_window(
    episode: SubsetEpisodeInput, from_timestamp: float, *, color_filter: str = "none"
) -> EncodingWindow:
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
        color_filter=color_filter,
    )


def media_reasons(composition: EditComposition, episode_id: str) -> tuple[str, ...]:
    """Processing reasons a camera window of this episode must carry in the object plan."""
    return (REMOVED_FRAMES_REASON,) if composition.removed_for(episode_id) else ()


@dataclass(frozen=True, slots=True)
class PreparedMediaWindow:
    source_path: str
    camera: str
    window: EncodingWindow


@dataclass(frozen=True, slots=True)
class NativeGenerationPlan:
    """Bound native component inputs; not durable admission or publication authority."""

    plan: ReleaseObjectPlan
    episodes: tuple[SubsetEpisodeInput, ...]
    windows: tuple[PreparedMediaWindow, ...]
    encode_options: EncodeOptions
    camera_statistics_json: str = "{}"

    def encoding_windows(self, obj: ObjectPlan) -> tuple[EncodingWindow, ...]:
        if obj not in self.plan.objects or obj.action != "transcode":
            raise ReleasePlanError("encoding requires a transcode object from this generation plan")
        indexed = {(item.source_path, item.window.episode_id, item.camera): item.window for item in self.windows}
        order = {episode.episode_id: index for index, episode in enumerate(self.episodes)}
        windows = sorted(obj.windows, key=lambda window: (order[window.episode_id], window.camera))
        return tuple(indexed[obj.source_path, window.episode_id, window.camera] for window in windows)


def prepare_saved_generation(
    frozen: FrozenInput,
    request: ObjectPlanRequest,
    sources: Mapping[str, SubsetEpisodeInput],
    *,
    encode_options: EncodeOptions | None = None,
    camera_statistics: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
) -> NativeGenerationPlan:
    """Bind frozen saved effects and component settings to a regenerated object plan.

    Source descriptors and camera windows must come from condition-bound format metadata.
    Before dispatch the caller revalidates storage/saved input identities, regenerates this
    value, and passes its plan to ``validate_processing_confirmation``. No reads or effects
    occur here; publishers and supervised workers enforce their own storage/runtime limits.
    """
    if request.frozen_input_sha256 != sha256_hex(serialize_model(frozen)):
        raise ReleasePlanError("object request does not match frozen input identity")
    if request.selected_episode_ids != frozen.selected_episode_ids:
        raise ReleasePlanError("object request does not match frozen episode order")
    if {item.path: item for item in request.source_objects} != {item.path: item for item in frozen.source_objects}:
        raise ReleasePlanError("object request does not match frozen source conditions")
    composition = compose_frame_removals(frozen)
    episodes = subset_episode_inputs(frozen, sources, composition)
    indexed = {episode.episode_id: episode for episode in episodes}
    known_cameras = {(window.episode_id, window.camera) for window in request.windows}
    if any(
        item.camera is not None and (item.episode_id, item.camera) not in known_cameras for item in composition.filters
    ):
        raise ReleasePlanError("saved transform names an unknown camera")
    settings = encode_options or EncodeOptions()
    media = []
    prepared = []
    rates = {}
    for window in request.windows:
        episode = indexed.get(window.episode_id)
        if episode is None:
            raise ReleasePlanError("camera window targets an unselected episode")
        if not math.isclose(window.to_timestamp - window.from_timestamp, episode.length / episode.fps, abs_tol=1e-4):
            raise ReleasePlanError("camera window does not match the recorded episode length")
        previous_rate = rates.setdefault(window.source.path, episode.fps)
        if previous_rate != episode.fps:
            raise ReleasePlanError("one video object cannot mix episode frame rates")
        color_filter = composition.filter_for(episode.episode_id, window.camera)
        reasons = (*window.processing_reasons, *media_reasons(composition, episode.episode_id))
        if color_filter != "none":
            reasons = (*reasons, f"color_filter:{color_filter}")
        media.append(replace(window, processing_reasons=tuple(sorted(set(reasons)))))
        prepared.append(
            PreparedMediaWindow(
                source_path=window.source.path,
                camera=window.camera,
                window=encoding_window(episode, window.from_timestamp, color_filter=color_filter),
            )
        )
    bound_settings = {
        "base": parse_strict_json(request.tool_settings_json.encode("utf-8")),
        "composer": "saved-removals-color-filters-v1",
        "encoding": asdict(settings),
        "episodes": [asdict(episode) | {"removed_frames": sorted(episode.removed_frames)} for episode in episodes],
    }
    bound_request = replace(
        request, windows=tuple(media), tool_settings_json=canonical_json_bytes(bound_settings).decode("utf-8")
    )
    classified = plan_release_objects(bound_request)
    preserved = {}
    missing = set(request.statistics_requirements)
    for obj in classified.objects:
        if obj.action != "copy":
            continue
        for window in obj.windows:
            summary = (camera_statistics or {}).get(window.episode_id, {}).get(window.camera)
            if summary is None:
                missing.add(window.camera)
                continue
            validate_feature_statistics(summary, expected_count=None)
            preserved.setdefault(window.episode_id, {})[window.camera] = dict(summary)
    bound_settings["copied_camera_statistics"] = preserved
    plan = plan_release_objects(
        replace(
            bound_request,
            statistics_requirements=tuple(sorted(missing)),
            tool_settings_json=canonical_json_bytes(bound_settings).decode("utf-8"),
        )
    )
    return NativeGenerationPlan(
        plan=plan,
        episodes=episodes,
        windows=tuple(prepared),
        encode_options=settings,
        camera_statistics_json=canonical_json_bytes(preserved).decode("utf-8"),
    )
