"""Side-effect-free SO-101 readiness probes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from .calibration import inspect_calibration_file
from .models import OperatorMode, PreflightCheck, PreflightCheckOutcome
from .profiles import ArmProfile, OperatorProfile


class PreflightRun:
    """Readiness output before lifecycle metadata is attached."""

    def __init__(self, checks: list[PreflightCheck], resource_fingerprint: str, ownership_complete: bool) -> None:
        self.checks = checks
        self.resource_fingerprint = resource_fingerprint
        self.ownership_complete = ownership_complete
        self.start_eligible = all(check.outcome is not PreflightCheckOutcome.BLOCKING for check in checks)


class OperatorPreflightRunner:
    """Inspect files, sysfs identity, output storage, and ownership without opening devices."""

    def __init__(
        self,
        *,
        data_root: Path,
        worker_executable: Path | None = None,
        host_lease_fd: int | None = None,
        sys_tty_root: Path = Path("/sys/class/tty"),
        sys_video_root: Path = Path("/sys/class/video4linux"),
        sys_usb_root: Path = Path("/sys/bus/usb/devices"),
        proc_root: Path = Path("/proc"),
        free_bytes: Callable[[Path], int] | None = None,
        device_mode_check: Callable[[Path], bool] | None = None,
        access_check: Callable[[Path, int], bool] = os.access,
        environ: Mapping[str, str] | None = None,
        credential_path: Path | None = None,
    ) -> None:
        self.data_root = data_root
        self.worker_executable = worker_executable
        self.host_lease_fd = host_lease_fd
        self.sys_tty_root = sys_tty_root
        self.sys_video_root = sys_video_root
        self.sys_usb_root = sys_usb_root
        self.proc_root = proc_root
        self.free_bytes = free_bytes or (lambda path: os.statvfs(path).f_bavail * os.statvfs(path).f_frsize)
        self.device_mode_check = device_mode_check or (lambda path: stat.S_ISCHR(path.stat().st_mode))
        self.access_check = access_check
        self.environ = environ if environ is not None else os.environ
        self.credential_path = credential_path or (Path.home() / ".cache/huggingface/token")

    def run(
        self,
        profile: OperatorProfile,
        *,
        mode: OperatorMode,
        upload_requested: bool = False,
    ) -> PreflightRun:
        evidence: dict[str, object] = {"profile": profile.fingerprint, "mode": mode.value}
        checks = [
            self._worker_check(evidence),
            self._lease_check(evidence),
            self._arm_check("leader", profile.leader, evidence),
            self._arm_check("follower", profile.follower, evidence),
            self._calibration_check("leader", profile.leader, evidence),
            self._calibration_check("follower", profile.follower, evidence),
            self._wrist_check(profile, evidence),
            self._front_check(profile, evidence),
            self._storage_check(profile, evidence),
            self._upload_check(upload_requested),
        ]
        ownership, complete = self._ownership_check(
            [profile.leader.port.resolve(), profile.follower.port.resolve(), profile.wrist_camera.path.resolve()]
        )
        checks.append(ownership)
        fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return PreflightRun(checks, fingerprint, complete)

    def _worker_check(self, evidence: dict[str, object]) -> PreflightCheck:
        executable = self.worker_executable
        if executable is None or not executable.is_file() or not self.access_check(executable, os.X_OK):
            return self._blocking("worker_contract", "Configured operator worker is unavailable")
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        evidence["worker"] = digest
        return PreflightCheck(
            name="worker_contract",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Worker executable and immutable hash verified; runtime version is verified during handshake",
        )

    def _lease_check(self, evidence: dict[str, object]) -> PreflightCheck:
        if self.host_lease_fd is None:
            return self._blocking("host_lease", "Operator host lease is not retained")
        try:
            mode = os.fstat(self.host_lease_fd).st_mode
        except OSError:
            return self._blocking("host_lease", "Operator host lease descriptor is invalid")
        if not stat.S_ISREG(mode):
            return self._blocking("host_lease", "Operator host lease is not a regular file")
        evidence["host_lease"] = "retained"
        return PreflightCheck(name="host_lease", outcome=PreflightCheckOutcome.PASSED, detail="Host lease retained")

    def _arm_check(self, role: str, arm: ArmProfile, evidence: dict[str, object]) -> PreflightCheck:
        if not arm.port.is_symlink() or not arm.port.exists():
            return self._blocking(f"{role}_device", "Stable device link is missing")
        target = arm.port.resolve()
        if not self.device_mode_check(target) or not self.access_check(target, os.R_OK | os.W_OK):
            return self._blocking(f"{role}_device", "Device is not an accessible character device")
        identity = self._identity(self.sys_tty_root / target.name)
        if identity != (arm.usb_vendor_id, arm.usb_product_id, arm.usb_serial):
            return self._blocking(f"{role}_device", "USB identity does not match the profile")
        evidence[f"{role}_device"] = identity
        return PreflightCheck(
            name=f"{role}_device",
            outcome=PreflightCheckOutcome.PASSED,
            detail="USB identity verified",
        )

    def _calibration_check(
        self,
        role: Literal["leader", "follower"],
        arm: ArmProfile,
        evidence: dict[str, object],
    ) -> PreflightCheck:
        result = inspect_calibration_file(arm.calibration_file, role)
        if not result.valid:
            return self._blocking(f"{role}_calibration", "; ".join(result.issues))
        evidence[f"{role}_calibration"] = result.sha256
        return PreflightCheck(
            name=f"{role}_calibration",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Saved joint values and hash verified; hardware not checked",
        )

    def _wrist_check(self, profile: OperatorProfile, evidence: dict[str, object]) -> PreflightCheck:
        camera = profile.wrist_camera
        if not camera.path.is_symlink() or not camera.path.exists():
            return self._blocking("wrist_camera", "Stable wrist camera path is missing")
        target = camera.path.resolve()
        if not self.device_mode_check(target) or not self.access_check(target, os.R_OK):
            return self._blocking("wrist_camera", "Wrist camera is not an accessible character device")
        identity = self._identity(self.sys_video_root / target.name)
        if identity[:2] != (camera.usb_vendor_id, camera.usb_product_id):
            return self._blocking("wrist_camera", "Wrist camera identity does not match the profile")
        evidence["wrist_camera"] = identity
        return PreflightCheck(name="wrist_camera", outcome=PreflightCheckOutcome.PASSED, detail="Camera verified")

    def _front_check(self, profile: OperatorProfile, evidence: dict[str, object]) -> PreflightCheck:
        camera = profile.front_camera
        matches = (
            {
                identity[2]
                for device in self.sys_usb_root.iterdir()
                if (identity := self._identity(device))
                == (camera.usb_vendor_id, camera.usb_product_id, camera.usb_descriptor_serial)
            }
            if self.sys_usb_root.is_dir()
            else set()
        )
        if len(matches) != 1:
            return self._blocking("front_camera", "Configured front camera was not found exactly once")
        evidence["front_camera"] = {"sdk_serial": camera.usb_serial, "descriptor_serial": camera.usb_descriptor_serial}
        return PreflightCheck(name="front_camera", outcome=PreflightCheckOutcome.PASSED, detail="Camera verified")

    def _storage_check(self, profile: OperatorProfile, evidence: dict[str, object]) -> PreflightCheck:
        root = self.data_root.resolve()
        if not root.is_dir() or not self.access_check(root, os.R_OK | os.W_OK | os.X_OK):
            return self._blocking("dataset_storage", "Dataset root is not a writable directory")
        available = self.free_bytes(root)
        if available < profile.minimum_free_bytes:
            return self._blocking("dataset_storage", "Dataset root has insufficient free space")
        evidence["dataset_storage"] = {"minimum_free_bytes": profile.minimum_free_bytes, "available": available}
        return PreflightCheck(name="dataset_storage", outcome=PreflightCheckOutcome.PASSED, detail="Output verified")

    def _upload_check(self, requested: bool) -> PreflightCheck:
        if not requested:
            return PreflightCheck(
                name="upload_credentials",
                outcome=PreflightCheckOutcome.SKIPPED,
                detail="Upload not requested",
            )
        if not self.environ.get("HF_TOKEN") and not (
            self.credential_path.is_file() and self.access_check(self.credential_path, os.R_OK)
        ):
            return self._blocking("upload_credentials", "Upload credentials are unavailable")
        return PreflightCheck(
            name="upload_credentials",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Credential present",
        )

    @staticmethod
    def _identity(path: Path) -> tuple[str, str, str]:
        current = path.resolve() if path.exists() else path
        for candidate in (current, *current.parents):
            try:
                values = tuple(
                    (candidate / name).read_text(encoding="utf-8").strip()
                    for name in ("idVendor", "idProduct", "serial")
                )
                return values  # type: ignore[return-value]
            except OSError:
                continue
        return "", "", ""

    def _ownership_check(self, devices: list[Path]) -> tuple[PreflightCheck, bool]:
        if not self.proc_root.is_dir():
            return PreflightCheck(
                name="device_ownership",
                outcome=PreflightCheckOutcome.WARNING,
                detail="Process ownership visibility is incomplete",
            ), False
        targets = {str(path) for path in devices}
        complete = True
        for process in self.proc_root.iterdir():
            if not process.name.isdigit() or process.name == str(os.getpid()):
                continue
            try:
                descriptors = list((process / "fd").iterdir())
            except OSError:
                complete = False
                continue
            for descriptor in descriptors:
                try:
                    if str(descriptor.resolve(strict=True)) in targets:
                        return (
                            self._blocking("device_ownership", "Configured device is held by another process"),
                            complete,
                        )
                except OSError:
                    continue
        return PreflightCheck(
            name="device_ownership",
            outcome=PreflightCheckOutcome.PASSED if complete else PreflightCheckOutcome.WARNING,
            detail="No visible external device holder" if complete else "Process visibility is incomplete",
        ), complete

    @staticmethod
    def _blocking(name: str, detail: str) -> PreflightCheck:
        return PreflightCheck(
            name=name,
            outcome=PreflightCheckOutcome.BLOCKING,
            detail=detail[:500],
            remediation="Resolve the blocking readiness check and rerun preflight.",
        )
