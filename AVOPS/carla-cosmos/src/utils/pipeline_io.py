"""Filesystem helpers for the three-stage pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Top-level package name, used to launch stages with ``python -m``.
PACKAGE = (__package__ or "").split(".")[0]
DEFAULT_DATA_ROOT = Path("~/datasets/carla_cosmos")
SCENE_ELEMENTS: tuple[str, ...] = (
    "calibration_estimate",
    "egomotion_estimate",
    "obstacle",
    "lane",
    "lane_line",
    "road_boundary",
    "crosswalk",
    "pole",
    "road_marking",
    "wait_line",
    "traffic_light",
    "traffic_sign",
)


def resolved_root(path: Path) -> Path:
    return path.expanduser().resolve()


def capture_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "captures" / clip_id


def capture_rows_dir(data_root: Path, clip_id: str) -> Path:
    return capture_dir(data_root, clip_id) / "cosmos_rows"


def rgb_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "rgb_videos" / clip_id


def scene_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "scene_annotations" / clip_id


def rdshq_scene_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "rdshq_scene_annotations" / clip_id


def control_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "control_videos" / clip_id


def multiview_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "multiview_inference" / clip_id


def realistic_dir(data_root: Path, clip_id: str) -> Path:
    return resolved_root(data_root) / "realistic_videos" / clip_id


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
