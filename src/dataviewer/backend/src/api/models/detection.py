"""
Detection Pydantic models for YOLO11 object detection system.

These models define the request/response schemas for object detection
endpoints and match the frontend TypeScript type definitions.
"""

from pydantic import Field, field_validator

from ..validation import SanitizedModel


class DetectionRequest(SanitizedModel):
    """Request parameters for running object detection."""

    frames: list[int] | None = Field(
        default=None,
        description="Specific frame indices to process. If None, processes all frames.",
    )
    confidence: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold for detections.",
    )
    model: str = Field(
        default="yolo11n",
        description="YOLO model variant: yolo11n, yolo11s, yolo11m, yolo11l, yolo11x",
    )

    @field_validator("frames")
    @classmethod
    def validate_frames(cls, frames: list[int] | None) -> list[int] | None:
        if frames is None:
            return None
        if any(frame < 0 for frame in frames):
            raise ValueError("Frame indices must be non-negative")
        return frames


class Detection(SanitizedModel):
    """Single object detection result."""

    class_id: int = Field(ge=0, description="COCO class ID")
    class_name: str = Field(description="Human-readable class name")
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence score")
    bbox: tuple[float, float, float, float] = Field(description="Bounding box as (x1, y1, x2, y2) in pixels")


class DetectionResult(SanitizedModel):
    """Detection results for a single frame."""

    frame: int = Field(ge=0, description="Frame index")
    detections: list[Detection] = Field(default_factory=list)
    processing_time_ms: float = Field(ge=0.0, description="Inference time in milliseconds")


class ClassSummary(SanitizedModel):
    """Summary statistics for a detection class."""

    count: int = Field(ge=0, description="Total detections of this class")
    avg_confidence: float = Field(ge=0.0, le=1.0, description="Average confidence")


class EpisodeDetectionSummary(SanitizedModel):
    """Complete detection results for an episode."""

    total_frames: int = Field(ge=0, description="Total frames in episode")
    processed_frames: int = Field(ge=0, description="Number of frames processed")
    total_detections: int = Field(ge=0, description="Total detections across all frames")
    detections_by_frame: list[DetectionResult] = Field(default_factory=list)
    class_summary: dict[str, ClassSummary] = Field(
        default_factory=dict,
        description="Detection statistics by class name",
    )
