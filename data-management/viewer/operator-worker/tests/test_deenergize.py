from __future__ import annotations

from pathlib import Path

from test_resources import FakeArm

from operator_worker.deenergize import deenergize_arms
from operator_worker.resources import ArmResource


def test_deenergize_opens_buses_only_and_verifies_torque_off(tmp_path: Path) -> None:
    leader = FakeArm()
    follower = FakeArm()
    resources = [
        ArmResource(
            "leader",
            leader,
            calibration_file=tmp_path / "leader.json",
            validate_calibration=lambda _path: None,
            set_exclusive=lambda _fd: None,
            motion_capable=False,
            configure_torque_off=lambda _bus: None,
        ),
        ArmResource(
            "follower",
            follower,
            calibration_file=tmp_path / "follower.json",
            validate_calibration=lambda _path: None,
            set_exclusive=lambda _fd: None,
            motion_capable=False,
            configure_torque_off=lambda _bus: None,
        ),
    ]

    report = deenergize_arms(resources)

    assert report.cleanup_complete is True
    assert report.released == ("follower", "leader")
    assert "enable_torque" not in leader.bus.events
    assert "enable_torque" not in follower.bus.events
