"""
LeRobot dataset loader service for parquet-based v2/v3 datasets.

Provides support for loading trajectory data, metadata, and video paths
from LeRobot datasets in the new parquet + video format.

LeRobot v3 structure:
- data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet
- meta/info.json, stats.json, tasks.parquet
- meta/episodes/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet
- videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# PyArrow is an optional dependency
try:
    import pyarrow.parquet as pq

    PARQUET_AVAILABLE = True
except ImportError:
    PARQUET_AVAILABLE = False


@dataclass
class LeRobotEpisodeData:
    """Episode data loaded from a LeRobot parquet dataset."""

    episode_index: int
    """Episode index within the dataset."""

    length: int
    """Number of frames in the episode."""

    timestamps: NDArray[np.float64]
    """Timestamp array of shape (N,)."""

    frame_indices: NDArray[np.int64]
    """Frame index array of shape (N,)."""

    joint_positions: NDArray[np.float64]
    """Joint positions (observation.state) array of shape (N, num_joints)."""

    joint_velocities: NDArray[np.float64] | None
    """Joint velocities array of shape (N, num_joints), if available."""

    actions: NDArray[np.float64]
    """Action array of shape (N, action_dim)."""

    task_index: int
    """Task index for this episode."""

    video_paths: dict[str, Path]
    """Video file paths by camera key."""

    metadata: dict[str, Any]
    """Additional metadata from info.json."""


class LeRobotLoaderError(Exception):
    """Exception raised for LeRobot loading failures."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


@dataclass
class LeRobotDatasetInfo:
    """Cached dataset info from meta/info.json."""

    codebase_version: str
    robot_type: str
    total_episodes: int
    total_frames: int
    total_tasks: int
    total_chunks: int
    chunks_size: int
    fps: float
    splits: dict[str, str]
    data_path: str
    video_path: str
    features: dict[str, dict[str, Any]]
    raw_info: dict[str, Any] = field(default_factory=dict)


class LeRobotLoader:
    """
    Loads episode data from LeRobot parquet-format datasets.

    Supports LeRobot v2/v3 format with the following structure:
    - data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet
    - meta/info.json: Dataset metadata
    - meta/stats.json: Feature statistics
    - meta/episodes/: Episode metadata parquet files
    - videos/: Video files organized by camera and chunk

    Example:
        >>> loader = LeRobotLoader(base_path="/data/datasets/ur10e_episodes")
        >>> info = loader.get_dataset_info()
        >>> episode = loader.load_episode(0)
        >>> print(f"Episode length: {episode.length}")
    """

    def __init__(self, base_path: str | Path):
        """
        Initialize the LeRobot loader.

        Args:
            base_path: Path to the LeRobot dataset directory.

        Raises:
            ImportError: If pyarrow is not installed.
            LeRobotLoaderError: If the dataset structure is invalid.
        """
        if not PARQUET_AVAILABLE:
            raise ImportError("LeRobot support requires pyarrow package. Install with: pip install pyarrow")

        self.base_path = Path(base_path)
        self._info: LeRobotDatasetInfo | None = None
        self._episode_index_cache: dict[int, tuple[int, int]] = {}  # episode -> (chunk, file)
        self._episodes_meta_cache: dict[int, dict[str, Any]] | None = None

    def _load_info(self) -> LeRobotDatasetInfo:
        """Load and cache dataset info from meta/info.json."""
        if self._info is not None:
            return self._info

        info_path = self.base_path / "meta" / "info.json"
        if not info_path.exists():
            raise LeRobotLoaderError(f"info.json not found at {info_path}")

        try:
            with open(info_path) as f:
                raw = json.load(f)

            self._info = LeRobotDatasetInfo(
                codebase_version=raw.get("codebase_version", "v2.0"),
                robot_type=raw.get("robot_type", "unknown"),
                total_episodes=raw.get("total_episodes", 0),
                total_frames=raw.get("total_frames", 0),
                total_tasks=raw.get("total_tasks", 1),
                total_chunks=raw.get("total_chunks", 1),
                chunks_size=raw.get("chunks_size", 1000),
                fps=raw.get("fps", 30.0),
                splits=raw.get("splits", {}),
                data_path=raw.get(
                    "data_path",
                    "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
                ),
                video_path=raw.get(
                    "video_path",
                    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
                ),
                features=raw.get("features", {}),
                raw_info=raw,
            )
            return self._info
        except json.JSONDecodeError as e:
            raise LeRobotLoaderError(f"Invalid info.json: {e}", cause=e)
        except Exception as e:
            raise LeRobotLoaderError(f"Failed to load info.json: {e}", cause=e)

    def get_dataset_info(self) -> LeRobotDatasetInfo:
        """
        Get dataset metadata.

        Returns:
            LeRobotDatasetInfo with dataset metadata.
        """
        return self._load_info()

    def _find_episode_location(self, episode_index: int) -> tuple[int, int]:
        """
        Find the chunk and file indices for an episode.

        In LeRobot format, episodes are usually stored one per chunk/file,
        where chunk_index == episode_index and file_index == 0.

        Returns:
            Tuple of (chunk_index, file_index).
        """
        if episode_index in self._episode_index_cache:
            return self._episode_index_cache[episode_index]

        info = self._load_info()

        # Standard layout: one episode per chunk
        chunk_idx = episode_index
        file_idx = 0

        # Verify the parquet file exists
        data_path = self._format_path(info.data_path, chunk_idx, file_idx)
        full_path = self.base_path / data_path

        if not full_path.exists():
            # Try searching all data files
            data_dir = self.base_path / "data"
            if data_dir.exists():
                for chunk_dir in sorted(data_dir.iterdir()):
                    if chunk_dir.is_dir() and chunk_dir.name.startswith("chunk-"):
                        for parquet_file in chunk_dir.glob("*.parquet"):
                            try:
                                table = pq.read_table(parquet_file)
                                df = table.to_pandas()
                                if "episode_index" in df.columns:
                                    episodes_in_file = df["episode_index"].unique()
                                    if episode_index in episodes_in_file:
                                        chunk_num = int(chunk_dir.name.split("-")[1])
                                        file_num = int(parquet_file.stem.split("-")[1])
                                        self._episode_index_cache[episode_index] = (
                                            chunk_num,
                                            file_num,
                                        )
                                        return chunk_num, file_num
                            except Exception:
                                continue

            raise LeRobotLoaderError(f"No data file found for episode {episode_index}")

        self._episode_index_cache[episode_index] = (chunk_idx, file_idx)
        return chunk_idx, file_idx

    def _format_path(self, template: str, chunk_index: int, file_index: int, video_key: str = "") -> str:
        """Format a path template with indices."""
        return template.format(chunk_index=chunk_index, file_index=file_index, video_key=video_key)

    def list_episodes(self) -> list[int]:
        """
        List all available episode indices.

        Returns:
            Sorted list of episode indices.
        """
        info = self._load_info()
        return list(range(info.total_episodes))

    def list_episodes_with_meta(self) -> dict[int, dict[str, Any]]:
        """
        Load per-episode metadata from meta/episodes/ parquet files.

        Reads length and task_index for all episodes from the episode metadata
        parquet files, avoiding the full-frame data parquet files. Results are
        cached in-memory after the first call.

        Returns:
            Dict mapping episode_index -> {length, task_index, cameras, fps, robot_type}.
            Falls back to zero-filled placeholders if meta/episodes/ is absent.
        """
        if self._episodes_meta_cache is not None:
            return self._episodes_meta_cache

        info = self._load_info()
        cameras = [k for k, v in info.features.items() if v.get("dtype") == "video"]
        meta_episodes_dir = self.base_path / "meta" / "episodes"
        result: dict[int, dict[str, Any]] = {}

        if meta_episodes_dir.exists():
            for chunk_dir in sorted(meta_episodes_dir.iterdir()):
                if not chunk_dir.is_dir() or not chunk_dir.name.startswith("chunk-"):
                    continue
                for parquet_file in sorted(chunk_dir.glob("*.parquet")):
                    try:
                        table = pq.read_table(parquet_file)
                        df = table.to_pandas()
                        for _, row in df.iterrows():
                            idx = int(row["episode_index"]) if "episode_index" in df.columns else int(row.name)
                            result[idx] = {
                                "length": int(row.get("length", 0)),
                                "task_index": int(row.get("task_index", 0)),
                                "cameras": cameras,
                                "fps": info.fps,
                                "robot_type": info.robot_type,
                            }
                    except Exception:
                        continue

        if not result:
            result = {
                idx: {
                    "length": 0,
                    "task_index": 0,
                    "cameras": cameras,
                    "fps": info.fps,
                    "robot_type": info.robot_type,
                }
                for idx in range(info.total_episodes)
            }

        self._episodes_meta_cache = result
        return result

    def load_episode(self, episode_index: int) -> LeRobotEpisodeData:
        """
        Load episode data from parquet files.

        Args:
            episode_index: Index of the episode to load.

        Returns:
            LeRobotEpisodeData containing the episode data.

        Raises:
            LeRobotLoaderError: If the episode cannot be loaded.
        """
        info = self._load_info()
        chunk_idx, file_idx = self._find_episode_location(episode_index)

        # Load parquet data
        data_path = self._format_path(info.data_path, chunk_idx, file_idx)
        full_path = self.base_path / data_path

        try:
            table = pq.read_table(full_path)
            df = table.to_pandas()

            # Filter to requested episode
            if "episode_index" in df.columns:
                df = df[df["episode_index"] == episode_index]

            if df.empty:
                raise LeRobotLoaderError(f"Episode {episode_index} not found in {full_path}")

            # Sort by frame_index
            if "frame_index" in df.columns:
                df = df.sort_values("frame_index")

            length = len(df)

            # Extract timestamps
            timestamps = df["timestamp"].values if "timestamp" in df.columns else np.arange(length) / info.fps

            # Extract frame indices
            frame_indices = df["frame_index"].values if "frame_index" in df.columns else np.arange(length)

            # Extract observation state (joint positions)
            joint_positions: NDArray[np.float64]
            if "observation.state" in df.columns:
                joint_positions = np.stack(df["observation.state"].values)
            elif "qpos" in df.columns:
                joint_positions = np.stack(df["qpos"].values)
            else:
                # Create zeros if no state data
                joint_positions = np.zeros((length, 6), dtype=np.float64)

            # Extract joint velocities if available
            joint_velocities: NDArray[np.float64] | None = None
            if "observation.velocity" in df.columns:
                joint_velocities = np.stack(df["observation.velocity"].values)
            elif "qvel" in df.columns:
                joint_velocities = np.stack(df["qvel"].values)

            # Extract actions
            actions: NDArray[np.float64] = (
                np.stack(df["action"].values) if "action" in df.columns else np.zeros_like(joint_positions)
            )

            # Get task index
            task_index = int(df["task_index"].iloc[0]) if "task_index" in df.columns else 0

            # Find video paths
            video_paths: dict[str, Path] = {}
            for feature_name, feature_info in info.features.items():
                if feature_info.get("dtype") == "video":
                    video_key = feature_name
                    video_rel_path = self._format_path(info.video_path, chunk_idx, file_idx, video_key)
                    video_full_path = self.base_path / video_rel_path
                    if video_full_path.exists():
                        video_paths[video_key] = video_full_path

            return LeRobotEpisodeData(
                episode_index=episode_index,
                length=length,
                timestamps=timestamps.astype(np.float64),
                frame_indices=frame_indices.astype(np.int64),
                joint_positions=joint_positions.astype(np.float64),
                joint_velocities=joint_velocities,
                actions=actions.astype(np.float64),
                task_index=task_index,
                video_paths=video_paths,
                metadata={
                    "robot_type": info.robot_type,
                    "fps": info.fps,
                    "codebase_version": info.codebase_version,
                },
            )

        except LeRobotLoaderError:
            raise
        except Exception as e:
            raise LeRobotLoaderError(f"Failed to load episode {episode_index}: {e}", cause=e)

    def get_episode_info(self, episode_index: int) -> dict[str, Any]:
        """
        Get metadata for an episode without loading full data.

        Reads from meta/episodes/ parquet files when available, avoiding the
        full frame data parquet. Falls back to the data parquet only when the
        episodes metadata directory is absent.

        Args:
            episode_index: Episode index.

        Returns:
            Dictionary with episode metadata.
        """
        meta_episodes_dir = self.base_path / "meta" / "episodes"
        if meta_episodes_dir.exists():
            episodes_meta = self.list_episodes_with_meta()
            if episode_index in episodes_meta:
                result = episodes_meta[episode_index].copy()
                result["episode_index"] = episode_index
                return result

        info = self._load_info()
        chunk_idx, file_idx = self._find_episode_location(episode_index)

        data_path = self._format_path(info.data_path, chunk_idx, file_idx)
        full_path = self.base_path / data_path

        try:
            table = pq.read_table(full_path)
            df = table.to_pandas()

            if "episode_index" in df.columns:
                df = df[df["episode_index"] == episode_index]

            length = len(df)
            task_index = int(df["task_index"].iloc[0]) if "task_index" in df.columns else 0

            cameras: list[str] = []
            for feature_name, feature_info in info.features.items():
                if feature_info.get("dtype") == "video":
                    cameras.append(feature_name)

            return {
                "episode_index": episode_index,
                "length": length,
                "fps": info.fps,
                "cameras": cameras,
                "task_index": task_index,
                "robot_type": info.robot_type,
            }

        except Exception as e:
            raise LeRobotLoaderError(f"Failed to get info for episode {episode_index}: {e}", cause=e)

    def get_video_path(self, episode_index: int, camera_key: str) -> Path | None:
        """
        Get the video file path for an episode and camera.

        Args:
            episode_index: Episode index.
            camera_key: Camera feature key (e.g., 'observation.images.color').

        Returns:
            Path to the video file, or None if not found.
        """
        info = self._load_info()
        chunk_idx, file_idx = self._find_episode_location(episode_index)

        video_rel_path = self._format_path(info.video_path, chunk_idx, file_idx, camera_key)
        video_full_path = self.base_path / video_rel_path

        if video_full_path.exists():
            return video_full_path
        return None

    def get_cameras(self) -> list[str]:
        """
        Get list of available camera keys.

        Returns:
            List of camera feature names.
        """
        info = self._load_info()
        cameras: list[str] = []
        for feature_name, feature_info in info.features.items():
            if feature_info.get("dtype") == "video":
                cameras.append(feature_name)
        return cameras


def is_lerobot_dataset(path: str | Path) -> bool:
    """
    Check if a path contains a LeRobot parquet-format dataset.

    Args:
        path: Path to check.

    Returns:
        True if the path contains a LeRobot dataset structure.
    """
    path = Path(path)
    info_path = path / "meta" / "info.json"
    data_dir = path / "data"
    return info_path.exists() and data_dir.exists()


def get_lerobot_loader(base_path: str | Path) -> LeRobotLoader:
    """
    Create a LeRobot loader for a dataset directory.

    Args:
        base_path: Path to the dataset directory.

    Returns:
        Configured LeRobotLoader instance.
    """
    return LeRobotLoader(base_path)
