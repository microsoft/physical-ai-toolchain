"""Optional local patches for Cosmos-Transfer2.5 interop."""

from __future__ import annotations

import re
from pathlib import Path

QHULL_SENTINEL = "largest-spread coordinate plane"
TRAFFIC_LIGHT_STATE_SENTINEL = "CARLA Cosmos traffic light state_sequence support"


def qhull_patch_target(cosmos_root: Path) -> Path:
    return (
        cosmos_root.expanduser().resolve()
        / "cosmos_transfer2/_src/imaginaire/auxiliary/world_scenario/utils/pcd_utils.py"
    )


def clipgt_loader_patch_target(cosmos_root: Path) -> Path:
    return (
        cosmos_root.expanduser().resolve()
        / "cosmos_transfer2/_src/imaginaire/auxiliary/world_scenario/dataloaders/clipgt_loader.py"
    )


def apply_robust_triangulation_patch(cosmos_root: Path) -> str:
    """Patch Cosmos polygon triangulation so CARLA x-z polygons do not crash QHull."""
    target = qhull_patch_target(cosmos_root)
    if not target.exists():
        raise FileNotFoundError(f"Could not find Cosmos pcd_utils.py at: {target}")

    text = target.read_text()
    if QHULL_SENTINEL in text:
        return f"Already patched: {target}"

    backup = target.with_suffix(target.suffix + ".carla_cosmos_backup")
    if not backup.exists():
        backup.write_text(text)

    replacement = '''def triangulate_polygon_3d(vertices3d: np.ndarray) -> np.ndarray:
    """
    Triangulate a 3D polygon by projecting to its largest-spread coordinate plane.

    Cosmos' stock implementation projects every polygon to x-y. CARLA-exported
    map polygons can arrive in an x-z ground plane after renderer coordinate
    conversion, which makes all y values identical and QHull fails.
    """
    vertices3d = np.asarray(vertices3d, dtype=float)

    if vertices3d.shape[0] >= 2 and np.allclose(vertices3d[0], vertices3d[-1]):
        vertices3d = vertices3d[:-1]
    if vertices3d.shape[0] < 3:
        return np.empty((0, 3, 3), dtype=float)

    finite_mask = np.isfinite(vertices3d).all(axis=1)
    vertices3d = vertices3d[finite_mask]
    if vertices3d.shape[0] < 3:
        return np.empty((0, 3, 3), dtype=float)

    ranges = np.ptp(vertices3d, axis=0)
    axes = np.argsort(ranges)[-2:]
    axes = np.sort(axes)
    if ranges[axes[0]] < 1e-6 or ranges[axes[1]] < 1e-6:
        return np.empty((0, 3, 3), dtype=float)

    vertices2d = vertices3d[:, axes]
    try:
        tri = Delaunay(vertices2d, qhull_options="QJ Qbb Qc Q12")
    except Exception:
        return np.empty((0, 3, 3), dtype=float)

    return vertices3d[tri.simplices]

'''

    pattern = re.compile(
        r"def triangulate_polygon_3d\(vertices3d: np\.ndarray\) -> np\.ndarray:\n.*?\n"
        r"(?=def filter_by_height_relative_to_ego\()",
        re.S,
    )
    patched_text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not find triangulate_polygon_3d function to patch in {target}")

    target.write_text(patched_text)
    return f"Patched: {target}"


def apply_clipgt_traffic_light_state_patch(cosmos_root: Path) -> str:
    """Patch Cosmos' ClipGT loader to consume CARLA traffic-light state_sequence fields."""
    target = clipgt_loader_patch_target(cosmos_root)
    if not target.exists():
        raise FileNotFoundError(f"Could not find Cosmos clipgt_loader.py at: {target}")

    text = target.read_text()
    if TRAFFIC_LIGHT_STATE_SENTINEL in text:
        return f"Already patched: {target}"

    backup = target.with_suffix(target.suffix + ".carla_cosmos_backup")
    if not backup.exists():
        backup.write_text(text)

    if "    TrafficLightState,\n" not in text:
        text = text.replace(
            "    TrafficLight,\n",
            "    TrafficLight,\n    TrafficLightState,\n",
            1,
        )
    if "    normalize_traffic_light_state_sequence,\n" not in text:
        text = text.replace(
            "    normalize_quaternions,\n)",
            "    normalize_quaternions,\n    normalize_traffic_light_state_sequence,\n)",
            1,
        )

    replacement = """            # CARLA Cosmos traffic light state_sequence support.
            feature_id = light.get("feature_id")
            state_sequence = light.get("state_sequence")
            normalized_states = normalize_traffic_light_state_sequence(
                state_sequence,
                max(1, scene_data.num_frames),
                feature_id=str(feature_id) if feature_id is not None else None,
            )
            states_dict = {
                frame_idx: state
                for frame_idx, state in enumerate(normalized_states)
                if state is not TrafficLightState.UNKNOWN
            }

            traffic_light = TrafficLight(
                element_id=f"traffic_light_{idx}",
                center=center,
                dimensions=dimensions,
                orientation=orientation,
                states=states_dict,
            )
            traffic_light.metadata["feature_id"] = str(feature_id) if feature_id is not None else None
            traffic_light.metadata["state_sequence"] = normalized_states
            scene_data.traffic_lights.append(traffic_light)
"""

    pattern = re.compile(
        r"            scene_data\.traffic_lights\.append\(\n"
        r"                TrafficLight\(\n"
        r"                    element_id=f\"traffic_light_\{idx\}\",\n"
        r"                    center=center,\n"
        r"                    dimensions=dimensions,\n"
        r"                    orientation=orientation,\n"
        r"                \)\n"
        r"            \)\n",
        re.S,
    )
    patched_text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not find ClipGT traffic-light append block to patch in {target}")

    target.write_text(patched_text)
    return f"Patched: {target}"
