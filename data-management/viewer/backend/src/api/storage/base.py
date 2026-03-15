"""
Abstract base class for annotation storage backends.

All storage adapters must implement this interface to support
annotation persistence across different storage backends.
"""

from abc import ABC, abstractmethod

from ..models.annotations import EpisodeAnnotationFile


class StorageAdapter(ABC):
    """Abstract base class for annotation storage backends."""

    @abstractmethod
    async def get_annotation(self, dataset_id: str, episode_index: int) -> EpisodeAnnotationFile | None:
        """
        Retrieve annotations for an episode.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.

        Returns:
            EpisodeAnnotationFile if annotations exist, None otherwise.
        """
        pass

    @abstractmethod
    async def save_annotation(self, dataset_id: str, episode_index: int, annotation: EpisodeAnnotationFile) -> None:
        """
        Save annotations for an episode.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.
            annotation: Complete annotation file to save.

        Raises:
            StorageError: If the save operation fails.
        """
        pass

    @abstractmethod
    async def list_annotated_episodes(self, dataset_id: str) -> list[int]:
        """
        List all episode indices with annotations for a dataset.

        Args:
            dataset_id: Unique identifier for the dataset.

        Returns:
            List of episode indices that have annotations.
        """
        pass

    @abstractmethod
    async def delete_annotation(self, dataset_id: str, episode_index: int) -> bool:
        """
        Delete annotations for an episode.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.

        Returns:
            True if annotations were deleted, False if they didn't exist.
        """
        pass

    async def close(self) -> None:  # noqa: B027
        """Release any held resources. Default implementation is a no-op."""

    async def get_annotations_batch(
        self, dataset_id: str, episode_indices: list[int]
    ) -> dict[int, EpisodeAnnotationFile | None]:
        """
        Retrieve annotations for multiple episodes.

        Default implementation calls get_annotation for each index.
        Subclasses may override for optimized batch retrieval.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_indices: List of episode indices to retrieve.

        Returns:
            Dictionary mapping episode index to annotation file (or None).
        """
        result = {}
        for idx in episode_indices:
            result[idx] = await self.get_annotation(dataset_id, idx)
        return result


class StorageError(Exception):
    """Exception raised for storage operation failures."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause
