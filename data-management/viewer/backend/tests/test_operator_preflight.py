"""Behavior tests for side-effect-free physical readiness checks."""

from __future__ import annotations

import json
from pathlib import Path

from src.api.operator.models import OperatorMode, PreflightCheckOutcome
from src.api.operator.preflight import OperatorPreflightRunner
from src.api.operator.profiles import load_operator_profile
from tests.test_operator_profiles import _write_profile


def test_given_unavailable_worker_when_preflight_runs_then_start_is_blocked(tmp_path: Path) -> None:
    # Arrange
    profile_path = tmp_path / "so101.toml"
    _write_profile(profile_path)
    calibration = {
        name: {"id": index, "drive_mode": 0, "homing_offset": 0, "range_min": 100, "range_max": 3900}
        for index, name in enumerate(
            ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"),
            start=1,
        )
    }
    for name in ("leader", "follower"):
        path = tmp_path / f"calibration/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(calibration), encoding="utf-8")
    data_root = tmp_path / "datasets"
    data_root.mkdir()
    profile = load_operator_profile(profile_path, environ={})
    runner = OperatorPreflightRunner(
        data_root=data_root,
        worker_executable=tmp_path / "missing-worker",
        host_lease_fd=None,
    )

    # Act
    result = runner.run(profile, mode=OperatorMode.RECORD)

    # Assert
    worker_check = next(check for check in result.checks if check.name == "worker_contract")
    assert worker_check.outcome is PreflightCheckOutcome.BLOCKING
    assert result.start_eligible is False
