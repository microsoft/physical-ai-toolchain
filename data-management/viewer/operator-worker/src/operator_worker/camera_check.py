"""Camera-only readiness checks that release devices before motion acquisition."""

from __future__ import annotations

from .config import WorkerProfile
from .resources import CameraResource, CleanupReport, ResourceTransaction


def check_cameras(resources: list[CameraResource]) -> CleanupReport:
    """Acquire, sample, and release camera resources as an independent check."""
    transaction = ResourceTransaction(resources)
    transaction.acquire_all()
    return transaction.release_all()


def check_profile_cameras(profile: WorkerProfile) -> CleanupReport:
    """Build camera adapters for a validated profile and run the readiness check."""
    from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig  # type: ignore[import-untyped]
    from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig  # type: ignore[import-untyped]

    wrist = OpenCVCamera(
        OpenCVCameraConfig(
            index_or_path=profile.wrist_camera.path,
            fps=profile.wrist_camera.fps,
            width=profile.wrist_camera.width,
            height=profile.wrist_camera.height,
            warmup_s=1,
        )
    )
    front = RealSenseCamera(
        RealSenseCameraConfig(
            serial_number_or_name=profile.front_camera.usb_serial,
            fps=profile.front_camera.fps,
            width=profile.front_camera.width,
            height=profile.front_camera.height,
            warmup_s=1,
        )
    )
    return check_cameras([CameraResource("wrist", wrist), CameraResource("front", front)])
