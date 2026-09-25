"""
Unit tests for Hugging Face Hub adapter.

These tests use mocking to avoid requiring actual Hub access.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestHuggingFaceHubAdapter:
    """Tests for HuggingFaceHubAdapter."""

    @pytest.fixture(autouse=True)
    def _set_adapter_inputs(self, huggingface_repo_id: str, tmp_path: Path) -> None:
        self.repo_id = huggingface_repo_id
        self.temp_dir = tmp_path

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    async def test_get_dataset_info(self, mock_fs_class, mock_download):
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
            cache_dir=str(self.temp_dir),
        )

        result = await adapter.get_dataset_info()

        assert result.id == self.repo_id
        assert result.name == "Test Dataset"
        assert result.total_episodes == 100
        assert result.fps == 30.0
        assert result.features["observation.images.top"].model_dump() == {
            "dtype": "video",
            "shape": [480, 640, 3],
            "names": None,
        }
        assert [task.model_dump() for task in result.tasks] == [{"task_index": 0, "description": "Pick up object"}]
        mock_download.assert_called_once_with(
            repo_id=self.repo_id,
            filename="meta/info.json",
            revision="main",
            token=None,
            cache_dir=str(self.temp_dir),
            repo_type="dataset",
        )

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    async def test_list_episodes_from_total(self, mock_fs_class, mock_download):
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
            cache_dir=str(self.temp_dir),
        )

        result = await adapter.list_episodes()

        assert len(result) == 5
        assert [ep.index for ep in result] == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    async def test_get_episode_data(self, mock_fs_class, mock_download):
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
            cache_dir=str(self.temp_dir),
        )

        result = await adapter.get_episode_data(episode_index=42)

        assert result.meta.model_dump() == {
            "index": 42,
            "length": 0,
            "task_index": 0,
            "has_annotations": False,
        }
        assert result.video_urls == {
            "top": (
                f"https://huggingface.co/datasets/{self.repo_id}/resolve/main/"
                "videos/chunk-000/observation.images.top/episode_000042.mp4"
            ),
            "wrist": (
                f"https://huggingface.co/datasets/{self.repo_id}/resolve/main/"
                "videos/chunk-000/observation.images.wrist/episode_000042.mp4"
            ),
        }
        assert result.trajectory_data == []

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

        assert url == (
            "https://huggingface.co/datasets/lerobot/koch-pick-place/resolve/"
            "v2.0/videos/chunk-001/observation.images.wrist/episode_001500.mp4"
        )

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_chunk_calculation(self):
        """Test episode to chunk index calculation."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id="test/dataset")

        expected_chunks = {0: "000", 999: "000", 1000: "001", 5500: "005"}
        for episode_index, chunk_index in expected_chunks.items():
            assert adapter.get_video_url(episode_index, "cam") == (
                "https://huggingface.co/datasets/test/dataset/resolve/main/"
                f"videos/chunk-{chunk_index}/observation.images.cam/episode_{episode_index:06d}.mp4"
            )

    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    def test_isinstance_storage_adapter(self):
        """Verify HuggingFaceHubAdapter inherits from StorageAdapter."""
        from src.api.storage.base import StorageAdapter
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id="test/dataset")
        assert isinstance(adapter, StorageAdapter)

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    async def test_write_methods_raise_not_implemented(self):
        """Verify all write methods raise NotImplementedError."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id="test/dataset")
        with pytest.raises(NotImplementedError):
            await adapter.save_annotation("ds", 0, None)
        with pytest.raises(NotImplementedError):
            await adapter.get_annotation("ds", 0)
        with pytest.raises(NotImplementedError):
            await adapter.list_annotated_episodes("ds")
        with pytest.raises(NotImplementedError):
            await adapter.delete_annotation("ds", 0)

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.hf_hub_download")
    @patch("src.api.storage.huggingface.HfFileSystem")
    async def test_get_dataset_info_offloads_download(self, mock_fs_class, mock_download):
        """Verify metadata retrieval offloads its blocking Hub download."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        info_path = self.temp_dir / "info.json"
        info_path.write_text('{"name": "Dataset"}')
        mock_download.return_value = str(info_path)
        adapter = HuggingFaceHubAdapter(
            repo_id=self.repo_id,
            cache_dir=str(self.temp_dir),
        )
        with patch("src.api.storage.huggingface.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            result = await adapter.get_dataset_info()

        assert result.name == "Dataset"
        assert mock_to_thread.call_count == 2


class TestHuggingFaceHubAdapterBranches:
    """Additional branch coverage tests for HuggingFaceHubAdapter."""

    @pytest.fixture(autouse=True)
    def _set_adapter_inputs(self, huggingface_repo_id: str, tmp_path: Path) -> None:
        self.repo_id = huggingface_repo_id
        self.temp_dir = tmp_path

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_get_dataset_info_wraps_download_error(self, mock_download, mock_fs_class):
        """Metadata retrieval converts Hub download failures into StorageError."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = RuntimeError("network down")
        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))

        with pytest.raises(StorageError, match="network down"):
            await adapter.get_dataset_info()

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_get_dataset_info_wraps_parse_error(self, mock_download, mock_fs_class):
        """get_dataset_info wraps non-StorageError exceptions as StorageError."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        # Write malformed JSON to trigger parse error
        info_path = Path(self.temp_dir) / "info.json"
        info_path.write_text("{ not valid json")
        mock_download.return_value = str(info_path)

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        with pytest.raises(StorageError, match=self.repo_id):
            await adapter.get_dataset_info()

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_get_dataset_info_with_string_tasks(self, mock_download, mock_fs_class):
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

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        result = await adapter.get_dataset_info()

        assert [t.description for t in result.tasks] == ["pick", "place"]
        assert [t.task_index for t in result.tasks] == [0, 1]

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_list_episodes_from_parquet_metadata(self, mock_download, mock_fs_class):
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

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        result = await adapter.list_episodes()

        assert [ep.index for ep in result] == [0, 2]

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_get_dataset_info_reraises_storage_error(self, mock_download, mock_fs_class):
        """get_dataset_info should re-raise StorageError from _download_file unchanged."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = OSError("hub unreachable")

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        with pytest.raises(StorageError) as excinfo:
            await adapter.get_dataset_info()
        assert "hub unreachable" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_list_episodes_reraises_storage_error(self, mock_download, mock_fs_class):
        """list_episodes should re-raise StorageError from get_dataset_info unchanged."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = OSError("hub unreachable")

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        with pytest.raises(StorageError) as excinfo:
            await adapter.list_episodes()
        assert "hub unreachable" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_get_episode_data_reraises_storage_error(self, mock_download, mock_fs_class):
        """get_episode_data should re-raise StorageError from get_dataset_info unchanged."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        mock_download.side_effect = OSError("hub unreachable")

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        with pytest.raises(StorageError) as excinfo:
            await adapter.get_episode_data(0)
        assert "hub unreachable" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch("src.api.storage.huggingface.HF_AVAILABLE", True)
    @patch("src.api.storage.huggingface.HfFileSystem")
    @patch("src.api.storage.huggingface.hf_hub_download")
    async def test_get_episode_data_wraps_unexpected_exception(self, mock_download, mock_fs_class):
        """get_episode_data should wrap non-StorageError exceptions in StorageError."""
        from src.api.storage.base import StorageError
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        adapter = HuggingFaceHubAdapter(repo_id=self.repo_id, cache_dir=str(self.temp_dir))
        # Pre-populate cache so get_dataset_info isn't called; then make features access fail
        adapter._info_cache = MagicMock()
        adapter._info_cache.get.side_effect = RuntimeError("boom")

        with pytest.raises(StorageError) as excinfo:
            await adapter.get_episode_data(5)
        assert "Failed to get episode 5" in str(excinfo.value)


class TestHuggingFaceHubAdapterImportError:
    """Tests for HuggingFaceHubAdapter when huggingface_hub is not installed."""

    @patch("src.api.storage.huggingface.HF_AVAILABLE", False)
    def test_raises_import_error(self):
        """Test that adapter raises ImportError when huggingface_hub is missing."""
        from src.api.storage.huggingface import HuggingFaceHubAdapter

        with pytest.raises(ImportError, match="huggingface_hub"):
            HuggingFaceHubAdapter(repo_id="test/dataset")
