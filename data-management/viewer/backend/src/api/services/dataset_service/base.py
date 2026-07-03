"""
DatasetFormatHandler protocol and shared trajectory conversion utilities.

Defines the contract that LeRobot, HDF5, and future format handlers
must implement to integrate with the DatasetService orchestrator.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from ...models.datasources import (
    DatasetInfo,
    EpisodeData,
    FeatureSchema,
    TrajectoryPoint,
    TrajectoryVariable,
)

_RESERVED_FEATURE_NAMES = {
    "timestamp",
    "frame_index",
    "episode_index",
    "index",
    "task_index",
}

_FEATURE_KIND_LABELS = {
    "state": "State",
    "action": "Action",
    "signal": "Signal",
    "velocity": "Velocity",
}


def normalize_feature_names(raw: Any) -> list[str] | None:
    """Coerce a feature ``names`` value into ``list[str]``.

    Some LeRobot ``info.json`` files store names as a list-of-lists
    (e.g. ``[["JOINT_A", "JOINT_B"]]``) or as a dict keyed by axis. Flatten
    nested sequences and stringify scalars so ``FeatureSchema`` validates
    instead of raising and dropping the dataset.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list | tuple):
        return [str(raw)]
    flat: list[str] = []
    for item in raw:
        if isinstance(item, list | tuple):
            flat.extend(str(x) for x in item)
        else:
            flat.append(str(item))
    return flat or None


def _schema_value(schema: FeatureSchema | Mapping[str, Any] | None, key: str) -> Any:
    if schema is None:
        return None
    if isinstance(schema, FeatureSchema):
        return getattr(schema, key, None)
    return schema.get(key)


def _humanize_feature_name(name: str) -> str:
    cleaned = name.removeprefix("observation.").replace("_", " ").replace(".", " ")
    return " ".join(part.capitalize() for part in cleaned.split())


def _feature_kind(feature_name: str, kind: str | None = None) -> str:
    if kind is not None:
        return kind
    if feature_name in {"observation.state", "qpos"}:
        return "state"
    if feature_name == "action":
        return "action"
    if feature_name in {"observation.velocity", "qvel"}:
        return "velocity"
    return "signal"


def _feature_matrix(values: NDArray[np.float64], length: int) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 0:
        return np.full((length, 1), float(array), dtype=np.float64)
    if array.ndim == 1:
        return array.reshape(-1, 1)
    return array.reshape(array.shape[0], -1)


def _dimension_names(
    feature_name: str,
    schema: FeatureSchema | Mapping[str, Any] | None,
    width: int,
) -> list[str]:
    raw_names = _schema_value(schema, "names")
    if isinstance(raw_names, list) and len(raw_names) == width:
        names = [str(name) for name in raw_names]
        if all(name for name in names):
            return names
    if width == 1 and isinstance(raw_names, list) and len(raw_names) == 1 and raw_names[0]:
        return [str(raw_names[0])]
    return [f"{_humanize_feature_name(feature_name)} {index + 1}" for index in range(width)]


def _variable_key(feature_name: str, width: int, index: int) -> str:
    if width == 1 and feature_name not in {"action", "observation.state", "qpos"}:
        return feature_name
    return f"{feature_name}[{index}]"


def _variable_label(feature_name: str, kind: str, width: int, dimension_name: str) -> str:
    if width == 1 and kind == "signal":
        return _humanize_feature_name(feature_name)
    if kind in {"state", "action"}:
        return dimension_name
    source_label = _FEATURE_KIND_LABELS.get(kind, _humanize_feature_name(feature_name))
    return f"{source_label}: {dimension_name}"


def is_plottable_feature(feature_name: str, schema: FeatureSchema | Mapping[str, Any] | None) -> bool:
    """Return True for numeric or boolean dataset features useful in trajectory plots."""
    if feature_name in _RESERVED_FEATURE_NAMES:
        return False
    dtype = str(_schema_value(schema, "dtype") or "").lower()
    if dtype in {"video", "image", "string", "str", "utf8", "bytes"}:
        return False
    return any(token in dtype for token in ("float", "int", "bool"))


def build_trajectory_variables(
    *,
    length: int,
    feature_values: Mapping[str, NDArray[np.float64] | None],
    feature_schemas: Mapping[str, FeatureSchema | Mapping[str, Any]] | None = None,
    feature_kinds: Mapping[str, str] | None = None,
) -> tuple[list[TrajectoryVariable], dict[str, NDArray[np.float64]]]:
    """Build ordered trajectory variable metadata and per-variable value arrays."""
    variables: list[TrajectoryVariable] = []
    variable_values: dict[str, NDArray[np.float64]] = {}

    for feature_name, values in feature_values.items():
        if values is None:
            continue

        schema = feature_schemas.get(feature_name) if feature_schemas is not None else None
        if not is_plottable_feature(feature_name, schema):
            continue

        matrix = _feature_matrix(values, length)
        if matrix.shape[0] == 0:
            continue

        width = matrix.shape[1]
        names = _dimension_names(feature_name, schema, width)
        kind = _feature_kind(feature_name, feature_kinds.get(feature_name) if feature_kinds else None)

        for index in range(width):
            key = _variable_key(feature_name, width, index)
            variables.append(
                TrajectoryVariable(
                    key=key,
                    label=_variable_label(feature_name, kind, width, names[index]),
                    source=feature_name,
                    index=index if width > 1 or feature_name in {"action", "observation.state", "qpos"} else None,
                    kind=kind,
                )
            )
            variable_values[key] = matrix[:, index]

    return variables, variable_values


def build_trajectory(
    *,
    length: int,
    timestamps: NDArray[np.float64],
    frame_indices: NDArray[np.int64] | None = None,
    joint_positions: NDArray[np.float64],
    joint_velocities: NDArray[np.float64] | None = None,
    end_effector_poses: NDArray[np.float64] | None = None,
    gripper_states: NDArray[np.float64] | None = None,
    trajectory_variables: list[TrajectoryVariable] | None = None,
    variable_values: Mapping[str, NDArray[np.float64]] | None = None,
    clamp_gripper: bool = False,
) -> list[TrajectoryPoint]:
    """
    Convert raw numpy arrays into a list of TrajectoryPoint models.

    Works for both LeRobot and HDF5 data by accepting optional arrays
    with sensible defaults (zeros) for missing fields. When ``joint_velocities``
    is not provided, it is estimated via finite differences of
    ``joint_positions`` over ``timestamps`` so the velocity view in the UI is
    populated for datasets that only record positions.
    """
    num_joints = joint_positions.shape[1] if joint_positions.ndim > 1 else 6

    if joint_velocities is None and joint_positions.ndim == 2 and joint_positions.shape[0] > 1 and length > 1:
        # Backwards/forwards differences along the time axis. Guard against
        # zero or non-monotonic timestamps by clamping dt to a small positive
        # value.
        dt = np.diff(timestamps)
        dt = np.where(dt > 1e-6, dt, 1e-6)
        diffs = np.diff(joint_positions, axis=0) / dt[:, None]
        joint_velocities = np.empty_like(joint_positions)
        joint_velocities[:-1] = diffs
        joint_velocities[-1] = diffs[-1]

    points: list[TrajectoryPoint] = []

    for i in range(length):
        joint_pos = joint_positions[i].tolist()
        joint_vel = joint_velocities[i].tolist() if joint_velocities is not None else [0.0] * num_joints
        ee_pose = end_effector_poses[i].tolist() if end_effector_poses is not None else [0.0] * 6
        gripper = float(gripper_states[i]) if gripper_states is not None else 0.0
        if clamp_gripper:
            gripper = max(0.0, min(1.0, gripper))
        point_variables: dict[str, float] = {}
        for variable in trajectory_variables or []:
            values = variable_values.get(variable.key) if variable_values is not None else None
            if values is None or i >= len(values):
                continue
            value = values[i]
            if isinstance(value, np.generic):
                value = value.item()
            if isinstance(value, bool):
                point_variables[variable.key] = 1.0 if value else 0.0
            elif isinstance(value, int | float):
                point_variables[variable.key] = float(value)

        points.append(
            TrajectoryPoint(
                timestamp=float(timestamps[i]),
                frame=int(frame_indices[i]) if frame_indices is not None else i,
                joint_positions=joint_pos,
                joint_velocities=joint_vel,
                end_effector_pose=ee_pose,
                gripper_state=gripper,
                variables=point_variables,
            )
        )

    return points


@runtime_checkable
class DatasetFormatHandler(Protocol):
    """Protocol for format-specific dataset operations."""

    def can_handle(self, dataset_path: Path) -> bool:
        """Return True if this handler supports the dataset at the given path."""
        raise NotImplementedError

    def has_loader(self, dataset_id: str) -> bool:
        """Return True if a loader is already initialized for this dataset."""
        raise NotImplementedError

    def discover(self, dataset_id: str, dataset_path: Path) -> DatasetInfo | None:
        """Build DatasetInfo from the dataset directory. Returns None on failure."""
        raise NotImplementedError

    def get_loader(self, dataset_id: str, dataset_path: Path) -> bool:
        """Get or create the underlying loader for a dataset. Returns True if successful."""
        raise NotImplementedError

    def list_episodes(self, dataset_id: str) -> tuple[list[int], dict[int, dict]]:
        """Return (sorted episode indices, {index: metadata dict})."""
        raise NotImplementedError

    def load_episode(
        self,
        dataset_id: str,
        episode_idx: int,
        dataset_info: DatasetInfo | None = None,
    ) -> EpisodeData | None:
        """Load complete episode data. Returns None on failure."""
        raise NotImplementedError

    def get_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        """Load trajectory data only. Returns empty list on failure."""
        raise NotImplementedError

    def get_frame_image(
        self,
        dataset_id: str,
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes | None:
        """Get a single JPEG frame image. Returns None if unavailable."""
        raise NotImplementedError

    def get_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        """List available camera names for an episode."""
        raise NotImplementedError

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """Get filesystem path to a video file. Returns None if unavailable."""
        raise NotImplementedError
