"""Strict, environment-local SO-101 operator profile loading."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, ValidationError, model_validator

_PROFILE_PATHS_ENV = "DATAVIEWER_OPERATOR_PROFILE_PATHS"
_SO101_ACTUATORS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
_OVERRIDES = {
    "OPERATOR_SO101_LEADER_PORT": ("leader", "port"),
    "OPERATOR_SO101_FOLLOWER_PORT": ("follower", "port"),
    "OPERATOR_SO101_WRIST_CAMERA_PATH": ("wrist_camera", "path"),
    "OPERATOR_SO101_FRONT_CAMERA_SERIAL": ("front_camera", "usb_serial"),
}


class OperatorProfileError(ValueError):
    """Raised when operator profile discovery or validation fails."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArmProfile(_StrictModel):
    """Environment-local identity for one SO-101 arm."""

    port: Path
    logical_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    usb_vendor_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_product_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_serial: str = Field(min_length=1, max_length=256, pattern=r"^[^<>\r\n]+$")
    calibration_file: Path


class WristCameraProfile(_StrictModel):
    """Environment-local identity and capture settings for the wrist camera."""

    path: Path
    usb_vendor_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_product_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    width: StrictInt = Field(gt=0, le=7680)
    height: StrictInt = Field(gt=0, le=4320)
    fps: StrictInt = Field(gt=0, le=240)


class FrontCameraProfile(_StrictModel):
    """Environment-local identity and capture settings for the front camera."""

    usb_vendor_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_product_id: str = Field(pattern=r"^[0-9A-Fa-f]{4}$")
    usb_serial: str = Field(min_length=1, max_length=256, pattern=r"^[^<>\r\n]+$")
    usb_descriptor_serial: str = Field(min_length=1, max_length=256, pattern=r"^[^<>\r\n]+$")
    product: str = Field(min_length=1, max_length=256, pattern=r"^[^<>\r\n]+$")
    width: StrictInt = Field(gt=0, le=7680)
    height: StrictInt = Field(gt=0, le=4320)
    fps: StrictInt = Field(gt=0, le=240)


class RecordingProfile(_StrictModel):
    """Bounded defaults for operator recordings."""

    fps: StrictInt = Field(gt=0, le=120)
    episode_time_s: StrictInt = Field(gt=0, le=3600)
    reset_time_s: StrictInt = Field(ge=0, le=600)
    upload_default: StrictBool = False


class OperatorProfile(_StrictModel):
    """Validated SO-101 identity and operating defaults."""

    version: Literal[1]
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    embodiment: Literal["SO-101"]
    actuator_names: tuple[str, ...]
    minimum_free_bytes: StrictInt = Field(ge=0)
    teleoperation_fps: StrictInt = Field(gt=0, le=60)
    max_relative_target: Annotated[StrictFloat, Field(gt=0, le=180)] | None = None
    leader: ArmProfile
    follower: ArmProfile
    wrist_camera: WristCameraProfile
    front_camera: FrontCameraProfile
    recording: RecordingProfile
    fingerprint: str = ""

    @model_validator(mode="after")
    def validate_so101_contract(self) -> Self:
        """Require one complete SO-101 actuator set and distinct arm identities."""
        if self.actuator_names != _SO101_ACTUATORS:
            raise ValueError("operator profile must define exactly the six SO-101 actuators")

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

        paths = (
            self.leader.port,
            self.follower.port,
            self.leader.calibration_file,
            self.follower.calibration_file,
            self.wrist_camera.path,
        )
        if any(not path.is_absolute() or "\x00" in str(path) for path in paths):
            raise ValueError("operator device and calibration locations must be an absolute local path")
        return self


def load_operator_profile(path: Path, *, environ: Mapping[str, str]) -> OperatorProfile:
    """Load one strict profile without opening any configured device."""
    unknown = sorted(key for key in environ if key.startswith("OPERATOR_SO101_") and key not in _OVERRIDES)
    if unknown:
        raise OperatorProfileError(f"Unknown OPERATOR_SO101 override: {unknown[0]}")

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for variable, (section, field_name) in _OVERRIDES.items():
            if value := environ.get(variable):
                data[section][field_name] = value
        for section, field_name in (
            ("leader", "port"),
            ("leader", "calibration_file"),
            ("follower", "port"),
            ("follower", "calibration_file"),
            ("wrist_camera", "path"),
        ):
            data[section][field_name] = Path(data[section][field_name]).expanduser()
        profile = OperatorProfile.model_validate(data)
    except ValidationError as error:
        messages = {str(item["msg"]) for item in error.errors()}
        if any("exactly the six SO-101 actuators" in message for message in messages):
            raise OperatorProfileError(
                "Invalid operator profile: exactly the six SO-101 actuators are required"
            ) from error
        if any("absolute local path" in message for message in messages):
            raise OperatorProfileError("Invalid operator profile: expected an absolute local path") from error
        raise OperatorProfileError("Invalid operator profile") from error
    except (OSError, TypeError, KeyError, tomllib.TOMLDecodeError) as error:
        raise OperatorProfileError("Invalid operator profile") from error

    canonical = profile.model_dump(mode="json", exclude={"fingerprint"})
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return profile.model_copy(update={"fingerprint": fingerprint})


def load_operator_profiles(*, environ: Mapping[str, str]) -> dict[str, OperatorProfile]:
    """Load profiles from the explicit environment-local path list."""
    raw_paths = environ.get(_PROFILE_PATHS_ENV, "")
    if not raw_paths:
        return {}

    configured_paths = raw_paths.split(os.pathsep)
    if any(not value for value in configured_paths):
        raise OperatorProfileError(f"{_PROFILE_PATHS_ENV} contains an empty path")

    profiles: dict[str, OperatorProfile] = {}
    for value in configured_paths:
        path = Path(value).expanduser()
        if not path.is_absolute() or "\x00" in value:
            raise OperatorProfileError(f"{_PROFILE_PATHS_ENV} must contain absolute local paths")
        profile = load_operator_profile(path, environ=environ)
        if profile.name in profiles:
            raise OperatorProfileError(f"Ambiguous operator profile name: {profile.name}")
        profiles[profile.name] = profile
    return profiles
