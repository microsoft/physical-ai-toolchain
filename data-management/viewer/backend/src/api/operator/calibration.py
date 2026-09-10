"""Read-only saved-calibration evidence for preflight and the operator UI."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .models import CalibrationFileCheck, CalibrationJoint, OperatorCalibrationReport
from .profiles import OperatorProfile

_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
_FIELDS = {"id", "drive_mode", "homing_offset", "range_min", "range_max"}
_MAX_FILE_BYTES = 65_536


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Calibration JSON contains duplicate keys")
        result[key] = value
    return result


def _joint_issue(name: str, values: object, motor_id: int) -> str | None:
    if not isinstance(values, dict) or set(values) != _FIELDS:
        return f"{name}: expected id, drive_mode, homing_offset, range_min, and range_max"
    if any(type(values[field]) is not int for field in _FIELDS):
        return f"{name}: calibration fields must be integers, not booleans or strings"
    if values["id"] != motor_id:
        return f"{name}: motor ID must be {motor_id}"
    if values["drive_mode"] not in (0, 1):
        return f"{name}: drive mode must be 0 or 1"
    if not -2047 <= values["homing_offset"] <= 2047:
        return f"{name}: homing offset must be within -2047 through 2047"
    if not 0 <= values["range_min"] < values["range_max"] <= 4095:
        return f"{name}: encoder range must increase within 0 through 4095"
    return None


def inspect_calibration_file(path: Path, role: Literal["leader", "follower"]) -> CalibrationFileCheck:
    """Validate saved values without importing LeRobot or opening device nodes."""
    result = CalibrationFileCheck(role=role, file_name=path.name.replace("\r", "").replace("\n", ""), valid=False)
    try:
        if not path.is_file():
            result.issues.append("Calibration file is missing or is not a regular file")
            return result
        with path.open("rb") as file:
            content = file.read(_MAX_FILE_BYTES + 1)
        if len(content) > _MAX_FILE_BYTES:
            result.issues.append("Calibration file exceeds the 64 KiB size limit")
            return result
        result.sha256 = hashlib.sha256(content).hexdigest()
        payload = json.loads(content, object_pairs_hook=_unique_object)
    except (OSError, ValueError, RecursionError):
        result.issues.append("Calibration file is unreadable or contains invalid JSON, encoding, or duplicate keys")
        return result
    if not isinstance(payload, dict) or set(payload) != set(_JOINTS):
        result.issues.append("Calibration must contain exactly the six SO-101 joint names")
        return result
    for motor_id, name in enumerate(_JOINTS, start=1):
        if issue := _joint_issue(name, payload[name], motor_id):
            result.issues.append(issue)
        else:
            result.joints.append(CalibrationJoint(name=name, **payload[name]))
    result.valid = not result.issues
    return result


def inspect_operator_calibration(profile: OperatorProfile) -> OperatorCalibrationReport:
    """Inspect both saved files without creating or consuming readiness evidence."""
    arms = [
        inspect_calibration_file(profile.leader.calibration_file, "leader"),
        inspect_calibration_file(profile.follower.calibration_file, "follower"),
    ]
    return OperatorCalibrationReport(
        profile=profile.name,
        checked_at=datetime.now(UTC),
        valid=all(arm.valid for arm in arms),
        arms=arms,
    )
