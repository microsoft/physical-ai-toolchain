"""Plan per-object copy, generated metadata, and necessary processing for a release.

Planning is pure: it opens no media, contacts no storage, and imports no decoder or encoder.
Route, target, destination, and capacity decisions are made here so that preview and
submission share one digest. Free-space observations are advisory until durable admission.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from physical_ai_curation.release.canonical import (
    canonical_json_bytes,
    normalize_release_path,
    parse_strict_json,
    sha256_hex,
)
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import PreviewAction, SourceObject, VersionIdentity
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore

TARGET_FORMAT_NAME = "lerobot"
TARGET_FORMAT_VERSION = "v3.0"
ROUTE_LOCAL = "local-to-local"
ROUTE_BLOB_SAME_ACCOUNT = "blob-same-account"
EXACT_COPY_TOOL = "exact-copy"
METADATA_TOOL = "generate-metadata"
PLAN_SCHEMA_VERSION = "1.0.0"
RESERVED_NAMESPACES = frozenset({"release", "evidence"})
ENV_PROCESSING_MEMORY = "CURATION_RELEASE_PROCESSING_MEMORY_BYTES"
ENV_RESERVE = "CURATION_RELEASE_RESERVE_BYTES"
ENV_TRANSCODE_PERCENT = "CURATION_RELEASE_TRANSCODE_OUTPUT_PERCENT"

_DEFAULT_PROCESSING_MEMORY_BYTES = 2 * 1024**3
_DEFAULT_RESERVE_BYTES = 1024**3
_DEFAULT_TRANSCODE_OUTPUT_PERCENT = 100
_LOCATOR_MARKERS = ("://", "?", "#", "sig=", "@")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOLERANCE_S = 1e-6

ObjectAction = Literal["copy", "transcode", "generate_metadata"]


class ReleasePlanError(ReleaseVerificationError):
    """A release planning request violates a rule before any effect."""


class UnsupportedRouteError(ReleasePlanError):
    """The route, destination, or target format is not supported."""


@dataclass(frozen=True, slots=True)
class ReleaseMediaLimits:
    processing_memory_bytes: int = _DEFAULT_PROCESSING_MEMORY_BYTES
    reserve_bytes: int = _DEFAULT_RESERVE_BYTES
    transcode_output_percent: int = _DEFAULT_TRANSCODE_OUTPUT_PERCENT

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (self.processing_memory_bytes, self.reserve_bytes, self.transcode_output_percent)
        ):
            raise ReleasePlanError("release limits must be integers")
        if self.processing_memory_bytes <= 0:
            raise ReleasePlanError("processing memory limit must be a positive integer")
        if self.reserve_bytes < 0:
            raise ReleasePlanError("capacity reserve must not be negative")
        if self.transcode_output_percent < 1:
            raise ReleasePlanError("transcode output percent must be positive")


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ReleasePlanError(f"{name} must be an integer") from exc


def load_media_limits(environ: Mapping[str, str]) -> ReleaseMediaLimits:
    return ReleaseMediaLimits(
        processing_memory_bytes=_env_int(environ, ENV_PROCESSING_MEMORY, _DEFAULT_PROCESSING_MEMORY_BYTES),
        reserve_bytes=_env_int(environ, ENV_RESERVE, _DEFAULT_RESERVE_BYTES),
        transcode_output_percent=_env_int(environ, ENV_TRANSCODE_PERCENT, _DEFAULT_TRANSCODE_OUTPUT_PERCENT),
    )


def _check_locator(value: str, name: str) -> str:
    if any(marker in value for marker in _LOCATOR_MARKERS):
        raise ReleasePlanError(f"{name} must not carry locator or credential material")
    return value


def _check_object_path(value: str, name: str) -> str:
    _check_locator(value, name)
    try:
        return normalize_release_path(value)
    except ReleaseVerificationError:
        raise ReleasePlanError(f"{name} is not a safe relative path") from None


def _check_output_path(value: str, name: str) -> str:
    path = _check_object_path(value, name)
    if path.split("/", 1)[0] in RESERVED_NAMESPACES:
        raise ReleasePlanError(f"{name} collides with a reserved release namespace")
    return path


@dataclass(frozen=True, slots=True)
class StorageEndpoint:
    alias: str
    kind: Literal["local", "blob"]
    account: str | None = None
    volume: str | None = None
    prefix: str = ""

    def __post_init__(self) -> None:
        if not self.alias:
            raise ReleasePlanError("storage alias is required")
        if self.kind not in ("local", "blob"):
            raise ReleasePlanError("storage kind must be local or blob")
        if self.kind == "blob" and not self.account:
            raise ReleasePlanError("blob endpoints require an account identity")
        if self.kind == "local" and not self.volume:
            raise ReleasePlanError("local endpoints require a volume identity")
        prefix = _check_output_path(self.prefix, "destination prefix") if self.prefix else ""
        object.__setattr__(self, "prefix", prefix)


def resolve_route(source: StorageEndpoint, destination: StorageEndpoint) -> str:
    if source.kind == "local" and destination.kind == "local":
        return ROUTE_LOCAL
    if source.kind == "blob" and destination.kind == "blob" and source.account == destination.account:
        return ROUTE_BLOB_SAME_ACCOUNT
    raise UnsupportedRouteError(f"unsupported route from {source.kind} to {destination.kind}")


def _require_target(target: VersionIdentity) -> None:
    if target.name != TARGET_FORMAT_NAME or target.version != TARGET_FORMAT_VERSION:
        raise UnsupportedRouteError("only LeRobot v3.0 targets are supported")


@dataclass(frozen=True, slots=True)
class MediaWindow:
    episode_id: str
    camera: str
    source: SourceObject
    from_timestamp: float
    to_timestamp: float
    object_duration_s: float
    processing_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MetadataSpec:
    output_path: str
    depends_on: tuple[str, ...]
    estimated_bytes: int = 0


@dataclass(frozen=True, slots=True)
class WindowRef:
    episode_id: str
    camera: str
    from_timestamp: float
    to_timestamp: float


@dataclass(frozen=True, slots=True)
class ObjectPlanRequest:
    source: StorageEndpoint
    destination: StorageEndpoint
    scratch: StorageEndpoint | None
    target_format: VersionIdentity
    frozen_input_sha256: str
    selected_episode_ids: tuple[str, ...]
    windows: tuple[MediaWindow, ...]
    tool: VersionIdentity
    tool_settings_json: str = "{}"
    metadata: tuple[MetadataSpec, ...] = ()
    source_objects: tuple[SourceObject, ...] = ()
    copy_paths: tuple[str, ...] = ()
    limits: ReleaseMediaLimits = field(default_factory=ReleaseMediaLimits)
    destination_available_bytes: int | None = None
    scratch_available_bytes: int | None = None
    statistics_requirements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObjectPlan:
    output_path: str
    action: ObjectAction
    tool: str
    source_path: str | None
    source_revision: str | None
    source_length: int | None
    source_sha256: str | None
    reasons: tuple[str, ...]
    affected_cameras: tuple[str, ...]
    windows: tuple[WindowRef, ...]
    dependencies: tuple[str, ...]
    confirmation_required: bool
    estimated_output_bytes: int
    estimated_scratch_bytes: int


@dataclass(frozen=True, slots=True)
class CapacityPlan:
    copied_bytes: int
    processed_output_bytes: int
    generated_bytes: int
    scratch_peak_bytes: int
    processing_memory_bytes: int
    reserve_bytes: int
    shared_volume: bool
    destination_required_bytes: int | None
    scratch_required_bytes: int | None
    combined_required_bytes: int | None

    @property
    def payload_bytes(self) -> int:
        return self.copied_bytes + self.processed_output_bytes + self.generated_bytes


@dataclass(frozen=True, slots=True)
class ReleaseObjectPlan:
    route: str
    source_alias: str
    destination_alias: str
    destination_prefix: str
    target: VersionIdentity
    source_objects: tuple[SourceObject, ...]
    objects: tuple[ObjectPlan, ...]
    capacity: CapacityPlan
    blocking: tuple[str, ...]
    warnings: tuple[str, ...]
    digest: str

    @property
    def submittable(self) -> bool:
        return not self.blocking

    def to_preview_actions(self) -> tuple[PreviewAction, ...]:
        return tuple(
            PreviewAction(
                object_path=obj.output_path,
                action=obj.action,
                route=self.route,
                tool=obj.tool,
                destination_alias=self.destination_alias,
                confirmation_required=obj.confirmation_required,
            )
            for obj in self.objects
        )


def _check_selection(selected: Sequence[str]) -> tuple[str, ...]:
    episodes = tuple(selected)
    if not episodes:
        raise ReleasePlanError("episode selection must not be empty")
    if any(not episode for episode in episodes):
        raise ReleasePlanError("selected episode identifiers must be non-empty")
    if len(set(episodes)) != len(episodes):
        raise ReleasePlanError("selected episode identifiers must be unique")
    return episodes


def _check_window_values(window: MediaWindow) -> None:
    values = (window.from_timestamp, window.to_timestamp, window.object_duration_s)
    if not all(math.isfinite(value) for value in values):
        raise ReleasePlanError("window timestamps must be finite")
    if window.object_duration_s <= 0:
        raise ReleasePlanError("object duration must be positive")
    if window.from_timestamp < 0 or window.to_timestamp <= window.from_timestamp:
        raise ReleasePlanError("window must satisfy 0 <= from < to")
    if window.to_timestamp > window.object_duration_s + _TOLERANCE_S:
        raise ReleasePlanError("window extends beyond object duration")
    if any(not reason for reason in window.processing_reasons):
        raise ReleasePlanError("processing reasons must be non-empty")


def _source_identities(windows: Sequence[MediaWindow], objects: Sequence[SourceObject]) -> dict[str, SourceObject]:
    identities = {}
    for source in (*objects, *(window.source for window in windows)):
        path = _check_object_path(source.path, "source path")
        known = identities.get(path)
        if known is not None and known != source:
            raise ReleasePlanError("contradictory source identity for one source object")
        identities[path] = source
    durations = {}
    for window in windows:
        known_duration = durations.get(window.source.path)
        if known_duration is not None and known_duration != window.object_duration_s:
            raise ReleasePlanError("contradictory source duration for one source object")
        durations[window.source.path] = window.object_duration_s
    return identities


def _group_windows(
    windows: Sequence[MediaWindow],
    selected: tuple[str, ...],
) -> dict[str, list[MediaWindow]]:
    groups: dict[str, list[MediaWindow]] = {}
    seen: set[tuple[str, str]] = set()
    for window in windows:
        if window.episode_id not in selected:
            raise ReleasePlanError("window references an unselected episode")
        if not window.camera:
            raise ReleasePlanError("window camera must be non-empty")
        key = (window.episode_id, window.camera)
        if key in seen:
            raise ReleasePlanError("duplicate episode and camera window")
        seen.add(key)
        _check_window_values(window)
        path = _check_object_path(window.source.path, "source path")
        groups.setdefault(path, []).append(window)
    return groups


def _merged_intervals(windows: Sequence[MediaWindow]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted((w.from_timestamp, w.to_timestamp) for w in windows):
        if merged and start <= merged[-1][1] + _TOLERANCE_S:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _tool_label(tool: VersionIdentity) -> str:
    return f"{tool.name}@{tool.version}"


def _plan_media_object(
    path: str,
    windows: Sequence[MediaWindow],
    source: SourceObject,
    duration: float,
    request: ObjectPlanRequest,
) -> ObjectPlan:
    output = _check_output_path(path, "output path")
    merged = _merged_intervals(windows)
    covered = sum(end - start for start, end in merged)
    full_object = len(merged) == 1 and merged[0][0] <= _TOLERANCE_S and merged[0][1] >= duration - _TOLERANCE_S
    reasons = tuple(sorted({reason for w in windows for reason in w.processing_reasons}))
    if not full_object and not reasons:
        reasons = ("partial_object_selection",)
    cameras = tuple(sorted({w.camera for w in windows}))
    refs = tuple(
        sorted(
            (WindowRef(w.episode_id, w.camera, w.from_timestamp, w.to_timestamp) for w in windows),
            key=lambda ref: (ref.episode_id, ref.camera, ref.from_timestamp, ref.to_timestamp),
        )
    )
    common = {
        "output_path": output,
        "source_path": source.path,
        "source_revision": source.revision,
        "source_length": source.length,
        "source_sha256": source.sha256,
        "affected_cameras": cameras,
        "windows": refs,
        "dependencies": (source.path,),
    }
    if not reasons:
        return ObjectPlan(
            action="copy",
            tool=EXACT_COPY_TOOL,
            reasons=("exact_full_object",),
            confirmation_required=False,
            estimated_output_bytes=source.length,
            estimated_scratch_bytes=0,
            **common,
        )
    if request.scratch is None or request.scratch.kind != "local":
        raise ReleasePlanError("processing requires a configured local scratch root")
    fraction = min(covered / duration, 1.0)
    output_bytes = math.ceil(source.length * fraction * request.limits.transcode_output_percent / 100)
    return ObjectPlan(
        action="transcode",
        tool=_tool_label(request.tool),
        reasons=reasons,
        confirmation_required=True,
        estimated_output_bytes=output_bytes,
        estimated_scratch_bytes=source.length + output_bytes,
        **common,
    )


def _plan_copy(path: str, sources: Mapping[str, SourceObject]) -> ObjectPlan:
    output = _check_output_path(path, "copy output path")
    if output.startswith("videos/"):
        raise ReleasePlanError("native media copies require explicit camera windows")
    if path not in sources:
        raise ReleasePlanError("copy dependency has no known source identity")
    source = sources[path]
    return ObjectPlan(
        output_path=output,
        action="copy",
        tool=EXACT_COPY_TOOL,
        source_path=path,
        source_revision=source.revision,
        source_length=source.length,
        source_sha256=source.sha256,
        reasons=("explicit_full_object_copy",),
        affected_cameras=(),
        windows=(),
        dependencies=(path,),
        confirmation_required=False,
        estimated_output_bytes=source.length,
        estimated_scratch_bytes=0,
    )


def _plan_metadata(spec: MetadataSpec, sources: Mapping[str, SourceObject]) -> ObjectPlan:
    output = _check_output_path(spec.output_path, "metadata output path")
    if type(spec.estimated_bytes) is not int or spec.estimated_bytes < 0:
        raise ReleasePlanError("metadata estimate must not be negative")
    dependencies = tuple(sorted({_check_object_path(dep, "metadata dependency") for dep in spec.depends_on}))
    for dependency in dependencies:
        if dependency not in sources:
            raise ReleasePlanError("metadata dependency has no known source identity")
    return ObjectPlan(
        output_path=output,
        action="generate_metadata",
        tool=METADATA_TOOL,
        source_path=None,
        source_revision=None,
        source_length=None,
        source_sha256=None,
        reasons=("generated_dataset_metadata",),
        affected_cameras=(),
        windows=(),
        dependencies=dependencies,
        confirmation_required=False,
        estimated_output_bytes=spec.estimated_bytes,
        estimated_scratch_bytes=0,
    )


def _capacity(
    objects: Sequence[ObjectPlan],
    request: ObjectPlanRequest,
) -> tuple[CapacityPlan, tuple[str, ...], tuple[str, ...]]:
    copied = sum(obj.estimated_output_bytes for obj in objects if obj.action == "copy")
    processed = sum(obj.estimated_output_bytes for obj in objects if obj.action == "transcode")
    generated = sum(obj.estimated_output_bytes for obj in objects if obj.action == "generate_metadata")
    scratch_peak = max((obj.estimated_scratch_bytes for obj in objects), default=0)
    has_processing = any(obj.action == "transcode" for obj in objects)
    destination_local = request.destination.kind == "local"
    destination_peak = (copied + processed + generated) if destination_local else 0
    reserve = request.limits.reserve_bytes
    shared = (
        destination_local
        and has_processing
        and request.scratch is not None
        and request.scratch.volume == request.destination.volume
    )
    blocking: list[str] = []
    warnings: list[str] = []
    if shared:
        combined = destination_peak + scratch_peak + reserve
        destination_required = None
        scratch_required = None
        observations = [
            available
            for available in (request.destination_available_bytes, request.scratch_available_bytes)
            if available is not None
        ]
        if not observations:
            warnings.append("shared-volume free space not observed")
        elif combined > min(observations):
            blocking.append(f"shared_volume shortage: required {combined} bytes")
    else:
        combined = None
        destination_required = destination_peak + reserve if destination_local else None
        scratch_required = scratch_peak + reserve if has_processing else None
        if destination_required is not None:
            if request.destination_available_bytes is None:
                warnings.append("destination free space not observed")
            elif destination_required > request.destination_available_bytes:
                blocking.append(f"destination shortage: required {destination_required} bytes")
        if scratch_required is not None:
            if request.scratch_available_bytes is None:
                warnings.append("scratch free space not observed")
            elif scratch_required > request.scratch_available_bytes:
                blocking.append(f"scratch shortage: required {scratch_required} bytes")
    if has_processing:
        processing_count = sum(1 for obj in objects if obj.action == "transcode")
        warnings.append(f"necessary processing for {processing_count} object(s)")
        warnings.append("processing output size is an estimate, not an upper bound")
    if any(obj.action == "generate_metadata" and obj.estimated_output_bytes == 0 for obj in objects):
        warnings.append("generated metadata output size is unknown")
    warnings.append("estimates are advisory until durable admission")
    capacity = CapacityPlan(
        copied_bytes=copied,
        processed_output_bytes=processed,
        generated_bytes=generated,
        scratch_peak_bytes=scratch_peak,
        processing_memory_bytes=request.limits.processing_memory_bytes if has_processing else 0,
        reserve_bytes=reserve,
        shared_volume=shared,
        destination_required_bytes=destination_required,
        scratch_required_bytes=scratch_required,
        combined_required_bytes=combined,
    )
    return capacity, tuple(blocking), tuple(warnings)


def _plan_digest(
    request: ObjectPlanRequest, route: str, objects: Sequence[ObjectPlan], sources: Mapping[str, SourceObject]
) -> str:
    payload: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "frozen_input_sha256": request.frozen_input_sha256,
        "selected_episode_ids": list(request.selected_episode_ids),
        "statistics_requirements": sorted(set(request.statistics_requirements)),
        "route": route,
        "source_alias": request.source.alias,
        "source_objects": [sources[path].model_dump(mode="json") for path in sorted(sources)],
        "destination": {"alias": request.destination.alias, "prefix": request.destination.prefix},
        "target": {"name": request.target_format.name, "version": request.target_format.version},
        "tool": {
            "name": request.tool.name,
            "version": request.tool.version,
            "settings_json": request.tool_settings_json,
        },
        "objects": [
            {
                "output_path": obj.output_path,
                "action": obj.action,
                "tool": obj.tool,
                "source_path": obj.source_path,
                "source_revision": obj.source_revision,
                "source_length": obj.source_length,
                "source_sha256": obj.source_sha256,
                "reasons": list(obj.reasons),
                "affected_cameras": list(obj.affected_cameras),
                "windows": [[ref.episode_id, ref.camera, ref.from_timestamp, ref.to_timestamp] for ref in obj.windows],
                "dependencies": list(obj.dependencies),
                "confirmation_required": obj.confirmation_required,
            }
            for obj in objects
        ],
    }
    return sha256_hex(canonical_json_bytes(payload))


def plan_release_objects(request: ObjectPlanRequest) -> ReleaseObjectPlan:
    route = resolve_route(request.source, request.destination)
    _require_target(request.target_format)
    if not _SHA256.match(request.frozen_input_sha256):
        raise ReleasePlanError("frozen input digest must be lowercase SHA-256 hex")
    try:
        settings_bytes = request.tool_settings_json.encode("utf-8")
        if canonical_json_bytes(parse_strict_json(settings_bytes)) != settings_bytes:
            raise ValueError
    except (ValueError, ReleaseVerificationError):
        raise ReleasePlanError("generation settings must be canonical finite JSON") from None
    for available in (request.destination_available_bytes, request.scratch_available_bytes):
        if available is not None and (type(available) is not int or available < 0):
            raise ReleasePlanError("available capacity must be a nonnegative integer")
    selected = _check_selection(request.selected_episode_ids)
    for feature in request.statistics_requirements:
        if not isinstance(feature, str) or not re.fullmatch(r"[A-Za-z0-9_.]+", feature):
            raise ReleasePlanError("statistics requirements must name dataset features")
    sources = _source_identities(request.windows, request.source_objects)
    groups = _group_windows(request.windows, selected)
    objects: list[ObjectPlan] = []
    for path in sorted(groups):
        windows = groups[path]
        duration = windows[0].object_duration_s
        objects.append(_plan_media_object(path, windows, sources[path], duration, request))
    objects.extend(_plan_metadata(spec, sources) for spec in request.metadata)
    objects.extend(_plan_copy(path, sources) for path in request.copy_paths)
    objects.sort(key=lambda obj: obj.output_path)
    seen: set[str] = set()
    for obj in objects:
        if obj.output_path in seen:
            raise ReleasePlanError("duplicate output destination")
        seen.add(obj.output_path)
    capacity, blocking, warnings = _capacity(objects, request)
    blocking += tuple(f"statistics required: {feature}" for feature in sorted(set(request.statistics_requirements)))
    return ReleaseObjectPlan(
        route=route,
        source_alias=request.source.alias,
        destination_alias=request.destination.alias,
        destination_prefix=request.destination.prefix,
        target=request.target_format,
        source_objects=tuple(sources[path] for path in sorted(sources)),
        objects=tuple(objects),
        capacity=capacity,
        blocking=blocking,
        warnings=warnings,
        digest=_plan_digest(request, route, objects, sources),
    )


class ReleaseExecutionError(ReleaseVerificationError):
    """A planned object cannot be executed without violating its bound identity."""


def validate_processing_confirmation(plan: ReleaseObjectPlan, *, confirmed_digest: str | None) -> None:
    """Require the current processing preview identity before dispatch.

    The composer must regenerate the plan from revalidated frozen inputs first.
    This is an effects-confirmation check, not authentication or a durable claim.
    """
    if not plan.submittable:
        raise ReleasePlanError("release plan is blocked; confirmation cannot override admission")
    needs_confirmation = any(obj.confirmation_required for obj in plan.objects)
    if (needs_confirmation or confirmed_digest is not None) and confirmed_digest != plan.digest:
        raise ReleasePlanError("processing confirmation is missing or belongs to a stale preview")


@dataclass(frozen=True, slots=True)
class SubsetEpisodeInput:
    """One selected source episode and the frames a subset removes from it."""

    episode_id: str
    original_episode_index: int
    length: int
    fps: float
    removed_frames: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class OutputFrame:
    output_episode_index: int
    output_frame_index: int
    original_episode_index: int
    original_frame_index: int
    timestamp_s: float


@dataclass(frozen=True, slots=True)
class SubsetTimeline:
    """Contiguous output numbering for a selected subset; timestamps restart per episode."""

    frames: tuple[OutputFrame, ...]
    episode_lengths: tuple[int, ...]


def subset_episode_lengths(episodes: Sequence[SubsetEpisodeInput]) -> tuple[int, ...]:
    """Validate subset descriptors and count retained frames without materializing a timeline."""
    _check_selection([episode.episode_id for episode in episodes])
    lengths = []
    for episode in episodes:
        if type(episode.original_episode_index) is not int or episode.original_episode_index < 0:
            raise ReleasePlanError("original episode index must be a nonnegative integer")
        if type(episode.length) is not int or episode.length <= 0:
            raise ReleasePlanError("episode length must be a positive integer")
        if (
            isinstance(episode.fps, bool)
            or not isinstance(episode.fps, (int, float))
            or not math.isfinite(episode.fps)
            or episode.fps <= 0
        ):
            raise ReleasePlanError("episode fps must be a positive finite number")
        removed = frozenset(episode.removed_frames)
        if any(type(frame) is not int or frame < 0 or frame >= episode.length for frame in removed):
            raise ReleasePlanError("removed frame index is out of range")
        length = episode.length - len(removed)
        if not length:
            raise ReleasePlanError("episode would be empty after frame removal")
        lengths.append(length)
    return tuple(lengths)


def iter_subset_frames(episodes: Sequence[SubsetEpisodeInput]) -> Iterator[OutputFrame]:
    """Yield the validated frame derivation without retaining the whole selection."""
    subset_episode_lengths(episodes)
    for output_index, episode in enumerate(episodes):
        output_frame = 0
        for original_frame in range(episode.length):
            if original_frame not in episode.removed_frames:
                yield OutputFrame(
                    output_episode_index=output_index,
                    output_frame_index=output_frame,
                    original_episode_index=episode.original_episode_index,
                    original_frame_index=original_frame,
                    timestamp_s=output_frame / episode.fps,
                )
                output_frame += 1


def build_subset_timeline(
    episodes: Sequence[SubsetEpisodeInput], *, limits: ReleaseMediaLimits | None = None
) -> SubsetTimeline:
    """Materialize small timelines after an advisory memory-budget admission.

    Runtime producers use ``iter_subset_frames`` for streaming; supervised workers
    still enforce actual memory high-water independently of this estimate.
    """
    lengths = subset_episode_lengths(episodes)
    memory_budget = (limits or ReleaseMediaLimits()).processing_memory_bytes
    if sum(lengths) * 512 > memory_budget:
        raise ReleasePlanError("frame timeline exceeds its estimated processing memory budget")
    return SubsetTimeline(frames=tuple(iter_subset_frames(episodes)), episode_lengths=lengths)


_COPY_CHUNK_BYTES = 1024**2


def execute_exact_copy(
    obj: ObjectPlan,
    source_root: Path,
    output_root: Path,
    *,
    chunk_size: int = _COPY_CHUNK_BYTES,
) -> int:
    """Copy one exact-copy object, verifying the planned source identity before publishing.

    Bytes stream through a bounded buffer and are hashed while written to a private partial
    file. The output is published with a create-only hard link, so existing outputs are never
    replaced. Source and output roots must be disjoint. Returns the number of bytes written;
    this object-level operation does not commit a release or reserve durable job capacity.
    """
    if obj.action != "copy" or obj.tool != EXACT_COPY_TOOL:
        raise ReleasePlanError("only exact-copy objects can be executed without processing")
    if obj.source_path is None or obj.source_length is None or obj.source_sha256 is None:
        raise ReleasePlanError("exact-copy object lacks a bound source identity")
    if type(chunk_size) is not int or not 0 < chunk_size <= CHUNK_BYTES:
        raise ReleasePlanError("chunk size must fit the bounded copy buffer")
    try:
        resolved_source = source_root.resolve(strict=True)
        resolved_output = output_root.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ReleaseExecutionError("copy roots are unavailable") from None
    if resolved_source.is_relative_to(resolved_output) or resolved_output.is_relative_to(resolved_source):
        raise ReleasePlanError("source and output roots must not overlap")
    source_path = _check_object_path(obj.source_path, "copy source path")
    output_path = _check_output_path(obj.output_path, "copy output path")
    source = LocalReleaseStore(source_root)
    destination = LocalReleaseStore(output_root)
    try:
        with closing(source.iter_bytes(source_path, chunk_size=chunk_size)) as chunks:
            return destination.write_verified_object(
                output_path, chunks, length=obj.source_length, sha256=obj.source_sha256
            )
    except (OSError, ReleaseVerificationError):
        raise ReleaseExecutionError("copy refused: unsafe path, source drift or conflicting output") from None
