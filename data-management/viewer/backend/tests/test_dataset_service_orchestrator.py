"""Behavior tests for the DatasetService orchestrator.

The suite exercises public service methods with temporary filesystem data and
mocked storage collaborators. It does not require repository datasets or cloud
connections.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.services.dataset_service.service import DatasetService


def _make_provider(**overrides: Any) -> MagicMock:
    provider = MagicMock()
    provider.sync_dataset_to_local = AsyncMock(return_value=False)
    provider.sync_meta_only_to_local = AsyncMock(return_value=False)
    provider.sync_hdf5_dataset_to_local = AsyncMock(return_value=False)
    provider.sync_hdf5_episode_to_local = AsyncMock(return_value=True)
    provider.count_hdf5_episodes = AsyncMock(return_value=0)
    provider.get_info_json = AsyncMock(return_value=None)
    provider.resolve_video_blob_path = AsyncMock(return_value="blob/path.mp4")
    provider.get_blob_properties = AsyncMock(return_value=None)
    provider.scan_all_dataset_ids = AsyncMock(return_value={"lerobot": [], "hdf5": []})
    provider.upload_video = AsyncMock(return_value=True)

    async def empty_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[bytes]:
        if False:
            yield b""

    provider.stream_video = empty_stream
    for name, value in overrides.items():
        setattr(provider, name, value)
    return provider


def _make_storage(annotated_episodes: list[int] | None = None) -> MagicMock:
    storage = MagicMock()
    storage.list_annotated_episodes = AsyncMock(return_value=annotated_episodes or [])
    return storage


def _make_handler(**overrides: Any) -> MagicMock:
    handler = MagicMock()
    handler.available = True
    handler.has_loader.return_value = False
    handler.can_handle.return_value = False
    handler.get_loader.return_value = False
    handler.discover.return_value = None
    handler.list_episodes.return_value = ([], {})
    handler.load_episode.return_value = None
    handler.get_trajectory.return_value = []
    handler.get_frame_image.return_value = None
    handler.get_cameras.return_value = []
    handler.get_video_path.return_value = None
    for name, value in overrides.items():
        setattr(handler, name, value)
    return handler


def _install_handlers(
    service: DatasetService,
    lerobot_handler: MagicMock,
    hdf5_handler: MagicMock | None = None,
) -> None:
    if hdf5_handler is None:
        hdf5_handler = _make_handler()
    service._lerobot_handler = lerobot_handler
    service._hdf5_handler = hdf5_handler
    service._handlers = [lerobot_handler, hdf5_handler]


def _patch_temp_directories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    created: dict[str, Path] = {}

    def make_temp_dir(*, prefix: str) -> str:
        path = tmp_path / prefix.rstrip("_")
        path.mkdir(exist_ok=True)
        created[prefix] = path
        return str(path)

    monkeypatch.setattr("src.api.services.dataset_service.service.tempfile.mkdtemp", make_temp_dir)
    return created


def _trajectory_point(frame: int = 0) -> TrajectoryPoint:
    return TrajectoryPoint(
        timestamp=float(frame),
        frame=frame,
        joint_positions=[float(frame)],
        joint_velocities=[0.0],
        end_effector_pose=[0.0] * 6,
        gripper_state=0.0,
    )


class TestDatasetDiscovery:
    pytestmark = pytest.mark.asyncio

    async def test_list_datasets_builds_blob_metadata_for_supported_formats(self, tmp_path: Path) -> None:
        provider = _make_provider(
            scan_all_dataset_ids=AsyncMock(
                return_value={
                    "lerobot": ["robot--run"],
                    "hdf5": ["archive--session"],
                }
            ),
            get_info_json=AsyncMock(
                return_value={
                    "robot_type": "so100",
                    "total_episodes": 12,
                    "fps": 24,
                    "features": {
                        "observation.state": {
                            "dtype": "float32",
                            "shape": [2],
                            "names": [["joint_a", "joint_b"]],
                        },
                        "action": {},
                    },
                }
            ),
            count_hdf5_episodes=AsyncMock(return_value=3),
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        datasets = {dataset.id: dataset for dataset in await service.list_datasets()}

        assert set(datasets) == {"robot--run", "archive--session"}
        assert datasets["robot--run"].name == "robot--run (so100)"
        assert datasets["robot--run"].total_episodes == 12
        assert datasets["robot--run"].fps == 24.0
        assert datasets["robot--run"].features["observation.state"].names == ["joint_a", "joint_b"]
        assert datasets["robot--run"].features["action"].dtype == "unknown"
        assert datasets["archive--session"].name == "session"
        assert datasets["archive--session"].group == "archive"
        assert datasets["archive--session"].total_episodes == 3

    async def test_list_datasets_keeps_registered_data_when_blob_scan_fails(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        provider = _make_provider(scan_all_dataset_ids=AsyncMock(side_effect=RuntimeError("unavailable")))
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        registered = DatasetInfo(id="registered", name="Registered", total_episodes=1, fps=30.0)
        await service.register_dataset(registered)

        with caplog.at_level("WARNING"):
            datasets = await service.list_datasets()

        assert datasets == [registered]
        assert "Failed to scan blob datasets: unavailable" in caplog.text

    async def test_list_datasets_continues_after_one_blob_dataset_fails(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        provider = _make_provider(
            scan_all_dataset_ids=AsyncMock(return_value={"lerobot": ["broken"], "hdf5": ["usable"]}),
            get_info_json=AsyncMock(side_effect=RuntimeError("bad metadata")),
            count_hdf5_episodes=AsyncMock(return_value=2),
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        with caplog.at_level("WARNING"):
            datasets = await service.list_datasets()

        assert [(dataset.id, dataset.total_episodes) for dataset in datasets] == [("usable", 2)]
        assert "Failed to discover blob dataset broken: bad metadata" in caplog.text

    async def test_repeated_listing_reuses_discovered_blob_metadata(self, tmp_path: Path) -> None:
        provider = _make_provider(
            scan_all_dataset_ids=AsyncMock(return_value={"lerobot": ["dataset"], "hdf5": []}),
            get_info_json=AsyncMock(return_value={"total_episodes": 4}),
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        first = await service.list_datasets()
        second = await service.list_datasets()

        assert first == second
        provider.get_info_json.assert_awaited_once_with("dataset")

    async def test_deleted_local_dataset_is_absent_from_listing_and_lookup(self, tmp_path: Path) -> None:
        dataset_path = tmp_path / "local"
        dataset_path.mkdir()
        info = DatasetInfo(id="local", name="Local", total_episodes=1, fps=30.0)
        handler = _make_handler()
        handler.can_handle.return_value = True
        handler.discover.return_value = info
        service = DatasetService(base_path=str(tmp_path))
        _install_handlers(service, handler)

        assert await service.list_datasets() == [info]

        dataset_path.rmdir()

        assert await service.list_datasets() == []
        assert await service.get_dataset("local") is None


class TestGetAndRegisterDataset:
    pytestmark = pytest.mark.asyncio

    async def test_register_dataset_makes_metadata_available(self, tmp_path: Path) -> None:
        service = DatasetService(base_path=str(tmp_path))
        info = DatasetInfo(id="registered", name="Registered", total_episodes=2, fps=25.0)

        await service.register_dataset(info)

        assert await service.get_dataset("registered") is info

    async def test_get_dataset_prefers_lerobot_blob_metadata(self, tmp_path: Path) -> None:
        provider = _make_provider(
            get_info_json=AsyncMock(return_value={"total_episodes": 7, "fps": 60}),
            count_hdf5_episodes=AsyncMock(return_value=5),
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        dataset = await service.get_dataset("dataset")

        assert dataset is not None
        assert dataset.id == "dataset"
        assert dataset.total_episodes == 7
        assert dataset.fps == 60.0
        provider.count_hdf5_episodes.assert_not_awaited()

    async def test_get_dataset_falls_back_to_hdf5_blob_metadata(self, tmp_path: Path) -> None:
        provider = _make_provider(
            get_info_json=AsyncMock(return_value=None),
            count_hdf5_episodes=AsyncMock(return_value=5),
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        dataset = await service.get_dataset("group--dataset")

        assert dataset is not None
        assert dataset.name == "dataset"
        assert dataset.group == "group"
        assert dataset.total_episodes == 5
        assert dataset.fps == 30.0


class TestListEpisodes:
    pytestmark = pytest.mark.asyncio

    async def test_lists_registered_episode_range_with_filters_and_pagination(self, tmp_path: Path) -> None:
        storage = _make_storage([0, 3])
        service = DatasetService(base_path=str(tmp_path), storage_adapter=storage)
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=5, fps=30.0))

        annotated = await service.list_episodes("dataset", has_annotations=True)
        unannotated_page = await service.list_episodes(
            "dataset",
            offset=1,
            limit=2,
            has_annotations=False,
            task_index=0,
        )

        assert [(episode.index, episode.has_annotations) for episode in annotated] == [(0, True), (3, True)]
        assert [(episode.index, episode.has_annotations) for episode in unannotated_page] == [
            (2, False),
            (4, False),
        ]
        assert await service.list_episodes("dataset", task_index=99) == []

    @pytest.mark.parametrize(
        ("dataset_id", "message"),
        [
            ("foo/bar", "Invalid dataset identifier"),
            ("foo\\bar", "Invalid dataset identifier"),
            ("..", "Invalid dataset identifier"),
            (".", "Invalid dataset identifier"),
            ("a----b", "Invalid dataset identifier"),
            ("a--b--c--d--e--f", "Dataset nesting too deep"),
        ],
    )
    async def test_rejects_unsafe_dataset_ids_before_blob_sync(
        self,
        tmp_path: Path,
        dataset_id: str,
        message: str,
    ) -> None:
        provider = _make_provider()
        service = DatasetService(
            base_path=str(tmp_path),
            storage_adapter=_make_storage(),
            blob_provider=provider,
        )

        with pytest.raises(ValueError, match=message):
            await service.list_episodes(dataset_id)

        provider.sync_meta_only_to_local.assert_not_awaited()

    async def test_accepts_five_level_dataset_id(self, tmp_path: Path) -> None:
        provider = _make_provider()
        service = DatasetService(
            base_path=str(tmp_path),
            storage_adapter=_make_storage(),
            blob_provider=provider,
        )
        info = DatasetInfo(id="a--b--c--d--e", name="Dataset", total_episodes=1, fps=30.0)
        await service.register_dataset(info)

        episodes = await service.list_episodes(info.id)

        assert [episode.index for episode in episodes] == [0]
        provider.sync_meta_only_to_local.assert_awaited_once_with(info.id, ANY)

    async def test_lists_blob_hdf5_episode_metadata(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_temp_directories(monkeypatch, tmp_path)
        provider = _make_provider(sync_hdf5_dataset_to_local=AsyncMock(return_value=True))
        hdf5_handler = _make_handler()
        hdf5_handler.get_loader.return_value = True
        hdf5_handler.list_episodes.return_value = (
            [2, 5],
            {
                2: {"length": 11, "task_index": 1},
                5: {"length": 17, "task_index": 2},
            },
        )
        service = DatasetService(
            base_path=str(tmp_path),
            storage_adapter=_make_storage([5]),
            blob_provider=provider,
        )
        _install_handlers(service, _make_handler(), hdf5_handler)

        episodes = await service.list_episodes("dataset")

        assert [
            (episode.index, episode.length, episode.task_index, episode.has_annotations) for episode in episodes
        ] == [
            (2, 11, 1, False),
            (5, 17, 2, True),
        ]


class TestEpisodeRetrievalAndCache:
    pytestmark = pytest.mark.asyncio

    async def test_get_episode_returns_empty_data_for_unknown_dataset(self, tmp_path: Path) -> None:
        service = DatasetService(base_path=str(tmp_path), storage_adapter=_make_storage())

        episode = await service.get_episode("unknown", 4)

        assert episode == EpisodeData(
            meta=EpisodeMeta(index=4, length=0, task_index=0, has_annotations=False),
            video_urls={},
            trajectory_data=[],
        )

    async def test_get_episode_rejects_indices_outside_registered_range(self, tmp_path: Path) -> None:
        service = DatasetService(base_path=str(tmp_path), storage_adapter=_make_storage())
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=2, fps=30.0))

        assert await service.get_episode("dataset", -1) is None
        assert await service.get_episode("dataset", 2) is None

    async def test_repeated_get_episode_uses_cache_and_refreshes_annotation_flag(self, tmp_path: Path) -> None:
        storage = _make_storage()
        episode = EpisodeData(meta=EpisodeMeta(index=0, length=8, task_index=1))
        handler = _make_handler()
        handler.has_loader.return_value = True
        handler.load_episode.return_value = episode
        service = DatasetService(base_path=str(tmp_path), storage_adapter=storage)
        _install_handlers(service, handler)
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=1, fps=30.0))

        first = await service.get_episode("dataset", 0)
        storage.list_annotated_episodes.return_value = [0]
        second = await service.get_episode("dataset", 0)

        assert first is second
        assert second is not None
        assert second.meta.has_annotations is True
        handler.load_episode.assert_called_once_with("dataset", 0, dataset_info=ANY)

    async def test_cache_invalidation_forces_episode_reload(self, tmp_path: Path) -> None:
        first_episode = EpisodeData(meta=EpisodeMeta(index=0, length=3, task_index=0))
        reloaded_episode = EpisodeData(meta=EpisodeMeta(index=0, length=9, task_index=0))
        handler = _make_handler()
        handler.has_loader.return_value = True
        handler.load_episode.side_effect = [first_episode, reloaded_episode]
        service = DatasetService(base_path=str(tmp_path), storage_adapter=_make_storage())
        _install_handlers(service, handler)
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=1, fps=30.0))

        assert (await service.get_episode("dataset", 0)).meta.length == 3
        assert (await service.get_episode("dataset", 0)).meta.length == 3
        assert service.invalidate_episode_cache("dataset", 0) == 1
        assert (await service.get_episode("dataset", 0)).meta.length == 9
        assert handler.load_episode.call_count == 2

    async def test_get_episode_loads_blob_hdf5_episode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_temp_directories(monkeypatch, tmp_path)
        provider = _make_provider(sync_hdf5_dataset_to_local=AsyncMock(return_value=True))
        episode = EpisodeData(meta=EpisodeMeta(index=2, length=6, task_index=1))
        hdf5_handler = _make_handler()
        hdf5_handler.get_loader.return_value = True
        hdf5_handler.load_episode.return_value = episode
        service = DatasetService(
            base_path=str(tmp_path),
            storage_adapter=_make_storage(),
            blob_provider=provider,
        )
        _install_handlers(service, _make_handler(), hdf5_handler)

        result = await service.get_episode("dataset", 2)

        assert result is episode
        assert result.meta.length == 6
        provider.sync_hdf5_episode_to_local.assert_awaited_with("dataset", ANY, 2)

    async def test_get_episode_trajectory_uses_cached_episode(self, tmp_path: Path) -> None:
        point = _trajectory_point()
        episode = EpisodeData(
            meta=EpisodeMeta(index=0, length=1, task_index=0),
            trajectory_data=[point],
        )
        handler = _make_handler()
        handler.has_loader.return_value = True
        handler.load_episode.return_value = episode
        service = DatasetService(base_path=str(tmp_path), storage_adapter=_make_storage())
        _install_handlers(service, handler)
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=1, fps=30.0))
        await service.get_episode("dataset", 0)

        trajectory = await service.get_episode_trajectory("dataset", 0)

        assert trajectory == [point]
        handler.get_trajectory.assert_not_called()

    async def test_get_episode_trajectory_falls_back_between_handlers(self, tmp_path: Path) -> None:
        point = _trajectory_point(2)
        primary = _make_handler()
        primary.has_loader.return_value = True
        secondary = _make_handler()
        secondary.get_trajectory.return_value = [point]
        service = DatasetService(base_path=str(tmp_path))
        _install_handlers(service, primary, secondary)

        assert await service.get_episode_trajectory("dataset", 2) == [point]


class TestFrameAndCameraAccess:
    pytestmark = pytest.mark.asyncio

    async def test_get_frame_image_returns_handler_frame(self, tmp_path: Path) -> None:
        handler = _make_handler()
        handler.has_loader.return_value = True
        handler.get_frame_image.return_value = b"jpeg"
        service = DatasetService(base_path=str(tmp_path))
        _install_handlers(service, handler)

        assert await service.get_frame_image("dataset", 1, 7, "wrist") == b"jpeg"

    async def test_get_frame_image_materializes_blob_video(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def stream_video(_path: str, **_kwargs: Any) -> AsyncIterator[bytes]:
            yield b"video"

        provider = _make_provider(
            get_info_json=AsyncMock(return_value={"total_episodes": 1, "fps": 24}),
            resolve_video_blob_path=AsyncMock(return_value="dataset/video.mp4"),
            stream_video=stream_video,
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        monkeypatch.setattr(service, "_try_handlers", MagicMock(return_value=None))
        monkeypatch.setattr(
            "src.api.services.dataset_service.service.tempfile.gettempdir",
            lambda: str(tmp_path),
        )

        def extract_frame(path: str, frame_idx: int, fps: float) -> bytes | None:
            if Path(path).read_bytes() == b"video" and frame_idx == 7 and fps == 24.0:
                return b"blob-jpeg"
            return None

        monkeypatch.setattr(service._lerobot_handler, "_extract_frame_ffmpeg", extract_frame)

        assert await service.get_frame_image("dataset", 0, 7, "wrist") == b"blob-jpeg"

    async def test_get_frame_image_falls_back_to_cv2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        provider = _make_provider(
            get_info_json=AsyncMock(return_value={"total_episodes": 1}),
            resolve_video_blob_path=AsyncMock(return_value="dataset/video.mp4"),
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        monkeypatch.setattr(service, "_try_handlers", MagicMock(return_value=None))
        monkeypatch.setattr(service, "materialize_blob_video", AsyncMock(return_value=video_path))
        monkeypatch.setattr(service._lerobot_handler, "_extract_frame_ffmpeg", lambda *_args: None)
        monkeypatch.setattr(service._lerobot_handler, "_extract_frame_cv2", lambda *_args: b"cv2-jpeg")

        assert await service.get_frame_image("dataset", 0, 3, "wrist") == b"cv2-jpeg"

    async def test_get_frame_image_returns_none_without_local_or_blob_video(self, tmp_path: Path) -> None:
        service = DatasetService(base_path=str(tmp_path))

        assert await service.get_frame_image("missing", 0, 0, "wrist") is None

    async def test_get_episode_cameras_falls_back_between_handlers(self, tmp_path: Path) -> None:
        primary = _make_handler()
        primary.has_loader.return_value = True
        secondary = _make_handler()
        secondary.get_cameras.return_value = ["wrist", "overhead"]
        service = DatasetService(base_path=str(tmp_path))
        _install_handlers(service, primary, secondary)

        assert await service.get_episode_cameras("dataset", 0) == ["wrist", "overhead"]


class TestBlobVideoAccess:
    pytestmark = pytest.mark.asyncio

    async def test_get_blob_video_path_reports_provider_result(self, tmp_path: Path) -> None:
        without_provider = DatasetService(base_path=str(tmp_path))
        provider = _make_provider(resolve_video_blob_path=AsyncMock(return_value="dataset/camera.mp4"))
        with_provider = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        assert await without_provider.get_blob_video_path("dataset", 0, "wrist") is None
        assert await with_provider.get_blob_video_path("dataset", 1, "wrist") == "dataset/camera.mp4"

    async def test_get_blob_video_stream_returns_range_headers_and_bytes(self, tmp_path: Path) -> None:
        async def stream_video(
            _path: str,
            *,
            offset: int | None = None,
            length: int | None = None,
        ) -> AsyncIterator[bytes]:
            assert (offset, length) == (10, 20)
            yield b"first"
            yield b"second"

        provider = _make_provider(
            get_blob_properties=AsyncMock(return_value={"size": 100, "content_type": "video/x-matroska"}),
            stream_video=stream_video,
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        result = await service.get_blob_video_stream("dataset/video.mkv", offset=10, length=20)

        assert result is not None
        headers, media_type, stream = result
        assert headers == {
            "Accept-Ranges": "bytes",
            "Content-Length": "20",
            "Content-Range": "bytes 10-29/100",
        }
        assert media_type == "video/x-matroska"
        assert [chunk async for chunk in stream] == [b"first", b"second"]

    async def test_get_blob_video_stream_defaults_non_video_content_type(self, tmp_path: Path) -> None:
        provider = _make_provider(
            get_blob_properties=AsyncMock(return_value={"size": 100, "content_type": "image/png"})
        )
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)

        result = await service.get_blob_video_stream("dataset/video")

        assert result is not None
        headers, media_type, _stream = result
        assert headers == {"Accept-Ranges": "bytes", "Content-Length": "100"}
        assert media_type == "video/mp4"

    async def test_materialize_blob_video_caches_download(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stream_calls = 0

        async def stream_video(_path: str, **_kwargs: Any) -> AsyncIterator[bytes]:
            nonlocal stream_calls
            stream_calls += 1
            yield b"chunk-1"
            yield b"chunk-2"

        provider = _make_provider(stream_video=stream_video)
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        monkeypatch.setattr(
            "src.api.services.dataset_service.service.tempfile.gettempdir",
            lambda: str(tmp_path),
        )

        first = await service.materialize_blob_video("dataset/video.mp4")
        second = await service.materialize_blob_video("dataset/video.mp4")

        assert first is not None
        assert first == second
        assert first.parent == tmp_path / "dvw_video_cache" / "blob"
        assert first.read_bytes() == b"chunk-1chunk-2"
        assert stream_calls == 1

    async def test_materialize_blob_video_removes_partial_download_on_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def stream_video(_path: str, **_kwargs: Any) -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("network failure")

        provider = _make_provider(stream_video=stream_video)
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        monkeypatch.setattr(
            "src.api.services.dataset_service.service.tempfile.gettempdir",
            lambda: str(tmp_path),
        )

        result = await service.materialize_blob_video("dataset/video-without-extension")

        cache_dir = tmp_path / "dvw_video_cache" / "blob"
        assert result is None
        assert list(cache_dir.iterdir()) == []


class TestVideoFilePath:
    def test_returns_handler_video_path(self, tmp_path: Path) -> None:
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        handler = _make_handler()
        handler.has_loader.return_value = True
        handler.get_video_path.return_value = str(video_path)
        service = DatasetService(base_path=str(tmp_path))
        _install_handlers(service, handler)

        assert service.get_video_file_path("dataset", 0, "wrist") == str(video_path)

    def test_generated_hdf5_video_is_uploaded_to_blob(self, tmp_path: Path) -> None:
        video_path = tmp_path / "video.mp4"
        provider = _make_provider()
        hdf5_handler = _make_handler()
        hdf5_handler.has_loader.return_value = True
        hdf5_handler._video_cache_path.return_value = video_path

        def generate_video(*_args: Any, **_kwargs: Any) -> str:
            video_path.write_bytes(b"video")
            return str(video_path)

        hdf5_handler.get_video_path.side_effect = generate_video
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        _install_handlers(service, _make_handler(), hdf5_handler)

        result = service.get_video_file_path("dataset", 2, "wrist")

        assert result == str(video_path)
        provider.upload_video.assert_awaited_once_with("dataset", "wrist", 2, video_path)

    def test_blob_upload_failure_does_not_hide_generated_video(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        video_path = tmp_path / "video.mp4"
        provider = _make_provider(upload_video=AsyncMock(side_effect=RuntimeError("upload failed")))
        hdf5_handler = _make_handler()
        hdf5_handler.has_loader.return_value = True
        hdf5_handler._video_cache_path.return_value = video_path

        def generate_video(*_args: Any, **_kwargs: Any) -> str:
            video_path.write_bytes(b"video")
            return str(video_path)

        hdf5_handler.get_video_path.side_effect = generate_video
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        _install_handlers(service, _make_handler(), hdf5_handler)

        with caplog.at_level("WARNING"):
            result = service.get_video_file_path("dataset", 2, "wrist")

        assert result == str(video_path)
        assert "Blob upload failed for dataset ep 2: upload failed" in caplog.text


class TestLifecycleAndCapabilities:
    pytestmark = pytest.mark.asyncio

    async def test_cleanup_removes_synced_metadata_and_allows_resync(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = _patch_temp_directories(monkeypatch, tmp_path)
        provider = _make_provider(sync_meta_only_to_local=AsyncMock(return_value=True))
        service = DatasetService(
            base_path=str(tmp_path),
            storage_adapter=_make_storage(),
            blob_provider=provider,
        )
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=1, fps=30.0))

        assert [episode.index for episode in await service.list_episodes("dataset")] == [0]
        synced_path = created["dvwm_"]
        assert synced_path.exists()

        service.cleanup_temp_dirs()

        assert not synced_path.exists()
        assert [episode.index for episode in await service.list_episodes("dataset")] == [0]
        assert provider.sync_meta_only_to_local.await_count == 2

    @pytest.mark.asyncio
    async def test_blob_synced_directory_is_accepted_as_safe_video_location(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        synced_paths: dict[str, Path] = {}
        base_path = tmp_path / "datasets"
        temp_path = tmp_path / "blob-sync"
        base_path.mkdir()
        temp_path.mkdir()

        async def sync_hdf5_dataset(dataset_id: str, destination: Path) -> bool:
            synced_paths[dataset_id] = destination
            (destination / "video.mp4").write_bytes(b"video")
            return True

        monkeypatch.setattr(tempfile, "tempdir", str(temp_path))
        provider = _make_provider(
            sync_hdf5_dataset_to_local=AsyncMock(side_effect=sync_hdf5_dataset),
        )
        service = DatasetService(
            base_path=str(base_path),
            storage_adapter=_make_storage(),
            blob_provider=provider,
        )
        await service.register_dataset(DatasetInfo(id="dataset", name="Dataset", total_episodes=1, fps=30.0))

        episodes = await service.list_episodes("dataset")
        synced_path = synced_paths["dataset"]
        video_path = synced_path / "video.mp4"

        assert [episode.index for episode in episodes] == [0]
        provider.sync_hdf5_dataset_to_local.assert_awaited_once_with("dataset", synced_path)
        assert synced_path.parent == temp_path
        assert not video_path.is_relative_to(base_path)
        assert video_path.read_bytes() == b"video"
        assert service.is_safe_video_path(str(video_path)) is True


class TestCapabilities:
    def test_video_path_outside_service_directories_is_rejected(self, tmp_path: Path) -> None:
        base_path = tmp_path / "datasets"
        base_path.mkdir()
        outside_path = tmp_path / "outside.mp4"
        outside_path.write_bytes(b"video")
        service = DatasetService(base_path=str(base_path))

        assert service.is_safe_video_path(str(base_path)) is True
        assert service.is_safe_video_path(str(outside_path)) is False

    def test_capability_queries_report_configured_state(self, tmp_path: Path) -> None:
        without_provider = DatasetService(base_path=str(tmp_path))
        with_provider = DatasetService(base_path=str(tmp_path), blob_provider=_make_provider())

        assert without_provider.has_blob_provider() is False
        assert with_provider.has_blob_provider() is True
        assert isinstance(with_provider.has_hdf5_support(), bool)
        assert isinstance(with_provider.has_lerobot_support(), bool)
        assert with_provider.dataset_has_hdf5("missing") is False
        assert with_provider.dataset_is_lerobot("missing") is False
