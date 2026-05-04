"""
Unit tests for DatasetService handler fallback chain.

Tests _try_handlers iteration and get_episode handler chain behavior
using stub handlers to verify fallback from primary to secondary handler.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta
from src.api.services.dataset_service.hdf5_handler import HDF5FormatHandler
from src.api.services.dataset_service.lerobot_handler import LeRobotFormatHandler
from src.api.services.dataset_service.service import DatasetService


class StubHandler:
    """Minimal handler whose methods return configurable values."""

    def __init__(self, name: str, *, results: dict | None = None) -> None:
        self.name = name
        self._results = results or {}
        self._has_loader_ids: set[str] = set()

    # Protocol methods
    def has_loader(self, dataset_id: str) -> bool:
        return dataset_id in self._has_loader_ids

    def can_handle(self, dataset_path) -> bool:
        return self._results.get("can_handle", False)

    def get_loader(self, dataset_id: str, dataset_path) -> bool:
        return False

    def get_trajectory(self, dataset_id: str, episode_idx: int):
        return self._results.get("get_trajectory", [])

    def get_frame_image(self, dataset_id: str, episode_idx: int, frame_idx: int, camera: str):
        return self._results.get("get_frame_image", None)

    def get_cameras(self, dataset_id: str, episode_idx: int):
        return self._results.get("get_cameras", [])

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str):
        return self._results.get("get_video_path", None)

    def list_episodes(self, dataset_id: str):
        return self._results.get("list_episodes", ([], {}))

    def load_episode(self, dataset_id: str, episode_idx: int, dataset_info=None):
        return self._results.get("load_episode", None)

    def discover(self, dataset_id: str, dataset_path):
        return self._results.get("discover", None)


@pytest.fixture
def service_with_stubs(tmp_path):
    """DatasetService with two stub handlers replacing real ones."""
    svc = DatasetService(base_path=str(tmp_path))
    return svc


class TestTryHandlersFallback:
    """Test _try_handlers iterates handlers and falls through on empty results."""

    def test_primary_returns_result(self, service_with_stubs):
        svc = service_with_stubs
        primary = StubHandler("primary", results={"get_trajectory": [{"mock": True}]})
        secondary = StubHandler("secondary", results={"get_trajectory": [{"wrong": True}]})
        primary._has_loader_ids.add("ds1")
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = svc._try_handlers("ds1", "get_trajectory", 0)
        assert result == [{"mock": True}]

    def test_fallback_when_primary_returns_empty(self, service_with_stubs):
        svc = service_with_stubs
        primary = StubHandler("primary", results={"get_trajectory": []})
        secondary = StubHandler("secondary", results={"get_trajectory": [{"fallback": True}]})
        primary._has_loader_ids.add("ds1")
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = svc._try_handlers("ds1", "get_trajectory", 0)
        assert result == [{"fallback": True}]

    def test_returns_none_when_all_empty(self, service_with_stubs):
        svc = service_with_stubs
        primary = StubHandler("primary", results={"get_trajectory": []})
        secondary = StubHandler("secondary", results={"get_trajectory": []})
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = svc._try_handlers("unknown", "get_trajectory", 0)
        assert result is None

    def test_skips_primary_tries_secondary_when_no_resolved_handler(self, service_with_stubs):
        svc = service_with_stubs
        primary = StubHandler("primary", results={"get_cameras": []})
        secondary = StubHandler("secondary", results={"get_cameras": ["cam_a"]})
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = svc._try_handlers("unresolved", "get_cameras", 0)
        assert result == ["cam_a"]


class TestResolveHandler:
    """Test _resolve_handler uses has_loader instead of private _get_loader."""

    def test_returns_handler_with_loader(self, service_with_stubs):
        svc = service_with_stubs
        stub = StubHandler("stub")
        stub._has_loader_ids.add("ds1")
        svc._handlers = [stub]
        svc._lerobot_handler = stub
        svc._hdf5_handler = StubHandler("other")

        handler = svc._resolve_handler("ds1")
        assert handler is stub

    def test_returns_none_when_no_loader_and_no_path(self, service_with_stubs):
        svc = service_with_stubs
        handler = svc._resolve_handler("nonexistent")
        assert handler is None


class TestListDatasetsRefresh:
    """Test local dataset discovery refresh removes deleted datasets."""

    def test_list_datasets_prunes_deleted_local_dataset(self, tmp_path):
        svc = DatasetService(base_path=str(tmp_path))
        dataset_dir = tmp_path / "deleted-dataset"
        dataset_dir.mkdir()

        dataset_info = DatasetInfo(
            id="deleted-dataset",
            name="Deleted Dataset",
            total_episodes=1,
            fps=30.0,
            features={},
            tasks=[],
        )

        handler = StubHandler(
            "local",
            results={
                "can_handle": True,
                "discover": dataset_info,
            },
        )
        svc._handlers = [handler]
        svc._lerobot_handler = handler
        svc._hdf5_handler = handler

        initial = asyncio.run(svc.list_datasets())

        assert [dataset.id for dataset in initial] == ["deleted-dataset"]

        dataset_dir.rmdir()

        refreshed = asyncio.run(svc.list_datasets())

        assert [dataset.id for dataset in refreshed] == []


class TestHasLoader:
    """Test has_loader on real handler instances."""

    def test_lerobot_handler_false_initially(self):
        h = LeRobotFormatHandler()
        assert h.has_loader("anything") is False

    def test_hdf5_handler_false_initially(self):
        h = HDF5FormatHandler()
        assert h.has_loader("anything") is False


class TestGetEpisodeHandlerChain:
    """Test async get_episode handler chain with blob fallback."""

    def test_get_episode_uses_primary_handler(self, service_with_stubs):
        svc = service_with_stubs
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=10, task_index=0),
            video_urls={},
            trajectory_data=[],
        )
        primary = StubHandler("primary", results={"load_episode": episode})
        secondary = StubHandler("secondary")
        primary._has_loader_ids.add("ds1")
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = asyncio.run(svc.get_episode("ds1", 0))
        assert result is not None
        assert result.meta.length == 10

    def test_get_episode_falls_through_to_secondary(self, service_with_stubs):
        svc = service_with_stubs
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=5, task_index=0),
            video_urls={},
            trajectory_data=[],
        )
        primary = StubHandler("primary", results={"load_episode": None})
        secondary = StubHandler("secondary", results={"load_episode": episode})
        primary._has_loader_ids.add("ds1")
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = asyncio.run(svc.get_episode("ds1", 0))
        assert result is not None
        assert result.meta.length == 5

    def test_get_episode_returns_empty_when_no_handler(self, service_with_stubs):
        svc = service_with_stubs
        primary = StubHandler("primary")
        secondary = StubHandler("secondary")
        svc._handlers = [primary, secondary]
        svc._lerobot_handler = primary
        svc._hdf5_handler = secondary

        result = asyncio.run(svc.get_episode("unknown", 0))
        assert result is not None
        assert result.meta.length == 0
        assert result.trajectory_data == []

    def test_get_episode_blob_sync_delegates_to_handler(self, tmp_path):
        """Blob sync path should delegate loader creation to the handler."""
        svc = DatasetService(base_path=str(tmp_path))

        # Create a fake blob-synced LeRobot structure
        synced_path = tmp_path / "blob_sync"
        (synced_path / "meta").mkdir(parents=True)
        (synced_path / "meta" / "info.json").write_text("{}")
        (synced_path / "data").mkdir()

        svc._blob_provider = True  # truthy to enter blob branch
        svc._ensure_blob_synced = AsyncMock(return_value=synced_path)

        # Patch get_loader to simulate successful loader registration
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=3, task_index=0),
            video_urls={},
            trajectory_data=[],
        )
        primary = StubHandler("primary", results={"load_episode": episode})

        def fake_get_loader(dataset_id, path):
            primary._has_loader_ids.add(dataset_id)
            return True

        primary.get_loader = fake_get_loader
        svc._lerobot_handler = primary
        svc._hdf5_handler = StubHandler("secondary")
        svc._handlers = [primary, svc._hdf5_handler]

        result = asyncio.run(svc.get_episode("blob_ds", 0))
        assert result is not None
        assert result.meta.length == 3
        svc._ensure_blob_synced.assert_awaited_once_with("blob_ds")
