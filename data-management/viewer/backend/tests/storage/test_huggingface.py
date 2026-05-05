"""
Unit tests for Hugging Face Hub adapter.

These tests use mocking to avoid requiring actual Hub access.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest


class TestHuggingFaceHubAdapter(TestCase):
    """Tests for HuggingFaceHubAdapter."""

    def setUp(self):
        """Set up test fixtures."""
        self.repo_id = "lerobot/test-dataset"
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    def test_get_dataset_info(self, mock_fs_class, mock_download):
        """Test getting dataset info from Hub."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        # Create mock info.json content
        info_data = {
            "name": "Test Dataset",
            "total_episodes": 100,
            "fps": 30.0,
            "features": {
                "observation.images.top": {"dtype": "video", "shape": [480, 640, 3]},
                "action": {"dtype": "float32", "shape": [7]},
            },
            "tasks": [
                {"task_index": 0, "description": "Pick up object"},
            ],
        }

        # Write mock file
        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text(json.dumps(info_data))
        mock_download.return_value = str(info_path)

        adapter = HuggingFaceHubAdapter(
            repo_id=self.repo_id,
            cache_dir=self.temp_dir,
        )

        result = asyncio.run(adapter.get_dataset_info())

        assert result.id == self.repo_id
        assert result.name == "Test Dataset"
        assert result.total_episodes == 100
        assert result.fps == 30.0
        assert "observation.images.top" in result.features
        assert len(result.tasks) == 1
        assert result.tasks[0].description == "Pick up object"

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    def test_list_episodes_from_total(self, mock_fs_class, mock_download):
        """Test listing episodes using total_episodes count."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        # Create mock info.json content
        info_data = {
            "name": "Test Dataset",
            "total_episodes": 5,
            "fps": 30.0,
            "features": {},
            "tasks": [],
        }

        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text(json.dumps(info_data))
        mock_download.return_value = str(info_path)

        # Mock filesystem to not find episode metadata
        mock_fs = MagicMock()
        mock_fs.ls.side_effect = FileNotFoundError()
        mock_fs_class.return_value = mock_fs

        adapter = HuggingFaceHubAdapter(
            repo_id=self.repo_id,
            cache_dir=self.temp_dir,
        )

        result = asyncio.run(adapter.list_episodes())

        assert len(result) == 5
        assert [ep.index for ep in result] == [0, 1, 2, 3, 4]

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    def test_get_episode_data(self, mock_fs_class, mock_download):
        """Test getting episode data with video URLs."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        # Create mock info.json content
        info_data = {
            "name": "Test Dataset",
            "total_episodes": 100,
            "fps": 30.0,
            "features": {
                "observation.images.top": {"dtype": "video", "shape": [480, 640, 3]},
                "observation.images.wrist": {"dtype": "video", "shape": [480, 640, 3]},
            },
            "tasks": [],
        }

        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text(json.dumps(info_data))
        mock_download.return_value = str(info_path)

        adapter = HuggingFaceHubAdapter(
            repo_id=self.repo_id,
            cache_dir=self.temp_dir,
        )

        result = asyncio.run(adapter.get_episode_data(episode_index=42))

        assert result.meta.index == 42
        assert "top" in result.video_urls
        assert "wrist" in result.video_urls
        assert "episode_000042.mp4" in result.video_urls["top"]

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_video_url_format(self):
        """Test video URL generation format."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(
            repo_id="lerobot/koch-pick-place",
            revision="main",
        )

        url = adapter.get_video_url(episode_index=5, camera_name="top")

        assert url == (
            "https://huggingface.co/datasets/lerobot/koch-pick-place/resolve/"
            "main/videos/chunk-000/observation.images.top/episode_000005.mp4"
        )

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_video_url_with_revision(self):
        """Test video URL with specific revision."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(
            repo_id="lerobot/koch-pick-place",
            revision="v2.0",
        )

        url = adapter.get_video_url(episode_index=1500, camera_name="wrist")

        # Episode 1500 should be in chunk-001
        assert "chunk-001" in url
        assert "v2.0" in url
        assert "episode_001500.mp4" in url

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_chunk_calculation(self):
        """Test episode to chunk index calculation."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id="test/dataset")

        # Episode 0-999 should be chunk-000
        url = adapter.get_video_url(0, "cam")
        assert "chunk-000" in url

        url = adapter.get_video_url(999, "cam")
        assert "chunk-000" in url

        # Episode 1000-1999 should be chunk-001
        url = adapter.get_video_url(1000, "cam")
        assert "chunk-001" in url

        # Episode 5500 should be chunk-005
        url = adapter.get_video_url(5500, "cam")
        assert "chunk-005" in url

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_isinstance_storage_adapter(self):
        """Verify HuggingFaceHubAdapter inherits from StorageAdapter."""
        from src.api.storage.base import StorageAdapter
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id="test/dataset")
        assert isinstance(adapter, StorageAdapter)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_write_methods_raise_not_implemented(self):
        """Verify all write methods raise NotImplementedError."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id="test/dataset")
        with self.assertRaises(NotImplementedError):
            asyncio.run(adapter.save_annotation("ds", 0, None))
        with self.assertRaises(NotImplementedError):
            asyncio.run(adapter.get_annotation("ds", 0))
        with self.assertRaises(NotImplementedError):
            asyncio.run(adapter.list_annotated_episodes("ds"))
        with self.assertRaises(NotImplementedError):
            asyncio.run(adapter.delete_annotation("ds", 0))

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    def test_download_file_uses_async_to_thread(self, mock_fs_class, mock_download):
        """Verify _download_file wraps hf_hub_download with asyncio.to_thread."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.return_value = str(Path(self.temp_dir) / "dummy.json")
        adapter = HuggingFaceHubAdapter(
            repo_id=self.repo_id,
            cache_dir=self.temp_dir,
        )
        with patch("src.api.storage.huggingface.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            asyncio.run(adapter._download_file("meta/info.json"))
            assert mock_to_thread.call_count >= 1


class TestHuggingFaceHubAdapterBranches(TestCase):
    """Additional branch coverage tests for HuggingFaceHubAdapter."""

    def setUp(self):
        self.repo_id = "lerobot/test-dataset"
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_download_file_wraps_exception_in_storage_error(self, mock_download, mock_fs_class):
        """_download_file converts hf_hub_download failures into StorageError."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = RuntimeError("network down")
        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)

        with self.assertRaises(StorageError) as ctx:
            asyncio.run(adapter._download_file("meta/info.json"))
        assert "network down" in str(ctx.exception)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_get_dataset_info_wraps_parse_error(self, mock_download, mock_fs_class):
        """get_dataset_info wraps non-StorageError exceptions as StorageError."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        # Write malformed JSON to trigger parse error
        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text("{ not valid json")
        mock_download.return_value = str(info_path)

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        with self.assertRaises(StorageError) as ctx:
            asyncio.run(adapter.get_dataset_info())
        assert self.repo_id in str(ctx.exception)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_get_dataset_info_with_string_tasks(self, mock_download, mock_fs_class):
        """get_dataset_info handles tasks given as plain strings."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        info_data = {
            "name": "Stringy Tasks",
            "total_episodes": 2,
            "fps": 10.0,
            "features": {"action": {"dtype": "float32", "shape": [7]}},
            "tasks": ["pick", "place"],
        }
        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text(json.dumps(info_data))
        mock_download.return_value = str(info_path)

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        result = asyncio.run(adapter.get_dataset_info())

        assert [t.description for t in result.tasks] == ["pick", "place"]
        assert [t.task_index for t in result.tasks] == [0, 1]

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_list_episodes_from_parquet_metadata(self, mock_download, mock_fs_class):
        """list_episodes parses chunk-*/episode_NNNNNN.parquet entries and skips bad names."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        info_data = {
            "name": "Parquet Discovery",
            "total_episodes": 0,
            "fps": 30.0,
            "features": {},
            "tasks": [],
        }
        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text(json.dumps(info_data))
        mock_download.return_value = str(info_path)

        mock_fs = MagicMock()

        def ls_side_effect(path):
            if path.endswith("/meta/episodes"):
                return [
                    f"datasets/{self.repo_id}/meta/episodes/chunk-000",
                    f"datasets/{self.repo_id}/meta/episodes/not-a-chunk",
                ]
            if path.endswith("chunk-000"):
                return [
                    f"{path}/episode_000002.parquet",
                    f"{path}/episode_000000.parquet",
                    f"{path}/episode_bad.parquet",  # ValueError → continue
                    f"{path}/README.md",  # non-parquet → skipped
                ]
            return []

        mock_fs.ls.side_effect = ls_side_effect
        mock_fs_class.return_value = mock_fs

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        result = asyncio.run(adapter.list_episodes())

        assert [ep.index for ep in result] == [0, 2]

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_get_dataset_info_reraises_storage_error(self, mock_download, mock_fs_class):
        """get_dataset_info should re-raise StorageError from _download_file unchanged."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = OSError("hub unreachable")

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        with pytest.raises(StorageError) as excinfo:
            asyncio.run(adapter.get_dataset_info())
        assert "hub unreachable" in str(excinfo.value)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_list_episodes_reraises_storage_error(self, mock_download, mock_fs_class):
        """list_episodes should re-raise StorageError from get_dataset_info unchanged."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = OSError("hub unreachable")

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        with pytest.raises(StorageError) as excinfo:
            asyncio.run(adapter.list_episodes())
        assert "hub unreachable" in str(excinfo.value)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_get_episode_data_reraises_storage_error(self, mock_download, mock_fs_class):
        """get_episode_data should re-raise StorageError from get_dataset_info unchanged."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = OSError("hub unreachable")

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        with pytest.raises(StorageError) as excinfo:
            asyncio.run(adapter.get_episode_data(0))
        assert "hub unreachable" in str(excinfo.value)

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    def test_get_episode_data_wraps_unexpected_exception(self, mock_download, mock_fs_class):
        """get_episode_data should wrap non-StorageError exceptions in StorageError."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=self.temp_dir)
        # Pre-populate cache so get_dataset_info isn't called; then make features access fail
        adapter._info_cache = MagicMock()
        adapter._info_cache.get.side_effect = RuntimeError("boom")

        with pytest.raises(StorageError) as excinfo:
            asyncio.run(adapter.get_episode_data(5))
        assert "Failed to get episode 5" in str(excinfo.value)


class TestHuggingFaceHubAdapterImportError(TestCase):
    """Tests for HuggingFaceHubAdapter when huggingface_hub is not installed."""

    @patch("src.api.storage.huggingface.HF_AVAILABLE", False)
    def test_raises_import_error(self):
        """Test that adapter raises ImportError when huggingface_hub is missing."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        with pytest.raises(ImportError, match="huggingface_hub"):
            HuggingFaceHubAdapter(repo_id="test/dataset")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
