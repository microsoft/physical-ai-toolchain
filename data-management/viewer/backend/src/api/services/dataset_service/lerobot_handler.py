"""
LeRobot format handler for parquet-based v2/v3 datasets.

Implements DatasetFormatHandler for LeRobot datasets with support for
parquet data files, mp4 video files, and meta/info.json metadata.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
from pathlib import Path

from ...models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, FeatureSchema, TaskInfo, TrajectoryPoint
from .base import build_trajectory

logger = logging.getLogger(__name__)

# LeRobot parquet support is optional
try:
    from ..lerobot_loader import LeRobotLoader, is_lerobot_dataset

    LEROBOT_AVAILABLE = True
except ImportError:
    LEROBOT_AVAILABLE = False
    LeRobotLoader = None

    def is_lerobot_dataset(x):
        return False


class LeRobotFormatHandler:
    """Handler for LeRobot parquet-format datasets."""

    def __init__(self) -> None:
        self._loaders: dict[str, LeRobotLoader] = {}

    @property
    def available(self) -> bool:
        return LEROBOT_AVAILABLE

    def can_handle(self, dataset_path: Path) -> bool:
        return LEROBOT_AVAILABLE and is_lerobot_dataset(dataset_path)

    def get_loader(self, dataset_id: str, dataset_path: Path) -> bool:
        """Get or create a LeRobot loader. Returns True if successful."""
        if not LEROBOT_AVAILABLE:
            return False

        if dataset_id in self._loaders:
            return True

        if not dataset_path.exists() or not is_lerobot_dataset(dataset_path):
            return False

        try:
            self._loaders[dataset_id] = LeRobotLoader(dataset_path)
            return True
        except Exception as e:
            logger.warning(
                "Failed to create LeRobot loader for %s: %s",
                dataset_id,
                str(e),
            )
            return False

    def _get_loader(self, dataset_id: str) -> LeRobotLoader | None:
        return self._loaders.get(dataset_id)

    def has_loader(self, dataset_id: str) -> bool:
        return dataset_id in self._loaders

    def list_episodes_from_path(self, path: Path) -> tuple[list[int], dict[int, dict]]:
        """List episodes from a path without registering a persistent loader."""
        if not LEROBOT_AVAILABLE:
            return [], {}
        try:
            loader = LeRobotLoader(path)
            episode_info_map = loader.list_episodes_with_meta()
            return sorted(episode_info_map.keys()), episode_info_map
        except Exception as e:
            logger.warning(
                "LeRobot list_episodes_from_path failed for %s: %s",
                str(path),
                type(e).__name__,
            )
            return [], {}

    def discover(self, dataset_id: str, dataset_path: Path) -> DatasetInfo | None:
        if not self.get_loader(dataset_id, dataset_path):
            return None

        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        try:
            lr_info = loader.get_dataset_info()

            features: dict[str, FeatureSchema] = {}
            for name, feat in lr_info.features.items():
                features[name] = FeatureSchema(
                    dtype=feat.get("dtype", "unknown"),
                    shape=feat.get("shape", []),
                )

            return DatasetInfo(
                id=dataset_id,
                name=f"{dataset_id} ({lr_info.robot_type})",
                total_episodes=lr_info.total_episodes,
                fps=lr_info.fps,
                features=features,
                tasks=[TaskInfo(task_index=idx, description=desc) for idx, desc in sorted(loader.get_tasks().items())],
            )
        except Exception as e:
            logger.warning(
                "Failed to discover LeRobot dataset %s: %s",
                dataset_id,
                str(e),
            )
            return None

    def list_episodes(self, dataset_id: str) -> tuple[list[int], dict[int, dict]]:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return [], {}

        try:
            episode_info_map = loader.list_episodes_with_meta()
            return sorted(episode_info_map.keys()), episode_info_map
        except Exception as e:
            logger.warning(
                "LeRobot list_episodes failed for %s: %s",
                dataset_id,
                str(e),
            )
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
            lr_data = loader.load_episode(episode_idx)

            trajectory_data = build_trajectory(
                length=lr_data.length,
                timestamps=lr_data.timestamps,
                frame_indices=lr_data.frame_indices,
                joint_positions=lr_data.joint_positions,
                joint_velocities=lr_data.joint_velocities,
                end_effector_poses=lr_data.actions[:, :6] if lr_data.actions is not None else None,
            )

            video_urls: dict[str, str] = {}
            for camera in lr_data.video_paths:
                video_urls[camera] = f"/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"

            # For blob datasets, add video URLs for cameras without local files
            if dataset_info is not None:
                for feat_name, feat in dataset_info.features.items():
                    if feat.dtype == "video" and feat_name not in video_urls:
                        video_urls[feat_name] = f"/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{feat_name}"

            return EpisodeData(
                meta=EpisodeMeta(
                    index=episode_idx,
                    length=lr_data.length,
                    task_index=lr_data.task_index,
                    has_annotations=False,  # Set by caller
                ),
                video_urls=video_urls,
                cameras=list(video_urls.keys()),
                trajectory_data=trajectory_data,
            )
        except Exception as e:
            logger.warning(
                "LeRobot load_episode failed for episode %s: %s",
                episode_idx,
                type(e).__name__,
            )
            return None

    def get_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return []

        try:
            lr_data = loader.load_episode(episode_idx)

            return build_trajectory(
                length=lr_data.length,
                timestamps=lr_data.timestamps,
                frame_indices=lr_data.frame_indices,
                joint_positions=lr_data.joint_positions,
                joint_velocities=lr_data.joint_velocities,
                end_effector_poses=lr_data.actions[:, :6] if lr_data.actions is not None else None,
            )
        except Exception as e:
            logger.warning(
                "LeRobot trajectory load failed for episode %s: %s",
                episode_idx,
                type(e).__name__,
            )
            return []

    def get_frame_image(
        self,
        dataset_id: str,
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes | None:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        video_path = loader.get_video_path(episode_idx, camera)
        if video_path is None:
            logger.warning(
                "No video for episode %s",
                episode_idx,
            )
            return None

        info = loader.get_dataset_info()
        fps = info.fps or 30.0

        result = self._extract_frame_ffmpeg(str(video_path), frame_idx, fps)
        if result is not None:
            return result

        return self._extract_frame_cv2(str(video_path), frame_idx)

    @staticmethod
    def _resolve_ffmpeg() -> str | None:
        """Locate a usable ffmpeg binary.

        Prefers the imageio-ffmpeg static binary (ships with libdav1d for AV1
        and libx264; not affected by host libGL breakage) over a system ffmpeg
        on PATH.
        """
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return shutil.which("ffmpeg")

    @staticmethod
    def _extract_frame_ffmpeg(video_path: str, frame_idx: int, fps: float) -> bytes | None:
        """Extract a single frame as JPEG using ffmpeg."""
        ffmpeg = LeRobotFormatHandler._resolve_ffmpeg()
        if ffmpeg is None:
            return None

        seek_time = frame_idx / fps
        try:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-ss",
                    f"{seek_time:.6f}",
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-f",
                    "image2",
                    "-c:v",
                    "mjpeg",
                    "-q:v",
                    "2",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout
        except Exception as e:
            logger.warning("ffmpeg frame extraction failed: %s", e)

        return None

    @staticmethod
    def _extract_frame_cv2(video_path: str, frame_idx: int) -> bytes | None:
        """Extract a single frame as JPEG using OpenCV (fallback)."""
        try:
            import cv2
            from PIL import Image
        except ImportError:
            logger.warning("Neither ffmpeg nor cv2 available for frame extraction")
            return None

        cap = cv2.VideoCapture(video_path)
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning("Failed to read frame %s", frame_idx)
                return None

            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        finally:
            cap.release()

    def get_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return []

        try:
            return loader.get_cameras()
        except Exception:
            return []

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        try:
            video_path = loader.get_video_path(episode_idx, camera)
            if video_path is not None:
                return str(video_path)
        except Exception as e:
            logger.warning("Failed to get video path: %s", str(e))

        return None
