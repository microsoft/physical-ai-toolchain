"""Experimental RDS-HQ style scene-annotation writing for Cosmos controls."""

from __future__ import annotations

import io
import json
import math
import tarfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from .scene_rows import infer_obstacle_motion_flags

CARLA_TO_COSMOS_FLU = np.diag([1.0, -1.0, 1.0]).astype(np.float32)
FLU_TO_RDF = np.array(
    [
        [0.0, -1.0, 0.0],
        [0.0, 0.0, -1.0],
        [1.0, 0.0, 0.0],
    ],
    dtype=np.float32,
)
RDSHQ_TIMEBASE_FPS = 30


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), default=_json_default).encode("utf-8")


def _npy_bytes(value: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, value, allow_pickle=False)
    return buffer.getvalue()


def _write_tar(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as archive:
        for name, payload in sorted(entries.items()):
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))


def _clip_member(clip_id: str, key: str) -> str:
    # WebDataset groups files by the prefix before the first dot.  Keep every
    # tar member under one sample so Cosmos' get_sample() receives all entries.
    # Use a dot-free prefix so clip IDs containing dots do not leak into the
    # decoded field names.
    return f"sample.{key}"


def _point(point: dict[str, Any]) -> list[float]:
    return [float(point["x"]), float(point["y"]), float(point["z"])]


def _point_array(points: Iterable[dict[str, Any]]) -> list[list[float]]:
    return [_point(point) for point in points]


def _quat_xyzw_to_matrix(quat: dict[str, Any]) -> np.ndarray:
    x = float(quat["x"])
    y = float(quat["y"])
    z = float(quat["z"])
    w = float(quat["w"])
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-8:
        return np.eye(3, dtype=np.float32)
    x /= norm
    y /= norm
    z /= norm
    w /= norm
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float32,
    )


def _pose_matrix(center: dict[str, Any], quat: dict[str, Any]) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float32)
    matrix[:3, :3] = _quat_xyzw_to_matrix(quat)
    matrix[:3, 3] = np.array(_point(center), dtype=np.float32)
    return matrix


def _carla_relative_rotation_matrix(pitch_deg: float, yaw_deg: float, roll_deg: float) -> np.ndarray:
    pitch = math.radians(float(pitch_deg))
    yaw = math.radians(float(yaw_deg))
    roll = math.radians(float(roll_deg))
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    return np.array(
        [
            [cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr],
            [cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr],
            [sp, -cp * sr, cp * cr],
        ],
        dtype=np.float32,
    )


def _camera_to_ego_from_manifest(camera: dict[str, Any]) -> np.ndarray:
    location = np.array(camera.get("location_xyz", [0.0, 0.0, 0.0]), dtype=np.float32)
    rotation_carla = _carla_relative_rotation_matrix(
        float(camera.get("pitch_deg", 0.0)),
        float(camera.get("yaw_deg", 0.0)),
        float(camera.get("roll_deg", 0.0)),
    )
    translation_flu = CARLA_TO_COSMOS_FLU @ location
    rotation_flu = CARLA_TO_COSMOS_FLU @ rotation_carla @ CARLA_TO_COSMOS_FLU

    # RDS-HQ world coordinates stay FLU, while camera rays are OpenCV/RDF
    # (x=right, y=down, z=forward).  Convert camera RDF vectors into the
    # camera's local FLU basis before applying the CARLA mount rotation.
    matrix = np.eye(4, dtype=np.float32)
    matrix[:3, :3] = rotation_flu @ FLU_TO_RDF.T
    matrix[:3, 3] = translation_flu
    return matrix


def _pinhole_as_ftheta_polynomial(width: int, height: int, horizontal_fov_deg: float) -> np.ndarray:
    fov_rad = math.radians(float(horizontal_fov_deg))
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


def _camera_intrinsic(camera: dict[str, Any], width: int, height: int) -> np.ndarray:
    coeffs = _pinhole_as_ftheta_polynomial(width, height, float(camera.get("fov_deg", 120.0)))
    return np.array(
        [
            width / 2.0,
            height / 2.0,
            float(width),
            float(height),
            *[float(value) for value in coeffs],
            0.0,  # is_bw_poly=False; polynomial maps angle -> pixel radius.
            1.0,
            0.0,
            0.0,
        ],
        dtype=np.float32,
    )


def _frame_token(frame_index: int, annotation_fps: int, timebase_fps: int = RDSHQ_TIMEBASE_FPS) -> str:
    return f"{round(frame_index * timebase_fps / annotation_fps):06d}"


def _object_type(category: str) -> str:
    normalized = category.lower()
    if normalized in {"automobile", "vehicle", "car", "other_vehicle"}:
        return "Automobile"
    if normalized in {"bus"}:
        return "Bus"
    if normalized in {"truck", "heavy_truck", "train_or_tram_car", "trolley_bus", "trailer"}:
        return "Heavy_truck"
    if normalized in {"pedestrian", "person"}:
        return "Person"
    if normalized in {"bicycle", "cyclist", "motorcycle", "rider"}:
        return "Rider"
    return "Other_vehicle"


def _polyline_label(points: list[list[float]], attributes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    shape: dict[str, Any] = {"polyline3d": {"vertices": points}}
    if attributes:
        shape["attributes"] = attributes
    return {"labelData": {"emptyLabel": False, "shape3d": shape}}


def _lane_label(polylines: list[list[list[float]]]) -> dict[str, Any]:
    return {
        "labelData": {
            "emptyLabel": False,
            "shape3d": {
                "polylines3d": {
                    "polylines": [{"vertices": points} for points in polylines if len(points) >= 2],
                }
            },
        }
    }


def _surface_label(points: list[list[float]]) -> dict[str, Any]:
    return {"labelData": {"emptyLabel": False, "shape3d": {"surface": {"vertices": points}}}}


def _cuboid_vertices(
    center: dict[str, Any], dimensions: dict[str, Any], orientation: dict[str, Any]
) -> list[list[float]]:
    c = np.array(_point(center), dtype=np.float32)
    dims = np.array(_point(dimensions), dtype=np.float32)
    rotation = _quat_xyzw_to_matrix(orientation)
    local = np.array(
        [
            [-0.5, -0.5, -0.5],
            [-0.5, 0.5, -0.5],
            [0.5, 0.5, -0.5],
            [0.5, -0.5, -0.5],
            [-0.5, -0.5, 0.5],
            [-0.5, 0.5, 0.5],
            [0.5, 0.5, 0.5],
            [0.5, -0.5, 0.5],
        ],
        dtype=np.float32,
    )
    vertices = c + (rotation @ (local * dims).T).T
    return vertices.astype(float).tolist()


def _cuboid_label(
    vertices: list[list[float]],
    *,
    feature_id: str | None = None,
) -> dict[str, Any]:
    shape: dict[str, Any] = {"cuboid3d": {"vertices": vertices}}
    if feature_id is not None:
        shape["attributes"] = [{"name": "feature_id", "text": feature_id}]
    return {"labelData": {"emptyLabel": False, "shape3d": shape}}


def _lane_line_attributes(colors: list[str], styles: list[str]) -> list[dict[str, Any]]:
    return [
        {"name": "colors", "enumsList": {"enumsList": [color.upper() for color in colors]}},
        {"name": "styles", "enumsList": {"enumsList": [style.upper() for style in styles]}},
    ]


def _write_pose_tars(
    output_dir: Path,
    clip_id: str,
    rows_by_element: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    view_keys: list[str],
    annotation_fps: int,
    width: int,
    height: int,
) -> None:
    egomotion_rows = rows_by_element.get("egomotion_estimate", [])
    camera_by_view = {
        camera.get("view_key"): camera
        for camera in manifest.get("cameras", [])
        if isinstance(camera, dict) and camera.get("view_key") in view_keys
    }

    vehicle_entries: dict[str, bytes] = {}
    pose_entries: dict[str, bytes] = {}
    intrinsic_entries: dict[str, bytes] = {}
    camera_to_ego = {key: _camera_to_ego_from_manifest(camera_by_view[key]) for key in view_keys}

    for key in view_keys:
        camera = camera_by_view[key]
        camera_name = str(camera["cosmos_name"])
        intrinsic = _camera_intrinsic(camera, width, height)
        intrinsic_entries[_clip_member(clip_id, f"ftheta_intrinsic.{camera_name}.npy")] = _npy_bytes(intrinsic)

    for frame_index, row in enumerate(egomotion_rows):
        frame = _frame_token(frame_index, annotation_fps)
        ego = row["egomotion_estimate"]
        ego_to_world = _pose_matrix(ego["location"], ego["orientation"])
        vehicle_entries[_clip_member(clip_id, f"{frame}.vehicle_pose.npy")] = _npy_bytes(ego_to_world)
        for key in view_keys:
            camera = camera_by_view[key]
            camera_name = str(camera["cosmos_name"])
            camera_to_world = ego_to_world @ camera_to_ego[key]
            pose_entries[_clip_member(clip_id, f"{frame}.pose.{camera_name}.npy")] = _npy_bytes(camera_to_world)

    _write_tar(output_dir / "vehicle_pose" / f"{clip_id}.tar", vehicle_entries)
    _write_tar(output_dir / "pose" / f"{clip_id}.tar", pose_entries)
    _write_tar(output_dir / "ftheta_intrinsic" / f"{clip_id}.tar", intrinsic_entries)


def _write_object_tar(
    output_dir: Path,
    clip_id: str,
    rows_by_element: dict[str, list[dict[str, Any]]],
    annotation_fps: int,
    static_threshold_m: float,
) -> None:
    obstacle_rows = rows_by_element.get("obstacle", [])
    motion_flags = infer_obstacle_motion_flags(obstacle_rows, static_threshold_m=static_threshold_m)
    rows_by_timestamp: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in obstacle_rows:
        rows_by_timestamp[int(row["key"]["timestamp_micros"])].append(row)

    timestamps = [int(row["key"]["timestamp_micros"]) for row in rows_by_element.get("egomotion_estimate", [])]
    entries: dict[str, bytes] = {}
    for frame_index, timestamp in enumerate(timestamps):
        frame_objects: dict[str, dict[str, Any]] = {}
        for row in rows_by_timestamp.get(timestamp, []):
            obstacle = row.get("obstacle", {})
            track_id = str(obstacle.get("trackline_id", ""))
            if not track_id:
                continue
            center = obstacle.get("center")
            size = obstacle.get("size")
            orientation = obstacle.get("orientation")
            if not isinstance(center, dict) or not isinstance(size, dict) or not isinstance(orientation, dict):
                continue
            frame_objects[track_id] = {
                "object_to_world": _pose_matrix(center, orientation).tolist(),
                "object_lwh": _point(size),
                "object_is_moving": bool(motion_flags.get(track_id, True)),
                "object_type": _object_type(str(obstacle.get("category", "other_vehicle"))),
            }
        frame = _frame_token(frame_index, annotation_fps)
        entries[_clip_member(clip_id, f"{frame}.all_object_info.json")] = _json_bytes(frame_objects)

    _write_tar(output_dir / "all_object_info" / f"{clip_id}.tar", entries)


def _traffic_light_state_sequence(light: dict[str, Any], num_frames: int) -> list[str]:
    raw_sequence = light.get("state_sequence") or []
    normalized: list[str] = []
    for frame_index in range(max(num_frames, 1)):
        value = raw_sequence[frame_index] if frame_index < len(raw_sequence) else "UNKNOWN"
        state = str(value).split(".")[-1].upper()
        if state not in {"RED", "YELLOW", "GREEN", "UNKNOWN", "OFF"}:
            state = "UNKNOWN"
        normalized.append(state)
    return normalized


def _write_map_tars(
    output_dir: Path, clip_id: str, rows_by_element: dict[str, list[dict[str, Any]]], num_frames: int
) -> None:
    lane_line_labels = []
    for row in rows_by_element.get("lane_line", []):
        lane_line = row.get("lane_line", {})
        points = _point_array(lane_line.get("line_rail") or [])
        if len(points) < 2:
            continue
        colors = list(lane_line.get("colors") or ["WHITE"])
        styles = list(lane_line.get("styles") or ["SOLID_SINGLE"])
        lane_line_labels.append(_polyline_label(points, _lane_line_attributes(colors, styles)))
    _write_tar(
        output_dir / "3d_lanelines" / f"{clip_id}.tar",
        {_clip_member(clip_id, "lanelines.json"): _json_bytes({"labels": lane_line_labels})},
    )

    lane_labels = []
    for row in rows_by_element.get("lane", []):
        lane = row.get("lane", {})
        polylines = [
            _point_array(lane.get("left_rail") or []),
            _point_array(lane.get("right_rail") or []),
        ]
        if any(len(points) >= 2 for points in polylines):
            lane_labels.append(_lane_label(polylines))
    _write_tar(
        output_dir / "3d_lanes" / f"{clip_id}.tar",
        {_clip_member(clip_id, "lanes.json"): _json_bytes({"labels": lane_labels})},
    )

    polyline_exports = {
        "road_boundary": ("3d_road_boundaries", "road_boundaries.json"),
        "pole": ("3d_poles", "poles.json"),
        "wait_line": ("3d_wait_lines", "wait_lines.json"),
    }
    for element, (folder, filename) in polyline_exports.items():
        labels = []
        for row in rows_by_element.get(element, []):
            value = row.get(element, {})
            points = _point_array(value.get("location") or [])
            if len(points) >= 2:
                labels.append(_polyline_label(points))
        _write_tar(
            output_dir / folder / f"{clip_id}.tar",
            {_clip_member(clip_id, filename): _json_bytes({"labels": labels})},
        )

    surface_exports = {
        "crosswalk": ("3d_crosswalks", "crosswalks.json"),
        "road_marking": ("3d_road_markings", "road_markings.json"),
    }
    for element, (folder, filename) in surface_exports.items():
        labels = []
        for row in rows_by_element.get(element, []):
            value = row.get(element, {})
            points = _point_array(value.get("location") or [])
            if len(points) >= 3:
                labels.append(_surface_label(points))
        _write_tar(
            output_dir / folder / f"{clip_id}.tar",
            {_clip_member(clip_id, filename): _json_bytes({"labels": labels})},
        )

    light_labels = []
    traffic_light_states: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows_by_element.get("traffic_light", [])):
        light = row.get("traffic_light", {})
        feature_id = str(light.get("feature_id") or f"carla_traffic_light_{index}")
        light_labels.append(
            _cuboid_label(
                _cuboid_vertices(light["center"], light["dimensions"], light["orientation"]),
                feature_id=feature_id,
            )
        )
        traffic_light_states[feature_id] = {
            "feature_id": feature_id,
            "state": _traffic_light_state_sequence(light, num_frames),
        }
    _write_tar(
        output_dir / "3d_traffic_lights" / f"{clip_id}.tar",
        {_clip_member(clip_id, "3d_traffic_lights.json"): _json_bytes({"labels": light_labels})},
    )
    _write_tar(
        output_dir / "3d_traffic_lights_status" / f"{clip_id}.tar",
        {_clip_member(clip_id, "aggregated_states.json"): _json_bytes({"traffic_light_states": traffic_light_states})},
    )

    sign_labels = []
    for row in rows_by_element.get("traffic_sign", []):
        sign = row.get("traffic_sign", {})
        sign_labels.append(_cuboid_label(_cuboid_vertices(sign["center"], sign["dimensions"], sign["orientation"])))
    _write_tar(
        output_dir / "3d_traffic_signs" / f"{clip_id}.tar",
        {_clip_member(clip_id, "3d_traffic_signs.json"): _json_bytes({"labels": sign_labels})},
    )


def write_rdshq_annotations(
    output_dir: Path,
    clip_id: str,
    rows_by_element: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    view_keys: list[str],
    *,
    annotation_fps: int,
    width: int,
    height: int,
    static_threshold_m: float = 0.20,
) -> None:
    """Write a CARLA capture as an RDS-HQ-like bundle accepted by Cosmos."""

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_pose_tars(output_dir, clip_id, rows_by_element, manifest, view_keys, annotation_fps, width, height)
    _write_object_tar(output_dir, clip_id, rows_by_element, annotation_fps, static_threshold_m)
    _write_map_tars(output_dir, clip_id, rows_by_element, len(rows_by_element.get("egomotion_estimate", [])))
