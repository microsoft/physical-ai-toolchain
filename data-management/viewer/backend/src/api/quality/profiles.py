"""Versioned profile contracts for deterministic quality checks."""

from __future__ import annotations

from pydantic import Field, model_validator

from ..models.reviews import ContractId, ImmutableContract


class FeatureRequirement(ImmutableContract):
    """Expected source feature dtype and per-frame shape."""

    name: str = Field(min_length=1, max_length=256)
    dtype: str = Field(min_length=1, max_length=64)
    shape: tuple[int, ...]


class CalibrationRequirement(ImmutableContract):
    """Required calibration artifact and sensor references."""

    relative_path: str = Field(min_length=1, max_length=1024)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    required_sensors: tuple[ContractId, ...]


class QualityProfile(ImmutableContract):
    """Versioned data requirements and tolerances for one dataset class."""

    profile_id: ContractId
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    fps: float = Field(gt=0)
    timestamp_tolerance_seconds: float = Field(ge=0)
    video_frame_count_tolerance: int = Field(default=1, ge=0)
    required_features: tuple[FeatureRequirement, ...]
    optional_features: tuple[FeatureRequirement, ...] = ()
    required_metadata_files: tuple[str, ...] = ()
    calibration: CalibrationRequirement | None = None
    require_task_label: bool = True

    @model_validator(mode="after")
    def validate_unique_features(self) -> QualityProfile:
        names = [feature.name for feature in (*self.required_features, *self.optional_features)]
        if len(names) != len(set(names)):
            raise ValueError("quality profile feature names must be unique")
        return self
