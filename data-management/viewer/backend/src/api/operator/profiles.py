"""Strict TOML operator profile loading and allowlisted overrides."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
)


class OperatorProfileError(ValueError):
    """Raised when an operator profile is invalid."""


class ArmProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: Path
    logical_id: str
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    calibration_file: Path


class WristCameraProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    usb_vendor_id: str
    usb_product_id: str
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    fps: StrictInt = Field(gt=0)


class FrontCameraProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    usb_descriptor_serial: str
    product: str
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    fps: StrictInt = Field(gt=0)


class RecordingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fps: StrictInt = Field(gt=0)
    episode_time_s: StrictInt = Field(gt=0)
    reset_time_s: StrictInt = Field(ge=0)
    upload_default: StrictBool = False


class OperatorProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    name: str
    embodiment: str
    actuator_names: list[str] = Field(min_length=1)
    minimum_free_bytes: StrictInt = Field(ge=0)
    teleoperation_fps: StrictInt = Field(gt=0, le=60)
    max_relative_target: Annotated[StrictFloat, Field(gt=0, le=180)] | None = None
    leader: ArmProfile
    follower: ArmProfile
    wrist_camera: WristCameraProfile
    front_camera: FrontCameraProfile
    recording: RecordingProfile
    fingerprint: str = ""


_OVERRIDES = {
    "OPERATOR_SO101_LEADER_PORT": ("leader", "port"),
    "OPERATOR_SO101_FOLLOWER_PORT": ("follower", "port"),
    "OPERATOR_SO101_WRIST_CAMERA_PATH": ("wrist_camera", "path"),
    "OPERATOR_SO101_FRONT_CAMERA_SERIAL": ("front_camera", "usb_serial"),
}


def load_operator_profile(path: Path, *, environ: Mapping[str, str]) -> OperatorProfile:
    """Load a strict profile and apply only documented environment overrides."""
    unknown = sorted(key for key in environ if key.startswith("OPERATOR_SO101_") and key not in _OVERRIDES)
    if unknown:
        raise OperatorProfileError(f"Unknown OPERATOR_SO101 override: {unknown[0]}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for variable, target in _OVERRIDES.items():
            if value := environ.get(variable):
                data[target[0]][target[1]] = value
        for section, field in (
            ("leader", "port"),
            ("leader", "calibration_file"),
            ("follower", "port"),
            ("follower", "calibration_file"),
            ("wrist_camera", "path"),
        ):
            data[section][field] = Path(data[section][field]).expanduser().absolute()
        profile = OperatorProfile.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValidationError, KeyError) as error:
        raise OperatorProfileError(f"Invalid operator profile: {error}") from error
    identities = {
        profile.leader.logical_id,
        profile.follower.logical_id,
        profile.leader.usb_serial,
        profile.follower.usb_serial,
        str(profile.leader.port),
        str(profile.follower.port),
        str(profile.leader.calibration_file),
        str(profile.follower.calibration_file),
    }
    if len(identities) != 8:
        raise OperatorProfileError("Leader and follower identities must be unique")
    if len(set(profile.actuator_names)) != len(profile.actuator_names):
        raise OperatorProfileError("Operator actuator names must be unique")
    canonical = profile.model_dump(mode="json", exclude={"fingerprint"})
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return profile.model_copy(update={"fingerprint": fingerprint})
