#!/usr/bin/env python3
"""Stage 1 CARLA capture implementation.

This module treats CARLA as the only live dependency and writes a portable
capture bundle under ``captures/<clip_id>/``:

1. Spawn an ego vehicle and optional traffic in CARLA.
2. Attach the selected 1-7 RGB cameras matching Cosmos Auto Multiview names.
3. Save synchronized RGB videos, camera timestamps, and JSON scene rows.

Run while the CARLA server is already running.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from tqdm import tqdm

from ..pipeline_io import SCENE_ELEMENTS, write_json
from .camera_views import COSMOS_NAME_TO_VIEW_KEY, resolve_views

try:
    import carla
except ImportError as exc:  # pragma: no cover - only runs on the target machine
    raise SystemExit(
        "Could not import the CARLA Python API. Activate the environment where you "
        "installed ~/sim/carla-0.9.16/PythonAPI/carla/dist/carla-*.whl."
    ) from exc

SCENARIOS_DIR = os.environ.get("CARLA_SCENARIOS_DIR", "/workspace/scenarios")
SCENARIO_RUNNER_ROOT = os.environ.get("SCENARIO_RUNNER_ROOT", "/opt/scenario_runner")


CARLA_TO_COSMOS_FLU = np.diag([1.0, -1.0, 1.0])
STATIC_VEHICLE_LABEL_NAMES = ("Vehicles", "Vehicle", "Cars", "Car")
STATIC_VEHICLE_FALLBACK_LABEL_NAMES = ("Any", "Static", "Dynamic")
STATIC_VEHICLE_NAME_TOKENS = (
    "vehicle",
    "vehicles",
    "car",
    "cars",
    "truck",
    "bus",
    "van",
    "pickup",
    "sedan",
    "suv",
    "hatchback",
    "taxi",
    "ambulance",
    "motorcycle",
    "bike",
    "bicycle",
)


@dataclass(frozen=True)
class CameraSpec:
    """One CARLA camera mounted on the ego vehicle."""

    cosmos_name: str
    yaw_deg: float
    fov_deg: float
    location_xyz: tuple[float, float, float]
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @property
    def rig_name(self) -> str:
        """DriveWorks-style name expected by Cosmos calibration."""
        return self.cosmos_name.replace("_", ":")


CAMERA_SPECS: tuple[CameraSpec, ...] = (
    CameraSpec("camera_front_wide_120fov", 0.0, 120.0, (1.70, 0.00, 1.55)),
    CameraSpec("camera_front_tele_30fov", 0.0, 30.0, (1.75, 0.00, 1.60)),
    CameraSpec("camera_cross_left_120fov", -60.0, 120.0, (1.55, -0.35, 1.55)),
    CameraSpec("camera_cross_right_120fov", 60.0, 120.0, (1.55, 0.35, 1.55)),
    CameraSpec("camera_rear_left_70fov", -135.0, 70.0, (-0.75, -0.35, 1.50)),
    CameraSpec("camera_rear_right_70fov", 135.0, 70.0, (-0.75, 0.35, 1.50)),
    CameraSpec("camera_rear_tele_30fov", 180.0, 30.0, (-0.90, 0.00, 1.50)),
)


def load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.expanduser().read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def parse_set_overrides(values: Iterable[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"--set expects key=value, got: {item}")
        key, raw_value = item.split("=", 1)
        key = key.strip().replace("-", "_").replace(".", "_")
        raw_value = raw_value.strip()
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        overrides[key] = value
    return overrides


def spec_to_manifest(spec: CameraSpec) -> dict[str, Any]:
    return {
        "view_key": COSMOS_NAME_TO_VIEW_KEY.get(spec.cosmos_name, spec.cosmos_name),
        "cosmos_name": spec.cosmos_name,
        "rig_name": spec.rig_name,
        "yaw_deg": spec.yaw_deg,
        "fov_deg": spec.fov_deg,
        "location_xyz": list(spec.location_xyz),
        "pitch_deg": spec.pitch_deg,
        "roll_deg": spec.roll_deg,
    }


def load_camera_specs(path: Path | None) -> tuple[CameraSpec, ...]:
    """Load optional camera spec overrides from JSON."""
    if path is None:
        return CAMERA_SPECS
    data = json.loads(path.expanduser().read_text())
    cameras = data.get("cameras", data) if isinstance(data, dict) else data
    if isinstance(cameras, dict):
        iterable = []
        for key, value in cameras.items():
            if not isinstance(value, dict):
                raise ValueError(f"Camera config entry {key} must be an object")
            value = {"view_key": key, **value}
            iterable.append(value)
    elif isinstance(cameras, list):
        iterable = cameras
    else:
        raise ValueError("--camera-config must contain a list or object of camera definitions")

    specs = []
    for entry in iterable:
        if not isinstance(entry, dict):
            raise ValueError("Every camera config entry must be an object")
        view_key = entry.get("view_key") or entry.get("key")
        default_spec = None
        if view_key:
            default_spec = next(
                (spec for spec in CAMERA_SPECS if COSMOS_NAME_TO_VIEW_KEY.get(spec.cosmos_name) == view_key),
                None,
            )
        cosmos_name = entry.get("cosmos_name") or (default_spec.cosmos_name if default_spec else None)
        if cosmos_name is None:
            raise ValueError(f"Camera config entry is missing cosmos_name: {entry}")
        location = (
            entry.get("location_xyz") or entry.get("location") or (default_spec.location_xyz if default_spec else None)
        )
        if location is None or len(location) != 3:
            raise ValueError(f"Camera {cosmos_name} must define location_xyz with three numbers")
        specs.append(
            CameraSpec(
                cosmos_name=str(cosmos_name),
                yaw_deg=float(entry.get("yaw_deg", default_spec.yaw_deg if default_spec else 0.0)),
                fov_deg=float(entry.get("fov_deg", default_spec.fov_deg if default_spec else 90.0)),
                location_xyz=tuple(float(v) for v in location),
                pitch_deg=float(entry.get("pitch_deg", default_spec.pitch_deg if default_spec else 0.0)),
                roll_deg=float(entry.get("roll_deg", default_spec.roll_deg if default_spec else 0.0)),
            )
        )
    if not specs:
        raise ValueError("Camera config did not define any cameras")
    return tuple(specs)


def select_camera_specs(
    all_specs: tuple[CameraSpec, ...], views: list[str]
) -> tuple[list[str], tuple[CameraSpec, ...]]:
    spec_by_key = {COSMOS_NAME_TO_VIEW_KEY.get(spec.cosmos_name, spec.cosmos_name): spec for spec in all_specs}
    selected_keys = resolve_views(views, available=spec_by_key.keys())
    return selected_keys, tuple(spec_by_key[key] for key in selected_keys)


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=None, help="Optional JSON config file.")
    pre_args, _ = pre_parser.parse_known_args()
    config_defaults = load_json_object(pre_args.config) if pre_args.config is not None else {}
    config_defaults = {str(key).replace("-", "_").replace(".", "_"): value for key, value in config_defaults.items()}

    parser = argparse.ArgumentParser(description=__doc__, parents=[pre_parser])
    parser.add_argument("--host", default="127.0.0.1", help="CARLA server host.")
    parser.add_argument("--port", type=int, default=2000, help="CARLA RPC port.")
    parser.add_argument("--town", default="Town10HD", help="CARLA map/town to load.")
    parser.add_argument("--openscenario", default=None, help="OpenSCENARIO file path")
    parser.add_argument("--scenario", default=None, help="ScenarioRunner XML scenario name")
    parser.add_argument("--clip-id", default="carla_000001", help="Output clip ID prefix.")
    parser.add_argument("--fps", type=int, default=None, help="Alias for --capture-fps. Defaults to 10.")
    parser.add_argument("--sim-fps", type=int, default=None, help="CARLA synchronous simulation FPS. Defaults to 10.")
    parser.add_argument(
        "--capture-fps", type=int, default=None, help="RGB/timestamp capture FPS. Defaults to --sim-fps."
    )
    parser.add_argument("--seconds", type=float, default=20.0, help="Capture duration.")
    parser.add_argument("--width", type=int, default=1280, help="Camera image width.")
    parser.add_argument("--height", type=int, default=720, help="Camera image height.")
    parser.add_argument("--out", type=Path, default=Path("~/datasets/carla_cosmos"), help="Output root.")
    parser.add_argument(
        "--views",
        nargs="+",
        default=["all"],
        help=(
            "Camera views to capture. Use all or any of: "
            "front_wide front_tele cross_left cross_right rear_left rear_right rear."
        ),
    )
    parser.add_argument("--seed", type=int, default=7, help="Random seed for repeatability.")
    parser.add_argument("--vehicles", type=int, default=30, help="Background NPC vehicles to spawn.")
    parser.add_argument(
        "--traffic-radius-m",
        type=float,
        default=150.0,
        help="Prefer background vehicle spawn points within this many meters of the ego start. Use <=0 to disable.",
    )
    parser.add_argument("--lead-vehicles", type=int, default=0, help="Extra NPC vehicles to place ahead of ego.")
    parser.add_argument("--lead-start-m", type=float, default=30.0, help="Distance to the first lead vehicle.")
    parser.add_argument("--lead-spacing-m", type=float, default=30.0, help="Spacing between forced lead vehicles.")
    parser.add_argument("--walkers", type=int, default=0, help="Pedestrians to spawn near the ego start.")
    parser.add_argument(
        "--walker-radius-m",
        type=float,
        default=90.0,
        help="Prefer pedestrian navigation points within this many meters of the ego start. Use <=0 to disable.",
    )
    parser.add_argument(
        "--static-vehicle-radius-m",
        type=float,
        default=150.0,
        help=(
            "Export CARLA static environment objects labeled Vehicles within this many meters of the ego start "
            "as non-moving obstacle tracks. Use <=0 to disable."
        ),
    )
    parser.add_argument(
        "--ego-spawn-index",
        type=int,
        default=None,
        help="Use a specific CARLA spawn-point index for repeatable route selection.",
    )
    parser.add_argument(
        "--prefer-junction-start",
        action="store_true",
        help="Prefer ego spawn points that are at or near upcoming junctions.",
    )
    parser.add_argument(
        "--ego-control",
        choices=("waypoint", "none"),
        default="waypoint",
        help="How to move the ego vehicle during capture. Use 'none' only for static camera debugging.",
    )
    parser.add_argument("--ego-speed-kmh", type=float, default=25.0, help="Target ego speed for waypoint control.")
    parser.add_argument("--ego-lookahead-m", type=float, default=8.0, help="Waypoint follower lookahead distance.")
    parser.add_argument(
        "--junction-turn-probability",
        type=float,
        default=0.0,
        help="Chance of taking a non-straight branch when the waypoint follower sees multiple route choices.",
    )
    parser.add_argument(
        "--map-waypoint-spacing-m",
        type=float,
        default=1.0,
        help="Waypoint spacing for exported lane/lane-line map records. Smaller values produce denser controls.",
    )
    parser.add_argument("--traffic-manager-port", type=int, default=8005, help="CARLA TrafficManager port.")
    parser.add_argument("--client-timeout", type=float, default=120.0, help="CARLA RPC timeout in seconds.")
    parser.add_argument("--camera-timeout", type=float, default=5.0, help="Seconds to wait for each camera frame.")
    parser.add_argument("--warmup-ticks", type=int, default=5, help="Synchronous ticks before capture starts.")
    parser.add_argument(
        "--sensor-tick",
        type=float,
        default=0.0,
        help="CARLA camera sensor_tick. 0.0 captures every simulator tick and is safest in synchronous mode.",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Leave spawned actors in CARLA for debugging. Default destroys them.",
    )
    parser.add_argument("--weather-preset", default=None, help="CARLA WeatherParameters preset name.")
    parser.add_argument("--weather-json", type=Path, default=None, help="JSON object with CARLA weather fields.")
    parser.add_argument("--camera-config", type=Path, default=None, help="Optional JSON camera rig override.")
    parser.add_argument(
        "--traffic-manager-config",
        type=Path,
        default=None,
        help="Optional JSON mapping of TrafficManager method names to values.",
    )
    parser.add_argument(
        "--scenario-hook",
        default=None,
        help="Optional module:function hook called at capture lifecycle events.",
    )
    parser.add_argument(
        "--set",
        dest="set_values",
        action="append",
        default=[],
        help="Override config values with key=value. JSON values are accepted.",
    )
    if config_defaults:
        parser.set_defaults(**config_defaults)
    args = parser.parse_args()
    for key, value in parse_set_overrides(args.set_values).items():
        setattr(args, key, value)

    resolved_fps = args.capture_fps or args.sim_fps or args.fps or 10
    args.capture_fps = int(resolved_fps)
    args.sim_fps = int(args.sim_fps or resolved_fps)
    if args.capture_fps != args.sim_fps:
        raise ValueError("Stage 1 currently requires --capture-fps to match --sim-fps in synchronous CARLA mode.")
    args.fps = args.capture_fps
    if isinstance(args.views, str):
        args.views = [args.views]
    return args


def log_step(message: str) -> None:
    print(f"[carla-cosmos] {message}", flush=True)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def signed_angle_to_location(transform: Any, target_location: Any) -> float:
    location = transform.location
    dx = float(target_location.x - location.x)
    dy = float(target_location.y - location.y)
    yaw_rad = math.radians(float(transform.rotation.yaw))
    forward_x = math.cos(yaw_rad)
    forward_y = math.sin(yaw_rad)
    dot = forward_x * dx + forward_y * dy
    cross = forward_x * dy - forward_y * dx
    return math.atan2(cross, dot)


def transform_to_matrix(transform: carla.Transform) -> np.ndarray:
    """Return CARLA local-to-world matrix as float64 numpy."""
    return np.asarray(transform.get_matrix(), dtype=np.float64)


def point_to_dict(point: Iterable[float]) -> dict[str, float]:
    x, y, z = [float(v) for v in point]
    return {"x": x, "y": y, "z": z}


def quat_to_dict(quat_xyzw: Iterable[float]) -> dict[str, float]:
    x, y, z, w = [float(v) for v in quat_xyzw]
    return {"x": x, "y": y, "z": z, "w": w}


def carla_point_to_ego_flu(location: carla.Location, ego0_inv: np.ndarray) -> np.ndarray:
    """Convert a CARLA world point to ego-start-relative Cosmos FLU."""
    point_world = np.array([location.x, location.y, location.z, 1.0], dtype=np.float64)
    point_local_carla = (ego0_inv @ point_world)[:3]
    return (CARLA_TO_COSMOS_FLU @ point_local_carla).astype(np.float32)


def carla_transform_to_ego_flu_pose(
    transform: carla.Transform,
    ego0_inv: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert a CARLA world transform to ego-start-relative Cosmos FLU pose."""
    return carla_matrix_to_ego_flu_pose(transform_to_matrix(transform), ego0_inv)


def carla_matrix_to_ego_flu_pose(
    local_to_world: np.ndarray,
    ego0_inv: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert a CARLA local-to-world matrix to ego-start-relative Cosmos FLU pose."""
    local_carla = ego0_inv @ local_to_world
    position_flu = CARLA_TO_COSMOS_FLU @ local_carla[:3, 3]
    rotation_flu = CARLA_TO_COSMOS_FLU @ local_carla[:3, :3] @ CARLA_TO_COSMOS_FLU
    quat_xyzw = Rotation.from_matrix(rotation_flu).as_quat()
    return position_flu.astype(np.float32), quat_xyzw.astype(np.float32)


def carla_bbox_size(bbox: Any, default_size: Iterable[float]) -> np.ndarray:
    """Return a CARLA bounding-box size vector, falling back when extents are unusable."""
    try:
        size = np.array([2.0 * bbox.extent.x, 2.0 * bbox.extent.y, 2.0 * bbox.extent.z], dtype=np.float32)
    except AttributeError:
        return np.array(list(default_size), dtype=np.float32)
    if np.any(~np.isfinite(size)) or np.any(size <= 0.01):
        return np.array(list(default_size), dtype=np.float32)
    return size


def bbox_local_to_world_matrix(transform: Any, bbox: Any) -> np.ndarray:
    """Compose an actor transform with a local CARLA bounding-box transform."""
    bbox_rotation = getattr(bbox, "rotation", carla.Rotation())
    bbox_transform = carla.Transform(bbox.location, bbox_rotation)
    return transform_to_matrix(transform) @ transform_to_matrix(bbox_transform)


def traffic_light_box_world_matrix(transform: Any, bbox: Any) -> np.ndarray:
    """Return a world matrix for CARLA traffic-light visual boxes.

    CARLA actor bounding boxes are actor-local, while TrafficLight.get_light_boxes()
    has varied across CARLA versions and maps.  Treat boxes near the actor origin
    as already-world-space; otherwise compose them as actor-local boxes.
    """
    try:
        if carla_location_distance_m(bbox.location, transform.location) <= 25.0:
            bbox_rotation = getattr(bbox, "rotation", transform.rotation)
            return transform_to_matrix(carla.Transform(bbox.location, bbox_rotation))
    except (AttributeError, RuntimeError, TypeError):
        # Fall back to treating the box as actor-local.
        pass
    return bbox_local_to_world_matrix(transform, bbox)


def actor_bbox_pose_and_size(
    actor: Any,
    ego0_inv: np.ndarray,
    *,
    default_size: Iterable[float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return center, quaternion, and size for an actor's local bounding box."""
    transform = actor.get_transform()
    bbox = actor.bounding_box
    center_flu, quat_flu = carla_matrix_to_ego_flu_pose(bbox_local_to_world_matrix(transform, bbox), ego0_inv)
    return center_flu, quat_flu, carla_bbox_size(bbox, default_size)


def traffic_light_state_name(actor: Any) -> str:
    """Return a Cosmos/RDS-HQ traffic-light state name from a CARLA actor."""
    try:
        state = actor.get_state()
    except (AttributeError, RuntimeError):
        state = getattr(actor, "state", None)
    state_name = str(state).split(".")[-1].upper()
    if state_name in {"RED", "YELLOW", "GREEN", "OFF"}:
        return state_name
    return "UNKNOWN"


def traffic_light_visual_boxes(actor: Any) -> list[Any]:
    """Return the visual light boxes for a CARLA traffic light when available."""
    get_light_boxes = getattr(actor, "get_light_boxes", None)
    if callable(get_light_boxes):
        try:
            boxes = list(get_light_boxes())
        except RuntimeError:
            boxes = []
        if boxes:
            return boxes
    try:
        return [actor.bounding_box]
    except (AttributeError, RuntimeError):
        return []


def relative_sensor_to_rig_flu(spec: CameraSpec) -> tuple[np.ndarray, np.ndarray]:
    """Convert CARLA sensor transform relative to ego to Cosmos FLU extrinsics."""
    transform = carla.Transform(
        carla.Location(*spec.location_xyz),
        carla.Rotation(pitch=spec.pitch_deg, yaw=spec.yaw_deg, roll=spec.roll_deg),
    )
    mat_carla = transform_to_matrix(transform)
    position_flu = CARLA_TO_COSMOS_FLU @ mat_carla[:3, 3]
    rotation_flu = CARLA_TO_COSMOS_FLU @ mat_carla[:3, :3] @ CARLA_TO_COSMOS_FLU
    rpy_xyz = Rotation.from_matrix(rotation_flu).as_euler("xyz", degrees=True)
    return position_flu.astype(np.float32), rpy_xyz.astype(np.float32)


def pinhole_as_ftheta_polynomial(width: int, height: int, horizontal_fov_deg: float) -> np.ndarray:
    """Approximate a CARLA pinhole camera as Cosmos' f-theta forward polynomial."""
    fov_rad = math.radians(horizontal_fov_deg)
    fx = width / (2.0 * math.tan(fov_rad / 2.0))
    cx = width / 2.0
    cy = height / 2.0
    max_radius = math.hypot(max(cx, width - cx), max(cy, height - cy))
    max_theta = math.atan(max_radius / fx)
    theta = np.linspace(0.0, max_theta, 512, dtype=np.float64)
    radius = fx * np.tan(theta)
    coeffs = np.polynomial.polynomial.polyfit(theta, radius, deg=5)
    coeffs[0] = 0.0
    return coeffs.astype(np.float32)


def make_rig_json(
    width: int,
    height: int,
    camera_specs: Iterable[CameraSpec] = CAMERA_SPECS,
) -> dict[str, Any]:
    sensors = []
    for spec in camera_specs:
        position_flu, rpy_xyz = relative_sensor_to_rig_flu(spec)
        coeffs = pinhole_as_ftheta_polynomial(width, height, spec.fov_deg)
        sensors.append(
            {
                "name": spec.rig_name,
                "protocol": "camera.virtual",
                "properties": {
                    "Model": "ftheta",
                    "cx": f"{width / 2.0:.6f}",
                    "cy": f"{height / 2.0:.6f}",
                    "width": str(width),
                    "height": str(height),
                    "polynomial": " ".join(f"{v:.10g}" for v in coeffs),
                    "polynomial-type": "angle-to-pixeldistance",
                    "linear-c": "1.0",
                    "linear-d": "0.0",
                    "linear-e": "0.0",
                },
                "nominalSensor2Rig_FLU": {
                    "t": [float(v) for v in position_flu],
                    "roll-pitch-yaw": [float(v) for v in rpy_xyz],
                },
                "correction_sensor_R_FLU": {"roll-pitch-yaw": [0.0, 0.0, 0.0]},
                "correction_rig_T": [0.0, 0.0, 0.0],
            }
        )
    return {"rig": {"name": "CARLA_COSMOS_RIG", "sensors": sensors}}


def common_static_key(clip_id: str, map_name: str) -> dict[str, str]:
    return {
        "clip_id": clip_id,
        "label_class_id": "carla",
        "map_id": map_name,
        "map_id_version": "1",
    }


def dynamic_key(clip_id: str, timestamp_micros: int) -> dict[str, Any]:
    return {
        "clip_id": clip_id,
        "timestamp_micros": int(timestamp_micros),
        "label_class_id": "carla",
    }


def location_list(points: Iterable[np.ndarray]) -> list[dict[str, float]]:
    return [point_to_dict(p) for p in points]


def lane_marking_color(marking: Any) -> str:
    color = str(getattr(marking, "color", "White")).split(".")[-1].upper()
    if "YELLOW" in color:
        return "YELLOW"
    if "WHITE" in color:
        return "WHITE"
    return "UNKNOWN"


def lane_marking_style(marking: Any) -> str:
    kind = str(getattr(marking, "type", "Solid")).split(".")[-1].upper()
    if "BROKEN" in kind or "DASHED" in kind:
        return "LONG_DASHED_SINGLE"
    if "SOLIDSOLID" in kind or "DOUBLE" in kind:
        return "SOLID_GROUP"
    if "NONE" in kind or "GRASS" in kind or "CURB" in kind:
        return "VIRTUAL"
    return "SOLID_SINGLE"


def traffic_sign_category(type_id: str) -> str:
    value = type_id.upper().replace(".", "_")
    if "STOP" in value:
        return "STOP"
    if "YIELD" in value:
        return "YIELD"
    if "SPEED" in value:
        return "SPEED_LIMIT"
    if "NO_ENTRY" in value or "NOENTRY" in value:
        return "NO_ENTRY"
    if "ONE_WAY" in value or "ONEWAY" in value:
        return "ONE_WAY"
    if "PEDESTRIAN" in value or "CROSSING" in value:
        return "PEDESTRIAN_CROSSING"
    return "UNKNOWN"


def lane_type_name(waypoint: Any | None) -> str:
    if waypoint is None:
        return "NONE"
    return str(getattr(waypoint, "lane_type", "NONE")).split(".")[-1].upper()


def adjacent_lane_type_name(waypoint: Any, side: str) -> str:
    try:
        adjacent = waypoint.get_left_lane() if side == "left" else waypoint.get_right_lane()
    except RuntimeError:
        return "NONE"
    return lane_type_name(adjacent)


def road_boundary_category(marking: Any, adjacent_lane_type: str) -> str | None:
    """Classify CARLA lane edges that should render as physical road boundaries."""
    kind = str(getattr(marking, "type", "")).split(".")[-1].upper()
    if kind == "CURB":
        return "tall_curb"
    if kind == "GRASS":
        return "road_boundary"
    if lane_marking_style(marking) != "VIRTUAL":
        return None
    if adjacent_lane_type in {"NONE", "SIDEWALK"}:
        return "tall_curb"
    if adjacent_lane_type in {"SHOULDER", "BORDER", "PARKING", "RESTRICTED", "BIKING", "MEDIAN"}:
        return "road_boundary"
    return None


def waypoint_boundary_points(
    waypoints: list[Any],
    side: str,
    ego0_inv: np.ndarray,
) -> list[np.ndarray]:
    points = []
    for wp in waypoints:
        loc = wp.transform.location
        right = wp.transform.get_right_vector()
        half_width = float(wp.lane_width) / 2.0
        sign = -1.0 if side == "left" else 1.0
        edge = carla.Location(
            x=loc.x + sign * right.x * half_width,
            y=loc.y + sign * right.y * half_width,
            z=loc.z + 0.05,
        )
        points.append(carla_point_to_ego_flu(edge, ego0_inv))
    return points


def collect_map_records(
    world: Any,
    clip_id: str,
    ego0_inv: np.ndarray,
    waypoint_spacing_m: float = 1.0,
) -> dict[str, list[dict[str, Any]]]:
    """Best-effort export of static CARLA map data to Cosmos schemas."""
    world_map = world.get_map()
    map_name = world_map.name
    static_key = common_static_key(clip_id, map_name)
    waypoint_spacing_m = max(0.25, float(waypoint_spacing_m))

    records: dict[str, list[dict[str, Any]]] = {
        "lane": [],
        "lane_line": [],
        "road_boundary": [],
        "crosswalk": [],
        "pole": [],
        "road_marking": [],
        "wait_line": [],
        "traffic_light": [],
        "traffic_sign": [],
    }

    lanes: dict[tuple[int, int, int], list[Any]] = {}
    for wp in world_map.generate_waypoints(waypoint_spacing_m):
        lane_type = str(wp.lane_type).split(".")[-1].upper()
        if lane_type != "DRIVING":
            continue
        key = (int(wp.road_id), int(wp.section_id), int(wp.lane_id))
        lanes.setdefault(key, []).append(wp)

    boundary_seen: set[tuple[int, int, int, str]] = set()
    for lane_key, waypoints in lanes.items():
        waypoints.sort(key=lambda item: float(item.s))
        if len(waypoints) < 2:
            continue

        left = waypoint_boundary_points(waypoints, "left", ego0_inv)
        right = waypoint_boundary_points(waypoints, "right", ego0_inv)
        first = waypoints[0]

        records["lane"].append(
            {
                "key": static_key,
                "lane": {
                    "left_rail": location_list(left),
                    "right_rail": location_list(right),
                    "left_edge_styles": [lane_marking_style(first.left_lane_marking)] * max(len(left) - 1, 1),
                    "right_edge_styles": [lane_marking_style(first.right_lane_marking)] * max(len(right) - 1, 1),
                    "left_edge_colors": [lane_marking_color(first.left_lane_marking)] * max(len(left) - 1, 1),
                    "right_edge_colors": [lane_marking_color(first.right_lane_marking)] * max(len(right) - 1, 1),
                    "lane_direction": "STRAIGHT",
                    "speed_limit": 0.0,
                    "vehicle_types": ["CAR"],
                },
                "version": 1,
            }
        )

        for side, points, marking in (
            ("left", left, first.left_lane_marking),
            ("right", right, first.right_lane_marking),
        ):
            if len(points) < 2:
                continue
            boundary_key = (*lane_key, side)
            if boundary_key in boundary_seen:
                continue
            boundary_seen.add(boundary_key)

            adjacent_lane_type = adjacent_lane_type_name(first, side)
            boundary_category = road_boundary_category(marking, adjacent_lane_type)
            if boundary_category is not None:
                records["road_boundary"].append(
                    {
                        "key": static_key,
                        "road_boundary": {
                            "category": boundary_category,
                            "location": location_list(points),
                        },
                        "version": 1,
                    }
                )

            style = lane_marking_style(marking)
            if style == "VIRTUAL":
                continue
            color = lane_marking_color(marking)

            records["lane_line"].append(
                {
                    "key": static_key,
                    "lane_line": {
                        "line_rail": location_list(points),
                        "styles": [style] * len(points),
                        "colors": [color] * len(points),
                        "left_driving_direction": "UNKNOWN",
                        "right_driving_direction": "UNKNOWN",
                    },
                    "version": 1,
                }
            )

    # CARLA returns one flat list containing closed crosswalk polygons.
    crosswalk_locations = list(world_map.get_crosswalks())
    current_polygon: list[np.ndarray] = []
    first_location: carla.Location | None = None
    for loc in crosswalk_locations:
        if first_location is None:
            first_location = loc
        current_polygon.append(carla_point_to_ego_flu(loc, ego0_inv))
        same_as_first = first_location is not None and loc.distance(first_location) < 0.05
        if same_as_first and len(current_polygon) > 3:
            records["crosswalk"].append(
                {
                    "key": static_key,
                    "crosswalk": {
                        "category": "PEDESTRIAN",
                        "location": location_list(current_polygon),
                    },
                    "version": 1,
                }
            )
            records["road_marking"].append(
                {
                    "key": static_key,
                    "road_marking": {
                        "category": "ROI_POLYGON_ROAD_MARKING_PED_XING",
                        "location": location_list(current_polygon),
                    },
                    "version": 1,
                }
            )
            current_polygon = []
            first_location = None

    for actor in world.get_actors():
        type_id = actor.type_id.lower()
        if not type_id.startswith("traffic."):
            continue

        transform = actor.get_transform()
        actor_id = int(getattr(actor, "id", len(records["traffic_light"]) + len(records["traffic_sign"])))

        if "traffic_light" in type_id:
            for box_index, bbox in enumerate(traffic_light_visual_boxes(actor)):
                center_flu, quat_flu = carla_matrix_to_ego_flu_pose(
                    traffic_light_box_world_matrix(transform, bbox),
                    ego0_inv,
                )
                dims = carla_bbox_size(bbox, [0.6, 0.35, 0.9])
                if float(np.max(dims)) > 3.0:
                    dims = np.array([0.6, 0.35, 0.9], dtype=np.float32)
                feature_id = f"carla_traffic_light_{actor_id}_{box_index}"
                records["traffic_light"].append(
                    {
                        "key": static_key,
                        "traffic_light": {
                            "center": point_to_dict(center_flu),
                            "dimensions": point_to_dict(dims),
                            "orientation": quat_to_dict(quat_flu),
                            "category": "traffic_light",
                            "actor_id": actor_id,
                            "feature_id": feature_id,
                            "state_sequence": [],
                        },
                        "version": 1,
                    }
                )
            pole_category = "LIGHT"
        else:
            center_flu, quat_flu, dims = actor_bbox_pose_and_size(
                actor,
                ego0_inv,
                default_size=[0.8, 0.3, 0.8],
            )
            records["traffic_sign"].append(
                {
                    "key": static_key,
                    "traffic_sign": {
                        "center": point_to_dict(center_flu),
                        "dimensions": point_to_dict(dims),
                        "orientation": quat_to_dict(quat_flu),
                        "category": traffic_sign_category(type_id),
                        "actor_id": actor_id,
                        "feature_id": f"carla_traffic_sign_{actor_id}",
                    },
                    "version": 1,
                }
            )
            pole_category = "SIGN"

        pole_base = carla_point_to_ego_flu(transform.location, ego0_inv)
        pole_top = pole_base.copy()
        pole_base[2] = 0.0
        pole_top[2] = max(float(pole_top[2]), 2.5)
        records["pole"].append(
            {
                "key": static_key,
                "pole": {
                    "category": pole_category,
                    "location": location_list([pole_base, pole_top]),
                },
                "version": 1,
            }
        )

        try:
            wp = world_map.get_waypoint(transform.location, project_to_road=True)
            right = wp.transform.get_right_vector()
            forward = wp.transform.get_forward_vector()
            stop_center = carla.Location(
                x=wp.transform.location.x - forward.x * 3.0,
                y=wp.transform.location.y - forward.y * 3.0,
                z=wp.transform.location.z + 0.05,
            )
            half = max(float(wp.lane_width), 3.0) / 2.0
            a = carla.Location(
                x=stop_center.x - right.x * half,
                y=stop_center.y - right.y * half,
                z=stop_center.z,
            )
            b = carla.Location(
                x=stop_center.x + right.x * half,
                y=stop_center.y + right.y * half,
                z=stop_center.z,
            )
            records["wait_line"].append(
                {
                    "key": static_key,
                    "wait_line": {
                        "category": "STOP" if "stop" in type_id else "UNKNOWN",
                        "location": location_list(
                            [
                                carla_point_to_ego_flu(a, ego0_inv),
                                carla_point_to_ego_flu(b, ego0_inv),
                            ]
                        ),
                        "is_implicit": True,
                        "intersection_subtype": "UNKNOWN",
                    },
                    "version": 1,
                }
            )
        except RuntimeError:
            # Stop-line metadata is best-effort; skip lights without a drivable waypoint.
            pass

    return records


def append_traffic_light_state_samples(rows_by_element: dict[str, list[dict[str, Any]]], world: Any) -> None:
    """Append one CARLA traffic-light state sample to every exported light box."""
    traffic_light_rows = rows_by_element.get("traffic_light", [])
    if not traffic_light_rows:
        return

    actors_by_id: dict[int, Any] = {}
    for actor in world.get_actors():
        if "traffic_light" not in getattr(actor, "type_id", "").lower():
            continue
        try:
            actors_by_id[int(actor.id)] = actor
        except (AttributeError, TypeError, ValueError):
            continue

    for row in traffic_light_rows:
        traffic_light = row.get("traffic_light", {})
        state_sequence = traffic_light.setdefault("state_sequence", [])
        actor = None
        try:
            actor = actors_by_id.get(int(traffic_light.get("actor_id")))
        except (TypeError, ValueError):
            actor = None
        state_sequence.append(traffic_light_state_name(actor) if actor is not None else "UNKNOWN")


def actor_category(actor: Any) -> str:
    type_id = actor.type_id.lower()
    if type_id.startswith("walker."):
        return "person"
    if "bicycle" in type_id or "motorcycle" in type_id:
        return "rider"
    if "truck" in type_id or "bus" in type_id or "trailer" in type_id:
        return "heavy_truck"
    return "automobile"


def carla_location_distance_m(a: Any, b: Any) -> float:
    dx = float(a.x - b.x)
    dy = float(a.y - b.y)
    dz = float(a.z - b.z)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def city_object_label_names() -> list[str]:
    label_cls = getattr(carla, "CityObjectLabel", None)
    if label_cls is None:
        return []
    names = []
    for name in dir(label_cls):
        if name.startswith("_"):
            continue
        try:
            value = getattr(label_cls, name)
        except AttributeError:
            continue
        if callable(value):
            continue
        names.append(name)
    return sorted(names)


def city_object_label_candidates(label_names: Iterable[str]) -> list[tuple[str, Any]]:
    label_cls = getattr(carla, "CityObjectLabel", None)
    if label_cls is None:
        return []
    available = {name.lower(): name for name in city_object_label_names()}
    candidates = []
    for requested_name in label_names:
        actual_name = available.get(requested_name.lower())
        if actual_name is None:
            continue
        candidates.append((actual_name, getattr(label_cls, actual_name)))
    return candidates


def environment_object_description(env_object: Any) -> str:
    parts = []
    for attr_name in ("name", "type", "label"):
        try:
            value = getattr(env_object, attr_name)
        except AttributeError:
            continue
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def environment_object_looks_like_vehicle(env_object: Any) -> bool:
    description = environment_object_description(env_object)
    return any(token in description for token in STATIC_VEHICLE_NAME_TOKENS)


def environment_object_center_world(env_object: Any) -> Any:
    bbox = env_object.bounding_box
    transform = env_object.transform
    bbox_loc = bbox.location
    transform_loc = transform.location

    # CARLA environment-object bounding boxes are commonly already in world
    # coordinates, while actor bounding boxes are local to their transform.
    # Handle both conventions defensively.
    if carla_location_distance_m(bbox_loc, transform_loc) <= 50.0:
        return bbox_loc
    return transform.transform(bbox_loc)


def collect_static_vehicle_obstacles(
    world: Any,
    ego_vehicle: Any,
    ego0_inv: np.ndarray,
    radius_m: float,
) -> list[dict[str, Any]]:
    if radius_m <= 0.0:
        return []

    vehicle_labels = city_object_label_candidates(STATIC_VEHICLE_LABEL_NAMES)
    exact_vehicle_label_available = bool(vehicle_labels)
    if exact_vehicle_label_available:
        labels_to_query = vehicle_labels
    else:
        labels_to_query = city_object_label_candidates(STATIC_VEHICLE_FALLBACK_LABEL_NAMES)[:1]

    if not labels_to_query:
        available = ", ".join(city_object_label_names()) or "unknown"
        log_step(
            f"Could not query static vehicle environment objects: no usable CityObjectLabel; available: {available}"
        )
        return []

    if not exact_vehicle_label_available:
        available = ", ".join(city_object_label_names()) or "unknown"
        queried = ", ".join(name for name, _ in labels_to_query)
        log_step(
            "CityObjectLabel has no vehicle-specific enum; "
            f"scanning {queried} environment objects for vehicle-like names. Available labels: {available}"
        )

    env_objects = []
    seen_env_object_ids: set[int] = set()
    for label_name, label in labels_to_query:
        try:
            label_objects = world.get_environment_objects(label)
        except RuntimeError as exc:
            log_step(f"Could not query {label_name} environment objects: {exc}")
            continue
        for env_object in label_objects:
            try:
                object_id = int(env_object.id)
            except (AttributeError, TypeError, ValueError):
                object_id = hash((label_name, environment_object_description(env_object), len(seen_env_object_ids)))
            if object_id in seen_env_object_ids:
                continue
            seen_env_object_ids.add(object_id)
            if exact_vehicle_label_available or environment_object_looks_like_vehicle(env_object):
                env_objects.append(env_object)

    ego_start = ego_vehicle.get_transform().location
    obstacles: list[dict[str, Any]] = []
    for env_object in env_objects:
        try:
            bbox = env_object.bounding_box
            transform = env_object.transform
            center_world = environment_object_center_world(env_object)
        except RuntimeError:
            continue

        if carla_location_distance_m(center_world, ego_start) > radius_m:
            continue

        size = np.array([2.0 * bbox.extent.x, 2.0 * bbox.extent.y, 2.0 * bbox.extent.z], dtype=np.float32)
        if np.any(~np.isfinite(size)) or np.any(size <= 0.01):
            continue

        center_flu = carla_point_to_ego_flu(center_world, ego0_inv)
        _, quat_flu = carla_transform_to_ego_flu_pose(transform, ego0_inv)
        obstacles.append(
            {
                "trackline_id": f"static_vehicle:{env_object.id}",
                "center": point_to_dict(center_flu),
                "size": point_to_dict(size),
                "orientation": quat_to_dict(quat_flu),
                "category": "automobile",
            }
        )

    return obstacles


def timestamp_obstacle_rows(
    clip_id: str,
    timestamp_micros: int,
    obstacles: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "key": dynamic_key(clip_id, timestamp_micros),
            "obstacle": obstacle,
            "version": 1,
        }
        for obstacle in obstacles
    ]


def collect_obstacle_rows(
    world: Any,
    ego_vehicle: Any,
    ego0_inv: np.ndarray,
    clip_id: str,
    timestamp_micros: int,
) -> list[dict[str, Any]]:
    rows = []
    actors = list(world.get_actors().filter("vehicle.*")) + list(world.get_actors().filter("walker.pedestrian.*"))

    for actor in actors:
        if actor.id == ego_vehicle.id:
            continue
        try:
            transform = actor.get_transform()
            bbox = actor.bounding_box
        except RuntimeError:
            continue

        center_world = transform.transform(bbox.location)
        center_flu = carla_point_to_ego_flu(center_world, ego0_inv)
        _, quat_flu = carla_transform_to_ego_flu_pose(transform, ego0_inv)
        size = np.array([2.0 * bbox.extent.x, 2.0 * bbox.extent.y, 2.0 * bbox.extent.z], dtype=np.float32)

        rows.extend(
            timestamp_obstacle_rows(
                clip_id,
                timestamp_micros,
                [
                    {
                        "trackline_id": str(actor.id),
                        "center": point_to_dict(center_flu),
                        "size": point_to_dict(size),
                        "orientation": quat_to_dict(quat_flu),
                        "category": actor_category(actor),
                    }
                ],
            )
        )

    return rows


def wait_for_camera_frame(
    frame_queue: queue.Queue[Any],
    frame_id: int,
    camera_name: str,
    timeout: float,
) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            image = frame_queue.get(timeout=max(0.01, deadline - time.time()))
        except queue.Empty as exc:
            raise TimeoutError(f"Timed out waiting for {camera_name} frame {frame_id}") from exc
        if image.frame == frame_id:
            return image
        if image.frame > frame_id:
            raise RuntimeError(
                f"{camera_name} produced frame {image.frame}, but expected {frame_id}. "
                "This usually means the sensor queues got out of sync."
            )
    raise TimeoutError(f"Timed out waiting for {camera_name} frame {frame_id}")


def wait_for_camera_frame_at_or_after(
    frame_queue: queue.Queue[Any],
    min_frame_id: int,
    camera_name: str,
    timeout: float,
) -> Any:
    """Return the first camera frame whose CARLA frame is >= min_frame_id."""
    deadline = time.time() + timeout
    last_seen: int | None = None
    while time.time() < deadline:
        try:
            image = frame_queue.get(timeout=max(0.01, deadline - time.time()))
        except queue.Empty as exc:
            extra = f"; last seen frame {last_seen}" if last_seen is not None else ""
            raise TimeoutError(f"Timed out waiting for {camera_name} frame >= {min_frame_id}{extra}") from exc
        last_seen = int(image.frame)
        if image.frame >= min_frame_id:
            return image
    extra = f"; last seen frame {last_seen}" if last_seen is not None else ""
    raise TimeoutError(f"Timed out waiting for {camera_name} frame >= {min_frame_id}{extra}")


def drain_camera_queues(camera_queues: dict[str, queue.Queue[Any]]) -> None:
    for frame_queue in camera_queues.values():
        while True:
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                break


def warmup_cameras(
    world: Any,
    camera_queues: dict[str, queue.Queue[Any]],
    ticks: int,
    timeout: float,
) -> None:
    """Tick until every camera has produced at least one frame."""
    seen_cameras: set[str] = set()
    max_attempts = max(1, ticks) + 20
    for warmup_idx in range(max_attempts):
        frame_id = world.tick()
        for camera_name, frame_queue in camera_queues.items():
            if camera_name in seen_cameras and warmup_idx >= ticks:
                continue
            try:
                image = wait_for_camera_frame_at_or_after(frame_queue, frame_id, camera_name, timeout)
            except TimeoutError:
                if warmup_idx < max_attempts - 1:
                    log_step(f"{camera_name} did not publish for warmup tick {frame_id}; retrying")
                    continue
                raise
            if camera_name not in seen_cameras:
                log_step(f"{camera_name} first sensor frame: {image.frame}")
            seen_cameras.add(camera_name)
        if warmup_idx + 1 >= ticks and len(seen_cameras) == len(camera_queues):
            break
    drain_camera_queues(camera_queues)


def image_to_bgr(image: Any) -> np.ndarray:
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = array.reshape((image.height, image.width, 4))
    return array[:, :, :3].copy()


class EgoWaypointFollower:
    """Small self-contained ego driver for short CARLA capture clips."""

    def __init__(
        self,
        world_map: Any,
        target_speed_kmh: float,
        lookahead_m: float,
        rng: random.Random,
        branch_probability: float,
    ) -> None:
        self.world_map = world_map
        self.target_speed_mps = max(0.1, target_speed_kmh / 3.6)
        self.lookahead_m = max(2.0, lookahead_m)
        self.rng = rng
        self.branch_probability = clamp(branch_probability, 0.0, 1.0)
        self.target_waypoint: Any | None = None

    def _signed_angle_to_location(self, transform: Any, target_location: Any) -> float:
        return signed_angle_to_location(transform, target_location)

    def _select_target_waypoint(self, transform: Any) -> Any | None:
        location = transform.location
        refresh_distance = max(2.0, self.lookahead_m * 0.5)
        if (
            self.target_waypoint is not None
            and location.distance(self.target_waypoint.transform.location) > refresh_distance
        ):
            return self.target_waypoint

        try:
            current = self.world_map.get_waypoint(
                location,
                project_to_road=True,
                lane_type=carla.LaneType.Driving,
            )
        except RuntimeError:
            current = None
        if current is None:
            return None

        candidates = list(current.next(self.lookahead_m))
        if not candidates:
            self.target_waypoint = current
            return self.target_waypoint

        candidates.sort(key=lambda wp: abs(self._signed_angle_to_location(transform, wp.transform.location)))
        if len(candidates) > 1 and self.rng.random() < self.branch_probability:
            self.target_waypoint = self.rng.choice(candidates[1:])
            return self.target_waypoint
        self.target_waypoint = candidates[0]
        return self.target_waypoint

    def apply_control(self, ego_vehicle: Any) -> None:
        transform = ego_vehicle.get_transform()
        waypoint = self._select_target_waypoint(transform)
        steer = 0.0
        if waypoint is not None:
            angle = self._signed_angle_to_location(transform, waypoint.transform.location)
            steer = clamp(1.8 * angle, -0.65, 0.65)

        velocity = ego_vehicle.get_velocity()
        speed_mps = math.sqrt(velocity.x * velocity.x + velocity.y * velocity.y + velocity.z * velocity.z)
        throttle = 0.45
        brake = 0.0
        if speed_mps > self.target_speed_mps + 1.5:
            throttle = 0.0
            brake = 0.20
        elif speed_mps > self.target_speed_mps:
            throttle = 0.05

        ego_vehicle.apply_control(
            carla.VehicleControl(
                throttle=float(throttle),
                steer=float(steer),
                brake=float(brake),
                hand_brake=False,
                reverse=False,
            )
        )


def vehicle_blueprints(world: Any) -> list[Any]:
    vehicle_bps = []
    for bp in world.get_blueprint_library().filter("vehicle.*"):
        if not bp.has_attribute("number_of_wheels"):
            vehicle_bps.append(bp)
            continue
        wheels_attr = bp.get_attribute("number_of_wheels")
        try:
            wheels = wheels_attr.as_int()
        except AttributeError:
            wheels = int(str(wheels_attr))
        if wheels >= 4:
            vehicle_bps.append(bp)
    if not vehicle_bps:
        vehicle_bps = list(world.get_blueprint_library().filter("vehicle.*"))
    return vehicle_bps


def configure_vehicle_blueprint(bp: Any, rng: random.Random, role_name: str | None = None) -> Any:
    if role_name is not None and bp.has_attribute("role_name"):
        bp.set_attribute("role_name", role_name)
    if bp.has_attribute("color"):
        colors = bp.get_attribute("color").recommended_values
        if colors:
            bp.set_attribute("color", rng.choice(colors))
    if bp.has_attribute("driver_id"):
        driver_ids = bp.get_attribute("driver_id").recommended_values
        if driver_ids:
            bp.set_attribute("driver_id", rng.choice(driver_ids))
    return bp


def spawn_point_junction_score(world_map: Any, transform: Any) -> float:
    try:
        waypoint = world_map.get_waypoint(
            transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
    except RuntimeError:
        return 0.0
    if waypoint is None:
        return 0.0
    if waypoint.is_junction:
        return 1000.0

    score = 0.0
    probe = waypoint
    distance_m = 0.0
    while distance_m < 80.0:
        candidates = list(probe.next(5.0))
        if not candidates:
            break
        if len(candidates) > 1:
            score = max(score, 200.0 - distance_m)
        probe = candidates[0]
        distance_m += 5.0
        if probe.is_junction:
            return 900.0 - distance_m
    return score


def ordered_ego_spawn_points(
    world: Any,
    rng: random.Random,
    spawn_index: int | None,
    prefer_junction_start: bool,
) -> list[Any]:
    spawn_points = list(world.get_map().get_spawn_points())
    if not spawn_points:
        return []

    if spawn_index is not None:
        if spawn_index < 0 or spawn_index >= len(spawn_points):
            raise ValueError(f"--ego-spawn-index must be between 0 and {len(spawn_points) - 1}")
        selected = spawn_points[spawn_index]
        rest = spawn_points[:spawn_index] + spawn_points[spawn_index + 1 :]
        rng.shuffle(rest)
        return [selected, *rest]

    rng.shuffle(spawn_points)
    if prefer_junction_start:
        world_map = world.get_map()
        spawn_points.sort(key=lambda transform: spawn_point_junction_score(world_map, transform), reverse=True)
    return spawn_points


def spawn_ego_vehicle(
    world: Any,
    rng: random.Random,
    spawn_index: int | None = None,
    prefer_junction_start: bool = False,
) -> Any:
    blueprints = world.get_blueprint_library().filter("vehicle.*")
    preferred = [bp for bp in blueprints if "lincoln.mkz_2020" in bp.id]
    ego_bp = preferred[0] if preferred else rng.choice(list(blueprints))
    configure_vehicle_blueprint(ego_bp, rng, role_name="hero")

    spawn_points = ordered_ego_spawn_points(world, rng, spawn_index, prefer_junction_start)
    for transform in spawn_points:
        ego = world.try_spawn_actor(ego_bp, transform)
        if ego is not None:
            return ego
    raise RuntimeError("Could not spawn ego vehicle. Try a different town or restart CARLA.")


def lifted_transform(transform: Any, z_offset: float = 0.35) -> Any:
    return carla.Transform(
        carla.Location(
            x=float(transform.location.x),
            y=float(transform.location.y),
            z=float(transform.location.z) + z_offset,
        ),
        transform.rotation,
    )


def spawn_vehicle_at_transform(
    world: Any,
    transform: Any,
    traffic_manager_port: int,
    rng: random.Random,
    vehicle_bps: list[Any],
) -> Any | None:
    if not vehicle_bps:
        return None
    bp = configure_vehicle_blueprint(rng.choice(vehicle_bps), rng, role_name="autopilot")
    actor = world.try_spawn_actor(bp, lifted_transform(transform))
    if actor is None:
        return None
    actor.set_autopilot(True, traffic_manager_port)
    return actor


def ordered_traffic_spawn_points(
    world: Any,
    ego_vehicle: Any,
    rng: random.Random,
    traffic_radius_m: float,
) -> list[Any]:
    spawn_points = list(world.get_map().get_spawn_points())
    rng.shuffle(spawn_points)
    if traffic_radius_m <= 0.0:
        return spawn_points

    ego_loc = ego_vehicle.get_transform().location
    near = []
    far = []
    for transform in spawn_points:
        if transform.location.distance(ego_loc) <= traffic_radius_m:
            near.append(transform)
        else:
            far.append(transform)
    return near + far


def spawn_traffic(
    world: Any,
    ego_vehicle: Any,
    count: int,
    traffic_manager_port: int,
    rng: random.Random,
    traffic_radius_m: float,
) -> list[Any]:
    vehicles: list[Any] = []
    vehicle_bps = vehicle_blueprints(world)
    spawn_points = ordered_traffic_spawn_points(world, ego_vehicle, rng, traffic_radius_m)
    ego_loc = ego_vehicle.get_transform().location

    for transform in spawn_points:
        if len(vehicles) >= count:
            break
        if transform.location.distance(ego_loc) < 10.0:
            continue
        actor = spawn_vehicle_at_transform(world, transform, traffic_manager_port, rng, vehicle_bps)
        if actor is None:
            continue
        vehicles.append(actor)

    return vehicles


def advance_waypoint_straightish(waypoint: Any, distance_m: float, reference_transform: Any) -> Any | None:
    candidates = list(waypoint.next(distance_m))
    if not candidates:
        return None
    candidates.sort(key=lambda wp: abs(signed_angle_to_location(reference_transform, wp.transform.location)))
    return candidates[0]


def route_waypoint_at_distance(waypoint: Any, distance_m: float, reference_transform: Any) -> Any | None:
    """Follow the straightest route branch for roughly distance_m meters."""
    probe = waypoint
    remaining_m = max(0.0, float(distance_m))
    while remaining_m > 0.0:
        step_m = min(5.0, remaining_m)
        next_probe = advance_waypoint_straightish(probe, step_m, reference_transform)
        if next_probe is None:
            return None
        probe = next_probe
        reference_transform = probe.transform
        remaining_m -= step_m
    return probe


def lead_vehicle_candidate_distances(target_m: float, min_distance_m: float) -> list[float]:
    distances = []
    for offset_m in (0.0, 4.0, -4.0, 8.0, -8.0, 12.0, 16.0, 24.0):
        distance_m = float(target_m) + offset_m
        if distance_m >= min_distance_m:
            distances.append(distance_m)
    return distances


def spawn_lead_vehicles(
    world: Any,
    ego_vehicle: Any,
    count: int,
    start_m: float,
    spacing_m: float,
    traffic_manager_port: int,
    rng: random.Random,
) -> list[Any]:
    vehicles: list[Any] = []
    if count <= 0:
        return vehicles

    try:
        waypoint = world.get_map().get_waypoint(
            ego_vehicle.get_transform().location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
    except RuntimeError:
        waypoint = None
    if waypoint is None:
        return vehicles

    vehicle_bps = vehicle_blueprints(world)
    route_start_transform = ego_vehicle.get_transform()
    first_gap = max(12.0, float(start_m))
    spacing = max(10.0, float(spacing_m))
    last_spawn_distance_m = 0.0
    for idx in range(count):
        target_distance_m = first_gap + idx * spacing
        min_distance_m = max(10.0, last_spawn_distance_m + 10.0)
        actor = None
        for distance_m in lead_vehicle_candidate_distances(target_distance_m, min_distance_m):
            candidate = route_waypoint_at_distance(waypoint, distance_m, route_start_transform)
            if candidate is None:
                continue
            actor = spawn_vehicle_at_transform(world, candidate.transform, traffic_manager_port, rng, vehicle_bps)
            if actor is not None:
                last_spawn_distance_m = distance_m
                break
        if actor is None:
            log_step(f"Could not place lead vehicle {idx + 1}/{count} near {target_distance_m:.1f} m ahead")
            continue
        vehicles.append(actor)
    return vehicles


def relative_xy(transform: Any, location: Any) -> tuple[float, float]:
    origin = transform.location
    dx = float(location.x - origin.x)
    dy = float(location.y - origin.y)
    forward = transform.get_forward_vector()
    right = transform.get_right_vector()
    forward_m = dx * float(forward.x) + dy * float(forward.y)
    right_m = dx * float(right.x) + dy * float(right.y)
    return forward_m, right_m


def pedestrian_visibility_score(ego_transform: Any, location: Any, radius_m: float) -> float | None:
    """Score navmesh points that are likely to appear in front/cross cameras."""
    forward_m, right_m = relative_xy(ego_transform, location)
    distance_m = math.hypot(forward_m, right_m)
    if distance_m < 8.0:
        return None
    if radius_m > 0.0 and distance_m > radius_m:
        return None
    if forward_m < -12.0 or forward_m > 75.0:
        return None
    if abs(right_m) > 45.0:
        return None

    score = 100.0
    score -= abs(forward_m - 28.0) * 1.2
    score -= max(0.0, abs(right_m) - 18.0) * 1.3
    if 10.0 <= forward_m <= 55.0:
        score += 20.0
    if 3.0 <= abs(right_m) <= 22.0:
        score += 15.0
    if -4.0 <= right_m <= 4.0 and 10.0 <= forward_m <= 40.0:
        score += 10.0
    return score


def add_spaced_location(
    selected: list[Any],
    location: Any,
    min_spacing_m: float,
) -> bool:
    if any(location.distance(existing) < min_spacing_m for existing in selected):
        return False
    selected.append(location)
    return True


def navigation_locations_near_ego(
    world: Any,
    ego_vehicle: Any,
    count: int,
    radius_m: float,
    rng: random.Random,
) -> list[Any]:
    ego_transform = ego_vehicle.get_transform()
    ego_loc = ego_transform.location
    scored_locations = []
    fallback_locations = []
    max_attempts = max(600, count * 150)
    for _ in range(max_attempts):
        loc = world.get_random_location_from_navigation()
        if loc is None:
            continue
        if loc.distance(ego_loc) < 8.0:
            continue
        if radius_m > 0.0 and loc.distance(ego_loc) > radius_m:
            continue
        fallback_locations.append(loc)
        score = pedestrian_visibility_score(ego_transform, loc, radius_m)
        if score is not None:
            scored_locations.append((score, rng.random(), loc))

    scored_locations.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected: list[Any] = []
    for _, _, loc in scored_locations:
        if add_spaced_location(selected, loc, 3.0) and len(selected) >= count:
            return selected

    rng.shuffle(fallback_locations)
    for loc in fallback_locations:
        if add_spaced_location(selected, loc, 3.0) and len(selected) >= count:
            return selected

    while len(selected) < count:
        loc = world.get_random_location_from_navigation()
        if loc is None or loc.distance(ego_loc) < 8.0:
            break
        if radius_m > 0.0 and loc.distance(ego_loc) > radius_m:
            continue
        add_spaced_location(selected, loc, 3.0)
    return selected[:count]


def spawn_walkers(
    world: Any,
    ego_vehicle: Any,
    count: int,
    radius_m: float,
    rng: random.Random,
) -> list[Any]:
    actors: list[Any] = []
    if count <= 0:
        return actors

    walker_bps = list(world.get_blueprint_library().filter("walker.pedestrian.*"))
    if not walker_bps:
        return actors
    controller_bp = world.get_blueprint_library().find("controller.ai.walker")
    locations = navigation_locations_near_ego(world, ego_vehicle, count, radius_m, rng)

    for loc in locations:
        bp = rng.choice(walker_bps)
        if bp.has_attribute("is_invincible"):
            bp.set_attribute("is_invincible", "false")
        if bp.has_attribute("speed"):
            speeds = bp.get_attribute("speed").recommended_values
            if len(speeds) > 1:
                bp.set_attribute("speed", speeds[1])
        walker = world.try_spawn_actor(bp, carla.Transform(loc, carla.Rotation()))
        if walker is None:
            continue
        actors.append(walker)

        controller = world.try_spawn_actor(controller_bp, carla.Transform(), walker)
        if controller is None:
            continue
        actors.append(controller)
        controller.start()
        destination = world.get_random_location_from_navigation()
        if destination is not None:
            controller.go_to_location(destination)
        controller.set_max_speed(0.8 + rng.random() * 0.8)

    return actors


def attach_cameras(
    world: Any,
    ego_vehicle: Any,
    width: int,
    height: int,
    fps: int,
    sensor_tick: float,
    camera_specs: Iterable[CameraSpec] = CAMERA_SPECS,
) -> tuple[list[Any], dict[str, queue.Queue[Any]]]:
    camera_bp = world.get_blueprint_library().find("sensor.camera.rgb")
    camera_bp.set_attribute("image_size_x", str(width))
    camera_bp.set_attribute("image_size_y", str(height))
    camera_bp.set_attribute("sensor_tick", f"{sensor_tick:.8f}")

    sensors = []
    queues: dict[str, queue.Queue[Any]] = {}
    for spec in camera_specs:
        camera_bp.set_attribute("fov", str(spec.fov_deg))
        transform = carla.Transform(
            carla.Location(*spec.location_xyz),
            carla.Rotation(pitch=spec.pitch_deg, yaw=spec.yaw_deg, roll=spec.roll_deg),
        )
        sensor = world.spawn_actor(camera_bp, transform, attach_to=ego_vehicle)
        frame_queue: queue.Queue[Any] = queue.Queue()
        sensor.listen(frame_queue.put)
        sensors.append(sensor)
        queues[spec.cosmos_name] = frame_queue
    return sensors, queues


def canonical_map_name(name: str) -> str:
    """Normalize CARLA map names for comparisons."""
    short = name.rsplit("/", 1)[-1]
    return short.removesuffix("_Opt")


def get_or_load_world(client: Any, town: str) -> Any:
    """Avoid reloading an already-loaded CARLA map.

    Reloading large maps such as Town10HD can crash fragile headless/container
    runs. If the server is already on the requested map or its _Opt variant,
    keep the existing world.
    """
    current_world = client.get_world()
    current_name = current_world.get_map().name
    if canonical_map_name(current_name) == canonical_map_name(town):
        log_step(f"Using already-loaded CARLA map: {current_name}")
        return current_world

    candidates = [town]
    if not town.endswith("_Opt"):
        candidates.append(f"{town}_Opt")

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            log_step(f"Loading CARLA map: {candidate}")
            return client.load_world(candidate)
        except RuntimeError as exc:
            last_error = exc
            log_step(f"Could not load {candidate}: {exc}")

    raise RuntimeError(
        f"Could not load requested town {town}. Current map is {current_name}. "
        "Try starting CARLA directly with the desired map or pass --town matching the loaded map."
    ) from last_error


def save_camera_timestamps(path: Path, timestamps_micros: list[int]) -> None:
    frames = [{"frame": idx, "timestamp": int(ts)} for idx, ts in enumerate(timestamps_micros)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(frames, indent=2) + "\n")


def load_scenario_hook(ref: str | None):
    if not ref:
        return None
    module_name, sep, function_name = ref.partition(":")
    if not sep:
        module_name, sep, function_name = ref.rpartition(".")
    if not module_name or not function_name:
        raise ValueError("--scenario-hook must be module:function or module.function")
    module = importlib.import_module(module_name)
    hook = getattr(module, function_name)
    if not callable(hook):
        raise ValueError(f"Scenario hook is not callable: {ref}")
    return hook


def call_scenario_hook(hook: Any, event: str, **kwargs: Any) -> None:
    if hook is None:
        return
    hook(event=event, **kwargs)


def apply_weather(world: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.weather_json is None and args.weather_preset is None:
        return {}

    weather = world.get_weather()
    metadata: dict[str, Any] = {}
    if args.weather_preset:
        preset = getattr(carla.WeatherParameters, args.weather_preset, None)
        if preset is None:
            available = [
                name
                for name in dir(carla.WeatherParameters)
                if not name.startswith("_")
                and isinstance(getattr(carla.WeatherParameters, name), carla.WeatherParameters)
            ]
            raise ValueError(f"Unknown weather preset {args.weather_preset}. Available presets: {', '.join(available)}")
        weather = preset
        metadata["preset"] = args.weather_preset

    if args.weather_json is not None:
        values = load_json_object(args.weather_json)
        for key, value in values.items():
            if not hasattr(weather, key):
                raise ValueError(f"WeatherParameters has no field '{key}'")
            setattr(weather, key, float(value))
        metadata.update(values)

    world.set_weather(weather)
    for key in (
        "cloudiness",
        "precipitation",
        "precipitation_deposits",
        "wind_intensity",
        "sun_azimuth_angle",
        "sun_altitude_angle",
        "fog_density",
        "wetness",
    ):
        if hasattr(weather, key):
            metadata.setdefault(key, float(getattr(weather, key)))
    return metadata


def apply_traffic_manager_config(traffic_manager: Any, path: Path | None) -> None:
    if traffic_manager is None or path is None:
        return
    config = load_json_object(path)
    for method_name, value in config.items():
        method = getattr(traffic_manager, method_name, None)
        if not callable(method):
            raise ValueError(f"TrafficManager has no callable method '{method_name}'")
        if isinstance(value, list):
            method(*value)
        elif isinstance(value, dict):
            method(**value)
        else:
            method(value)


def write_rows_json(rows_dir: Path, rows_by_element: dict[str, list[dict[str, Any]]]) -> None:
    rows_dir.mkdir(parents=True, exist_ok=True)
    for element_name in SCENE_ELEMENTS:
        rows = rows_by_element.get(element_name, [])
        write_json(rows_dir / f"{element_name}.json", rows)


def main() -> None:
    args = parse_args()
    out_root = args.out.expanduser().resolve()
    capture_root = out_root / "captures" / args.clip_id
    rgb_dir = capture_root / "rgb_videos"
    rgb_dir.mkdir(parents=True, exist_ok=True)

    all_camera_specs = load_camera_specs(args.camera_config)
    active_view_keys, active_camera_specs = select_camera_specs(all_camera_specs, args.views)
    log_step(f"Active camera views: {', '.join(active_view_keys)}")

    rng = random.Random(args.seed)
    np.random.seed(args.seed)

    scenario_hook = load_scenario_hook(args.scenario_hook)

    log_step(f"Connecting to CARLA at {args.host}:{args.port} with timeout {args.client_timeout}s")
    client = carla.Client(args.host, args.port)
    client.set_timeout(float(args.client_timeout))

    target_town = args.town
    if args.openscenario and os.path.exists(args.openscenario):
        try:
            with open(args.openscenario, encoding="utf-8") as f:
                content = f.read()
                m = re.search(r'<LogicFile\s+filepath="([^"]+)"', content)
                if m:
                    target_town = m.group(1)
        except Exception:
            # Keep the requested town when the OpenSCENARIO file cannot be read.
            pass
    elif args.scenario:
        xml_path = os.path.join(SCENARIOS_DIR, f"{args.scenario}.xml")
        if not os.path.exists(xml_path) and "cutin" in args.scenario.lower():
            xml_path = os.path.join(SCENARIOS_DIR, "CutIn.xml")
        if not os.path.exists(xml_path):
            xml_path = os.path.join(SCENARIO_RUNNER_ROOT, "srunner", "examples", f"{args.scenario}.xml")
        if os.path.exists(xml_path):
            try:
                import xml.etree.ElementTree as ET

                tree = ET.parse(xml_path)
                for s in tree.iter("scenario"):
                    if "town" in s.attrib:
                        target_town = s.attrib["town"]
                        break
            except Exception:
                # Keep the current town when the scenario XML cannot be parsed.
                pass

    log_step(f"Resolving CARLA world (target map: {target_town})")
    world = get_or_load_world(client, target_town)
    weather_metadata = apply_weather(world, args)
    traffic_manager = None
    if args.vehicles > 0 or args.lead_vehicles > 0:
        log_step(f"Configuring TrafficManager on port {args.traffic_manager_port}")
        traffic_manager = client.get_trafficmanager(args.traffic_manager_port)
        traffic_manager.set_random_device_seed(args.seed)
        traffic_manager.set_synchronous_mode(True)
        traffic_manager.set_global_distance_to_leading_vehicle(2.5)
        apply_traffic_manager_config(traffic_manager, args.traffic_manager_config)
    else:
        log_step("Skipping TrafficManager because no NPC vehicles were requested")
    call_scenario_hook(
        scenario_hook,
        "before_spawn",
        args=args,
        client=client,
        world=world,
        traffic_manager=traffic_manager,
    )

    log_step("Applying synchronous world settings")
    original_settings = world.get_settings()
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 1.0 / args.sim_fps
    settings.no_rendering_mode = False
    world.apply_settings(settings)

    sensors: list[Any] = []
    spawned_actors: list[Any] = []
    writers: dict[str, cv2.VideoWriter] = {}

    try:
        log_step("Handling Ego Vehicle Spawning (Native or ScenarioRunner)")
        sr_process = None
        if args.openscenario or args.scenario:
            sr_env = os.environ.copy()
            sr_env["SCENARIO_RUNNER_ROOT"] = SCENARIO_RUNNER_ROOT
            sr_env["PYTHONPATH"] = os.pathsep.join([SCENARIO_RUNNER_ROOT, sr_env.get("PYTHONPATH", "")])
            sr_env["SCENARIO_RUNNER_NO_CLEANUP"] = "1"

            sr_cmd = [
                sys.executable,
                os.path.join(SCENARIO_RUNNER_ROOT, "scenario_runner.py"),
                "--timeout",
                "600",
                "--trafficManagerPort",
                str(args.traffic_manager_port),
                "--host",
                str(args.host),
                "--port",
                str(args.port),
            ]
            if args.openscenario:
                sr_cmd.extend(["--openscenario", args.openscenario, "--reloadWorld"])
            if args.scenario:
                xml_path = os.path.join(SCENARIOS_DIR, f"{args.scenario}.xml")
                if not os.path.exists(xml_path) and "cutin" in args.scenario.lower():
                    xml_path = os.path.join(SCENARIOS_DIR, "CutIn.xml")
                if not os.path.exists(xml_path):
                    xml_path = os.path.join(SCENARIO_RUNNER_ROOT, "srunner", "examples", f"{args.scenario}.xml")
                scenario_arg = args.scenario
                if os.path.exists(xml_path):
                    try:
                        import xml.etree.ElementTree as ET

                        tree = ET.parse(xml_path)
                        names = [s.attrib.get("name") for s in tree.iter("scenario") if s.attrib.get("name")]
                        if args.scenario not in names:
                            matched = None
                            for s in tree.iter("scenario"):
                                name = s.attrib.get("name", "")
                                stype = s.attrib.get("type", "")
                                if stype == args.scenario or args.scenario in name:
                                    matched = name
                                    break
                            if matched:
                                scenario_arg = matched
                            elif names:
                                scenario_arg = names[0]
                    except Exception as e:
                        print(f"Warning parsing xml {xml_path}: {e}")
                sr_cmd.extend(["--scenario", scenario_arg, "--configFile", xml_path, "--reloadWorld"])
            subprocess.run(["pkill", "-9", "-f", "scenario_runner.py"], stderr=subprocess.DEVNULL)
            time.sleep(0.5)
            try:
                cur_w = client.get_world()
                for a in cur_w.get_actors().filter("vehicle.*"):
                    if a.attributes.get("role_name") == "hero":
                        a.destroy()
            except Exception:
                # Best-effort cleanup of a stale hero vehicle before ScenarioRunner starts.
                pass
            log_step(f"Launching ScenarioRunner: {' '.join(sr_cmd)}")
            sr_process = subprocess.Popen(sr_cmd, env=sr_env)

            # Poll for hero vehicle
            ego_vehicle = None
            log_step("Waiting for ScenarioRunner to spawn 'hero' vehicle...")
            for _ in range(60):
                time.sleep(1)
                try:
                    world = client.get_world()
                    for actor in world.get_actors().filter("vehicle.*"):
                        if "role_name" in actor.attributes and actor.attributes["role_name"] == "hero":
                            ego_vehicle = actor
                            break
                except Exception:
                    # The world may be reloading; retry on the next poll.
                    pass
                if ego_vehicle:
                    break
            if not ego_vehicle:
                raise RuntimeError("ScenarioRunner failed to spawn 'hero' vehicle within 60 seconds.")
            # Ensure fresh world reference and sync settings after ScenarioRunner world reload
            world = client.get_world()
            settings = world.get_settings()
            settings.synchronous_mode = True
            settings.fixed_delta_seconds = 1.0 / args.sim_fps
            settings.no_rendering_mode = False
            world.apply_settings(settings)
        else:
            ego_vehicle = spawn_ego_vehicle(world, rng, args.ego_spawn_index, args.prefer_junction_start)
            spawned_actors.append(ego_vehicle)
        call_scenario_hook(
            scenario_hook,
            "after_ego_spawn",
            args=args,
            client=client,
            world=world,
            traffic_manager=traffic_manager,
            ego_vehicle=ego_vehicle,
            spawned_actors=spawned_actors,
        )
        ego_controller = None
        if args.ego_control == "waypoint":
            ego_controller = EgoWaypointFollower(
                world.get_map(),
                args.ego_speed_kmh,
                args.ego_lookahead_m,
                rng,
                args.junction_turn_probability,
            )
            log_step(f"Ego waypoint control enabled at {args.ego_speed_kmh:.1f} km/h")
        else:
            log_step("Ego control disabled; captured videos will be static unless CARLA moves the vehicle externally")

        if args.lead_vehicles > 0:
            log_step(f"Spawning up to {args.lead_vehicles} lead NPC vehicles")
            lead_traffic = spawn_lead_vehicles(
                world,
                ego_vehicle,
                args.lead_vehicles,
                args.lead_start_m,
                args.lead_spacing_m,
                args.traffic_manager_port,
                rng,
            )
            log_step(f"Spawned {len(lead_traffic)} lead NPC vehicles")
            spawned_actors.extend(lead_traffic)

        if args.vehicles > 0:
            log_step(
                f"Spawning up to {args.vehicles} background NPC vehicles "
                f"(near-ego radius {args.traffic_radius_m:.1f} m)"
            )
            traffic = spawn_traffic(
                world,
                ego_vehicle,
                args.vehicles,
                args.traffic_manager_port,
                rng,
                args.traffic_radius_m,
            )
            log_step(f"Spawned {len(traffic)} background NPC vehicles")
            spawned_actors.extend(traffic)

        if args.walkers > 0:
            log_step(f"Spawning up to {args.walkers} pedestrians near ego")
            walkers = spawn_walkers(world, ego_vehicle, args.walkers, args.walker_radius_m, rng)
            walker_count = sum(1 for actor in walkers if getattr(actor, "type_id", "").startswith("walker."))
            log_step(f"Spawned {walker_count} pedestrians")
            spawned_actors.extend(walkers)

        call_scenario_hook(
            scenario_hook,
            "after_spawn",
            args=args,
            client=client,
            world=world,
            traffic_manager=traffic_manager,
            ego_vehicle=ego_vehicle,
            spawned_actors=spawned_actors,
        )

        log_step(f"Attaching {len(active_camera_specs)} RGB camera(s)")
        sensors, camera_queues = attach_cameras(
            world,
            ego_vehicle,
            args.width,
            args.height,
            args.fps,
            args.sensor_tick,
            active_camera_specs,
        )

        # Let sensors and traffic manager settle, then define the Cosmos world origin.
        log_step(f"Running {args.warmup_ticks} camera warmup ticks")
        warmup_cameras(world, camera_queues, args.warmup_ticks, args.camera_timeout)
        ego0_matrix = transform_to_matrix(ego_vehicle.get_transform())
        ego0_inv = np.linalg.inv(ego0_matrix)
        static_vehicle_obstacles = collect_static_vehicle_obstacles(
            world,
            ego_vehicle,
            ego0_inv,
            args.static_vehicle_radius_m,
        )
        log_step(f"Exporting {len(static_vehicle_obstacles)} static vehicle environment obstacles")
        map_records = collect_map_records(world, args.clip_id, ego0_inv, args.map_waypoint_spacing_m)
        log_step(
            "Exporting static map records: "
            f"{len(map_records.get('traffic_light', []))} traffic light box(es), "
            f"{len(map_records.get('traffic_sign', []))} traffic sign(s), "
            f"{len(map_records.get('pole', []))} pole(s)"
        )

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        for spec in active_camera_specs:
            path = rgb_dir / f"{spec.cosmos_name}.mp4"
            writer = cv2.VideoWriter(str(path), fourcc, float(args.fps), (args.width, args.height))
            if not writer.isOpened():
                raise RuntimeError(f"Could not open video writer for {path}")
            writers[spec.cosmos_name] = writer

        calibration_rows = [
            {
                "key": {"clip_id": args.clip_id, "timestamp_micros": -1},
                "calibration_estimate": {
                    "name": "CARLA_COSMOS_RIG",
                    "rig_json": json.dumps(make_rig_json(args.width, args.height, active_camera_specs)),
                },
                "version": 1,
            }
        ]

        egomotion_rows: list[dict[str, Any]] = []
        obstacle_rows: list[dict[str, Any]] = []
        timestamps_micros: list[int] = []

        total_frames = round(args.seconds * args.fps)
        log_step(f"Capturing {total_frames} frames at {args.fps} FPS")
        final_ego_displacement_m = 0.0
        first_spec = active_camera_specs[0]
        for frame_idx in tqdm(range(total_frames), desc="Capturing CARLA frames"):
            if sr_process is not None and sr_process.poll() is not None and len(timestamps_micros) >= total_frames:
                log_step(f"Scenario completed by ScenarioRunner after {len(timestamps_micros)} frames. Ending capture.")
                break
            if ego_controller is not None:
                ego_controller.apply_control(ego_vehicle)
            call_scenario_hook(
                scenario_hook,
                "before_capture_tick",
                args=args,
                client=client,
                world=world,
                traffic_manager=traffic_manager,
                ego_vehicle=ego_vehicle,
                frame_index=frame_idx,
            )
            frame_id = world.tick()

            first_image = wait_for_camera_frame_at_or_after(
                camera_queues[first_spec.cosmos_name],
                frame_id,
                first_spec.cosmos_name,
                args.camera_timeout,
            )
            timestamp_micros = round(float(first_image.timestamp) * 1_000_000.0)
            timestamps_micros.append(timestamp_micros)
            writers[first_spec.cosmos_name].write(image_to_bgr(first_image))

            for spec in active_camera_specs[1:]:
                image = wait_for_camera_frame_at_or_after(
                    camera_queues[spec.cosmos_name],
                    frame_id,
                    spec.cosmos_name,
                    args.camera_timeout,
                )
                writers[spec.cosmos_name].write(image_to_bgr(image))

            ego_position, ego_quat = carla_transform_to_ego_flu_pose(ego_vehicle.get_transform(), ego0_inv)
            final_ego_displacement_m = float(np.linalg.norm(ego_position[:2]))
            egomotion_rows.append(
                {
                    "key": {"clip_id": args.clip_id, "timestamp_micros": timestamp_micros},
                    "egomotion_estimate": {
                        "name": "carla_egomotion",
                        "location": point_to_dict(ego_position),
                        "orientation": quat_to_dict(ego_quat),
                    },
                    "version": 1,
                }
            )
            obstacle_rows.extend(collect_obstacle_rows(world, ego_vehicle, ego0_inv, args.clip_id, timestamp_micros))
            obstacle_rows.extend(timestamp_obstacle_rows(args.clip_id, timestamp_micros, static_vehicle_obstacles))
            append_traffic_light_state_samples(map_records, world)

        log_step(f"Ego displacement during capture: {final_ego_displacement_m:.2f} m")

        for writer in writers.values():
            writer.release()

        rows_by_element: dict[str, list[dict[str, Any]]] = {
            "calibration_estimate": calibration_rows,
            "egomotion_estimate": egomotion_rows,
            "obstacle": obstacle_rows,
            **map_records,
        }

        write_rows_json(capture_root / "cosmos_rows", rows_by_element)
        for spec in active_camera_specs:
            save_camera_timestamps(capture_root / "timestamps" / f"{spec.cosmos_name}.json", timestamps_micros)

        manifest = {
            "schema_version": 1,
            "clip_id": args.clip_id,
            "fps": args.fps,
            "sim_fps": args.sim_fps,
            "capture_fps": args.capture_fps,
            "seconds": args.seconds,
            "frames": len(timestamps_micros),
            "width": args.width,
            "height": args.height,
            "town": world.get_map().name,
            "seed": args.seed,
            "active_views": active_view_keys,
            "vehicles": args.vehicles,
            "lead_vehicles": args.lead_vehicles,
            "walkers": args.walkers,
            "traffic_radius_m": args.traffic_radius_m,
            "walker_radius_m": args.walker_radius_m,
            "static_vehicle_radius_m": args.static_vehicle_radius_m,
            "static_vehicle_obstacles": len(static_vehicle_obstacles),
            "ego_spawn_index": args.ego_spawn_index,
            "prefer_junction_start": args.prefer_junction_start,
            "junction_turn_probability": args.junction_turn_probability,
            "map_waypoint_spacing_m": args.map_waypoint_spacing_m,
            "rgb_dir": str(rgb_dir),
            "capture_dir": str(capture_root),
            "weather": weather_metadata,
            "cameras": [spec_to_manifest(spec) for spec in active_camera_specs],
            "resolved_config": {key: value for key, value in vars(args).items() if key not in {"set_values"}},
            "notes": [
                "CARLA coordinates were converted from X-forward/Y-right/Z-up to Cosmos FLU X-forward/Y-left/Z-up.",
                "Optional map metadata is best-effort from CARLA map/traffic actor APIs.",
            ],
        }
        write_json(capture_root / "manifest.json", manifest)

        call_scenario_hook(
            scenario_hook,
            "after_capture",
            args=args,
            client=client,
            world=world,
            traffic_manager=traffic_manager,
            manifest=manifest,
        )

        print(f"\nDone. RGB videos: {rgb_dir}")
        print(f"Done. Portable capture: {capture_root}")

    finally:
        for writer in writers.values():
            writer.release()
        for sensor in sensors:
            with contextlib.suppress(RuntimeError):
                sensor.stop()
        if not args.no_cleanup:
            for actor in spawned_actors:
                if getattr(actor, "type_id", "").startswith("controller.ai.walker"):
                    with contextlib.suppress(RuntimeError):
                        actor.stop()
            for actor in sensors + spawned_actors:
                with contextlib.suppress(Exception):
                    if actor is not None and getattr(actor, "is_alive", False):
                        actor.destroy()
        if traffic_manager is not None:
            traffic_manager.set_synchronous_mode(False)
        world.apply_settings(original_settings)
        if sr_process is not None:
            try:
                sr_process.terminate()
                sr_process.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    sr_process.kill()
        subprocess.run(["pkill", "-9", "-f", "scenario_runner.py"], stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
