"""Strict SO-101 calibration validation without interactive fallback."""

from __future__ import annotations

import json
from pathlib import Path


class CalibrationError(ValueError):
    """Raised when a calibration file is unsafe or malformed."""


JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
_FIELDS = ("id", "drive_mode", "homing_offset", "range_min", "range_max")
_MAX_FILE_BYTES = 65_536


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationError("Calibration JSON contains duplicate keys")
        result[key] = value
    return result


def validate_calibration_file(path: Path) -> dict[str, dict[str, int]]:
    """Load and validate an exact six-joint SO-101 calibration."""
    try:
        if not path.is_file():
            raise CalibrationError("Calibration file is missing or is not a regular file")
        with path.open("rb") as file:
            content = file.read(_MAX_FILE_BYTES + 1)
        if len(content) > _MAX_FILE_BYTES:
            raise CalibrationError("Calibration file exceeds the 64 KiB size limit")
        payload = json.loads(content, object_pairs_hook=_unique_object)
    except CalibrationError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise CalibrationError("Calibration file is unavailable or malformed") from error
    if not isinstance(payload, dict) or set(payload) != set(JOINTS):
        raise CalibrationError("Calibration must contain exactly the six SO-101 joint names")
    for motor_id, joint in enumerate(JOINTS, start=1):
        values = payload[joint]
        if (
            not isinstance(values, dict)
            or set(values) != set(_FIELDS)
            or any(type(values[field]) is not int for field in _FIELDS)
        ):
            raise CalibrationError(f"Calibration entry is invalid: {joint}")
        if values["id"] != motor_id:
            raise CalibrationError(f"Calibration motor ID for {joint} must be {motor_id}")
        if values["drive_mode"] not in (0, 1):
            raise CalibrationError("Calibration drive mode must be 0 or 1")
        if not (0 <= values["range_min"] < values["range_max"] <= 4095):
            raise CalibrationError("Calibration ranges must be ordered within 0 through 4095")
        if not (-2047 <= values["homing_offset"] <= 2047):
            raise CalibrationError("Calibration homing offset must be within -2047 through 2047")
    return {joint: payload[joint] for joint in JOINTS}
