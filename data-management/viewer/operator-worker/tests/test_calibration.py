from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_worker.calibration import CalibrationError, validate_calibration_file

JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)


def _calibration() -> dict[str, dict[str, int]]:
    return {
        name: {
            "id": index,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 100,
            "range_max": 4000,
        }
        for index, name in enumerate(JOINTS, start=1)
    }


def test_valid_six_joint_calibration_passes(tmp_path: Path) -> None:
    path = tmp_path / "arm.json"
    path.write_text(json.dumps(_calibration()), encoding="utf-8")

    result = validate_calibration_file(path)

    assert tuple(result) == JOINTS


@pytest.mark.parametrize("mutation", ["missing", "duplicate_id", "degenerate", "out_of_range"])
def test_invalid_calibration_fails_without_prompt(tmp_path: Path, mutation: str) -> None:
    payload = _calibration()
    if mutation == "missing":
        payload.pop("gripper")
    elif mutation == "duplicate_id":
        payload["gripper"]["id"] = 5
    elif mutation == "degenerate":
        payload["gripper"]["range_max"] = payload["gripper"]["range_min"]
    else:
        payload["gripper"]["range_max"] = 5000
    path = tmp_path / "arm.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CalibrationError):
        validate_calibration_file(path)
