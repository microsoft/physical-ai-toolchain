from __future__ import annotations

from pathlib import Path

import pytest

from operator_worker.deenergize import deenergize_arms
from operator_worker.resources import AcquisitionError, ArmResource, ResourceSafetyError, ResourceTransaction


class FakeSerial:
    def fileno(self) -> int:
        return 42


class FakeBus:
    def __init__(self, *, torque_off: bool = True) -> None:
        self.port_handler = type("PortHandler", (), {"ser": FakeSerial()})()
        self.events: list[str] = []
        self.torque_off = torque_off
        self.enabled = False

    def connect(self) -> None:
        self.events.append("connect")

    def disable_torque(self, num_retry: int = 0) -> None:
        self.events.append("disable")
        self.enabled = False

    def enable_torque(self) -> None:
        self.events.append("enable")
        self.enabled = True

    def sync_read(self, register: str) -> dict[str, int]:
        self.events.append(f"read:{register}")
        if register == "Present_Position":
            return {"joint": 1}
        return {"joint": int(self.enabled or not self.torque_off)}

    def sync_write(self, register: str, values: dict[str, object]) -> None:
        self.events.append(f"write:{register}")

    def disconnect(self, disable_torque: bool = True) -> None:
        self.events.append("disconnect")


class FakeArm:
    def __init__(self, *, torque_off: bool = True) -> None:
        self.bus = FakeBus(torque_off=torque_off)
        self.is_calibrated = True


def _resource(name: str, arm: FakeArm, path: Path) -> ArmResource:
    return ArmResource(
        name,
        arm,
        calibration_file=path,
        validate_calibration=lambda _path: None,
        configure_torque_off=lambda _bus: None,
        motion_capable=False,
        set_exclusive=lambda _fd: None,
        expected_motor_names={"joint"},
    )


def test_given_multiple_resources_when_deenergized_then_torque_precedes_reverse_release(tmp_path: Path) -> None:
    leader = FakeArm()
    follower = FakeArm()

    report = deenergize_arms(
        [
            _resource("leader", leader, tmp_path / "leader.json"),
            _resource("follower", follower, tmp_path / "follower.json"),
        ]
    )

    assert report.cleanup_complete is True
    assert report.released == ("follower", "leader")
    assert leader.bus.events[-3:] == ["disable", "read:Torque_Enable", "disconnect"]


def test_given_unverified_torque_off_when_released_then_cleanup_is_terminal(tmp_path: Path) -> None:
    arm = FakeArm(torque_off=False)
    transaction = ResourceTransaction([_resource("follower", arm, tmp_path / "follower.json")])

    with pytest.raises(AcquisitionError):
        transaction.acquire_all()

    with pytest.raises(AcquisitionError, match="cannot be restarted"):
        transaction.acquire_all()


def test_given_enabled_arm_when_released_then_disconnect_still_follows_torque_attempt(tmp_path: Path) -> None:
    arm = FakeArm(torque_off=False)
    resource = _resource("follower", arm, tmp_path / "follower.json")
    arm.bus.torque_off = True
    resource.acquire()
    arm.bus.torque_off = False

    with pytest.raises(ResourceSafetyError, match="torque-off"):
        resource.release()

    assert arm.bus.events[-1] == "disconnect"
