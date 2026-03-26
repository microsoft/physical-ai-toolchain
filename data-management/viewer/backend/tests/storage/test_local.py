"""
Unit tests for local filesystem storage adapter.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from src.api.models.annotations import TaskCompletenessRating
from src.api.storage.local import LocalStorageAdapter, StorageError

from .conftest import create_test_annotation


class TestLocalStorageAdapter(TestCase):
    """Tests for LocalStorageAdapter."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.adapter = LocalStorageAdapter(self.temp_dir)
        self.dataset_id = "test-dataset"

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_annotation_not_found(self):
        """Test getting a non-existent annotation returns None."""
        result = asyncio.run(self.adapter.get_annotation(self.dataset_id, 0))
        assert result is None

    def test_save_and_get_annotation(self):
        """Test saving and retrieving an annotation."""
        annotation = create_test_annotation(episode_index=5)

        # Save annotation
        asyncio.run(self.adapter.save_annotation(self.dataset_id, 5, annotation))

        # Verify file exists
        expected_path = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes" / "episode_000005.json"
        assert expected_path.exists()

        # Retrieve annotation
        result = asyncio.run(self.adapter.get_annotation(self.dataset_id, 5))
        assert result is not None
        assert result.episode_index == 5
        assert result.annotations[0].task_completeness.rating == TaskCompletenessRating.SUCCESS

    def test_save_overwrites_existing(self):
        """Test that saving an annotation overwrites existing one."""
        # Save initial annotation
        annotation1 = create_test_annotation(episode_index=1)
        asyncio.run(self.adapter.save_annotation(self.dataset_id, 1, annotation1))

        # Save updated annotation
        annotation2 = create_test_annotation(episode_index=1)
        annotation2.annotations[0].notes = "Updated notes"
        asyncio.run(self.adapter.save_annotation(self.dataset_id, 1, annotation2))

        # Retrieve and verify updated
        result = asyncio.run(self.adapter.get_annotation(self.dataset_id, 1))
        assert result.annotations[0].notes == "Updated notes"

    def test_list_annotated_episodes_empty(self):
        """Test listing episodes when no annotations exist."""
        result = asyncio.run(self.adapter.list_annotated_episodes(self.dataset_id))
        assert result == []

    def test_list_annotated_episodes(self):
        """Test listing episodes with annotations."""
        # Create several annotations
        for idx in [3, 1, 5, 2]:
            annotation = create_test_annotation(episode_index=idx)
            asyncio.run(self.adapter.save_annotation(self.dataset_id, idx, annotation))

        # List should return sorted indices
        result = asyncio.run(self.adapter.list_annotated_episodes(self.dataset_id))
        assert result == [1, 2, 3, 5]

    def test_delete_annotation(self):
        """Test deleting an annotation."""
        # Save annotation
        annotation = create_test_annotation(episode_index=10)
        asyncio.run(self.adapter.save_annotation(self.dataset_id, 10, annotation))

        # Verify exists
        assert asyncio.run(self.adapter.get_annotation(self.dataset_id, 10)) is not None

        # Delete
        result = asyncio.run(self.adapter.delete_annotation(self.dataset_id, 10))
        assert result is True

        # Verify deleted
        assert asyncio.run(self.adapter.get_annotation(self.dataset_id, 10)) is None

    def test_delete_annotation_not_found(self):
        """Test deleting a non-existent annotation returns False."""
        result = asyncio.run(self.adapter.delete_annotation(self.dataset_id, 999))
        assert result is False

    def test_invalid_json_raises_error(self):
        """Test that invalid JSON raises StorageError."""
        # Create invalid JSON file
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)
        invalid_file = annotations_dir / "episode_000001.json"
        invalid_file.write_text("{invalid json")

        with pytest.raises(StorageError):
            asyncio.run(self.adapter.get_annotation(self.dataset_id, 1))

    def test_atomic_write(self):
        """Test that writes are atomic (no partial files)."""
        annotation = create_test_annotation(episode_index=1)
        asyncio.run(self.adapter.save_annotation(self.dataset_id, 1, annotation))

        # Verify no temp files left behind
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        temp_files = list(annotations_dir.glob("*.tmp"))
        assert len(temp_files) == 0

    def test_multiple_datasets(self):
        """Test that different datasets are isolated."""
        # Save to two datasets
        annotation1 = create_test_annotation(episode_index=1)
        annotation2 = create_test_annotation(episode_index=1)

        asyncio.run(self.adapter.save_annotation("dataset-a", 1, annotation1))
        asyncio.run(self.adapter.save_annotation("dataset-b", 1, annotation2))

        # Verify isolation
        result_a = asyncio.run(self.adapter.list_annotated_episodes("dataset-a"))
        result_b = asyncio.run(self.adapter.list_annotated_episodes("dataset-b"))

        assert result_a == [1]
        assert result_b == [1]

        # Delete from one doesn't affect other
        asyncio.run(self.adapter.delete_annotation("dataset-a", 1))
        assert asyncio.run(self.adapter.get_annotation("dataset-a", 1)) is None
        assert asyncio.run(self.adapter.get_annotation("dataset-b", 1)) is not None

    def test_save_uses_async_tempfile(self):
        """Verify save_annotation delegates sync I/O to asyncio.to_thread."""
        annotation = create_test_annotation(episode_index=0)
        with patch("src.api.storage.local.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            asyncio.run(self.adapter.save_annotation(self.dataset_id, 0, annotation))
            assert mock_to_thread.call_count >= 1

    def test_path_traversal_rejected(self):
        """Verify dataset_id with path traversal components raises StorageError."""
        annotation = create_test_annotation(episode_index=0)
        with pytest.raises(StorageError, match="path traversal detected"):
            asyncio.run(self.adapter.save_annotation("../../etc", 0, annotation))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
