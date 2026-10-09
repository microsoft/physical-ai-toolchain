"""
Local filesystem storage adapter for annotations.

Stores annotations in the dataset's annotations/ directory structure
following the LeRobot v3 format specification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import aiofiles
import aiofiles.os
from fastapi import HTTPException

from ..models.annotations import EpisodeAnnotationFile
from ..validation import validate_path_containment
from .base import RevisionConflictError, StorageAdapter, StorageError, VersionedValue
from .local_revision import delete_conditional, write_conditional
from .paths import resource_directory
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
        try:
            safe_path = await asyncio.to_thread(validate_path_containment, path, self.base_path)
        except HTTPException as exc:
            raise StorageError("Annotation path escapes the configured dataset directory", cause=exc) from exc
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

    def _get_annotation_path(
        self,
        dataset_id: str,
        episode_index: int,
        resource_scope: str | None = None,
    ) -> Path:
        """Get the file path for an episode's annotations."""
        directory = self._get_annotations_dir(dataset_id).parent / resource_directory(resource_scope)
        return validate_path_containment(directory / f"episode_{episode_index:06d}.json", self.base_path)

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
        file_path = await asyncio.to_thread(self._get_annotation_path, dataset_id, episode_index)

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
        *,
        resource_scope: str | None = None,
    ) -> VersionedValue[EpisodeAnnotationFile]:
        """Retrieve an annotation and its strong content ETag."""
        file_path = await asyncio.to_thread(self._get_annotation_path, dataset_id, episode_index, resource_scope)
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
        resource_scope: str | None = None,
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
        file_path = await asyncio.to_thread(self._get_annotation_path, dataset_id, episode_index, resource_scope)
        annotations_dir = file_path.parent

        try:
            await self._ensure_directory(annotations_dir)
            return await asyncio.to_thread(
                write_conditional,
                file_path,
                self._serialize(annotation),
                if_match=if_match,
                if_none_match=if_none_match,
            )

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
        annotations_dir = await asyncio.to_thread(self._get_annotations_dir, dataset_id)

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
        file_path = await asyncio.to_thread(self._get_annotation_path, dataset_id, episode_index)

        try:
            return await asyncio.to_thread(delete_conditional, file_path, if_match=if_match)

        except RevisionConflictError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to delete annotation file {file_path}: {e}", cause=e)
