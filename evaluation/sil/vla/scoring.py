"""Conservative live-receptacle placement scoring, independent of model inference."""

from __future__ import annotations

from typing import Any

import numpy as np

from .config import canonical, finite_array, require


def rotation_matrix(quaternion: np.ndarray) -> np.ndarray:
    """Normalize a finite WXYZ quaternion and return its rotation matrix."""
    q = finite_array(quaternion, (4,), "Quaternion")
    norm = float(np.linalg.norm(q))
    require(np.isfinite(norm) and norm > 0, "Quaternion must have a finite nonzero norm")
    w, x, y, z = q / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def placement_margins(object_pose: np.ndarray, target_pose: np.ndarray, geometry: dict[str, Any]) -> np.ndarray:
    """Transform authored object-envelope corners into the moving target's reference frame."""
    object_pose = finite_array(object_pose, (7,), "Object pose")
    target_pose = finite_array(target_pose, (7,), "Target pose")
    reference = finite_array(geometry["target_reference_pose"], (7,), "Target reference pose")
    corners = finite_array(geometry["object_local_corners"], (8, 3), "Object corners")
    bounds = finite_array(geometry["target_reference_bounds"], (2, 3), "Target bounds")
    wall, floor = geometry["wall_margin_m"], geometry["floor_tolerance_m"]
    require(
        type(wall) in (int, float)
        and type(floor) in (int, float)
        and np.isfinite([wall, floor]).all()
        and wall >= 0
        and floor >= 0,
        "Invalid target margins",
    )
    require(bool(np.all(bounds[1] > bounds[0])), "Target bounds have no volume")
    world = object_pose[:3] + corners @ rotation_matrix(object_pose[3:]).T
    transform = rotation_matrix(reference[3:]) @ rotation_matrix(target_pose[3:]).T
    mapped = reference[:3] + (world - target_pose[:3]) @ transform.T
    low, high, center = mapped.min(axis=0), mapped.max(axis=0), mapped.mean(axis=0)
    margins = np.array(
        [
            low[0] - bounds[0, 0] - wall,
            bounds[1, 0] - wall - high[0],
            low[1] - bounds[0, 1] - wall,
            bounds[1, 1] - wall - high[1],
            low[2] - bounds[0, 2] + floor,
            bounds[1, 2] - center[2],
        ]
    )
    require(bool(np.isfinite(margins).all()), "Placement calculation overflow")
    return margins


def consecutive(flags: np.ndarray) -> np.ndarray:
    """Count consecutive true frames ending at each index."""
    flags = np.asarray(flags, dtype=bool)
    require(flags.ndim == 1, "Frame flags must be one-dimensional")
    indices = np.arange(flags.size)
    return indices - np.maximum.accumulate(np.where(flags, -1, indices))


def _first(flags: np.ndarray, frames: int) -> int | None:
    matches = np.flatnonzero(consecutive(flags) >= frames)
    return int(matches[0]) if matches.size else None


def score_gear(
    data: dict[str, np.ndarray], initial: dict[str, np.ndarray], initial_command: np.ndarray, config: dict[str, Any]
) -> dict[str, Any]:
    """Score ordered contact, lift, release, final containment dwell, and command dynamics.

    Lift requires sustained bilateral contact before its qualifying sample. Contact
    clearing alone does not prove intentional release; the final live-bin dwell is
    independently required. This version is not a rescore of historical experiments.
    """
    count = len(data["actions"])
    require(count > 0, "Cannot score an empty trajectory")
    shapes = {
        "gear_pose": 7,
        "ee_pose": 7,
        "contact_force": 2,
        "gear_velocity": 6,
        "bin_pose": 7,
        "bin_velocity": 6,
        "placement_margins": 6,
    }
    arrays = {key: finite_array(data[f"evidence.{key}"], (count, width), key) for key, width in shapes.items()}
    initial = {key: finite_array(initial[key], (7,), f"Initial {key}") for key in ("gear_pose", "bin_pose")}
    for pose in initial.values():
        rotation_matrix(pose[3:])
    for name in ("gear_pose", "ee_pose", "bin_pose"):
        norms = np.linalg.norm(arrays[name][:, 3:], axis=1)
        require(bool(np.isfinite(norms).all() and np.all(norms > 0)), f"Invalid {name} quaternion")
    forces = arrays["contact_force"]
    require(bool(np.all(forces >= 0)), "Contact magnitudes cannot be negative")
    threshold = config["contact_force_min_n"]
    frames = config["contact_consecutive_frames"]
    bilateral = np.all(forces >= threshold, axis=1)
    clear = np.all(forces < threshold, axis=1)
    contact = _first(bilateral, frames)
    lift = arrays["gear_pose"][:, 2] - initial["gear_pose"][2]
    contact_height = initial["gear_pose"][2] if contact is None else arrays["gear_pose"][contact - frames + 1, 2]
    precontact_lift = max(0.0, float(contact_height - initial["gear_pose"][2]))
    measured_lift = lift - precontact_lift
    qualified = (consecutive(bilateral) >= frames) & (measured_lift >= config["required_lift_m"] - 1e-12)
    lifted = np.maximum.accumulate(qualified)
    release = _first(clear & lifted, frames)
    release_start = count if release is None else release - frames + 1
    drift_max = None
    micro_lift = False
    if contact is not None:
        relative = np.array(
            [
                (gear[:3] - ee[:3]) @ rotation_matrix(ee[3:])
                for gear, ee in zip(arrays["gear_pose"], arrays["ee_pose"], strict=True)
            ]
        )
        drift = np.linalg.norm(relative - relative[contact], axis=1)
        transport = (np.arange(count) >= contact) & (np.arange(count) < release_start)
        transport &= measured_lift >= 0.6 * config["micro_lift_m"]
        if np.any(transport):
            drift_max = float(drift[transport].max())
            micro_lift = bool(np.any(transport & bilateral & (drift <= config["retention_drift_m"])))
    released = np.zeros(count, dtype=bool) if release is None else np.arange(count) >= release
    dwell = consecutive(np.all(arrays["placement_margins"] >= 0, axis=1) & clear & lifted & released)
    required_dwell = max(1, round(config["bin_settle_seconds"] * config["control_hz"]))
    values = np.concatenate(
        (
            finite_array(initial_command, (7,), "Initial command")[None],
            finite_array(data["actions"], (count, 7), "Executed targets"),
        )
    )
    derivatives = {}
    for label in ("max_joint_speed_rad_s", "max_joint_acceleration_rad_s2", "max_joint_jerk_rad_s3"):
        values = np.diff(values, axis=0) * config["control_hz"]
        derivatives[label] = float(np.max(np.abs(values))) if values.size else 0.0
    speed = float(np.linalg.norm(arrays["gear_velocity"][:, :3], axis=1).max())
    gates = {
        "bilateral_contact": contact is not None,
        "micro_lift_retained": micro_lift,
        "qualified_lift": bool(lifted[-1]),
        "retention_before_release": drift_max is not None and drift_max <= config["retention_drift_m"],
        "contact_cleared_after_lift": release is not None,
        "live_bin_final_dwell": bool(dwell[-1] >= required_dwell),
        "dynamics": speed <= config["max_gear_speed_m_s"]
        and all(value <= config[key] + 1e-5 for key, value in derivatives.items()),
    }
    result = {
        "scorer": "ur10e_live_bin_v1",
        "success": all(gates.values()),
        "gates": gates,
        "placement_success": all(value for key, value in gates.items() if key != "dynamics"),
        "maximum_lift_m": float(lift.max()),
        "maximum_gear_speed_m_s": speed,
        "maximum_transport_drift_m": drift_max,
        "retention_frame": "end_effector_local; reference_at_confirmed_contact",
        "final_dwell_frames": int(dwell[-1]),
        "required_dwell_frames": required_dwell,
        "target_command_derivatives": derivatives,
        "final_placement_margins_m": arrays["placement_margins"][-1].tolist(),
        "maximum_bin_displacement_m": float(
            np.linalg.norm(arrays["bin_pose"][:, :3] - initial["bin_pose"][:3], axis=1).max()
        ),
        "limitations": [
            "Conservative box envelope, not mesh penetration",
            "Body-level filtered contact, not pad-face proof",
            "Contact clearing is not independent release-intent evidence",
            "No hardware validation",
        ],
    }
    canonical(result)
    return result
