"""Centralized hardware ownership and reverse-order rollback."""

from __future__ import annotations

import fcntl
import termios
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ResourceSafetyError(RuntimeError):
    """Raised when hardware cannot be established or released safely."""


class AcquisitionError(ResourceSafetyError):
    """Raised when acquisition fails after rollback."""


@dataclass(frozen=True)
class CleanupReport:
    cleanup_complete: bool
    released: tuple[str, ...]
    errors: tuple[str, ...]
    torque_verified_off: bool = False


class ManagedResource(Protocol):
    name: str

    def acquire(self) -> None: ...

    def release(self) -> None: ...


class ResourceTransaction:
    """Own resources for one lifecycle and permanently latch cleanup failures."""

    def __init__(
        self,
        resources: Sequence[ManagedResource],
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._resources = list(resources)
        self._cancel_requested = cancel_requested or (lambda: False)
        self._acquired: list[ManagedResource] = []
        self._last_cleanup: CleanupReport | None = None
        self._terminal = False

    def acquire_all(self) -> None:
        if self._terminal or self._acquired:
            raise AcquisitionError("Resource transaction cannot be restarted")
        try:
            for resource in self._resources:
                if self._cancel_requested():
                    raise ResourceSafetyError("Resource acquisition cancelled")
                self._acquired.append(resource)
                resource.acquire()
                if self._cancel_requested():
                    raise ResourceSafetyError("Resource acquisition cancelled")
        except Exception as error:
            report = self.release_all()
            detail = f"Resource acquisition failed: {error}"
            if report.errors:
                detail += f"; cleanup errors: {report.errors}"
            raise AcquisitionError(detail) from error

    def release_all(self) -> CleanupReport:
        if not self._acquired and self._last_cleanup is not None:
            return self._last_cleanup
        released: list[str] = []
        errors: list[str] = []
        while self._acquired:
            resource = self._acquired.pop()
            try:
                resource.release()
                released.append(resource.name)
            except Exception as error:
                errors.append(f"{resource.name}: {error}")
        self._terminal = bool(errors)
        self._last_cleanup = CleanupReport(not errors, tuple(released), tuple(errors))
        return self._last_cleanup


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


def set_serial_exclusive(file_descriptor: int) -> None:
    """Prevent a second process from opening an acquired serial device."""
    fcntl.ioctl(file_descriptor, termios.TIOCEXCL)


class CameraResource:
    def __init__(self, name: str, camera: Camera) -> None:
        self.name = name
        self.camera = camera
        self._attempted = False

    def acquire(self) -> None:
        self._attempted = True
        self.camera.connect()
        if self.camera.read_latest() is None:
            raise ResourceSafetyError(f"{self.name} did not produce a frame")

    def release(self) -> None:
        if self._attempted:
            self._attempted = False
            self.camera.disconnect()


class ArmResource:
    """Own one arm bus and verify torque state around every release."""

    def __init__(
        self,
        name: str,
        arm: Arm,
        *,
        calibration_file: Path,
        validate_calibration: Callable[[Path], Any],
        configure_torque_off: Callable[[Bus], None],
        motion_capable: bool,
        set_exclusive: Callable[[int], None] = set_serial_exclusive,
        verify_identity: Callable[[], None] | None = None,
        expected_motor_names: set[str] | None = None,
    ) -> None:
        self.name = name
        self.arm = arm
        self.calibration_file = calibration_file
        self.validate_calibration = validate_calibration
        self.configure_torque_off = configure_torque_off
        self.motion_capable = motion_capable
        self.set_exclusive = set_exclusive
        self.verify_identity = verify_identity or (lambda: None)
        self.expected_motor_names = expected_motor_names
        self._attempted = False
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
        if not self._torque_map_matches(self.arm.bus.sync_read("Torque_Enable"), expected_value=0):
            raise ResourceSafetyError(f"{self.name} failed to verify torque off after configuration")
        self.torque_verified_off = True
        if self.motion_capable:
            positions = self.arm.bus.sync_read("Present_Position")
            self.arm.bus.sync_write("Goal_Position", positions)

    def enable_motion(self) -> None:
        if not self.motion_capable or not self._attempted:
            raise ResourceSafetyError(f"{self.name} cannot enable motion")
        self.arm.bus.enable_torque()
        if not self._torque_map_matches(self.arm.bus.sync_read("Torque_Enable"), expected_value=1):
            raise ResourceSafetyError(f"{self.name} failed to verify torque enable on every motor")
        self.torque_verified_off = False

    def release(self) -> None:
        if not self._attempted:
            return
        errors: list[str] = []
        try:
            self.arm.bus.disable_torque(num_retry=5)
            if self._torque_map_matches(self.arm.bus.sync_read("Torque_Enable"), expected_value=0):
                self.torque_verified_off = True
            else:
                errors.append(f"{self.name} torque-off verification failed")
        except Exception as error:
            errors.append(f"{self.name} torque cleanup failed: {error}")
        finally:
            try:
                self.arm.bus.disconnect(disable_torque=False)
            except Exception as error:
                errors.append(f"{self.name} serial close failed: {error}")
            self._attempted = False
        if errors:
            raise ResourceSafetyError("; ".join(errors))

    def _torque_map_matches(self, torque: dict[str, Any], *, expected_value: int) -> bool:
        if not torque:
            return False
        if self.expected_motor_names is not None and set(torque) != self.expected_motor_names:
            return False
        return all(int(value) == expected_value for value in torque.values())
