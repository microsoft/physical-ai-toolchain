"""
HDF5 data loader service for LeRobot episode data.

Provides support for loading trajectory data, images, and metadata
from HDF5 files following the LeRobot dataset format.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

# h5py is an optional dependency
try:
    import h5py

    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False


@dataclass
class HDF5EpisodeData:
    """Episode data loaded from an HDF5 file."""

    episode_index: int
    """Episode index within the dataset."""

    length: int
    """Number of frames in the episode."""

    timestamps: NDArray[np.float64]
    """Timestamp array of shape (N,)."""

    joint_positions: NDArray[np.float64]
    """Joint positions array of shape (N, num_joints)."""

    joint_velocities: NDArray[np.float64] | None
    """Joint velocities array of shape (N, num_joints), if available."""

    end_effector_pose: NDArray[np.float64] | None
    """End-effector pose array of shape (N, 6 or 7), if available."""

    gripper_states: NDArray[np.float64] | None
    """Gripper state array of shape (N,), if available."""

    actions: NDArray[np.float64] | None
    """Action array of shape (N, action_dim), if available."""

    images: dict[str, NDArray[np.uint8]]
    """Camera images by camera name, each of shape (N, H, W, C)."""

    task_index: int
    """Task index for this episode."""

    metadata: dict
    """Additional metadata from the HDF5 file."""


class HDF5LoaderError(Exception):
    """Exception raised for HDF5 loading failures."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class HDF5Loader:
    """
    Loads episode data from HDF5 files.

    Supports LeRobot HDF5 format with the following structure:
    - /data/qpos: Joint positions (N, num_joints)
    - /data/qvel: Joint velocities (N, num_joints) [optional]
    - /data/action: Actions (N, action_dim)
    - /data/timestamps: Timestamps (N,) [optional]
    - /observations/images/<camera_name>: Images (N, H, W, C)
    - /observations/state: Robot state [optional]
    - /metadata: Episode metadata attributes

    Example:
        >>> loader = HDF5Loader(base_path="/data/datasets/my_dataset")
        >>> episode = loader.load_episode(0)
        >>> print(f"Episode length: {episode.length}")
    """

    def __init__(self, base_path: str | Path):
        """
        Initialize the HDF5 loader.

        Args:
            base_path: Base path to the dataset directory containing HDF5 files.

        Raises:
            ImportError: If h5py is not installed.
        """
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 support requires h5py package. Install with: pip install h5py")

        self.base_path = Path(base_path)
        self._episode_cache: dict[int, Path] = {}

    def _find_episode_file(self, episode_index: int) -> Path:
        """
        Find the HDF5 file for a given episode index.

        Searches for files matching common naming patterns:
        - episode_{index:06d}.hdf5
        - episode_{index}.hdf5
        - ep_{index:06d}.hdf5
        - data/episode_{index:06d}.hdf5

        Args:
            episode_index: Episode index to find.

        Returns:
            Path to the HDF5 file.

        Raises:
            HDF5LoaderError: If no matching file is found.
        """
        if episode_index in self._episode_cache:
            return self._episode_cache[episode_index]

        # Try different naming patterns
        patterns = [
            f"episode_{episode_index:06d}.hdf5",
            f"episode_{episode_index}.hdf5",
            f"ep_{episode_index:06d}.hdf5",
            f"ep_{episode_index}.hdf5",
            f"data/episode_{episode_index:06d}.hdf5",
            f"data/episode_{episode_index}.hdf5",
            f"episodes/episode_{episode_index:06d}.hdf5",
            f"episodes/episode_{episode_index}.hdf5",
        ]

        for pattern in patterns:
            file_path = self.base_path / pattern
            if file_path.exists():
                self._episode_cache[episode_index] = file_path
                return file_path

        raise HDF5LoaderError(f"No HDF5 file found for episode {episode_index} in {self.base_path}")

    def list_episodes(self) -> list[int]:
        """
        List all available episode indices.

        Returns:
            Sorted list of episode indices.
        """
        episode_indices = set()

        # Search for HDF5 files in common locations
        search_paths = [
            self.base_path,
            self.base_path / "data",
            self.base_path / "episodes",
        ]

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for file_path in search_path.glob("*.hdf5"):
                # Extract episode index from filename
                filename = file_path.stem
                for prefix in ["episode_", "ep_"]:
                    if filename.startswith(prefix):
                        try:
                            index_str = filename[len(prefix) :]
                            episode_indices.add(int(index_str))
                        except ValueError:
                            continue

        return sorted(episode_indices)

    def load_episode(
        self,
        episode_index: int,
        load_images: bool = False,
        image_cameras: list[str] | None = None,
    ) -> HDF5EpisodeData:
        """
        Load episode data from an HDF5 file.

        Args:
            episode_index: Index of the episode to load.
            load_images: Whether to load image data (can be memory intensive).
            image_cameras: Specific cameras to load (None = all cameras).

        Returns:
            HDF5EpisodeData containing the episode data.

        Raises:
            HDF5LoaderError: If the file cannot be read or is invalid.
        """
        file_path = self._find_episode_file(episode_index)

        try:
            with h5py.File(file_path, "r") as f:
                return self._parse_hdf5_file(f, episode_index, load_images, image_cameras)
        except HDF5LoaderError:
            raise
        except Exception as e:
            raise HDF5LoaderError(
                f"Failed to load episode {episode_index} from {file_path}: {e}",
                cause=e,
            )

    def _parse_hdf5_file(
        self,
        f: "h5py.File",
        episode_index: int,
        load_images: bool,
        image_cameras: list[str] | None,
    ) -> HDF5EpisodeData:
        """Parse an HDF5 file and extract episode data."""
        # Load joint positions (required) - check multiple formats
        joint_positions = self._load_array(f, ["data/qpos", "qpos", "observations/qpos", "observation/state"])
        if joint_positions is None:
            raise HDF5LoaderError(f"No joint position data found in episode {episode_index}")

        length = len(joint_positions)

        # Load timestamps (optional, generate if missing)
        timestamps = self._load_array(f, ["data/timestamps", "timestamps", "timestamp", "time"])
        if timestamps is None:
            # Generate timestamps assuming 30 FPS
            fps = self._get_attr(f, "fps", 30.0)
            timestamps = np.arange(length) / fps

        # Load optional arrays
        joint_velocities = self._load_array(f, ["data/qvel", "qvel", "observations/qvel"])
        end_effector_pose = self._load_array(
            f, ["data/ee_pose", "ee_pose", "observations/ee_pose", "data/cartesian_pos"]
        )
        gripper_states = self._load_array(f, ["data/gripper", "gripper", "observations/gripper", "data/gripper_state"])
        actions = self._load_array(f, ["data/action", "action", "actions"])

        # Load images if requested
        images: dict[str, NDArray[np.uint8]] = {}
        if load_images:
            images = self._load_images(f, image_cameras)

        # Load metadata
        metadata = self._load_metadata(f)
        task_index = self._get_attr(f, "task_index", 0)
        if isinstance(task_index, bytes):
            task_index = int(task_index.decode())

        return HDF5EpisodeData(
            episode_index=episode_index,
            length=length,
            timestamps=timestamps,
            joint_positions=joint_positions,
            joint_velocities=joint_velocities,
            end_effector_pose=end_effector_pose,
            gripper_states=gripper_states,
            actions=actions,
            images=images,
            task_index=int(task_index),
            metadata=metadata,
        )

    def _load_array(self, f: "h5py.File", paths: list[str]) -> NDArray[np.float64] | None:
        """Try to load an array from multiple possible paths."""
        for path in paths:
            if path in f:
                data = f[path][:]
                return np.asarray(data, dtype=np.float64)
        return None

    def _load_images(self, f: "h5py.File", cameras: list[str] | None) -> dict[str, NDArray[np.uint8]]:
        """Load image data from the HDF5 file."""
        images: dict[str, NDArray[np.uint8]] = {}

        # Check for images in common locations
        image_groups = ["observations/images", "observation/images", "images", "data/images"]

        for group_path in image_groups:
            if group_path not in f:
                continue

            group = f[group_path]
            if not isinstance(group, h5py.Group):
                continue

            for camera_name in group:
                if cameras is not None and camera_name not in cameras:
                    continue

                try:
                    image_data = group[camera_name][:]
                    images[camera_name] = np.asarray(image_data, dtype=np.uint8)
                except Exception:
                    continue

        return images

    def _load_metadata(self, f: "h5py.File") -> dict:
        """Load metadata attributes from the HDF5 file."""
        metadata = {}

        # Load root attributes
        for key in f.attrs:
            value = f.attrs[key]
            if isinstance(value, bytes):
                value = value.decode()
            elif isinstance(value, np.ndarray):
                value = value.tolist()
            metadata[key] = value

        # Load metadata group if present
        if "metadata" in f:
            meta_group = f["metadata"]
            if isinstance(meta_group, h5py.Group):
                for key in meta_group.attrs:
                    value = meta_group.attrs[key]
                    if isinstance(value, bytes):
                        value = value.decode()
                    elif isinstance(value, np.ndarray):
                        value = value.tolist()
                    metadata[key] = value

        return metadata

    def _get_attr(self, f: "h5py.File", key: str, default):
        """Get an attribute from the file, returning default if not found."""
        if key in f.attrs:
            value = f.attrs[key]
            if isinstance(value, bytes):
                return value.decode()
            return value
        return default

    def get_episode_info(self, episode_index: int) -> dict:
        """
        Get metadata for an episode without loading full data.

        Args:
            episode_index: Episode index.

        Returns:
            Dictionary with episode metadata (length, fps, cameras, etc.)
        """
        file_path = self._find_episode_file(episode_index)

        try:
            with h5py.File(file_path, "r") as f:
                # Get length from first data array found
                length = 0
                for path in ["data/qpos", "qpos", "observations/qpos", "data/action"]:
                    if path in f:
                        length = len(f[path])
                        break

                # Get FPS
                fps = self._get_attr(f, "fps", 30.0)

                # Get available cameras
                cameras = []
                for group_path in [
                    "observations/images",
                    "observation/images",
                    "images",
                    "data/images",
                ]:
                    if group_path in f and isinstance(f[group_path], h5py.Group):
                        cameras = list(f[group_path].keys())
                        break

                # Get other metadata
                task_index = self._get_attr(f, "task_index", 0)

                return {
                    "episode_index": episode_index,
                    "length": length,
                    "fps": float(fps),
                    "cameras": cameras,
                    "task_index": int(task_index) if task_index else 0,
                    "file_path": str(file_path),
                }

        except Exception as e:
            raise HDF5LoaderError(f"Failed to get info for episode {episode_index}: {e}", cause=e)


def get_hdf5_loader(base_path: str | Path) -> HDF5Loader:
    """
    Create an HDF5 loader for a dataset directory.

    Args:
        base_path: Path to the dataset directory.

    Returns:
        Configured HDF5Loader instance.
    """
    return HDF5Loader(base_path)
