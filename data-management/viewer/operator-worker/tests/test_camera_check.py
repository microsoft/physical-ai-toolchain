from __future__ import annotations

from operator_worker.camera_check import check_cameras
from operator_worker.resources import CameraResource


class Camera:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def connect(self) -> None:
        self.events.append(f"connect:{self.name}")

    def read_latest(self) -> object:
        self.events.append(f"frame:{self.name}")
        return object()

    def disconnect(self) -> None:
        self.events.append(f"disconnect:{self.name}")


def test_camera_only_mode_opens_and_closes_only_cameras() -> None:
    events: list[str] = []
    report = check_cameras(
        [
            CameraResource("wrist", Camera("wrist", events)),
            CameraResource("front", Camera("front", events)),
        ]
    )

    assert report.cleanup_complete is True
    assert report.released == ("front", "wrist")
    assert events == [
        "connect:wrist",
        "frame:wrist",
        "connect:front",
        "frame:front",
        "disconnect:front",
        "disconnect:wrist",
    ]
