"""Dependency isolation contract for LeRobot release assembly."""

from __future__ import annotations

import importlib.util


def test_backend_uses_current_pyav_without_lerobot_runtime() -> None:
    # Act
    import av

    # Assert
    assert av.__version__ == "18.1.0"
    assert importlib.util.find_spec("lerobot") is None
