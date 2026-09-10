"""Hardware resource wrappers with explicit safety invariants."""

from __future__ import annotations

import fcntl
import termios
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class ResourceSafetyError(RuntimeError):
    """Raised when hardware state cannot be established or verified safely."""


class Bus(Protocol):
    port_handler: Any

    def connect(self) -> None: ...

    def disable_torque(self, num_retry: int = 0) -> None: ...

    def enable_torque(self) -> None: ...

    def sync_read(self, register: str) -> dict[str, Any]: ...

    def sync_write(self, register: str, values: dict[str, Any]) -> None: ...

    def disconnect(self, disable_torque: bool = True) -> None: ...


class Arm(Protocol):
    bus: Bus

    @property
    def is_calibrated(self) -> bool: ...


class Camera(Protocol):
    def connect(self) -> None: ...

    def read_latest(self) -> Any: ...

    def disconnect(self) -> None: ...


def set_serial_exclusive(fd: int) -> None:
    """Apply Linux TIOCEXCL after the SDK opens its serial descriptor."""
    fcntl.ioctl(fd, termios.TIOCEXCL)


class CameraResource:
    """Own one camera and require a frame before acquisition completes."""

    def __init__(self, name: str, camera: Camera) -> None:
        self.name = name
        self.camera = camera
        self._attempted = False

    def acquire(self) -> None:
        self._attempted = True
        self.camera.connect()
        frame = self.camera.read_latest()
        if frame is None:
            raise ResourceSafetyError(f"{self.name} did not produce a frame")

    def release(self) -> None:
        if not self._attempted:
            return
        self._attempted = False
        self.camera.disconnect()


class ArmResource:
    """Own one SO-101 bus while enforcing calibration and torque invariants."""

    def __init__(
        self,
        name: str,
        arm: Arm,
        *,
        calibration_file: Path,
        validate_calibration: Callable[[Path], Any],
        set_exclusive: Callable[[int], None] = set_serial_exclusive,
        motion_capable: bool,
        configure_torque_off: Callable[[Bus], None],
        verify_identity: Callable[[], None] | None = None,
        expected_motor_names: set[str] | None = None,
    ) -> None:
        self.name = name
        self.arm = arm
        self.calibration_file = calibration_file
        self.validate_calibration = validate_calibration
        self.set_exclusive = set_exclusive
        self.motion_capable = motion_capable
        self.configure_torque_off = configure_torque_off
        self.verify_identity = verify_identity or (lambda: None)
        self.expected_motor_names = expected_motor_names
        self._attempted = False
        self._motion_enabled = False
        self.torque_verified_off = True

    def acquire(self) -> None:
        self.validate_calibration(self.calibration_file)
        self.verify_identity()
        self.torque_verified_off = False
        self._attempted = True
        self.arm.bus.connect()
        serial_handle = self.arm.bus.port_handler.ser
        if serial_handle is None:
            raise ResourceSafetyError(f"{self.name} serial descriptor is unavailable")
        self.set_exclusive(serial_handle.fileno())
        self.verify_identity()
        self.arm.bus.disable_torque(num_retry=5)
        if not self.arm.is_calibrated:
            raise ResourceSafetyError(f"{self.name} motor calibration does not match the profile")
        self.configure_torque_off(self.arm.bus)
        self.arm.bus.disable_torque(num_retry=5)
        torque = self.arm.bus.sync_read("Torque_Enable")
        if not self._torque_map_matches(torque, expected_value=0):
            raise ResourceSafetyError(f"{self.name} failed to verify torque off after configuration")
        self.torque_verified_off = True
        if self.motion_capable:
            positions = self.arm.bus.sync_read("Present_Position")
            self.arm.bus.sync_write("Goal_Position", positions)

    def enable_motion(self) -> None:
        if not self.motion_capable or not self._attempted:
            raise ResourceSafetyError(f"{self.name} cannot enable motion")
        self.arm.bus.enable_torque()
        torque = self.arm.bus.sync_read("Torque_Enable")
        if not self._torque_map_matches(torque, expected_value=1):
            raise ResourceSafetyError(f"{self.name} failed to verify torque enable on every motor")
        self._motion_enabled = True
        self.torque_verified_off = False

    def release(self) -> None:
        if not self._attempted:
            return
        errors: list[str] = []
        try:
            self.arm.bus.disable_torque(num_retry=5)
            torque = self.arm.bus.sync_read("Torque_Enable")
            if not self._torque_map_matches(torque, expected_value=0):
                errors.append(f"{self.name} torque-off verification failed")
            else:
                self.torque_verified_off = True
        except Exception as error:
            errors.append(f"{self.name} torque cleanup failed: {error}")
        finally:
            try:
                self.arm.bus.disconnect(disable_torque=False)
            except Exception as error:
                errors.append(f"{self.name} serial close failed: {error}")
            self._attempted = False
            self._motion_enabled = False
        if errors:
            raise ResourceSafetyError("; ".join(errors))

    def _torque_map_matches(self, torque: dict[str, Any], *, expected_value: int) -> bool:
        if not torque:
            return False
        if self.expected_motor_names is not None and set(torque) != self.expected_motor_names:
            return False
        return all(int(value) == expected_value for value in torque.values())
