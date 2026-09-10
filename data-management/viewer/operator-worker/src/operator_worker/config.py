"""Strict worker-side profile snapshot models."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ArmConfig(StrictModel):
    port: Path
    logical_id: str
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    calibration_file: Path

    @field_validator("port", "calibration_file", mode="before")
    @classmethod
    def decode_path(cls, value: object, info: ValidationInfo) -> Path | str:
        if not isinstance(value, (str, Path)):
            raise TypeError("path must be a string")
        return value if info.mode == "json" else Path(value)


class WristCameraConfig(StrictModel):
    path: Path
    usb_vendor_id: str
    usb_product_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)

    @field_validator("path", mode="before")
    @classmethod
    def decode_path(cls, value: object, info: ValidationInfo) -> Path | str:
        if not isinstance(value, (str, Path)):
            raise TypeError("path must be a string")
        return value if info.mode == "json" else Path(value)


class FrontCameraConfig(StrictModel):
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    usb_descriptor_serial: str
    product: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)


class RecordingConfig(StrictModel):
    fps: int
    episode_time_s: int
    reset_time_s: int
    upload_default: bool


class SessionSettings(StrictModel):
    mode: Literal["teleoperate", "record", "policy"]
    control_fps: int = Field(ge=1, le=120)
    camera_fps: dict[str, Annotated[int, Field(ge=1, le=60)]]
    max_relative_target: float | None = Field(default=None, gt=0, le=180)
    dataset_name: str = "so101-demo"
    dataset_root: Path | None = None
    dataset_id: str | None = None
    repo_id: str | None = None
    task: str = Field(min_length=1, max_length=500)
    save_destination: Literal["local", "local_and_hub"]
    hub_repo_id: str | None = None
    num_episodes: int = Field(ge=1, le=1_000)
    episode_time_s: int = Field(ge=1, le=3_600)
    reset_time_s: int = Field(ge=0, le=600)
    rollout_time_s: int = Field(default=30, ge=1, le=300)
    policy_python: Path | None = None
    policy_checkpoint: Path | None = None
    policy_cuda_visible_devices: str | None = None

    @field_validator("dataset_root", "policy_python", "policy_checkpoint", mode="before")
    @classmethod
    def decode_optional_path(cls, value: object, info: ValidationInfo) -> Path | str | None:
        if value is None:
            return None
        if not isinstance(value, (str, Path)):
            raise TypeError("path must be a string")
        return value if info.mode == "json" else Path(value)


class WorkerProfile(StrictModel):
    version: Literal[1]
    name: Literal["so101"]
    embodiment: str
    actuator_names: list[str] = Field(min_length=1)
    minimum_free_bytes: int
    teleoperation_fps: int = Field(gt=0, le=60)
    max_relative_target: float | None = Field(default=None, gt=0, le=180)
    leader: ArmConfig
    follower: ArmConfig
    wrist_camera: WristCameraConfig
    front_camera: FrontCameraConfig
    recording: RecordingConfig
    fingerprint: str

    def computed_fingerprint(self) -> str:
        canonical = self.model_dump(mode="json", exclude={"fingerprint"})
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
