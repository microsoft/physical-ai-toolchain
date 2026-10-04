"""
Format-neutral episode edit model shared by the dataset exporters.

Holds the edit operations an export applies, the progress and result types the
export endpoints stream, and the frame plan that maps output frames to source frames.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ..models.datasources import FrameInsertion
from .image_transform import CropRegion, ImageTransform, ResizeDimensions


@dataclass
class SubtaskSegment:
    """A labeled segment of frames representing a sub-task."""

    id: str
    """Unique identifier."""
    label: str
    """Human-readable label."""
    frame_range: tuple[int, int]
    """Frame range [start, end] inclusive."""
    color: str | None
    """Display color (hex), when known."""
    source: str
    """How this segment was created: 'manual', 'auto' or 'recorded'."""
    description: str | None = None
    """Optional description."""


SUBTASK_FILE_SUFFIX = ".subtasks.json"
"""Suffix of the file beside an exported HDF5 episode that holds its subtasks."""


def read_subtask_file(path: Path, length: int) -> list[SubtaskSegment]:
    """
    Return the valid subtasks in a subtask file for an episode of ``length`` frames.

    An entry needs a non-empty label and a frame range inside the episode; other entries are skipped,
    and a missing or unreadable file has no subtasks.
    """
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    subtasks = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        label, frame_range = entry.get("label"), entry.get("frame_range")
        if not isinstance(label, str) or not label.strip() or not isinstance(frame_range, list):
            continue
        if len(frame_range) != 2 or any(type(frame) is not int for frame in frame_range):
            continue
        if not 0 <= frame_range[0] <= frame_range[1] < length:
            continue
        segment_id, color, source, description = (entry.get(key) for key in ("id", "color", "source", "description"))
        subtasks.append(
            SubtaskSegment(
                id=segment_id if isinstance(segment_id, str) else f"recorded-{len(subtasks)}",
                label=label,
                frame_range=(frame_range[0], frame_range[1]),
                color=color if isinstance(color, str) else None,
                source=source if isinstance(source, str) else "recorded",
                description=description if isinstance(description, str) else None,
            )
        )
    return subtasks


@dataclass
class TrajectoryAdjustment:
    """Per-frame change to joint positions, keyed by state channel index; a set value replaces that channel's delta."""

    frame_index: int
    """Original frame index the adjustment applies to."""
    channel_deltas: dict[int, float] = field(default_factory=dict)
    """Values added to channels."""
    channel_values: dict[int, float] = field(default_factory=dict)
    """Values that replace channels."""


@dataclass
class EpisodeEditOperations:
    """Complete set of edit operations for an episode."""

    dataset_id: str
    """Dataset identifier."""
    episode_index: int
    """Episode index within the dataset."""
    global_transform: ImageTransform | None = None
    """Transform applied to all cameras."""
    camera_transforms: dict[str, ImageTransform] | None = None
    """Per-camera transform overrides."""
    removed_frames: set[int] | None = None
    """Frame indices to exclude from export."""
    inserted_frames: list[FrameInsertion] | None = None
    """Frame insertion specifications for interpolated frames."""
    subtasks: list[SubtaskSegment] | None = None
    """Sub-task segments for this episode."""
    trajectory_adjustments: list[TrajectoryAdjustment] | None = None
    """Joint-position adjustments, exported as derived values beside the recorded positions."""


@dataclass
class ExportProgress:
    """Progress update during export."""

    current_episode: int
    """Current episode being processed."""
    total_episodes: int
    """Total episodes to process."""
    current_frame: int
    """Current frame being processed."""
    total_frames: int
    """Total frames in current episode."""
    percentage: float
    """Overall progress percentage (0-100)."""
    status: str
    """Current operation description."""


@dataclass
class ExportResult:
    """Result of an export operation."""

    success: bool
    """Whether export completed successfully."""
    output_files: list[str]
    """Output file paths."""
    error: str | None = None
    """Error message if failed."""
    stats: dict[str, Any] = field(default_factory=dict)
    """Export statistics."""


class ExportError(Exception):
    """An export cannot be produced from the requested episodes and edits."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


ProgressCallback = Callable[[ExportProgress], None]


@dataclass(frozen=True)
class PlannedFrame:
    """One output frame: a kept source frame, or an insertion blended from it toward the next kept frame."""

    source: int
    """Kept source frame, or the kept frame an insertion follows."""
    following: int | None = None
    """Next kept source frame for an insertion; None for a kept frame."""
    factor: float = 0.0
    """Interpolation factor toward ``following`` for an insertion."""


def plan_frames(length: int, removed: set[int] | None, insertions: list[FrameInsertion] | None) -> list[PlannedFrame]:
    """Order the output frames of an episode after removals and insertions.

    An insertion applies only after a kept frame that has a kept successor; insertions after the
    same frame are ordered by interpolation factor.
    """
    kept = [index for index in range(length) if not removed or index not in removed]
    following = dict(pairwise(kept))
    factors: dict[int, list[float]] = {}
    for insertion in insertions or []:
        if insertion.after_frame_index in following:
            factors.setdefault(insertion.after_frame_index, []).append(insertion.interpolation_factor)
    plan: list[PlannedFrame] = []
    for index in kept:
        plan.append(PlannedFrame(index))
        plan.extend(PlannedFrame(index, following[index], factor) for factor in sorted(factors.get(index, [])))
    return plan


def output_indices(plan: list[PlannedFrame]) -> dict[int, int]:
    """Map each kept source frame to its index in the output."""
    return {frame.source: position for position, frame in enumerate(plan) if frame.following is None}


def remap_subtasks(subtasks: list[SubtaskSegment], index_map: dict[int, int]) -> list[dict[str, Any]]:
    """Return subtasks with output ranges clamped to their surviving frames, dropping any with none left."""
    remapped = []
    for subtask in subtasks:
        first, last = subtask.frame_range
        kept = [output for source, output in index_map.items() if first <= source <= last]
        if not kept:
            continue
        remapped.append(
            {
                "id": subtask.id,
                "label": subtask.label,
                "frame_range": [min(kept), max(kept)],
                "color": subtask.color,
                "source": subtask.source,
                "description": subtask.description,
            }
        )
    return remapped


def apply_trajectory_adjustments(positions: NDArray, adjustments: list[TrajectoryAdjustment]) -> NDArray:
    """Return a copy of positions with each adjustment applied at its original frame."""
    adjusted = np.array(positions, dtype=np.float64, copy=True)
    frames, channels = adjusted.shape
    for adjustment in adjustments:
        if not 0 <= adjustment.frame_index < frames:
            raise ExportError(
                f"trajectory adjustment frame {adjustment.frame_index} is outside the episode's {frames} frames"
            )
        for channel in (*adjustment.channel_deltas, *adjustment.channel_values):
            if not 0 <= channel < channels:
                raise ExportError(
                    f"trajectory adjustment channel {channel} is outside the episode's {channels} joint channels"
                )
        for channel, delta in adjustment.channel_deltas.items():
            if channel not in adjustment.channel_values:
                adjusted[adjustment.frame_index, channel] += delta
        for channel, value in adjustment.channel_values.items():
            adjusted[adjustment.frame_index, channel] = value
    return adjusted


def parse_edit_operations(data: dict) -> EpisodeEditOperations:
    """
    Parse edit operations from API request data.

    Args:
        data: Dict with edit operation fields.

    Returns:
        EpisodeEditOperations instance.
    """
    global_transform = None
    if data.get("globalTransform"):
        gt = data["globalTransform"]
        global_transform = ImageTransform(
            crop=CropRegion(**gt["crop"]) if gt.get("crop") else None,
            resize=ResizeDimensions(**gt["resize"]) if gt.get("resize") else None,
        )

    camera_transforms = None
    if data.get("cameraTransforms"):
        camera_transforms = {}
        for camera, ct in data["cameraTransforms"].items():
            camera_transforms[camera] = ImageTransform(
                crop=CropRegion(**ct["crop"]) if ct.get("crop") else None,
                resize=ResizeDimensions(**ct["resize"]) if ct.get("resize") else None,
            )

    removed_frames = None
    if data.get("removedFrames"):
        removed_frames = set(data["removedFrames"])

    inserted_frames = None
    if data.get("insertedFrames"):
        inserted_frames = [
            FrameInsertion(
                after_frame_index=ins["afterFrameIndex"],
                interpolation_factor=ins.get("interpolationFactor", 0.5),
            )
            for ins in data["insertedFrames"]
        ]

    subtasks = None
    if data.get("subtasks") is not None:
        subtasks = [
            SubtaskSegment(
                id=st["id"],
                label=st["label"],
                frame_range=tuple(st["frameRange"]),
                color=st["color"],
                source=st["source"],
                description=st.get("description"),
            )
            for st in data["subtasks"]
        ]

    trajectory_adjustments = None
    if data.get("trajectoryAdjustments"):
        trajectory_adjustments = [
            TrajectoryAdjustment(
                frame_index=adjustment["frameIndex"],
                channel_deltas={
                    int(channel): float(value) for channel, value in (adjustment.get("channelDeltas") or {}).items()
                },
                channel_values={
                    int(channel): float(value) for channel, value in (adjustment.get("channelValues") or {}).items()
                },
            )
            for adjustment in data["trajectoryAdjustments"]
        ]

    return EpisodeEditOperations(
        dataset_id=data.get("datasetId", ""),
        episode_index=data.get("episodeIndex", 0),
        global_transform=global_transform,
        camera_transforms=camera_transforms,
        removed_frames=removed_frames,
        inserted_frames=inserted_frames,
        subtasks=subtasks,
        trajectory_adjustments=trajectory_adjustments,
    )
