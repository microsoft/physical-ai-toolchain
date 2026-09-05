"""Worker-side hardware identity and resource-fingerprint verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import WorkerProfile


class IdentityError(RuntimeError):
    """Raised when observed hardware identity differs from the profile."""


def _identity(path: Path) -> tuple[str, str, str]:
    current = path.resolve() if path.exists() else path
    for candidate in (current, *current.parents):
        try:
            return tuple(
                (candidate / name).read_text(encoding="utf-8").strip() for name in ("idVendor", "idProduct", "serial")
            )  # type: ignore[return-value]
        except OSError:
            continue
    return "", "", ""


def verify_tty_identity(
    port: Path,
    expected: tuple[str, str, str],
    *,
    sys_tty_root: Path = Path("/sys/class/tty"),
) -> None:
    observed = _identity(sys_tty_root / port.resolve().name)
    if observed != expected:
        raise IdentityError(f"TTY identity mismatch for {port}: expected {expected}, observed {observed}")


def compute_resource_fingerprint(
    profile: WorkerProfile,
    *,
    mode: str,
    sys_tty_root: Path = Path("/sys/class/tty"),
    sys_video_root: Path = Path("/sys/class/video4linux"),
    sys_usb_root: Path = Path("/sys/bus/usb/devices"),
) -> str:
    leader_identity = _identity(sys_tty_root / profile.leader.port.resolve().name)
    follower_identity = _identity(sys_tty_root / profile.follower.port.resolve().name)
    wrist_identity = _identity(sys_video_root / profile.wrist_camera.path.resolve().name)
    expected_leader = (
        profile.leader.usb_vendor_id,
        profile.leader.usb_product_id,
        profile.leader.usb_serial,
    )
    expected_follower = (
        profile.follower.usb_vendor_id,
        profile.follower.usb_product_id,
        profile.follower.usb_serial,
    )
    if leader_identity != expected_leader or follower_identity != expected_follower:
        raise IdentityError("SO-101 arm identity changed after preflight")
    expected_wrist = (
        profile.wrist_camera.usb_vendor_id,
        profile.wrist_camera.usb_product_id,
    )
    if wrist_identity[:2] != expected_wrist:
        raise IdentityError("Wrist camera identity changed after preflight")
    descriptor_matches = {
        identity[2]
        for device in sys_usb_root.iterdir()
        if (identity := _identity(device))
        == (
            profile.front_camera.usb_vendor_id,
            profile.front_camera.usb_product_id,
            profile.front_camera.usb_descriptor_serial,
        )
    }
    if descriptor_matches != {profile.front_camera.usb_descriptor_serial}:
        raise IdentityError("D405 USB descriptor identity changed after preflight")
    evidence = {
        "profile": profile.fingerprint,
        "mode": mode,
        "leader_device": leader_identity,
        "follower_device": follower_identity,
        "leader_calibration": hashlib.sha256(profile.leader.calibration_file.read_bytes()).hexdigest(),
        "follower_calibration": hashlib.sha256(profile.follower.calibration_file.read_bytes()).hexdigest(),
        "wrist_camera": wrist_identity,
        "front_camera": {
            "sdk_serial": profile.front_camera.usb_serial,
            "usb_descriptor_serial": profile.front_camera.usb_descriptor_serial,
        },
    }
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
