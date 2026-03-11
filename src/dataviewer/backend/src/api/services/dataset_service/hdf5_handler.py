"""
HDF5 format handler for episode datasets.

Implements DatasetFormatHandler for HDF5-based datasets with support for
per-episode .hdf5 files containing trajectory, image, and metadata.
"""

import io
import logging
from pathlib import Path

from ...models.datasources import (
    DatasetInfo,
    EpisodeData,
    EpisodeMeta,
    TrajectoryPoint,
)
from .base import build_trajectory

logger = logging.getLogger(__name__)

# HDF5 support is optional
try:
    from ..hdf5_loader import HDF5Loader

    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False
    HDF5Loader = None


class HDF5FormatHandler:
    """Handler for HDF5-based episode datasets."""

    def __init__(self) -> None:
        self._loaders: dict[str, HDF5Loader] = {}

    @property
    def available(self) -> bool:
        return HDF5_AVAILABLE

    def can_handle(self, dataset_path: Path) -> bool:
        if not HDF5_AVAILABLE:
            return False
        if not dataset_path.exists():
            return False
        # Only match directories containing HDF5 files directly (or in
        # data/episodes subdirs). Parent folders with only nested session
        # directories are discovered at the service layer instead.
        for search_dir in (dataset_path, dataset_path / "data", dataset_path / "episodes"):
            if search_dir.is_dir() and next(search_dir.glob("*.hdf5"), None) is not None:
                return True
        return False

    def get_loader(self, dataset_id: str, dataset_path: Path) -> bool:
        """Get or create an HDF5 loader. Returns True if successful."""
        if not HDF5_AVAILABLE:
            return False

        if dataset_id in self._loaders:
            return True

        if not dataset_path.exists():
            return False

        if next(dataset_path.glob("**/*.hdf5"), None) is None:
            return False

        self._loaders[dataset_id] = HDF5Loader(dataset_path)
        return True

    def _get_loader(self, dataset_id: str) -> HDF5Loader | None:
        return self._loaders.get(dataset_id)

    def has_loader(self, dataset_id: str) -> bool:
        return dataset_id in self._loaders

    def discover(self, dataset_id: str, dataset_path: Path) -> DatasetInfo | None:
        if not self.get_loader(dataset_id, dataset_path):
            return None

        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        episode_count = 0
        fps = 30.0

        try:
            episode_indices = loader.list_episodes()
            episode_count = len(episode_indices)

            if episode_indices:
                info = loader.get_episode_info(episode_indices[0])
                fps = info.get("fps", 30.0)
        except Exception as e:
            logger.warning("HDF5 discover failed for %s: %s", dataset_id, type(e).__name__)
            hdf5_files = list(dataset_path.glob("*.hdf5"))
            episode_count = len(hdf5_files)

        return DatasetInfo(
            id=dataset_id,
            name=dataset_id,
            total_episodes=episode_count,
            fps=fps,
            features={},
            tasks=[],
        )

    def list_episodes(self, dataset_id: str) -> tuple[list[int], dict[int, dict]]:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return [], {}

        try:
            episode_indices = loader.list_episodes()
            episode_info_map: dict[int, dict] = {}
            for idx in episode_indices:
                try:
                    info = loader.get_episode_info(idx)
                    episode_info_map[idx] = {
                        "length": info.get("length", 0),
                        "task_index": info.get("task_index", 0),
                    }
                except Exception as e:
                    logger.warning("HDF5 episode info failed for idx %d: %s", idx, type(e).__name__)
                    episode_info_map[idx] = {"length": 0, "task_index": 0}
            return episode_indices, episode_info_map
        except Exception as e:
            logger.warning("HDF5 list_episodes failed for %s: %s", dataset_id, type(e).__name__)
            return [], {}

    def load_episode(
        self,
        dataset_id: str,
        episode_idx: int,
        dataset_info: DatasetInfo | None = None,
    ) -> EpisodeData | None:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        try:
            hdf5_data = loader.load_episode(episode_idx, load_images=False)

            trajectory_data = build_trajectory(
                length=hdf5_data.length,
                timestamps=hdf5_data.timestamps,
                joint_positions=hdf5_data.joint_positions,
                joint_velocities=hdf5_data.joint_velocities,
                end_effector_poses=hdf5_data.end_effector_pose,
                gripper_states=hdf5_data.gripper_states,
                clamp_gripper=True,
            )

            video_urls: dict[str, str] = {}
            cameras = hdf5_data.metadata.get("cameras", [])

            return EpisodeData(
                meta=EpisodeMeta(
                    index=episode_idx,
                    length=hdf5_data.length,
                    task_index=hdf5_data.task_index,
                    has_annotations=False,  # Set by caller
                ),
                video_urls=video_urls,
                cameras=cameras,
                trajectory_data=trajectory_data,
            )
        except Exception as e:
            logger.warning("HDF5 load_episode failed for %s ep %d: %s", dataset_id, episode_idx, type(e).__name__)
            return None

    def get_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return []

        try:
            hdf5_data = loader.load_episode(episode_idx, load_images=False)

            return build_trajectory(
                length=hdf5_data.length,
                timestamps=hdf5_data.timestamps,
                joint_positions=hdf5_data.joint_positions,
                joint_velocities=hdf5_data.joint_velocities,
                end_effector_poses=hdf5_data.end_effector_pose,
                gripper_states=hdf5_data.gripper_states,
                clamp_gripper=True,
            )
        except Exception as e:
            logger.warning("HDF5 get_trajectory failed for %s ep %d: %s", dataset_id, episode_idx, type(e).__name__)
            return []

    def get_frame_image(
        self,
        dataset_id: str,
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes | None:
        camera = camera.replace("\r\n", "").replace("\n", "")
        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        try:
            from PIL import Image

            hdf5_data = loader.load_episode(episode_idx, load_images=True, image_cameras=[camera])

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

    def get_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return []

        try:
            info = loader.get_episode_info(episode_idx)
            return info.get("cameras", [])
        except Exception as e:
            logger.warning("HDF5 get_cameras failed for %s ep %d: %s", dataset_id, episode_idx, type(e).__name__)
            return []

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        # HDF5 datasets don't have separate video files
        return None
