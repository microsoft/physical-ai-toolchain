"""Validated non-destructive episode edit descriptors."""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from ..validation import SanitizedModel

FrameIndex = Annotated[int, Field(ge=0, strict=True)]
PositiveDimension = Annotated[int, Field(gt=0, strict=True)]


class EditModel(SanitizedModel):
    """Reject unsupported operations and non-finite numeric values."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CropRegion(EditModel):
    x: FrameIndex
    y: FrameIndex
    width: PositiveDimension
    height: PositiveDimension


class ResizeDimensions(EditModel):
    width: PositiveDimension
    height: PositiveDimension


class ColorAdjustment(EditModel):
    brightness: float | None = Field(None, ge=-1, le=1)
    contrast: float | None = Field(None, ge=-1, le=1)
    saturation: float | None = Field(None, ge=-1, le=1)
    gamma: float | None = Field(None, ge=0.1, le=3)
    hue: float | None = Field(None, ge=-180, le=180)


class ImageTransform(EditModel):
    crop: CropRegion | None = None
    resize: ResizeDimensions | None = None
    colorAdjustment: ColorAdjustment | None = None
    colorFilter: Literal["none", "grayscale", "sepia", "invert", "warm", "cool"] | None = None


class FrameInsertion(EditModel):
    afterFrameIndex: FrameIndex
    interpolationFactor: float = Field(0.5, ge=0, le=1)


class TrajectoryAdjustment(EditModel):
    frameIndex: FrameIndex
    rightArmDelta: tuple[float, float, float] | None = None
    leftArmDelta: tuple[float, float, float] | None = None
    rightGripperOverride: float | None = None
    leftGripperOverride: float | None = None


class SubtaskSegment(EditModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    frameRange: tuple[FrameIndex, FrameIndex]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    source: Literal["manual", "auto"]
    description: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> SubtaskSegment:
        if self.frameRange[0] > self.frameRange[1]:
            raise ValueError("Subtask frame range must be ordered")
        return self


class EpisodeEditOperations(EditModel):
    datasetId: str = Field(min_length=1)
    episodeIndex: FrameIndex
    globalTransform: ImageTransform | None = None
    cameraTransforms: dict[str, ImageTransform] | None = None
    removedFrames: list[FrameIndex] | None = None
    insertedFrames: list[FrameInsertion] | None = None
    subtasks: list[SubtaskSegment] | None = None
    trajectoryAdjustments: list[TrajectoryAdjustment] | None = None

    def validate_context(self, frame_count: int, cameras: set[str]) -> None:
        """Validate operations against the current source episode."""
        if set(self.cameraTransforms or {}) - cameras:
            raise ValueError("Unknown camera in edit descriptor")
        indices = list(self.removedFrames or [])
        indices.extend(item.frameIndex for item in self.trajectoryAdjustments or [])
        indices.extend(index for segment in self.subtasks or [] for index in segment.frameRange)
        if any(index >= frame_count for index in indices):
            raise ValueError("Edit frame index is outside the source episode")
        if any(item.afterFrameIndex >= frame_count - 1 for item in self.insertedFrames or []):
            raise ValueError("Frame insertion requires two adjacent source frames")
        segments = self.subtasks or []
        if len({segment.id for segment in segments}) != len(segments):
            raise ValueError("Subtask identities must be unique")
        ordered = sorted(segments, key=lambda segment: segment.frameRange[0])
        if any(first.frameRange[1] >= second.frameRange[0] for first, second in pairwise(ordered)):
            raise ValueError("Subtask frame ranges must not overlap")


class SavedEpisodeEdits(EditModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    dataset_id: str
    episode_index: FrameIndex
    source_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    operations: EpisodeEditOperations
    updated_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> SavedEpisodeEdits:
        if self.operations.datasetId != self.dataset_id or self.operations.episodeIndex != self.episode_index:
            raise ValueError("Edit descriptor identity does not match its operations")
        return self


class SaveEpisodeEditsRequest(EditModel):
    source_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    operations: EpisodeEditOperations


class EpisodeEditState(EditModel):
    source_id: str
    source_revision: str
    author_id: str
    saved: SavedEpisodeEdits | None = None
