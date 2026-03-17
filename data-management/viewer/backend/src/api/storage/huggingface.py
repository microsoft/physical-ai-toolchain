"""
Hugging Face Hub adapter for dataset loading.

Provides read-only access to LeRobot datasets hosted on the Hugging Face Hub.
Annotations are stored separately using another storage adapter.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..models.annotations import EpisodeAnnotationFile
from ..models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, FeatureSchema, TaskInfo
from .base import StorageAdapter, StorageError

# Hugging Face SDK imports are optional
try:
    from huggingface_hub import HfFileSystem, hf_hub_download

    HF_AVAILABLE = True
except ImportError:
    HfFileSystem = None
    hf_hub_download = None
    HF_AVAILABLE = False


class HuggingFaceHubAdapter(StorageAdapter):
    """
    Hugging Face Hub adapter for reading LeRobot datasets.

    This adapter provides read-only access to dataset metadata, episode info,
    and video URLs. Annotations are stored separately.
    """

    def __init__(
        self,
        repo_id: str,
        revision: str | None = None,
        token: str | None = None,
        cache_dir: str | None = None,
    ):
        """
        Initialize the Hugging Face Hub adapter.

        Args:
            repo_id: Repository ID in format "owner/repo".
            revision: Git revision (branch, tag, or commit hash).
            token: Hugging Face API token for private repos.
            cache_dir: Local directory for caching downloaded files.

        Raises:
            ImportError: If huggingface_hub is not installed.
        """
        if not HF_AVAILABLE:
            raise ImportError(
                "Hugging Face Hub support requires huggingface_hub package. Install with: pip install huggingface_hub"
            )

        self.repo_id = repo_id
        self.revision = revision or "main"
        self.token = token
        self.cache_dir = cache_dir
        self._info_cache: dict | None = None
        self._fs: HfFileSystem | None = None

    def _get_fs(self) -> HfFileSystem:
        """Get or create the HfFileSystem client."""
        if self._fs is None:
            self._fs = HfFileSystem(token=self.token)
        return self._fs

    async def _download_file(self, filename: str) -> Path:
        """Download a file from the Hub and return the local path."""
        try:
            local_path = await asyncio.to_thread(
                hf_hub_download,
                repo_id=self.repo_id,
                filename=filename,
                revision=self.revision,
                token=self.token,
                cache_dir=self.cache_dir,
                repo_type="dataset",
            )
            return Path(local_path)
        except Exception as e:
            raise StorageError(f"Failed to download {filename} from {self.repo_id}: {e}", cause=e)

    def _read_json_file(self, path: str) -> dict:
        """Read and parse a JSON file synchronously."""
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    async def get_dataset_info(self) -> DatasetInfo:
        """
        Get dataset metadata from info.json.

        Returns:
            DatasetInfo containing dataset metadata.
        """
        try:
            # Download and parse info.json
            info_path = await self._download_file("meta/info.json")
            info_data = await asyncio.to_thread(self._read_json_file, str(info_path))

            self._info_cache = info_data

            # Parse features
            features = {}
            for name, schema in info_data.get("features", {}).items():
                if isinstance(schema, dict):
                    features[name] = FeatureSchema(
                        dtype=schema.get("dtype", "unknown"),
                        shape=schema.get("shape", []),
                    )

            # Parse tasks if available
            tasks = []
            tasks_data = info_data.get("tasks", [])
            for i, task in enumerate(tasks_data):
                if isinstance(task, dict):
                    tasks.append(
                        TaskInfo(
                            task_index=task.get("task_index", i),
                            description=task.get("description", f"Task {i}"),
                        )
                    )
                elif isinstance(task, str):
                    tasks.append(TaskInfo(task_index=i, description=task))

            return DatasetInfo(
                id=self.repo_id,
                name=info_data.get("name", self.repo_id.split("/")[-1]),
                total_episodes=info_data.get("total_episodes", 0),
                fps=info_data.get("fps", 30.0),
                features=features,
                tasks=tasks,
            )

        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to parse dataset info for {self.repo_id}: {e}", cause=e)

    async def list_episodes(self) -> list[EpisodeMeta]:
        """
        List all episodes in the dataset with metadata.

        Returns:
            List of EpisodeMeta for all episodes.
        """
        try:
            # Get dataset info if not cached
            if self._info_cache is None:
                await self.get_dataset_info()

            total_episodes = self._info_cache.get("total_episodes", 0)
            episodes = []

            # Try to load episode metadata if available
            fs = self._get_fs()
            episodes_dir = f"datasets/{self.repo_id}/meta/episodes"

            try:
                # Check for episode parquet files
                chunk_dirs = fs.ls(episodes_dir)
                for chunk_dir in chunk_dirs:
                    if "chunk-" in chunk_dir:
                        episode_files = fs.ls(chunk_dir)
                        for ep_file in episode_files:
                            if ep_file.endswith(".parquet"):
                                # Extract episode index from filename
                                filename = ep_file.split("/")[-1]
                                try:
                                    index_str = filename.replace("episode_", "")
                                    index_str = index_str.replace(".parquet", "")
                                    index = int(index_str)
                                    episodes.append(
                                        EpisodeMeta(
                                            index=index,
                                            length=0,  # Needs parquet read
                                            task_index=0,
                                            has_annotations=False,
                                        )
                                    )
                                except ValueError:
                                    continue
            except Exception:
                # Fall back to generating episode list from total count
                pass

            # If no episodes found from metadata, generate from total count
            if not episodes and total_episodes > 0:
                episodes = [
                    EpisodeMeta(
                        index=i,
                        length=0,
                        task_index=0,
                        has_annotations=False,
                    )
                    for i in range(total_episodes)
                ]

            return sorted(episodes, key=lambda e: e.index)

        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to list episodes for {self.repo_id}: {e}", cause=e)

    async def get_episode_data(self, episode_index: int) -> EpisodeData:
        """
        Get episode data including video URLs and trajectory data.

        Args:
            episode_index: Index of the episode to retrieve.

        Returns:
            EpisodeData containing metadata, video URLs, and trajectory data.
        """
        try:
            if self._info_cache is None:
                await self.get_dataset_info()

            # Determine chunk number (assuming 1000 episodes per chunk)
            chunk_index = episode_index // 1000
            chunk_name = f"chunk-{chunk_index:03d}"

            # Build video URLs for each camera
            video_urls = {}
            features = self._info_cache.get("features", {})

            for feature_name in features:
                if feature_name.startswith("observation.images."):
                    camera_name = feature_name.replace("observation.images.", "")
                    ep_idx_str = f"episode_{episode_index:06d}.mp4"
                    video_path = f"videos/{chunk_name}/{feature_name}/{ep_idx_str}"
                    # Return Hub URL for the video
                    video_urls[camera_name] = (
                        f"https://huggingface.co/datasets/{self.repo_id}/resolve/{self.revision}/{video_path}"
                    )

            return EpisodeData(
                meta=EpisodeMeta(
                    index=episode_index,
                    length=0,  # Would need to read data for actual length
                    task_index=0,
                    has_annotations=False,
                ),
                video_urls=video_urls,
                trajectory_data=[],  # Would need to load parquet for trajectory
            )

        except StorageError:
            raise
        except Exception as e:
            raise StorageError(
                f"Failed to get episode {episode_index} from {self.repo_id}: {e}",
                cause=e,
            )

    def get_video_url(self, episode_index: int, camera_name: str) -> str:
        """
        Get the video URL for a specific episode and camera.

        Args:
            episode_index: Episode index.
            camera_name: Camera name (e.g., "top", "wrist").

        Returns:
            URL to the video file on Hugging Face Hub.
        """
        chunk_index = episode_index // 1000
        chunk_name = f"chunk-{chunk_index:03d}"
        feature_name = f"observation.images.{camera_name}"
        video_path = f"videos/{chunk_name}/{feature_name}/episode_{episode_index:06d}.mp4"

        return f"https://huggingface.co/datasets/{self.repo_id}/resolve/{self.revision}/{video_path}"

    async def get_annotation(self, dataset_id: str, episode_index: int) -> EpisodeAnnotationFile | None:
        raise NotImplementedError("HuggingFaceHubAdapter is read-only")

    async def save_annotation(self, dataset_id: str, episode_index: int, annotation: EpisodeAnnotationFile) -> None:
        raise NotImplementedError("HuggingFaceHubAdapter is read-only")

    async def list_annotated_episodes(self, dataset_id: str) -> list[int]:
        raise NotImplementedError("HuggingFaceHubAdapter is read-only")

    async def delete_annotation(self, dataset_id: str, episode_index: int) -> bool:
        raise NotImplementedError("HuggingFaceHubAdapter is read-only")
