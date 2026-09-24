"""Behavior tests for portable SO-101 operator profiles."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.api.operator.profiles import (
    OperatorProfileError,
    load_operator_profile,
    load_operator_profiles,
)

_ACTUATORS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


def _write_profile(
    path: Path,
    *,
    name: str = "so101",
    actuator_names: list[str] | None = None,
    leader_port: str | None = None,
    extra: str = "",
) -> None:
    actuators = actuator_names or _ACTUATORS
    actuator_lines = ",\n    ".join(f'"{actuator}"' for actuator in actuators)
    root = path.parent
    path.write_text(
        f"""version = 1
name = "{name}"
embodiment = "SO-101"
actuator_names = [
    {actuator_lines},
]
minimum_free_bytes = 1024
teleoperation_fps = 30
{extra}
[leader]
port = "{leader_port or root / "dev/leader"}"
logical_id = "leader-arm"
usb_vendor_id = "1a86"
usb_product_id = "55d3"
usb_serial = "leader-serial"
calibration_file = "{root / "calibration/leader.json"}"

[follower]
port = "{root / "dev/follower"}"
logical_id = "follower-arm"
usb_vendor_id = "1a86"
usb_product_id = "55d3"
usb_serial = "follower-serial"
calibration_file = "{root / "calibration/follower.json"}"

[wrist_camera]
path = "{root / "dev/wrist"}"
usb_vendor_id = "05a3"
usb_product_id = "9230"
width = 640
height = 480
fps = 30

[front_camera]
usb_vendor_id = "8086"
usb_product_id = "0b5b"
usb_serial = "front-serial"
usb_descriptor_serial = "front-descriptor"
product = "Intel RealSense D405"
width = 640
height = 480
fps = 30

[recording]
fps = 30
episode_time_s = 60
reset_time_s = 30
upload_default = false
""",
        encoding="utf-8",
    )


def test_given_no_local_profile_paths_when_loading_profiles_then_hardware_profiles_are_empty() -> None:
    # Act
    profiles = load_operator_profiles(environ={})

    # Assert
    assert profiles == {}


def test_given_one_absolute_local_profile_when_loading_then_contract_is_resolved(tmp_path: Path) -> None:
    # Arrange
    profile_path = tmp_path / "so101.toml"
    _write_profile(profile_path)

    # Act
    profiles = load_operator_profiles(environ={"DATAVIEWER_OPERATOR_PROFILE_PATHS": str(profile_path)})

    # Assert
    assert profiles["so101"].actuator_names == tuple(_ACTUATORS)
    assert len(profiles["so101"].fingerprint) == 64


def test_given_duplicate_profile_names_when_loading_then_selection_is_rejected(tmp_path: Path) -> None:
    # Arrange
    first_path = tmp_path / "first.toml"
    second_path = tmp_path / "second.toml"
    _write_profile(first_path)
    _write_profile(second_path)

    # Act & Assert
    with pytest.raises(OperatorProfileError, match="Ambiguous operator profile name"):
        load_operator_profiles(
            environ={"DATAVIEWER_OPERATOR_PROFILE_PATHS": os.pathsep.join((str(first_path), str(second_path)))},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"extra": 'unexpected = "value"'}, "Invalid operator profile"),
        ({"actuator_names": _ACTUATORS[:-1]}, "exactly the six SO-101 actuators"),
        ({"leader_port": "relative/leader"}, "absolute local path"),
    ],
)
def test_given_invalid_profile_contract_when_loading_then_it_fails_closed(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    # Arrange
    profile_path = tmp_path / "so101.toml"
    _write_profile(profile_path, **mutation)

    # Act & Assert
    with pytest.raises(OperatorProfileError, match=message):
        load_operator_profile(profile_path, environ={})


def test_given_unknown_so101_override_when_loading_then_it_is_rejected(tmp_path: Path) -> None:
    # Arrange
    profile_path = tmp_path / "so101.toml"
    _write_profile(profile_path)

    # Act & Assert
    with pytest.raises(OperatorProfileError, match="Unknown OPERATOR_SO101 override"):
        load_operator_profile(profile_path, environ={"OPERATOR_SO101_UNSAFE_PORT": "/dev/ttyACM0"})


def test_given_committed_example_when_loading_then_it_cannot_authorize_hardware() -> None:
    # Arrange
    profile_path = Path(__file__).parents[1] / "src/api/operator/profile_data/so101.example.toml"

    # Act & Assert
    with pytest.raises(OperatorProfileError, match="Invalid operator profile"):
        load_operator_profile(profile_path, environ={})
