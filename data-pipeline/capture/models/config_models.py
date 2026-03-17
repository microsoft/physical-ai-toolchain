"""Pydantic models for ROS 2 edge recording configuration validation.

Provides type-safe validation for YAML configuration files controlling
topic recording, episode triggers, disk monitoring, and gap detection.
Generates JSON Schema for IDE autocomplete and documentation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TopicConfig(BaseModel):
    """ROS 2 topic recording configuration.

    Attributes:
        name: ROS 2 topic name (must start with /).
        frequency_hz: Target recording frequency in Hz, constrained to (0, 1000] range.
        compression: Compression algorithm to apply to recorded data.
    """

    name: str = Field(
        description="ROS 2 topic name (must start with /)",
        examples=["/joint_states", "/camera/image_raw"],
    )
    frequency_hz: Annotated[float, Field(gt=0, le=1000)] = Field(description="Target recording frequency in Hz")
    compression: Literal["none", "lz4", "zstd"] = Field(default="none", description="Compression algorithm")

    @field_validator("name")
    @classmethod
    def validate_topic_name(cls, v: str) -> str:
        """Validate ROS 2 topic naming convention.

        Args:
            v: Topic name to validate.

        Returns:
            Validated topic name.

        Raises:
            ValueError: If topic name does not start with /.
        """
        if not v.startswith("/"):
            raise ValueError(f"Topic name must start with /: {v}")
        return v


class GpioTriggerConfig(BaseModel):
    """GPIO pin trigger configuration.

    Attributes:
        type: Discriminator field, always "gpio".
        pin: GPIO pin number using BCM numbering, constrained to [0, 27].
        active_high: True if trigger on HIGH signal, False if LOW.
    """

    type: Literal["gpio"] = "gpio"
    pin: Annotated[int, Field(ge=0, le=27)] = Field(description="GPIO pin number (BCM numbering)")
    active_high: bool = Field(default=True, description="True if trigger on HIGH, False if LOW")


class PositionTriggerConfig(BaseModel):
    """Position-based trigger configuration.

    Attributes:
        type: Discriminator field, always "position".
        joint_indices: Joint indices to monitor for position-based triggering.
        tolerances: Position tolerance per joint (radians or meters).
    """

    type: Literal["position"] = "position"
    joint_indices: list[int] = Field(min_length=1, description="Joint indices to monitor")
    tolerances: list[float] = Field(
        min_length=1,
        description="Position tolerance per joint (radians or meters)",
    )

    @model_validator(mode="after")
    def validate_tolerance_length(self) -> PositionTriggerConfig:
        """Validate matching array lengths for joint_indices and tolerances.

        Returns:
            Validated model instance.

        Raises:
            ValueError: If joint_indices and tolerances lengths mismatch.
        """
        if len(self.tolerances) != len(self.joint_indices):
            raise ValueError(
                f"Tolerance count ({len(self.tolerances)}) must match joint index count ({len(self.joint_indices)})"
            )
        return self


class VrTriggerConfig(BaseModel):
    """VR controller trigger configuration.

    Attributes:
        type: Discriminator field, always "vr".
        controller: VR controller side (left or right).
        button: Button name to monitor for trigger events.
    """

    type: Literal["vr"] = "vr"
    controller: Literal["left", "right"] = Field(description="VR controller side")
    button: Literal["trigger", "grip", "primary", "secondary"] = Field(description="Button name to monitor")


TriggerConfig = Annotated[
    GpioTriggerConfig | PositionTriggerConfig | VrTriggerConfig,
    Field(discriminator="type"),
]


class DiskThresholds(BaseModel):
    """Disk usage alert thresholds.

    Attributes:
        warning_percent: Warning threshold percentage (0-100).
        critical_percent: Critical threshold percentage (0-100).
    """

    warning_percent: Annotated[int, Field(ge=0, le=100)] = Field(default=80, description="Warning threshold percentage")
    critical_percent: Annotated[int, Field(ge=0, le=100)] = Field(
        default=95, description="Critical threshold percentage"
    )

    @model_validator(mode="after")
    def validate_threshold_order(self) -> DiskThresholds:
        """Validate warning threshold is less than critical threshold.

        Returns:
            Validated model instance.

        Raises:
            ValueError: If warning_percent >= critical_percent.
        """
        if self.warning_percent >= self.critical_percent:
            raise ValueError(
                f"Warning threshold ({self.warning_percent}%) must be less than critical ({self.critical_percent}%)"
            )
        return self


class GapDetectionConfig(BaseModel):
    """Message gap detection configuration.

    Attributes:
        threshold_ms: Gap detection threshold in milliseconds.
        severity: Severity level for gap detection events.
    """

    threshold_ms: Annotated[float, Field(gt=0)] = Field(
        default=100.0, description="Gap detection threshold in milliseconds"
    )
    severity: Literal["warning", "error", "critical"] = Field(
        default="warning", description="Severity level for gap detection events"
    )


class RecordingConfig(BaseModel):
    """Root configuration for ROS 2 edge recording system.

    Attributes:
        topics: List of ROS 2 topics to record.
        trigger: Episode trigger configuration.
        disk_thresholds: Disk usage alert configuration.
        gap_detection: Data gap detection parameters.
        output_dir: Root directory for recorded episodes.
    """

    model_config = {
        "extra": "forbid",
        "validate_default": True,
        "validate_assignment": True,
    }

    topics: list[TopicConfig] = Field(description="List of ROS 2 topics to record")
    trigger: TriggerConfig = Field(description="Episode trigger configuration")
    disk_thresholds: DiskThresholds = Field(
        default_factory=DiskThresholds,
        description="Disk usage alert configuration",
    )
    gap_detection: GapDetectionConfig = Field(
        default_factory=GapDetectionConfig,
        description="Data gap detection parameters",
    )
    output_dir: Path = Field(
        default=Path("/data/recordings"),
        description="Root directory for recorded episodes",
    )

    @model_validator(mode="after")
    def validate_topic_uniqueness(self) -> RecordingConfig:
        """Validate topic names are unique across the configuration.

        Returns:
            Validated model instance.

        Raises:
            ValueError: If duplicate topic names are found.
        """
        topic_names = [topic.name for topic in self.topics]
        duplicates = [name for name in topic_names if topic_names.count(name) > 1]
        if duplicates:
            unique_duplicates = sorted(set(duplicates))
            raise ValueError(f"Duplicate topic names found: {unique_duplicates}")
        return self

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, v: Path) -> Path:
        """Validate output directory is absolute, exists, and is writable.

        Args:
            v: Output directory path to validate.

        Returns:
            Validated output directory path.

        Raises:
            ValueError: If path is not absolute, does not exist, is not a directory, or is not writable.
        """
        if not v.is_absolute():
            raise ValueError(f"output_dir must be an absolute path, got: {v}")

        if not v.exists():
            raise ValueError(f"output_dir does not exist: {v}")

        if not v.is_dir():
            raise ValueError(f"output_dir is not a directory: {v}")

        if not os.access(v, os.W_OK):
            raise ValueError(f"output_dir is not writable: {v}")

        return v
