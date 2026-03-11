"""
DatasetFormatHandler protocol and shared trajectory conversion utilities.

Defines the contract that LeRobot, HDF5, and future format handlers
must implement to integrate with the DatasetService orchestrator.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from ...models.datasources import (
    DatasetInfo,
    EpisodeData,
    TrajectoryPoint,
)


def build_trajectory(
    *,
    length: int,
    timestamps: NDArray[np.float64],
    frame_indices: NDArray[np.int64] | None = None,
    joint_positions: NDArray[np.float64],
    joint_velocities: NDArray[np.float64] | None = None,
    end_effector_poses: NDArray[np.float64] | None = None,
    gripper_states: NDArray[np.float64] | None = None,
    clamp_gripper: bool = False,
) -> list[TrajectoryPoint]:
    """
    Convert raw numpy arrays into a list of TrajectoryPoint models.

    Works for both LeRobot and HDF5 data by accepting optional arrays
    with sensible defaults (zeros) for missing fields.
    """
    num_joints = joint_positions.shape[1] if joint_positions.ndim > 1 else 6
    points: list[TrajectoryPoint] = []

    for i in range(length):
        joint_pos = joint_positions[i].tolist()
        joint_vel = joint_velocities[i].tolist() if joint_velocities is not None else [0.0] * num_joints
        ee_pose = end_effector_poses[i].tolist() if end_effector_poses is not None else [0.0] * 6
        gripper = float(gripper_states[i]) if gripper_states is not None else 0.0
        if clamp_gripper:
            gripper = max(0.0, min(1.0, gripper))

        points.append(
            TrajectoryPoint(
                timestamp=float(timestamps[i]),
                frame=int(frame_indices[i]) if frame_indices is not None else i,
                joint_positions=joint_pos,
                joint_velocities=joint_vel,
                end_effector_pose=ee_pose,
                gripper_state=gripper,
            )
        )

    return points


@runtime_checkable
class DatasetFormatHandler(Protocol):
    """Protocol for format-specific dataset operations."""

    def can_handle(self, dataset_path: Path) -> bool:
        """Return True if this handler supports the dataset at the given path."""
        pass

    def has_loader(self, dataset_id: str) -> bool:
        """Return True if a loader is already initialized for this dataset."""
        pass

    def discover(self, dataset_id: str, dataset_path: Path) -> DatasetInfo | None:
        """Build DatasetInfo from the dataset directory. Returns None on failure."""
        pass

    def get_loader(self, dataset_id: str, dataset_path: Path) -> bool:
        """Get or create the underlying loader for a dataset. Returns True if successful."""
        pass

    def list_episodes(self, dataset_id: str) -> tuple[list[int], dict[int, dict]]:
        """Return (sorted episode indices, {index: metadata dict})."""
        pass

    def load_episode(
        self,
        dataset_id: str,
        episode_idx: int,
        dataset_info: DatasetInfo | None = None,
    ) -> EpisodeData | None:
        """Load complete episode data. Returns None on failure."""
        pass

    def get_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        """Load trajectory data only. Returns empty list on failure."""
        pass

    def get_frame_image(
        self,
        dataset_id: str,
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes | None:
        """Get a single JPEG frame image. Returns None if unavailable."""
        pass

    def get_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        """List available camera names for an episode."""
        pass

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """Get filesystem path to a video file. Returns None if unavailable."""
        pass
