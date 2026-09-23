"""
Local filesystem storage adapter for annotations.

Stores annotations in the dataset's annotations/ directory structure
following the LeRobot v3 format specification.
"""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path

import aiofiles
import aiofiles.os
from fastapi import HTTPException

from ..models.annotations import EpisodeAnnotationFile
from ..validation import validate_path_containment
from .base import RevisionConflictError, StorageAdapter, StorageError, VersionedValue
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
        self._resource_locks: dict[Path, asyncio.Lock] = {}

    def _resource_lock(self, path: Path) -> asyncio.Lock:
        """Return the lock that serializes one annotation resource."""
        return self._resource_locks.setdefault(path, asyncio.Lock())

    @staticmethod
    def _etag(content: str) -> str:
        """Create a quoted strong ETag from canonical stored bytes."""
        return f'"{hashlib.sha256(content.encode()).hexdigest()}"'

    @staticmethod
    def _serialize(annotation: EpisodeAnnotationFile) -> str:
        """Serialize an annotation deterministically for storage and hashing."""
        return json.dumps(
            annotation.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            cls=DateTimeEncoder,
        )

    async def _read_content(self, path: Path) -> str | None:
        safe_base = os.path.realpath(str(self.base_path))
        normalized = os.path.normpath(os.path.realpath(str(path)))
        if not normalized.startswith(safe_base + os.sep):
            raise StorageError("Annotation path escapes the configured dataset directory")
        safe_path = Path(normalized)
        if not await aiofiles.os.path.exists(safe_path):
            return None
        async with aiofiles.open(safe_path, encoding="utf-8") as file:
            return await file.read()

    def _get_annotations_dir(self, dataset_id: str) -> Path:
        """Get the annotations directory for a dataset. Resolves -- to nested dirs."""
        parts = dataset_id.split("--") if "--" in dataset_id else [dataset_id]
        try:
            return validate_path_containment(
                self.base_path.joinpath(*parts, "annotations", "episodes"),
                self.base_path,
            )
        except HTTPException as exc:
            raise StorageError(f"Invalid dataset_id: path traversal detected in '{dataset_id}'", cause=exc) from exc

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

    async def get_annotation_versioned(
        self,
        dataset_id: str,
        episode_index: int,
    ) -> VersionedValue[EpisodeAnnotationFile]:
        """Retrieve an annotation and its strong content ETag."""
        file_path = self._get_annotation_path(dataset_id, episode_index)
        try:
            content = await self._read_content(file_path)
            if content is None:
                return VersionedValue(value=None, etag=None)
            return VersionedValue(
                value=EpisodeAnnotationFile.model_validate(json.loads(content)),
                etag=self._etag(content),
            )
        except json.JSONDecodeError as exc:
            raise StorageError(f"Invalid JSON in annotation file {file_path}: {exc}", cause=exc) from exc
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to read annotation file {file_path}: {exc}", cause=exc) from exc

    async def save_annotation(
        self,
        dataset_id: str,
        episode_index: int,
        annotation: EpisodeAnnotationFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
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
            async with self._resource_lock(file_path):
                await self._ensure_directory(annotations_dir)
                current_content = await self._read_content(file_path)
                current_etag = self._etag(current_content) if current_content is not None else None
                if if_none_match and current_content is not None:
                    raise RevisionConflictError(current_etag)
                if if_match is not None and if_match != current_etag:
                    raise RevisionConflictError(current_etag)

                json_content = self._serialize(annotation)
                next_etag = self._etag(json_content)

                temp_fd, temp_path = await asyncio.to_thread(
                    tempfile.mkstemp,
                    dir=str(annotations_dir),
                    suffix=".tmp",
                    prefix="annotation_",
                )
                try:
                    async with aiofiles.open(temp_fd, "w", encoding="utf-8") as f:
                        await f.write(json_content)

                    await asyncio.to_thread(os.replace, temp_path, str(file_path))
                except Exception:
                    if await asyncio.to_thread(os.path.exists, temp_path):
                        await asyncio.to_thread(os.unlink, temp_path)
                    raise

                return next_etag

        except RevisionConflictError:
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

    async def delete_annotation(
        self,
        dataset_id: str,
        episode_index: int,
        *,
        if_match: str | None = None,
    ) -> bool:
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
            async with self._resource_lock(file_path):
                current_content = await self._read_content(file_path)
                if current_content is None:
                    if if_match is not None:
                        raise RevisionConflictError(None)
                    return False
                current_etag = self._etag(current_content)
                if if_match is not None and if_match != current_etag:
                    raise RevisionConflictError(current_etag)

                await aiofiles.os.remove(file_path)
                return True

        except RevisionConflictError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to delete annotation file {file_path}: {e}", cause=e)
