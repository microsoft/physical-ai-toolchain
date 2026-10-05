"""Tests for DatasetService handler fallback behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.services.dataset_service import service as dataset_service_module
from src.api.services.dataset_service.base import DatasetFormatHandler
from src.api.services.dataset_service.service import DatasetService


def _handler() -> MagicMock:
    handler = MagicMock(spec=DatasetFormatHandler)
    handler.has_loader.return_value = False
    handler.can_handle.return_value = False
    handler.get_loader.return_value = False
    handler.get_trajectory.return_value = []
    handler.get_cameras.return_value = []
    handler.load_episode.return_value = None
    handler.discover.return_value = None
    return handler


def _install_handlers(service: DatasetService, primary: MagicMock, secondary: MagicMock) -> None:
    service._handlers = [primary, secondary]
    service._lerobot_handler = primary
    service._hdf5_handler = secondary


def _trajectory_point(frame: int) -> TrajectoryPoint:
    return TrajectoryPoint(
        timestamp=frame / 10,
        frame=frame,
        joint_positions=[0.0] * 6,
        joint_velocities=[0.0] * 6,
        end_effector_pose=[0.0] * 6,
        gripper_state=0.0,
    )


@pytest.fixture
def service(tmp_path: Path) -> DatasetService:
    return DatasetService(base_path=str(tmp_path))


@pytest.mark.asyncio
class TestPublicHandlerFallback:
    async def test_trajectory_uses_resolved_handler(self, service: DatasetService) -> None:
        primary = _handler()
        secondary = _handler()
        trajectory = [_trajectory_point(0)]
        primary.has_loader.return_value = True
        primary.get_trajectory.return_value = trajectory
        _install_handlers(service, primary, secondary)

        result = await service.get_episode_trajectory("dataset", 0)

        assert result == trajectory
        primary.get_trajectory.assert_called_once_with("dataset", 0)
        secondary.get_trajectory.assert_not_called()

    async def test_trajectory_falls_back_when_resolved_handler_returns_empty(self, service: DatasetService) -> None:
        primary = _handler()
        secondary = _handler()
        fallback = [_trajectory_point(3)]
        primary.has_loader.return_value = True
        secondary.get_trajectory.return_value = fallback
        _install_handlers(service, primary, secondary)

        result = await service.get_episode_trajectory("dataset", 0)

        assert result == fallback
        primary.get_trajectory.assert_called_once_with("dataset", 0)
        secondary.get_trajectory.assert_called_once_with("dataset", 0)

    async def test_cameras_try_handlers_when_dataset_is_unresolved(self, service: DatasetService) -> None:
        primary = _handler()
        secondary = _handler()
        secondary.get_cameras.return_value = ["wrist"]
        _install_handlers(service, primary, secondary)

        result = await service.get_episode_cameras("unknown", 0)

        assert result == ["wrist"]
        primary.get_cameras.assert_called_once_with("unknown", 0)
        secondary.get_cameras.assert_called_once_with("unknown", 0)

    async def test_trajectory_returns_empty_when_no_handler_has_data(self, service: DatasetService) -> None:
        primary = _handler()
        secondary = _handler()
        _install_handlers(service, primary, secondary)

        result = await service.get_episode_trajectory("unknown", 0)

        assert result == []
        primary.get_trajectory.assert_called_once_with("unknown", 0)
        secondary.get_trajectory.assert_called_once_with("unknown", 0)


@pytest.mark.asyncio
class TestListDatasetsRefresh:
    async def test_list_datasets_prunes_deleted_local_dataset(self, tmp_path: Path) -> None:
        service = DatasetService(base_path=str(tmp_path))
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
        handler = _handler()
        handler.can_handle.return_value = True
        handler.discover.return_value = dataset_info
        _install_handlers(service, handler, _handler())

        initial = await service.list_datasets()
        assert [dataset.id for dataset in initial] == ["deleted-dataset"]

        dataset_dir.rmdir()
        refreshed = await service.list_datasets()

        assert refreshed == []


@pytest.mark.asyncio
class TestGetEpisodeHandlerChain:
    async def test_get_episode_uses_resolved_handler(self, service: DatasetService) -> None:
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=10, task_index=0),
            video_urls={},
            trajectory_data=[],
        )
        primary = _handler()
        secondary = _handler()
        primary.has_loader.return_value = True
        primary.load_episode.return_value = episode
        _install_handlers(service, primary, secondary)

        result = await service.get_episode("dataset", 0)

        assert result is episode
        primary.load_episode.assert_called_once_with("dataset", 0, dataset_info=None)
        secondary.load_episode.assert_not_called()

    async def test_get_episode_falls_back_to_secondary(self, service: DatasetService) -> None:
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=5, task_index=0),
            video_urls={},
            trajectory_data=[],
        )
        primary = _handler()
        secondary = _handler()
        primary.has_loader.return_value = True
        secondary.load_episode.return_value = episode
        _install_handlers(service, primary, secondary)

        result = await service.get_episode("dataset", 0)

        assert result is episode
        primary.load_episode.assert_called_once_with("dataset", 0, dataset_info=None)
        secondary.load_episode.assert_called_once_with("dataset", 0, dataset_info=None)

    async def test_get_episode_returns_empty_episode_when_no_handler_has_data(self, service: DatasetService) -> None:
        primary = _handler()
        secondary = _handler()
        _install_handlers(service, primary, secondary)

        result = await service.get_episode("unknown", 2)

        assert result == EpisodeData(
            meta=EpisodeMeta(index=2, length=0, task_index=0, has_annotations=False),
            video_urls={},
            trajectory_data=[],
        )

    async def test_get_episode_delegates_blob_loader_creation(
        self,
        service: DatasetService,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        synced_path = tmp_path / "blob-sync"
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=3, task_index=0),
            video_urls={},
            trajectory_data=[],
        )
        primary = _handler()
        secondary = _handler()
        primary.get_loader.return_value = True
        primary.load_episode.return_value = episode
        _install_handlers(service, primary, secondary)
        ensure_blob_synced = AsyncMock(return_value=synced_path)
        monkeypatch.setattr(dataset_service_module, "LEROBOT_AVAILABLE", True)
        monkeypatch.setattr(service, "_blob_provider", MagicMock())
        monkeypatch.setattr(service, "_ensure_blob_synced", ensure_blob_synced)

        result = await service.get_episode("blob-dataset", 0)

        assert result is episode
        ensure_blob_synced.assert_awaited_once_with("blob-dataset")
        primary.get_loader.assert_called_once_with("blob-dataset", synced_path)
        primary.load_episode.assert_called_once_with("blob-dataset", 0, dataset_info=None)
