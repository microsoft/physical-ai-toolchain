"""
Dataset service for managing dataset access and metadata.

Provides a unified interface for accessing datasets across different
storage backends (local, Azure, Hugging Face) with support for HDF5 files
and LeRobot parquet-format datasets.
"""

import logging
import os
from pathlib import Path

from ..models.datasources import (
    DatasetInfo,
    EpisodeData,
    EpisodeMeta,
    FeatureSchema,
    TrajectoryPoint,
)
from ..storage import LocalStorageAdapter

logger = logging.getLogger(__name__)


# HDF5 support is optional
try:
    from .hdf5_loader import HDF5Loader

    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False
    HDF5Loader = None

# LeRobot parquet support is optional
try:
    from .lerobot_loader import (
        LeRobotLoader,
        is_lerobot_dataset,
    )

    LEROBOT_AVAILABLE = True
except ImportError:
    LEROBOT_AVAILABLE = False
    LeRobotLoader = None
    is_lerobot_dataset = lambda x: False  # noqa: E731


class DatasetService:
    """
    Service for dataset and episode operations.

    Abstracts storage backend details and provides a consistent
    API for accessing dataset metadata and episode data.
    Supports loading trajectory data from HDF5 files and LeRobot parquet datasets.
    """

    def __init__(self, base_path: str | None = None):
        """
        Initialize the dataset service.

        Args:
            base_path: Base path for local dataset storage.
                       Defaults to HMI_DATA_PATH env var or ./data.
        """
        if base_path is None:
            base_path = os.environ.get("HMI_DATA_PATH", "./data")
        self.base_path = base_path
        self._datasets: dict[str, DatasetInfo] = {}
        self._storage = LocalStorageAdapter(base_path)
        self._hdf5_loaders: dict[str, HDF5Loader] = {}
        self._lerobot_loaders: dict[str, LeRobotLoader] = {}

    def _get_lerobot_loader(self, dataset_id: str) -> LeRobotLoader | None:
        """
        Get or create a LeRobot loader for a dataset.

        Args:
            dataset_id: Dataset identifier.

        Returns:
            LeRobotLoader if available and dataset is LeRobot format, None otherwise.
        """
        dataset_id = dataset_id.replace("\r\n", "").replace("\n", "")
        if not LEROBOT_AVAILABLE:
            return None

        if dataset_id not in self._lerobot_loaders:
            from pathlib import Path

            dataset_path = Path(self.base_path) / dataset_id
            if dataset_path.exists() and is_lerobot_dataset(dataset_path):
                try:
                    self._lerobot_loaders[dataset_id] = LeRobotLoader(dataset_path)
                except Exception as e:
                    logger.warning(
                        "Failed to create LeRobot loader for %s: %s",
                        dataset_id.replace("\r\n", "").replace("\n", ""),
                        str(e).replace("\r\n", "").replace("\n", ""),
                    )
                    return None

        return self._lerobot_loaders.get(dataset_id)

    def _get_hdf5_loader(self, dataset_id: str) -> HDF5Loader | None:
        """
        Get or create an HDF5 loader for a dataset.

        Args:
            dataset_id: Dataset identifier.

        Returns:
            HDF5Loader if available and dataset has HDF5 files, None otherwise.
        """
        if not HDF5_AVAILABLE:
            return None

        if dataset_id not in self._hdf5_loaders:
            from pathlib import Path

            dataset_path = Path(self.base_path) / dataset_id
            if dataset_path.exists():
                # Check if there are HDF5 files
                hdf5_files = list(dataset_path.glob("**/*.hdf5"))
                if hdf5_files:
                    self._hdf5_loaders[dataset_id] = HDF5Loader(dataset_path)

        return self._hdf5_loaders.get(dataset_id)

    def _discover_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """
        Discover and create DatasetInfo from filesystem.

        Checks if a directory exists at base_path/dataset_id containing HDF5 files
        or LeRobot parquet structure. If found, creates a DatasetInfo and caches it.

        Args:
            dataset_id: Dataset identifier (directory name).

        Returns:
            DatasetInfo if directory with supported data exists, None otherwise.
        """
        from pathlib import Path

        dataset_path = Path(self.base_path) / dataset_id

        # Validate directory exists
        if not dataset_path.exists() or not dataset_path.is_dir():
            return None

        # Check for LeRobot parquet format first
        if LEROBOT_AVAILABLE and is_lerobot_dataset(dataset_path):
            return self._discover_lerobot_dataset(dataset_id, dataset_path)

        # Check for HDF5 files
        hdf5_files = list(dataset_path.glob("*.hdf5"))
        if not hdf5_files:
            return None

        # Get episode count and metadata from HDF5 loader
        hdf5_loader = self._get_hdf5_loader(dataset_id)
        episode_count = 0
        fps = 30.0  # Default FPS

        if hdf5_loader is not None:
            try:
                episode_indices = hdf5_loader.list_episodes()
                episode_count = len(episode_indices)

                # Try to get FPS from first episode metadata
                if episode_indices:
                    info = hdf5_loader.get_episode_info(episode_indices[0])
                    fps = info.get("fps", 30.0)
            except Exception:
                episode_count = len(hdf5_files)

        # Create DatasetInfo
        dataset_info = DatasetInfo(
            id=dataset_id,
            name=dataset_id,  # Use directory name as display name
            total_episodes=episode_count,
            fps=fps,
            features={},
            tasks=[],
        )

        # Cache for future lookups
        self._datasets[dataset_id] = dataset_info

        return dataset_info

    def _discover_lerobot_dataset(self, dataset_id: str, dataset_path: Path) -> DatasetInfo | None:
        """
        Discover and create DatasetInfo from a LeRobot parquet dataset.

        Args:
            dataset_id: Dataset identifier.
            dataset_path: Path to the dataset directory.

        Returns:
            DatasetInfo if valid LeRobot dataset, None otherwise.
        """
        dataset_id = dataset_id.replace("\r\n", "").replace("\n", "")
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is None:
            return None

        try:
            lr_info = lerobot_loader.get_dataset_info()

            # Convert LeRobot features to FeatureSchema
            features: dict[str, FeatureSchema] = {}
            for name, feat in lr_info.features.items():
                features[name] = FeatureSchema(
                    dtype=feat.get("dtype", "unknown"),
                    shape=feat.get("shape", []),
                )

            dataset_info = DatasetInfo(
                id=dataset_id,
                name=f"{dataset_id} ({lr_info.robot_type})",
                total_episodes=lr_info.total_episodes,
                fps=lr_info.fps,
                features=features,
                tasks=[],  # Could load from tasks.parquet if needed
            )

            self._datasets[dataset_id] = dataset_info
            return dataset_info

        except Exception as e:
            logger.warning(
                "Failed to discover LeRobot dataset %s: %s",
                dataset_id.replace("\r\n", "").replace("\n", ""),
                str(e).replace("\r\n", "").replace("\n", ""),
            )
            return None

    async def list_datasets(self) -> list[DatasetInfo]:
        """
        List all available datasets.

        Scans the base_path directory for subdirectories containing HDF5 files
        or LeRobot parquet datasets and returns DatasetInfo for each.

        Returns:
            List of DatasetInfo for all available datasets.
        """
        from pathlib import Path

        base = Path(self.base_path)
        if not base.exists():
            return list(self._datasets.values())

        # Scan for dataset directories
        discovered_ids: set[str] = set()
        try:
            for item in base.iterdir():
                if item.is_dir():
                    # Check for LeRobot format first
                    if LEROBOT_AVAILABLE and is_lerobot_dataset(item):
                        discovered_ids.add(item.name)
                        continue

                    # Check for HDF5 files
                    hdf5_files = list(item.glob("*.hdf5"))
                    if hdf5_files:
                        discovered_ids.add(item.name)
        except OSError:
            # Permission or access error - return cached only
            return list(self._datasets.values())

        # Discover any datasets not already cached
        for dataset_id in discovered_ids:
            if dataset_id not in self._datasets:
                self._discover_dataset(dataset_id)

        return list(self._datasets.values())

    async def get_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """
        Get metadata for a specific dataset.

        First checks the registered datasets cache. If not found,
        attempts to discover the dataset from the filesystem.

        Args:
            dataset_id: Unique dataset identifier.

        Returns:
            DatasetInfo if found or discovered, None otherwise.
        """
        # Check cache first
        dataset = self._datasets.get(dataset_id)
        if dataset is not None:
            return dataset

        # Attempt filesystem discovery
        return self._discover_dataset(dataset_id)

    async def register_dataset(self, dataset: DatasetInfo) -> None:
        """
        Register a dataset for access.

        Args:
            dataset: Dataset metadata to register.
        """
        self._datasets[dataset.id] = dataset

    async def list_episodes(
        self,
        dataset_id: str,
        offset: int = 0,
        limit: int = 100,
        has_annotations: bool | None = None,
        task_index: int | None = None,
    ) -> list[EpisodeMeta]:
        """
        List episodes for a dataset with filtering.

        Supports both registered datasets, HDF5-based datasets, and LeRobot datasets.

        Args:
            dataset_id: Dataset identifier.
            offset: Number of episodes to skip.
            limit: Maximum episodes to return.
            has_annotations: Filter by annotation status.
            task_index: Filter by task index.

        Returns:
            List of EpisodeMeta matching the filters.
        """
        dataset_id = dataset_id.replace("\r\n", "").replace("\n", "")
        dataset = self._datasets.get(dataset_id)

        # Get list of annotated episodes
        annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))

        episode_indices: list[int] = []
        episode_info_map: dict[int, dict] = {}

        # Try LeRobot loader first
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is not None:
            try:
                episode_indices = lerobot_loader.list_episodes()
                # Optionally preload episode info for filtering
                for idx in episode_indices:
                    try:
                        episode_info_map[idx] = lerobot_loader.get_episode_info(idx)
                    except Exception:
                        episode_info_map[idx] = {"length": 0, "task_index": 0}
            except Exception as e:
                logger.warning(
                    "LeRobot list_episodes failed for %s: %s",
                    dataset_id.replace("\r\n", "").replace("\n", ""),
                    str(e).replace("\r\n", "").replace("\n", ""),
                )
                episode_indices = []

        # Fall back to HDF5 loader
        if not episode_indices:
            hdf5_loader = self._get_hdf5_loader(dataset_id)
            if hdf5_loader is not None:
                try:
                    episode_indices = hdf5_loader.list_episodes()
                except Exception:
                    episode_indices = []

        # Fall back to dataset total_episodes if no loaders found data
        if not episode_indices and dataset is not None:
            episode_indices = list(range(dataset.total_episodes))

        if not episode_indices:
            return []

        # Generate episode metadata
        episodes = []
        for idx in episode_indices:
            has_annot = idx in annotated_indices

            # Apply filters
            if has_annotations is not None and has_annot != has_annotations:
                continue

            # Get episode info from cache or loaders
            ep_length = 0
            ep_task_index = 0

            if idx in episode_info_map:
                ep_length = episode_info_map[idx].get("length", 0)
                ep_task_index = episode_info_map[idx].get("task_index", 0)
            elif lerobot_loader is not None:
                try:
                    ep_info = lerobot_loader.get_episode_info(idx)
                    ep_length = ep_info.get("length", 0)
                    ep_task_index = ep_info.get("task_index", 0)
                except Exception:
                    pass  # Best-effort; episode info unavailable
            elif self._get_hdf5_loader(dataset_id) is not None:
                try:
                    ep_info = self._get_hdf5_loader(dataset_id).get_episode_info(idx)
                    ep_length = ep_info.get("length", 0)
                    ep_task_index = ep_info.get("task_index", 0)
                except Exception:
                    pass  # Best-effort; episode info unavailable

            if task_index is not None and ep_task_index != task_index:
                continue

            episodes.append(
                EpisodeMeta(
                    index=idx,
                    length=ep_length,
                    task_index=ep_task_index,
                    has_annotations=has_annot,
                )
            )

        # Apply pagination
        return episodes[offset : offset + limit]

    async def get_episode(self, dataset_id: str, episode_idx: int) -> EpisodeData | None:
        """
        Get complete data for a specific episode.

        Loads trajectory data from HDF5 files or LeRobot parquet datasets.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.

        Returns:
            EpisodeData if found, None otherwise.
        """
        dataset_id = dataset_id.replace("\r\n", "").replace("\n", "")
        dataset = self._datasets.get(dataset_id)

        # Get annotation status
        annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))

        trajectory_data: list[TrajectoryPoint] = []
        video_urls: dict[str, str] = {}
        ep_length = 0
        ep_task_index = 0

        # Try LeRobot loader first
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is not None:
            try:
                lr_data = lerobot_loader.load_episode(episode_idx)
                ep_length = lr_data.length
                ep_task_index = lr_data.task_index

                # Convert LeRobot data to TrajectoryPoint list
                num_joints = lr_data.joint_positions.shape[1] if lr_data.joint_positions.ndim > 1 else 6

                for i in range(lr_data.length):
                    joint_pos = lr_data.joint_positions[i].tolist()
                    joint_vel = (
                        lr_data.joint_velocities[i].tolist()
                        if lr_data.joint_velocities is not None
                        else [0.0] * num_joints
                    )
                    # Use action as surrogate for end-effector if not available
                    ee_pose = lr_data.actions[i][:6].tolist() if lr_data.actions is not None else [0.0] * 6
                    gripper = 0.0  # LeRobot UR10e doesn't have gripper in this dataset

                    trajectory_data.append(
                        TrajectoryPoint(
                            timestamp=float(lr_data.timestamps[i]),
                            frame=int(lr_data.frame_indices[i]),
                            joint_positions=joint_pos,
                            joint_velocities=joint_vel,
                            end_effector_pose=ee_pose,
                            gripper_state=gripper,
                        )
                    )

                # Generate video URLs for cameras
                for camera in lr_data.video_paths:
                    video_urls[camera] = f"/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"

                return EpisodeData(
                    meta=EpisodeMeta(
                        index=episode_idx,
                        length=ep_length,
                        task_index=ep_task_index,
                        has_annotations=episode_idx in annotated_indices,
                    ),
                    video_urls=video_urls,
                    trajectory_data=trajectory_data,
                )

            except Exception as e:
                logger.warning(
                    "LeRobot load_episode failed for episode %s: %s",
                    str(episode_idx).replace("\r\n", "").replace("\n", ""),
                    type(e).__name__.replace("\r\n", "").replace("\n", ""),
                )
                # Fall through to try HDF5

        # Try to load from HDF5
        hdf5_loader = self._get_hdf5_loader(dataset_id)
        if hdf5_loader is not None:
            try:
                hdf5_data = hdf5_loader.load_episode(episode_idx, load_images=False)
                ep_length = hdf5_data.length
                ep_task_index = hdf5_data.task_index

                # Convert HDF5 data to TrajectoryPoint list
                for i in range(hdf5_data.length):
                    # Get joint positions
                    joint_pos = hdf5_data.joint_positions[i].tolist()

                    # Get joint velocities (use zeros if not available)
                    joint_vel = (
                        hdf5_data.joint_velocities[i].tolist()
                        if hdf5_data.joint_velocities is not None
                        else [0.0] * len(joint_pos)
                    )

                    # Get end-effector pose (use zeros if not available)
                    ee_pose = (
                        hdf5_data.end_effector_pose[i].tolist()
                        if hdf5_data.end_effector_pose is not None
                        else [0.0] * 6
                    )

                    # Get gripper state
                    gripper = float(hdf5_data.gripper_states[i]) if hdf5_data.gripper_states is not None else 0.0

                    trajectory_data.append(
                        TrajectoryPoint(
                            timestamp=float(hdf5_data.timestamps[i]),
                            frame=i,
                            joint_positions=joint_pos,
                            joint_velocities=joint_vel,
                            end_effector_pose=ee_pose,
                            gripper_state=max(0.0, min(1.0, gripper)),
                        )
                    )

                # Get video URLs from metadata if available
                cameras = hdf5_data.metadata.get("cameras", [])
                for camera in cameras:
                    video_urls[camera] = f"/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"

            except Exception:
                # Fall back to empty data if HDF5 loading fails
                pass

        # Validate episode index if we have dataset info
        if dataset is not None and (episode_idx < 0 or episode_idx >= dataset.total_episodes):
            return None

        return EpisodeData(
            meta=EpisodeMeta(
                index=episode_idx,
                length=ep_length,
                task_index=ep_task_index,
                has_annotations=episode_idx in annotated_indices,
            ),
            video_urls=video_urls,
            trajectory_data=trajectory_data,
        )

    async def get_episode_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        """
        Get only the trajectory data for an episode.

        Optimized for analysis without loading full episode data.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.

        Returns:
            List of TrajectoryPoint, empty if not found.
        """
        dataset_id = dataset_id.replace("\r\n", "").replace("\n", "")
        # Try LeRobot loader first
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is not None:
            try:
                lr_data = lerobot_loader.load_episode(episode_idx)
                trajectory_data: list[TrajectoryPoint] = []
                num_joints = lr_data.joint_positions.shape[1] if lr_data.joint_positions.ndim > 1 else 6

                for i in range(lr_data.length):
                    joint_pos = lr_data.joint_positions[i].tolist()
                    joint_vel = (
                        lr_data.joint_velocities[i].tolist()
                        if lr_data.joint_velocities is not None
                        else [0.0] * num_joints
                    )
                    ee_pose = lr_data.actions[i][:6].tolist() if lr_data.actions is not None else [0.0] * 6
                    gripper = 0.0

                    trajectory_data.append(
                        TrajectoryPoint(
                            timestamp=float(lr_data.timestamps[i]),
                            frame=int(lr_data.frame_indices[i]),
                            joint_positions=joint_pos,
                            joint_velocities=joint_vel,
                            end_effector_pose=ee_pose,
                            gripper_state=gripper,
                        )
                    )

                return trajectory_data

            except Exception as e:
                logger.warning(
                    "LeRobot trajectory load failed for episode %s: %s",
                    str(episode_idx).replace("\r\n", "").replace("\n", ""),
                    type(e).__name__.replace("\r\n", "").replace("\n", ""),
                )

        # Fall back to HDF5 loader
        hdf5_loader = self._get_hdf5_loader(dataset_id)
        if hdf5_loader is None:
            return []

        try:
            hdf5_data = hdf5_loader.load_episode(episode_idx, load_images=False)
            trajectory_data: list[TrajectoryPoint] = []

            for i in range(hdf5_data.length):
                joint_pos = hdf5_data.joint_positions[i].tolist()
                joint_vel = (
                    hdf5_data.joint_velocities[i].tolist()
                    if hdf5_data.joint_velocities is not None
                    else [0.0] * len(joint_pos)
                )
                ee_pose = (
                    hdf5_data.end_effector_pose[i].tolist() if hdf5_data.end_effector_pose is not None else [0.0] * 6
                )
                gripper = float(hdf5_data.gripper_states[i]) if hdf5_data.gripper_states is not None else 0.0

                trajectory_data.append(
                    TrajectoryPoint(
                        timestamp=float(hdf5_data.timestamps[i]),
                        frame=i,
                        joint_positions=joint_pos,
                        joint_velocities=joint_vel,
                        end_effector_pose=ee_pose,
                        gripper_state=max(0.0, min(1.0, gripper)),
                    )
                )

            return trajectory_data

        except Exception:
            return []

    def has_hdf5_support(self) -> bool:
        """Check if HDF5 support is available."""
        return HDF5_AVAILABLE

    def has_lerobot_support(self) -> bool:
        """Check if LeRobot parquet support is available."""
        return LEROBOT_AVAILABLE

    def dataset_has_hdf5(self, dataset_id: str) -> bool:
        """Check if a dataset has HDF5 files."""
        return self._get_hdf5_loader(dataset_id) is not None

    def dataset_is_lerobot(self, dataset_id: str) -> bool:
        """Check if a dataset is in LeRobot parquet format."""
        return self._get_lerobot_loader(dataset_id) is not None

    def _get_dataset_path(self, dataset_id: str) -> str | None:
        """
        Get the filesystem path for a dataset.

        Args:
            dataset_id: Dataset identifier (directory name).

        Returns:
            Absolute path to the dataset directory, or None if not found.
        """
        from pathlib import Path

        dataset_path = Path(self.base_path) / dataset_id
        if dataset_path.exists() and dataset_path.is_dir():
            return str(dataset_path.resolve())
        return None

    async def get_frame_image(self, dataset_id: str, episode_idx: int, frame_idx: int, camera: str) -> bytes | None:
        """
        Get a single frame image from an episode.

        Supports both HDF5 datasets (direct image arrays) and LeRobot
        datasets (frame extraction from mp4 via ffmpeg).

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            frame_idx: Frame index within the episode.
            camera: Camera name.

        Returns:
            JPEG image bytes, or None if not found.
        """
        dataset_id = dataset_id.replace("\r\n", "").replace("\n", "")
        camera = camera.replace("\r\n", "").replace("\n", "")
        # Try LeRobot loader first (mp4 frame extraction)
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is not None:
            return self._extract_frame_from_video(lerobot_loader, episode_idx, frame_idx, camera)

        # Fall back to HDF5 loader
        hdf5_loader = self._get_hdf5_loader(dataset_id)
        if hdf5_loader is None:
            logger.warning("No loader found for dataset %s", dataset_id.replace("\r\n", "").replace("\n", ""))
            return None

        try:
            hdf5_data = hdf5_loader.load_episode(episode_idx, load_images=True, image_cameras=[camera])

            if camera not in hdf5_data.images:
                logger.warning(
                    "Camera %s not found in episode. Available cameras: %s",
                    camera.replace("\r\n", "").replace("\n", ""),
                    list(hdf5_data.images.keys()),
                )
                return None

            if frame_idx < 0 or frame_idx >= len(hdf5_data.images[camera]):
                logger.warning(
                    "Frame %d out of range (0-%d)",
                    frame_idx,
                    len(hdf5_data.images[camera]) - 1,
                )
                return None

            import io

            from PIL import Image

            frame = hdf5_data.images[camera][frame_idx]
            img = Image.fromarray(frame)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()

        except Exception as e:
            logger.warning(
                "Error loading frame %s from episode %s: %s",
                str(frame_idx).replace("\r\n", "").replace("\n", ""),
                str(episode_idx).replace("\r\n", "").replace("\n", ""),
                type(e).__name__.replace("\r\n", "").replace("\n", ""),
            )
            return None

    def _extract_frame_from_video(
        self,
        loader: "LeRobotLoader",
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes | None:
        """Extract a single JPEG frame from a LeRobot mp4 video using OpenCV."""
        camera = camera.replace("\r\n", "").replace("\n", "")
        import io

        import cv2
        from PIL import Image

        video_path = loader.get_video_path(episode_idx, camera)
        if video_path is None:
            logger.warning(
                "No video for episode %s",
                str(episode_idx).replace("\r\n", "").replace("\n", ""),
            )
            return None

        cap = cv2.VideoCapture(str(video_path))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning(
                    "Failed to read frame %s",
                    str(frame_idx).replace("\r\n", "").replace("\n", ""),
                )
                return None

            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        finally:
            cap.release()

    async def get_episode_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        """
        Get list of available cameras for an episode.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.

        Returns:
            List of camera names.
        """
        # Try LeRobot loader first
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is not None:
            try:
                return lerobot_loader.get_cameras()
            except Exception:
                pass  # Best-effort; loader may not support camera listing

        # Fall back to HDF5 loader
        hdf5_loader = self._get_hdf5_loader(dataset_id)
        if hdf5_loader is None:
            return []

        try:
            info = hdf5_loader.get_episode_info(episode_idx)
            return info.get("cameras", [])
        except Exception:
            return []

    def get_video_file_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """
        Get the filesystem path to a video file.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            camera: Camera name/key.

        Returns:
            Absolute path to the video file, or None if not found.
        """
        lerobot_loader = self._get_lerobot_loader(dataset_id)
        if lerobot_loader is not None:
            try:
                video_path = lerobot_loader.get_video_path(episode_idx, camera)
                if video_path is not None:
                    return str(video_path)
            except Exception as e:
                logger.warning("Failed to get video path: %s", str(e).replace("\r\n", "").replace("\n", ""))

        return None


# Global service instance
_dataset_service: DatasetService | None = None


def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    Returns:
        DatasetService singleton.
    """
    global _dataset_service
    if _dataset_service is None:
        _dataset_service = DatasetService()
    return _dataset_service
