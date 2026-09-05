"""Side-effect-free SO-101 readiness probes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

from .models import OperatorMode, PreflightCheck, PreflightCheckOutcome
from .profiles import ArmProfile, OperatorProfile


class PreflightRun:
    """Pure readiness output before lifecycle metadata is attached."""

    def __init__(
        self,
        checks: list[PreflightCheck],
        resource_fingerprint: str,
        ownership_complete: bool,
    ) -> None:
        self.checks = checks
        self.resource_fingerprint = resource_fingerprint
        self.ownership_complete = ownership_complete
        self.start_eligible = all(check.outcome is not PreflightCheckOutcome.BLOCKING for check in checks)


class OperatorPreflightRunner:
    """Inspect configured resources without opening device nodes."""

    def __init__(
        self,
        *,
        data_root: Path,
        sys_tty_root: Path = Path("/sys/class/tty"),
        sys_video_root: Path = Path("/sys/class/video4linux"),
        sys_usb_root: Path = Path("/sys/bus/usb/devices"),
        proc_root: Path = Path("/proc"),
        free_bytes: Callable[[Path], int] | None = None,
        device_mode_check: Callable[[Path], bool] | None = None,
        access_check: Callable[[Path, int], bool] = os.access,
        environ: Mapping[str, str] | None = None,
        credential_path: Path | None = None,
        policy_python: Path | None = None,
        policy_checkpoint: Path | None = None,
    ) -> None:
        self.data_root = data_root
        self.sys_tty_root = sys_tty_root
        self.sys_video_root = sys_video_root
        self.sys_usb_root = sys_usb_root
        self.proc_root = proc_root
        self.free_bytes = free_bytes or (lambda path: os.statvfs(path).f_bavail * os.statvfs(path).f_frsize)
        self.device_mode_check = device_mode_check or (lambda path: stat.S_ISCHR(path.stat().st_mode))
        self.access_check = access_check
        self.environ = environ if environ is not None else os.environ
        self.credential_path = credential_path or (Path.home() / ".cache/huggingface/token")
        self.policy_python = policy_python
        self.policy_checkpoint = policy_checkpoint

    def run(
        self,
        profile: OperatorProfile,
        *,
        mode: OperatorMode,
        upload_requested: bool = False,
    ) -> PreflightRun:
        checks: list[PreflightCheck] = []
        evidence: dict[str, object] = {
            "profile": profile.fingerprint,
            "mode": mode.value,
        }
        checks.append(self._arm_check("leader", profile.leader, evidence))
        checks.append(self._arm_check("follower", profile.follower, evidence))
        checks.append(self._calibration_check("leader", profile.leader, evidence))
        checks.append(self._calibration_check("follower", profile.follower, evidence))
        checks.append(self._wrist_check(profile, evidence))
        checks.append(self._front_check(profile, evidence))
        checks.append(self._storage_check(profile, evidence))
        checks.append(self._upload_check(upload_requested))
        if mode is OperatorMode.POLICY:
            checks.append(self._policy_runtime_check(evidence))
        ownership_check, ownership_complete = self._ownership_check(
            [
                profile.leader.port.resolve(),
                profile.follower.port.resolve(),
                profile.wrist_camera.path.resolve(),
                *self._front_camera_nodes(profile),
            ]
        )
        checks.append(ownership_check)
        fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return PreflightRun(checks, fingerprint, ownership_complete)

    def _policy_runtime_check(self, evidence: dict[str, object]) -> PreflightCheck:
        python = self.policy_python
        checkpoint = self.policy_checkpoint
        if python is None or not python.is_file() or not os.access(python, os.X_OK):
            return self._blocking("policy_runtime", "Configured GR00T Python executable is unavailable")
        required = (
            "config.json",
            "model.safetensors",
            "policy_preprocessor.json",
            "policy_postprocessor.json",
        )
        if checkpoint is None or any(not (checkpoint / name).is_file() for name in required):
            return self._blocking("policy_runtime", "Configured GR00T checkpoint is incomplete")
        return PreflightCheck(
            name="policy_runtime",
            outcome=PreflightCheckOutcome.PASSED,
            detail="GR00T runtime and checkpoint verified",
        )

    def _arm_check(self, role: str, arm: ArmProfile, evidence: dict[str, object]) -> PreflightCheck:
        if not arm.port.is_symlink() or not arm.port.exists():
            return self._blocking(f"{role}_device", "Stable device link is missing")
        target = arm.port.resolve()
        if not self.device_mode_check(target) or not self.access_check(target, os.R_OK | os.W_OK):
            return self._blocking(f"{role}_device", "Device is not an accessible character device")
        identity = self._identity(self.sys_tty_root / target.name)
        expected = (arm.usb_vendor_id, arm.usb_product_id, arm.usb_serial)
        if identity != expected:
            return self._blocking(f"{role}_device", "USB identity does not match the profile")
        evidence[f"{role}_device"] = identity
        return PreflightCheck(
            name=f"{role}_device",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Stable USB identity verified",
        )

    def _calibration_check(self, role: str, arm: ArmProfile, evidence: dict[str, object]) -> PreflightCheck:
        try:
            content = arm.calibration_file.read_bytes()
            payload = json.loads(content)
        except (OSError, json.JSONDecodeError):
            return self._blocking(f"{role}_calibration", "Calibration file is missing or malformed")
        expected = {
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
            "gripper",
        }
        if set(payload) != expected:
            return self._blocking(
                f"{role}_calibration",
                "Calibration must contain the expected six joints",
            )
        digest = hashlib.sha256(content).hexdigest()
        evidence[f"{role}_calibration"] = digest
        return PreflightCheck(
            name=f"{role}_calibration",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Six-joint calibration verified",
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
            return self._blocking("wrist_camera", "Wrist camera identity does not match")
        evidence["wrist_camera"] = identity
        return PreflightCheck(
            name="wrist_camera",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Wrist camera identity verified",
        )

    def _front_check(self, profile: OperatorProfile, evidence: dict[str, object]) -> PreflightCheck:
        camera = profile.front_camera
        matches: set[str] = set()
        if self.sys_usb_root.is_dir():
            for device in self.sys_usb_root.iterdir():
                identity = self._identity(device)
                if identity == (
                    camera.usb_vendor_id,
                    camera.usb_product_id,
                    camera.usb_descriptor_serial,
                ):
                    matches.add(identity[2])
        if len(matches) != 1:
            observed = (
                sorted(
                    {
                        identity[2]
                        for device in self.sys_usb_root.iterdir()
                        if (identity := self._identity(device))[:2] == (camera.usb_vendor_id, camera.usb_product_id)
                    }
                )
                if self.sys_usb_root.is_dir()
                else []
            )
            return self._blocking(
                "front_camera",
                f"Configured RealSense USB descriptor serial {camera.usb_descriptor_serial} "
                f"(SDK serial {camera.usb_serial}) was not found exactly once; "
                f"observed: {observed or 'none'}",
            )
        evidence["front_camera"] = {
            "sdk_serial": camera.usb_serial,
            "usb_descriptor_serial": camera.usb_descriptor_serial,
        }
        return PreflightCheck(
            name="front_camera",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Configured RealSense identity verified",
        )

    def _storage_check(self, profile: OperatorProfile, evidence: dict[str, object]) -> PreflightCheck:
        root = self.data_root.resolve()
        if not root.is_dir() or not self.access_check(root, os.R_OK | os.W_OK | os.X_OK):
            return self._blocking("dataset_storage", "Dataset root is not a writable directory")
        available = self.free_bytes(root)
        if available < profile.minimum_free_bytes:
            return self._blocking("dataset_storage", "Dataset root has insufficient free space")
        return PreflightCheck(
            name="dataset_storage",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Dataset root and free-space threshold verified",
        )

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
            return self._blocking("upload_credentials", "Hugging Face credentials are unavailable")
        return PreflightCheck(
            name="upload_credentials",
            outcome=PreflightCheckOutcome.PASSED,
            detail="Upload credential presence verified",
        )

    @staticmethod
    def _identity(path: Path) -> tuple[str, str, str]:
        current = path.resolve() if path.exists() else path
        for candidate in (current, *current.parents):
            try:
                return tuple(
                    (candidate / name).read_text(encoding="utf-8").strip()
                    for name in ("idVendor", "idProduct", "serial")
                )  # type: ignore[return-value]
            except OSError:
                continue
        return "", "", ""

    def _ownership_check(self, devices: list[Path]) -> tuple[PreflightCheck, bool]:
        if not self.proc_root.is_dir():
            return (
                PreflightCheck(
                    name="device_ownership",
                    outcome=PreflightCheckOutcome.WARNING,
                    detail="Process ownership visibility is incomplete",
                ),
                False,
            )
        targets = {str(path) for path in devices}
        complete = True
        holders: list[str] = []
        for process in self.proc_root.iterdir():
            if not process.name.isdigit():
                continue
            try:
                descriptors = list((process / "fd").iterdir())
            except OSError:
                complete = False
                continue
            for descriptor in descriptors:
                try:
                    target = str(descriptor.resolve(strict=True))
                except OSError:
                    continue
                if target in targets and process.name != str(os.getpid()):
                    holders.append(process.name)
        if holders:
            return (
                self._blocking(
                    "device_ownership",
                    f"Configured device is held by process {holders[0]}",
                ),
                complete,
            )
        return (
            PreflightCheck(
                name="device_ownership",
                outcome=(PreflightCheckOutcome.PASSED if complete else PreflightCheckOutcome.WARNING),
                detail=(
                    "No visible external device holder"
                    if complete
                    else "No holder found; process visibility is incomplete"
                ),
            ),
            complete,
        )

    def _front_camera_nodes(self, profile: OperatorProfile) -> list[Path]:
        expected = (
            profile.front_camera.usb_vendor_id,
            profile.front_camera.usb_product_id,
            profile.front_camera.usb_descriptor_serial,
        )
        nodes: list[Path] = []
        if not self.sys_video_root.is_dir():
            return nodes
        for video_device in self.sys_video_root.iterdir():
            if self._identity(video_device) == expected:
                nodes.append(Path("/dev") / video_device.name)
        return nodes

    @staticmethod
    def _blocking(name: str, detail: str) -> PreflightCheck:
        remediation = {
            "leader_device": "Restore the configured leader udev link and permissions.",
            "follower_device": "Restore the configured follower udev link and permissions.",
            "leader_calibration": "Recalibrate the configured leader arm.",
            "follower_calibration": "Recalibrate the configured follower arm.",
            "wrist_camera": "Reconnect the wrist camera and verify its stable by-id path.",
            "front_camera": "Confirm the intended D405 serial before updating the profile.",
            "dataset_storage": "Select a writable dataset root with sufficient free space.",
            "upload_credentials": "Configure a readable Hugging Face token or disable upload.",
            "device_ownership": "Stop the process holding the configured device and rerun preflight.",
            "policy_runtime": "Configure a complete local GR00T checkpoint and executable runtime.",
        }.get(name, "Resolve the blocking readiness check and rerun preflight.")
        return PreflightCheck(
            name=name,
            outcome=PreflightCheckOutcome.BLOCKING,
            detail=detail,
            remediation=remediation,
        )
