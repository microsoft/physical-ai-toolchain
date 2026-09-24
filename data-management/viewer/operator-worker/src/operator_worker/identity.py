"""Hardware and profile identity verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class IdentityError(RuntimeError):
    """Raised when observed identity differs from the authorized snapshot."""


def compute_profile_fingerprint(profile: Mapping[str, Any]) -> str:
    """Compute the canonical fingerprint without trusting its claimed value."""
    canonical = {key: value for key, value in profile.items() if key != "fingerprint"}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def compute_simulation_resource_fingerprint(profile: Mapping[str, Any], *, mode: str) -> str:
    """Compute a deterministic simulation identity without probing local devices."""
    evidence = {
        "execution_mode": "simulation",
        "mode": mode,
        "profile": profile.get("fingerprint", compute_profile_fingerprint(profile)),
    }
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def read_device_identity(path: Path) -> tuple[str, str, str]:
    """Read USB identity by walking from a sysfs device to its parents."""
    current = path.resolve() if path.exists() else path
    for candidate in (current, *current.parents):
        try:
            values = tuple(
                (candidate / name).read_text(encoding="utf-8").strip() for name in ("idVendor", "idProduct", "serial")
            )
        except OSError:
            continue
        return values  # type: ignore[return-value]
    return "", "", ""


def verify_tty_identity(
    port: Path,
    expected: tuple[str, str, str],
    *,
    sys_tty_root: Path = Path("/sys/class/tty"),
) -> None:
    """Fail if a serial path now resolves to different USB hardware."""
    observed = read_device_identity(sys_tty_root / port.resolve().name)
    if observed != expected:
        raise IdentityError(f"TTY identity mismatch for {port}: expected {expected}, observed {observed}")


def compute_resource_fingerprint(
    profile: Mapping[str, Any],
    *,
    mode: str,
    sys_tty_root: Path = Path("/sys/class/tty"),
    sys_video_root: Path = Path("/sys/class/video4linux"),
    sys_usb_root: Path = Path("/sys/bus/usb/devices"),
) -> str:
    """Re-observe all hardware and calibration identities for a session."""
    leader = profile["leader"]
    follower = profile["follower"]
    wrist = profile["wrist_camera"]
    front = profile["front_camera"]
    leader_identity = read_device_identity(sys_tty_root / Path(leader["port"]).resolve().name)
    follower_identity = read_device_identity(sys_tty_root / Path(follower["port"]).resolve().name)
    wrist_identity = read_device_identity(sys_video_root / Path(wrist["path"]).resolve().name)
    if leader_identity != (leader["usb_vendor_id"], leader["usb_product_id"], leader["usb_serial"]):
        raise IdentityError("SO-101 leader identity changed after preflight")
    if follower_identity != (follower["usb_vendor_id"], follower["usb_product_id"], follower["usb_serial"]):
        raise IdentityError("SO-101 follower identity changed after preflight")
    if wrist_identity[:2] != (wrist["usb_vendor_id"], wrist["usb_product_id"]):
        raise IdentityError("Wrist camera identity changed after preflight")
    expected_front = (front["usb_vendor_id"], front["usb_product_id"], front["usb_descriptor_serial"])
    descriptor_matches = {
        identity[2] for device in sys_usb_root.iterdir() if (identity := read_device_identity(device)) == expected_front
    }
    if descriptor_matches != {front["usb_descriptor_serial"]}:
        raise IdentityError("D405 USB descriptor identity changed after preflight")
    evidence = {
        "profile": profile["fingerprint"],
        "mode": mode,
        "leader_device": leader_identity,
        "follower_device": follower_identity,
        "leader_calibration": hashlib.sha256(Path(leader["calibration_file"]).read_bytes()).hexdigest(),
        "follower_calibration": hashlib.sha256(Path(follower["calibration_file"]).read_bytes()).hexdigest(),
        "wrist_camera": wrist_identity,
        "front_camera": {
            "sdk_serial": front["usb_serial"],
            "usb_descriptor_serial": front["usb_descriptor_serial"],
        },
    }
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
