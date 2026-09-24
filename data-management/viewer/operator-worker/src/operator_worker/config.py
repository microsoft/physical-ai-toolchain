"""Strict worker-side validation for an environment-local SO-101 profile."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from .calibration import JOINTS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ArmConfig(StrictModel):
    port: Path
    logical_id: str = Field(min_length=1, max_length=128)
    usb_vendor_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_product_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_serial: str = Field(min_length=1, max_length=256)
    calibration_file: Path

    @field_validator("port", "calibration_file", mode="before")
    @classmethod
    def decode_path(cls, value: object) -> Path:
        if not isinstance(value, (str, Path)):
            raise TypeError("path must be a string")
        return Path(value)


class WristCameraConfig(StrictModel):
    path: Path
    usb_vendor_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_product_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    width: StrictInt = Field(gt=0, le=7680)
    height: StrictInt = Field(gt=0, le=4320)
    fps: StrictInt = Field(gt=0, le=60)

    @field_validator("path", mode="before")
    @classmethod
    def decode_path(cls, value: object) -> Path:
        if not isinstance(value, (str, Path)):
            raise TypeError("path must be a string")
        return Path(value)


class FrontCameraConfig(StrictModel):
    usb_vendor_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_product_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_serial: str = Field(min_length=1, max_length=256)
    usb_descriptor_serial: str = Field(min_length=1, max_length=256)
    product: str = Field(min_length=1, max_length=256)
    width: StrictInt = Field(gt=0, le=7680)
    height: StrictInt = Field(gt=0, le=4320)
    fps: StrictInt = Field(gt=0, le=60)


class RecordingConfig(StrictModel):
    fps: StrictInt = Field(gt=0, le=120)
    episode_time_s: StrictInt = Field(gt=0, le=3_600)
    reset_time_s: StrictInt = Field(ge=0, le=600)
    upload_default: StrictBool = False


class WorkerProfile(StrictModel):
    """Validated physical identity snapshot accepted by the worker."""

    version: Literal[1]
    name: str = Field(min_length=1, max_length=64)
    embodiment: Literal["SO-101"]
    actuator_names: tuple[str, ...]
    minimum_free_bytes: StrictInt = Field(ge=0)
    teleoperation_fps: StrictInt = Field(gt=0, le=60)
    max_relative_target: Annotated[StrictFloat, Field(gt=0, le=180)] | None = None
    leader: ArmConfig
    follower: ArmConfig
    wrist_camera: WristCameraConfig
    front_camera: FrontCameraConfig
    recording: RecordingConfig
    fingerprint: str = ""

    @field_validator("actuator_names", mode="before")
    @classmethod
    def decode_actuator_names(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise TypeError("actuator_names must be a string array")
        return tuple(value)

    @model_validator(mode="after")
    def validate_portable_contract(self) -> Self:
        if self.actuator_names != JOINTS:
            raise ValueError("worker profile must define exactly the six SO-101 actuators")
        paths = (
            self.leader.port,
            self.follower.port,
            self.leader.calibration_file,
            self.follower.calibration_file,
            self.wrist_camera.path,
        )
        if any(not path.is_absolute() or "\x00" in str(path) for path in paths):
            raise ValueError("worker profile paths must be absolute local paths")
        identities = (
            self.leader.logical_id,
            self.follower.logical_id,
            self.leader.usb_serial,
            self.follower.usb_serial,
            str(self.leader.port),
            str(self.follower.port),
            str(self.leader.calibration_file),
            str(self.follower.calibration_file),
        )
        if len(set(identities)) != len(identities):
            raise ValueError("leader and follower identities must be unique")
        return self
