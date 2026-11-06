"""Helpers for improving portable scene rows during Stage 2."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np


def _point_to_array(point: dict[str, Any]) -> np.ndarray:
    return np.array([float(point["x"]), float(point["y"]), float(point["z"])], dtype=np.float32)


def _array_to_point(point: np.ndarray) -> dict[str, float]:
    return {"x": float(point[0]), "y": float(point[1]), "z": float(point[2])}


def _quat_to_array(quat: dict[str, Any]) -> np.ndarray:
    return np.array([float(quat["x"]), float(quat["y"]), float(quat["z"]), float(quat["w"])], dtype=np.float32)


def _array_to_quat(quat: np.ndarray) -> dict[str, float]:
    return {"x": float(quat[0]), "y": float(quat[1]), "z": float(quat[2]), "w": float(quat[3])}


def _average_quaternions(quaternions: list[np.ndarray]) -> np.ndarray | None:
    normalized = []
    for quat in quaternions:
        norm = float(np.linalg.norm(quat))
        if norm <= 1e-8:
            continue
        normalized.append(quat / norm)
    if not normalized:
        return None

    reference = normalized[0]
    aligned = [quat if float(np.dot(reference, quat)) >= 0.0 else -quat for quat in normalized]
    average = np.mean(np.stack(aligned), axis=0)
    norm = float(np.linalg.norm(average))
    if norm <= 1e-8:
        return reference.astype(np.float32)
    return (average / norm).astype(np.float32)


def group_obstacle_rows(obstacle_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in obstacle_rows:
        obstacle = row.get("obstacle", {})
        track_id = obstacle.get("trackline_id")
        if track_id is None:
            continue
        tracks[str(track_id)].append(row)
    return dict(tracks)


def infer_obstacle_motion_flags(
    obstacle_rows: list[dict[str, Any]],
    *,
    static_threshold_m: float = 0.20,
) -> dict[str, bool]:
    """Infer track motion from existing Stage 1 obstacle rows.

    Returns a mapping of track ID to ``is_moving``.  Unknown one-observation
    tracks are kept moving unless they are explicit CARLA static environment
    vehicle rows.
    """

    flags: dict[str, bool] = {}
    for track_id, rows in group_obstacle_rows(obstacle_rows).items():
        if track_id.startswith("static_vehicle:"):
            flags[track_id] = False
            continue

        centers = []
        for row in rows:
            center = row.get("obstacle", {}).get("center")
            if isinstance(center, dict):
                centers.append(_point_to_array(center))

        if len(centers) < 2:
            flags[track_id] = True
            continue

        stacked = np.stack(centers)
        displacement = float(np.linalg.norm(stacked[:, :2] - stacked[0, :2], axis=1).max())
        flags[track_id] = displacement > static_threshold_m
    return flags


def stabilize_static_obstacle_rows(
    rows_by_element: dict[str, list[dict[str, Any]]],
    *,
    static_threshold_m: float = 0.20,
) -> dict[str, Any]:
    """Stabilize existing stationary obstacle tracks without re-running CARLA.

    This mutates ``rows_by_element`` in place.  Stationary tracks get a single
    averaged center, size, and orientation across all existing observations.
    """

    obstacle_rows = rows_by_element.get("obstacle", [])
    motion_flags = infer_obstacle_motion_flags(obstacle_rows, static_threshold_m=static_threshold_m)
    tracks = group_obstacle_rows(obstacle_rows)

    rows_stabilized = 0
    static_tracks = 0
    for track_id, is_moving in motion_flags.items():
        if is_moving:
            continue
        track_rows = tracks.get(track_id, [])
        centers = []
        sizes = []
        quaternions = []
        for row in track_rows:
            obstacle = row.get("obstacle", {})
            center = obstacle.get("center")
            size = obstacle.get("size")
            orientation = obstacle.get("orientation")
            if isinstance(center, dict):
                centers.append(_point_to_array(center))
            if isinstance(size, dict):
                sizes.append(_point_to_array(size))
            if isinstance(orientation, dict):
                quaternions.append(_quat_to_array(orientation))

        if not centers:
            continue

        center_mean = np.mean(np.stack(centers), axis=0).astype(np.float32)
        size_mean = np.mean(np.stack(sizes), axis=0).astype(np.float32) if sizes else None
        quat_mean = _average_quaternions(quaternions)

        for row in track_rows:
            obstacle = row.get("obstacle", {})
            obstacle["center"] = _array_to_point(center_mean)
            if size_mean is not None:
                obstacle["size"] = _array_to_point(size_mean)
            if quat_mean is not None:
                obstacle["orientation"] = _array_to_quat(quat_mean)
            rows_stabilized += 1
        static_tracks += 1

    return {
        "enabled": True,
        "static_threshold_m": float(static_threshold_m),
        "tracks_total": len(motion_flags),
        "static_tracks": static_tracks,
        "moving_tracks": len(motion_flags) - static_tracks,
        "rows_stabilized": rows_stabilized,
    }
