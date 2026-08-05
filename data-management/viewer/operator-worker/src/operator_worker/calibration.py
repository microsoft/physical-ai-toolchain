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


def validate_calibration_file(path: Path) -> dict[str, dict[str, int]]:
    """Load and validate an exact six-joint SO-101 calibration."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationError(
            f"Calibration file is unavailable or malformed: {path}"
        ) from error
    if not isinstance(payload, dict) or tuple(payload) != JOINTS:
        raise CalibrationError("Calibration must contain the ordered SO-101 joint set")
    ids: set[int] = set()
    for joint in JOINTS:
        values = payload[joint]
        if not isinstance(values, dict) or any(
            field not in values or type(values[field]) is not int for field in _FIELDS
        ):
            raise CalibrationError(f"Calibration entry is invalid: {joint}")
        motor_id = values["id"]
        if motor_id in ids or motor_id not in range(1, 7):
            raise CalibrationError(
                "Calibration motor IDs must be unique values 1 through 6"
            )
        ids.add(motor_id)
        if values["drive_mode"] not in (0, 1):
            raise CalibrationError("Calibration drive mode must be 0 or 1")
        if not (0 <= values["range_min"] < values["range_max"] <= 4095):
            raise CalibrationError(
                "Calibration ranges must be ordered within 0 through 4095"
            )
        if not (-4095 <= values["homing_offset"] <= 4095):
            raise CalibrationError(
                "Calibration homing offset is outside the supported range"
            )
    return payload
