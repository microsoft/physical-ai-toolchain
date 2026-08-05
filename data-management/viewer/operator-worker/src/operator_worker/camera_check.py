"""Camera-only acquisition mode used before any arm bus is opened."""

from __future__ import annotations

from .acquisition import CleanupReport, ResourceTransaction
from .config import WorkerProfile
from .resources import CameraResource


def check_cameras(resources: list[CameraResource]) -> CleanupReport:
    transaction = ResourceTransaction(resources)
    transaction.acquire_all()
    return transaction.release_all()


def check_profile_cameras(profile: WorkerProfile) -> CleanupReport:
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
    return check_cameras(
        [CameraResource("wrist", wrist), CameraResource("front", front)]
    )
