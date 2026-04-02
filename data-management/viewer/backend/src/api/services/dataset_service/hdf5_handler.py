"""
HDF5 format handler for episode datasets.

Implements DatasetFormatHandler for HDF5-based datasets with support for
per-episode .hdf5 files containing trajectory, image, and metadata.
Generates cached mp4 videos from HDF5 image arrays for unified playback.
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
    from ..hdf5_loader import HDF5Loader, load_all_frames, load_single_frame

    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False
    HDF5Loader = None
    load_single_frame = None  # type: ignore[assignment]
    load_all_frames = None  # type: ignore[assignment]


def _generate_video(images, output_path: Path, fps: float = 30.0) -> bool:
    """Encode numpy image array to browser-compatible H.264 mp4 using ffmpeg."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg not found, falling back to cv2 video encoding")
        return _generate_video_cv2(images, output_path, fps)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = images.shape[1], images.shape[2]
    try:
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{w}x{h}",
                "-r",
                str(fps),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(len(images)):
            proc.stdin.write(images[i].tobytes())
        proc.stdin.close()
        proc.wait()
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.warning("ffmpeg video generation failed: %s", e)
        return _generate_video_cv2(images, output_path, fps)


def _generate_video_cv2(images, output_path: Path, fps: float = 30.0) -> bool:
    """Fallback: encode using cv2 VideoWriter."""
    try:
        import cv2
    except ImportError:
        logger.warning("cv2 not available for video generation fallback")
        return False

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        h, w = images.shape[1], images.shape[2]
        fourcc = cv2.VideoWriter_fourcc(*"avc1")
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (w, h))
        for i in range(len(images)):
            writer.write(cv2.cvtColor(images[i], cv2.COLOR_RGB2BGR))
        writer.release()
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.warning("cv2 video generation failed: %s", e)
        return False


def _encode_jpeg(frame_array) -> bytes:
    """Encode a single numpy frame to JPEG bytes."""
    from PIL import Image

    img = Image.fromarray(frame_array)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


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
            logger.warning(
                "HDF5 discover failed for %s: %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                type(e).__name__,
            )
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
            logger.warning(
                "HDF5 list_episodes failed for %s: %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                type(e).__name__,
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

            for camera in cameras:
                video_urls[camera] = f"/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"

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
            logger.warning(
                "HDF5 load_episode failed for %s ep %d: %s",
                dataset_id,
                episode_idx,
                e,
            )
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
            logger.warning(
                "HDF5 get_trajectory failed for %s ep %d: %s",
                dataset_id,
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
        """Load a single frame using h5py slice indexing."""
        loader = self._get_loader(dataset_id)
        if loader is None:
            return None

        try:
            file_path = loader._find_episode_file(episode_idx)
            frame = load_single_frame(file_path, camera, frame_idx)
            if frame is None:
                return None
            return _encode_jpeg(frame)
        except Exception as e:
            logger.warning(
                "HDF5 get_frame_image failed for %s ep %d frame %d: %s",
                dataset_id,
                episode_idx,
                frame_idx,
                type(e).__name__,
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
            logger.warning(
                "HDF5 get_cameras failed for %s ep %d: %s",
                dataset_id,
                episode_idx,
                type(e).__name__,
            )
            return []

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """Return cached mp4 path, generating synchronously if missing."""
        cache_path = self._video_cache_path(dataset_id, episode_idx, camera)
        if cache_path is None:
            return None

        if cache_path.exists():
            return str(cache_path)

        if self._generate_episode_video(dataset_id, episode_idx, camera, cache_path):
            return str(cache_path)
        return None

    def _video_cache_path(self, dataset_id: str, episode_idx: int, camera: str) -> Path | None:
        """Build the cache path for a generated episode video."""
        loader = self._get_loader(dataset_id)
        if loader is None:
            return None
        return loader.base_path / "meta" / "videos" / camera / f"episode_{episode_idx:06d}.mp4"

    def _generate_episode_video(self, dataset_id: str, episode_idx: int, camera: str, cache_path: Path) -> bool:
        """Load HDF5 frames and encode to MP4."""
        loader = self._get_loader(dataset_id)
        if loader is None:
            return False

        try:
            file_path = loader._find_episode_file(episode_idx)
            images = load_all_frames(file_path, camera)
            if images is None or len(images) == 0:
                return False

            info = loader.get_episode_info(episode_idx)
            fps = info.get("fps", 30.0)

            if _generate_video(images, cache_path, fps):
                logger.info(
                    "Generated video for %s ep %d camera %s (%d frames)",
                    dataset_id,
                    episode_idx,
                    camera,
                    len(images),
                )
                return True
        except Exception as e:
            logger.warning(
                "Video generation failed for %s ep %d: %s",
                dataset_id,
                episode_idx,
                type(e).__name__,
            )

        return False
