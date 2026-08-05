from __future__ import annotations

from pathlib import Path

import pytest

from operator_worker.resources import ArmResource, CameraResource, ResourceSafetyError


class FakeSerial:
    def fileno(self) -> int:
        return 42


class FakePortHandler:
    def __init__(self) -> None:
        self.ser = FakeSerial()


class FakeBus:
    def __init__(
        self,
        *,
        calibrated: bool = True,
        torque_off: bool = True,
        torque_on: bool = True,
    ) -> None:
        self.calibrated = calibrated
        self.torque_off = torque_off
        self.torque_on = torque_on
        self.port_handler = FakePortHandler()
        self.events: list[str] = []
        self.positions = {"shoulder_pan": 10.0, "gripper": 30.0}
        self.torque_enabled = False

    def connect(self) -> None:
        self.events.append("connect")

    def disable_torque(self, num_retry: int = 0) -> None:
        self.events.append("disable_torque")
        self.torque_enabled = False

    def enable_torque(self) -> None:
        self.events.append("enable_torque")
        self.torque_enabled = True

    def sync_read(self, register: str):
        self.events.append(f"read:{register}")
        if register == "Present_Position":
            return self.positions
        if self.torque_enabled:
            return {name: 1 if self.torque_on else 0 for name in self.positions}
        return {name: 0 if self.torque_off else 1 for name in self.positions}

    def sync_write(self, register: str, values: dict[str, float]) -> None:
        self.events.append(f"write:{register}:{values}")

    def disconnect(self, disable_torque: bool = True) -> None:
        self.events.append(f"disconnect:{disable_torque}")


class FakeArm:
    def __init__(
        self,
        *,
        calibrated: bool = True,
        torque_off: bool = True,
        torque_on: bool = True,
    ) -> None:
        self.bus = FakeBus(
            calibrated=calibrated, torque_off=torque_off, torque_on=torque_on
        )

    @property
    def is_calibrated(self) -> bool:
        return self.bus.calibrated


class FakeCamera:
    def __init__(self, *, frame: object | None = object()) -> None:
        self.frame = frame
        self.events: list[str] = []

    def connect(self) -> None:
        self.events.append("connect")

    def read_latest(self):
        self.events.append("read_latest")
        return self.frame

    def disconnect(self) -> None:
        self.events.append("disconnect")


def test_arm_acquisition_sets_exclusive_and_keeps_torque_disabled(
    tmp_path: Path,
) -> None:
    arm = FakeArm()
    exclusive_fds: list[int] = []
    resource = ArmResource(
        "follower",
        arm,
        calibration_file=tmp_path / "unused.json",
        validate_calibration=lambda _path: None,
        set_exclusive=exclusive_fds.append,
        motion_capable=True,
        configure_torque_off=lambda bus: bus.events.append("configure_torque_off"),
    )

    resource.acquire()

    assert exclusive_fds == [42]
    assert arm.bus.events == [
        "connect",
        "disable_torque",
        "configure_torque_off",
        "disable_torque",
        "read:Torque_Enable",
        "read:Present_Position",
        "write:Goal_Position:{'shoulder_pan': 10.0, 'gripper': 30.0}",
    ]
    assert "enable_torque" not in arm.bus.events

    resource.enable_motion()
    assert arm.bus.events[-2:] == ["enable_torque", "read:Torque_Enable"]


def test_arm_not_acquired_is_already_torque_safe(tmp_path: Path) -> None:
    resource = ArmResource(
        "follower",
        FakeArm(),
        calibration_file=tmp_path / "unused.json",
        validate_calibration=lambda _path: None,
        set_exclusive=lambda _fd: None,
        motion_capable=True,
        configure_torque_off=lambda _bus: None,
    )

    assert resource.torque_verified_off is True

    resource.acquire()

    assert resource.torque_verified_off is True


def test_enable_motion_requires_all_motor_torque_readback(tmp_path: Path) -> None:
    arm = FakeArm(torque_on=False)
    resource = ArmResource(
        "follower",
        arm,
        calibration_file=tmp_path / "unused.json",
        validate_calibration=lambda _path: None,
        set_exclusive=lambda _fd: None,
        motion_capable=True,
        configure_torque_off=lambda _bus: None,
        expected_motor_names={"shoulder_pan", "gripper"},
    )
    resource.acquire()

    with pytest.raises(ResourceSafetyError, match="enable"):
        resource.enable_motion()


def test_arm_release_requires_verified_torque_off(tmp_path: Path) -> None:
    arm = FakeArm()
    resource = ArmResource(
        "follower",
        arm,
        calibration_file=tmp_path / "unused.json",
        validate_calibration=lambda _path: None,
        set_exclusive=lambda _fd: None,
        motion_capable=True,
        configure_torque_off=lambda _bus: None,
    )
    resource.acquire()
    arm.bus.torque_off = False

    with pytest.raises(ResourceSafetyError, match="torque"):
        resource.release()

    assert arm.bus.events[-1] == "disconnect:False"


def test_camera_requires_frame_and_disconnects_after_partial_failure() -> None:
    camera = FakeCamera(frame=None)
    resource = CameraResource("wrist", camera)

    with pytest.raises(ResourceSafetyError, match="frame"):
        resource.acquire()
    resource.release()

    assert camera.events == ["connect", "read_latest", "disconnect"]
