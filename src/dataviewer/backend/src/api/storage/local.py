"""
Local filesystem storage adapter for annotations.

Stores annotations in the dataset's annotations/ directory structure
following the LeRobot v3 format specification.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import aiofiles
import aiofiles.os

from ..models.annotations import EpisodeAnnotationFile
from .base import StorageAdapter, StorageError
from .serializers import DateTimeEncoder


class LocalStorageAdapter(StorageAdapter):
    """
    Local filesystem storage adapter for annotation persistence.

    Stores annotations in the dataset's annotations/episodes/ directory,
    with each episode having its own JSON file.
    """

    def __init__(self, base_path: str):
        """
        Initialize the local storage adapter.

        Args:
            base_path: Base path to the dataset directory.
        """
        self.base_path = Path(base_path)

    def _get_annotations_dir(self, dataset_id: str) -> Path:
        """Get the annotations directory for a dataset."""
        resolved = (self.base_path / dataset_id / "annotations" / "episodes").resolve()
        if not resolved.is_relative_to(self.base_path.resolve()):
            raise StorageError(f"Invalid dataset_id: path traversal detected in '{dataset_id}'")
        return resolved

    def _get_annotation_path(self, dataset_id: str, episode_index: int) -> Path:
        """Get the file path for an episode's annotations."""
        return self._get_annotations_dir(dataset_id) / f"episode_{episode_index:06d}.json"

    async def _ensure_directory(self, path: Path) -> None:
        """Ensure a directory exists, creating it if necessary."""
        try:
            await aiofiles.os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise StorageError(f"Failed to create directory {path}: {e}", cause=e)

    async def get_annotation(self, dataset_id: str, episode_index: int) -> EpisodeAnnotationFile | None:
        """
        Retrieve annotations for an episode from local filesystem.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.

        Returns:
            EpisodeAnnotationFile if annotations exist, None otherwise.
        """
        file_path = self._get_annotation_path(dataset_id, episode_index)

        try:
            if not await aiofiles.os.path.exists(file_path):
                return None

            async with aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
                return EpisodeAnnotationFile.model_validate(data)

        except json.JSONDecodeError as e:
            raise StorageError(f"Invalid JSON in annotation file {file_path}: {e}", cause=e)
        except Exception as e:
            raise StorageError(f"Failed to read annotation file {file_path}: {e}", cause=e)

    async def save_annotation(self, dataset_id: str, episode_index: int, annotation: EpisodeAnnotationFile) -> None:
        """
        Save annotations for an episode using atomic write.

        Uses a write-to-temp-then-rename strategy for atomicity.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.
            annotation: Complete annotation file to save.

        Raises:
            StorageError: If the save operation fails.
        """
        file_path = self._get_annotation_path(dataset_id, episode_index)
        annotations_dir = self._get_annotations_dir(dataset_id)

        try:
            # Ensure directory exists
            await self._ensure_directory(annotations_dir)

            # Serialize to JSON
            json_content = json.dumps(
                annotation.model_dump(mode="json"),
                indent=2,
                cls=DateTimeEncoder,
            )

            # Write to temp file first, then rename for atomicity
            temp_fd, temp_path = await asyncio.to_thread(
                tempfile.mkstemp,
                dir=str(annotations_dir),
                suffix=".tmp",
                prefix="annotation_",
            )
            try:
                async with aiofiles.open(temp_fd, "w", encoding="utf-8") as f:
                    await f.write(json_content)

                # Atomic rename
                await asyncio.to_thread(os.replace, temp_path, str(file_path))

            except Exception:
                # Clean up temp file on failure
                if await asyncio.to_thread(os.path.exists, temp_path):
                    await asyncio.to_thread(os.unlink, temp_path)
                raise

        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to save annotation file {file_path}: {e}", cause=e)

    async def list_annotated_episodes(self, dataset_id: str) -> list[int]:
        """
        List all episode indices with annotations for a dataset.

        Args:
            dataset_id: Unique identifier for the dataset.

        Returns:
            Sorted list of episode indices that have annotations.
        """
        annotations_dir = self._get_annotations_dir(dataset_id)

        try:
            if not await aiofiles.os.path.exists(annotations_dir):
                return []

            episode_indices = []
            for entry in await asyncio.to_thread(os.listdir, str(annotations_dir)):
                if entry.startswith("episode_") and entry.endswith(".json"):
                    # Extract episode index from filename
                    try:
                        index_str = entry[8:-5]  # Remove "episode_" prefix and ".json" suffix
                        episode_indices.append(int(index_str))
                    except ValueError:
                        continue  # Skip malformed filenames

            return sorted(episode_indices)

        except Exception as e:
            raise StorageError(f"Failed to list annotations for {dataset_id}: {e}", cause=e)

    async def delete_annotation(self, dataset_id: str, episode_index: int) -> bool:
        """
        Delete annotations for an episode.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.

        Returns:
            True if annotations were deleted, False if they didn't exist.
        """
        file_path = self._get_annotation_path(dataset_id, episode_index)

        try:
            if not await aiofiles.os.path.exists(file_path):
                return False

            await aiofiles.os.remove(file_path)
            return True

        except Exception as e:
            raise StorageError(f"Failed to delete annotation file {file_path}: {e}", cause=e)
