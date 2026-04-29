"""Unit tests for DatasetService orchestrator branches.

Covers blob provider integration, eviction/cleanup, prefetch scheduling,
discovery fallbacks, and path safety checks using mocked dependencies so
the suite runs without a real sample dataset or Azure connection.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.services.dataset_service.service import (
    DatasetService,
    _validate_dataset_id,
)


def _make_provider(**overrides: Any) -> AsyncMock:
    """Return an AsyncMock BlobDatasetProvider with sensible defaults."""
    provider = AsyncMock()
    provider.sync_dataset_to_local = AsyncMock(return_value=True)
    provider.sync_meta_only_to_local = AsyncMock(return_value=True)
    provider.sync_hdf5_dataset_to_local = AsyncMock(return_value=True)
    provider.sync_hdf5_episode_to_local = AsyncMock(return_value=True)
    provider.count_hdf5_episodes = AsyncMock(return_value=0)
    provider.get_info_json = AsyncMock(return_value=None)
    provider.resolve_video_blob_path = AsyncMock(return_value="blob/path.mp4")
    provider.get_blob_properties = AsyncMock(return_value=None)
    provider.scan_all_dataset_ids = AsyncMock(return_value={"lerobot": [], "hdf5": []})
    provider.upload_video = AsyncMock(return_value=None)

    async def _empty_stream(*_args: Any, **_kwargs: Any):
        if False:
            yield b""

    provider.stream_video = _empty_stream
    for name, value in overrides.items():
        setattr(provider, name, value)
    return provider


class TestValidateDatasetId:
    def test_rejects_forward_slash(self):
        with pytest.raises(ValueError, match="Invalid dataset identifier"):
            _validate_dataset_id("foo/bar")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="Invalid dataset identifier"):
            _validate_dataset_id("foo\\bar")

    def test_rejects_dotdot(self):
        with pytest.raises(ValueError, match="Invalid dataset identifier"):
            _validate_dataset_id("..")

    def test_rejects_dot(self):
        with pytest.raises(ValueError, match="Invalid dataset identifier"):
            _validate_dataset_id(".")

    def test_rejects_empty_segment(self):
        with pytest.raises(ValueError, match="Invalid dataset identifier"):
            _validate_dataset_id("a----b")

    def test_rejects_too_deep(self):
        with pytest.raises(ValueError, match="too deep"):
            _validate_dataset_id("a--b--c--d--e--f")

    def test_accepts_flat_id(self):
        assert _validate_dataset_id("flat_dataset") == "flat_dataset"

    def test_accepts_nested(self):
        assert _validate_dataset_id("a--b--c--d--e") == "a--b--c--d--e"


class TestEnsureBlobSynced:
    def test_no_provider_returns_none(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service._ensure_blob_synced("ds") is None

        asyncio.run(_run())

    def test_returns_cached_path(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider()
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            cached = tmp_path / "cached"
            cached.mkdir()
            service._blob_synced["ds"] = cached
            assert await service._ensure_blob_synced("ds") == cached
            provider.sync_dataset_to_local.assert_not_awaited()

        asyncio.run(_run())

    def test_success_records_path(self, tmp_path, monkeypatch):
        async def _run() -> None:
            provider = _make_provider()
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            synced = tmp_path / "dvw_x"
            synced.mkdir()
            monkeypatch.setattr(
                "src.api.services.dataset_service.service.tempfile.mkdtemp",
                lambda *, prefix: str(synced),
            )
            result = await service._ensure_blob_synced("ds")
            assert result == synced
            assert service._blob_synced["ds"] == synced

        asyncio.run(_run())

    def test_failure_removes_temp_dir(self, tmp_path, monkeypatch):
        async def _run() -> None:
            provider = _make_provider(sync_dataset_to_local=AsyncMock(return_value=False))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            synced = tmp_path / "dvw_fail"
            synced.mkdir()
            monkeypatch.setattr(
                "src.api.services.dataset_service.service.tempfile.mkdtemp",
                lambda *, prefix: str(synced),
            )
            result = await service._ensure_blob_synced("ds\rname")
            assert result is None
            assert not synced.exists()
            assert "ds" not in service._blob_synced

        asyncio.run(_run())


class TestEnsureBlobMetaSynced:
    def test_no_provider_returns_none(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service._ensure_blob_meta_synced("ds") is None

        asyncio.run(_run())

    def test_cached_path(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider()
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            cached = tmp_path / "meta"
            cached.mkdir()
            service._blob_meta_synced["ds"] = cached
            assert await service._ensure_blob_meta_synced("ds") == cached

        asyncio.run(_run())

    def test_failure_removes_temp_dir(self, tmp_path, monkeypatch):
        async def _run() -> None:
            provider = _make_provider(sync_meta_only_to_local=AsyncMock(return_value=False))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            synced = tmp_path / "dvwm_fail"
            synced.mkdir()
            monkeypatch.setattr(
                "src.api.services.dataset_service.service.tempfile.mkdtemp",
                lambda *, prefix: str(synced),
            )
            assert await service._ensure_blob_meta_synced("ds") is None
            assert not synced.exists()

        asyncio.run(_run())


class TestEnsureBlobHdf5Synced:
    def test_no_provider_returns_none(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service._ensure_blob_hdf5_synced("ds") is None

        asyncio.run(_run())

    def test_cached_path(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider()
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            cached = tmp_path / "hdf5"
            cached.mkdir()
            service._blob_hdf5_synced["ds"] = cached
            assert await service._ensure_blob_hdf5_synced("ds") == cached

        asyncio.run(_run())

    def test_success_records_path(self, tmp_path, monkeypatch):
        async def _run() -> None:
            provider = _make_provider()
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            synced = tmp_path / "dvwh_x"
            synced.mkdir()
            monkeypatch.setattr(
                "src.api.services.dataset_service.service.tempfile.mkdtemp",
                lambda *, prefix: str(synced),
            )
            result = await service._ensure_blob_hdf5_synced("ds")
            assert result == synced
            assert service._blob_hdf5_synced["ds"] == synced

        asyncio.run(_run())

    def test_failure_removes_temp_dir(self, tmp_path, monkeypatch):
        async def _run() -> None:
            provider = _make_provider(sync_hdf5_dataset_to_local=AsyncMock(return_value=False))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            synced = tmp_path / "dvwh_fail"
            synced.mkdir()
            monkeypatch.setattr(
                "src.api.services.dataset_service.service.tempfile.mkdtemp",
                lambda *, prefix: str(synced),
            )
            assert await service._ensure_blob_hdf5_synced("ds") is None
            assert not synced.exists()

        asyncio.run(_run())


class TestDiscoverBlobHdf5Dataset:
    def test_no_provider(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service._discover_blob_hdf5_dataset("ds") is None

        asyncio.run(_run())

    def test_zero_episodes_returns_none(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(count_hdf5_episodes=AsyncMock(return_value=0))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            assert await service._discover_blob_hdf5_dataset("ds") is None

        asyncio.run(_run())

    def test_flat_id_no_group(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(count_hdf5_episodes=AsyncMock(return_value=3))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            info = await service._discover_blob_hdf5_dataset("flat")
            assert info is not None
            assert info.id == "flat"
            assert info.name == "flat"
            assert info.group is None
            assert info.total_episodes == 3
            assert "flat" in service._blob_dataset_ids

        asyncio.run(_run())

    def test_nested_id_sets_group(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(count_hdf5_episodes=AsyncMock(return_value=1))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            info = await service._discover_blob_hdf5_dataset("a--b--c")
            assert info.name == "c"
            assert info.group == "a--b"

        asyncio.run(_run())


class TestDiscoverBlobDataset:
    def test_no_provider(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service._discover_blob_dataset("ds") is None

        asyncio.run(_run())

    def test_no_info_json(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(get_info_json=AsyncMock(return_value=None))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            assert await service._discover_blob_dataset("ds") is None

        asyncio.run(_run())

    def test_with_features_and_robot_type(self, tmp_path):
        async def _run() -> None:
            info_payload = {
                "robot_type": "so100",
                "total_episodes": 12,
                "fps": 24,
                "features": {
                    "obs.state": {"dtype": "float32", "shape": [6]},
                    "action": {},
                },
            }
            provider = _make_provider(get_info_json=AsyncMock(return_value=info_payload))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            info = await service._discover_blob_dataset("ds")
            assert info is not None
            assert info.id == "ds"
            assert info.name == "ds (so100)"
            assert info.total_episodes == 12
            assert info.fps == 24.0
            assert info.features["obs.state"].dtype == "float32"
            assert info.features["action"].dtype == "unknown"
            assert "ds" in service._blob_dataset_ids

        asyncio.run(_run())

    def test_without_robot_type(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(get_info_json=AsyncMock(return_value={"total_episodes": 0}))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            info = await service._discover_blob_dataset("ds")
            assert info.name == "ds"

        asyncio.run(_run())


class TestBlobVideoStreaming:
    def test_get_blob_video_path_no_provider(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service.get_blob_video_path("ds", 0, "cam") is None

        asyncio.run(_run())

    def test_get_blob_video_path_returns_provider_value(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(resolve_video_blob_path=AsyncMock(return_value="x/y.mp4"))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            assert await service.get_blob_video_path("ds", 1, "cam") == "x/y.mp4"

        asyncio.run(_run())

    def test_get_blob_video_stream_no_provider(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service.get_blob_video_stream("blob") is None

        asyncio.run(_run())

    def test_stream_without_props(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider()
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            result = await service.get_blob_video_stream("blob")
            assert result is not None
            headers, media_type, _stream = result
            assert headers == {"Accept-Ranges": "bytes"}
            assert media_type == "video/mp4"

        asyncio.run(_run())

    def test_stream_with_props_no_offset(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(
                get_blob_properties=AsyncMock(return_value={"size": 100, "content_type": "video/x-matroska"})
            )
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            headers, media_type, _stream = await service.get_blob_video_stream("blob")
            assert headers["Content-Length"] == "100"
            assert "Content-Range" not in headers
            assert media_type == "video/x-matroska"

        asyncio.run(_run())

    def test_stream_with_props_and_offset(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(
                get_blob_properties=AsyncMock(return_value={"size": 100, "content_type": "image/png"})
            )
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            headers, media_type, stream = await service.get_blob_video_stream("blob", offset=10, length=20)
            assert headers["Content-Length"] == "20"
            assert headers["Content-Range"] == "bytes 10-29/100"
            # non-video mime falls back to default
            assert media_type == "video/mp4"

            chunks = [chunk async for chunk in stream]
            assert chunks == []

        asyncio.run(_run())

    def test_stream_with_props_offset_no_length(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(get_blob_properties=AsyncMock(return_value={"size": 100}))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            headers, _media, _stream = await service.get_blob_video_stream("blob", offset=40)
            assert headers["Content-Length"] == "60"
            assert headers["Content-Range"] == "bytes 40-99/100"

        asyncio.run(_run())


class TestEvictionAndCleanup:
    def test_evict_removes_hdf5_synced_dir(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        target = tmp_path / "dvwh_x"
        target.mkdir()
        # _evict_dataset only handles _blob_synced and _blob_meta_synced;
        # confirm hdf5 entry is left untouched but other state clears.
        service._blob_hdf5_synced["ds"] = target
        service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=0, fps=30.0)
        service._local_dataset_ids.add("ds")
        service._blob_dataset_ids.add("ds")

        service._evict_dataset("ds")

        assert "ds" not in service._datasets
        assert "ds" not in service._local_dataset_ids
        assert "ds" not in service._blob_dataset_ids
        # hdf5 sync dir intentionally retained by evict
        assert target.exists()

    def test_evict_handler_loaders_cleared(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        service._lerobot_handler._loaders = {"ds": object()}
        service._hdf5_handler._loaders = {"ds": object()}
        service._evict_dataset("ds")
        assert "ds" not in service._lerobot_handler._loaders
        assert "ds" not in service._hdf5_handler._loaders

    def test_cleanup_temp_dirs_handles_missing_dir(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        ghost = tmp_path / "ghost"
        service._blob_synced["a"] = ghost  # never created
        service._blob_meta_synced["b"] = ghost
        # ignore_errors=True keeps cleanup idempotent
        service.cleanup_temp_dirs()
        assert service._blob_synced == {}
        assert service._blob_meta_synced == {}


class TestListDatasetsBlobAndPrune:
    def test_blob_scan_failure_does_not_raise(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(scan_all_dataset_ids=AsyncMock(side_effect=RuntimeError("nope")))
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            result = await service.list_datasets()
            assert result == []

        asyncio.run(_run())

    def test_blob_scan_discovers_both_types(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(
                scan_all_dataset_ids=AsyncMock(return_value={"lerobot": ["lr1"], "hdf5": ["hd1"]}),
                get_info_json=AsyncMock(return_value={"total_episodes": 4, "fps": 30.0}),
                count_hdf5_episodes=AsyncMock(return_value=2),
            )
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            ids = {d.id for d in await service.list_datasets()}
            assert ids == {"lr1", "hd1"}

        asyncio.run(_run())

    def test_blob_scan_skips_already_known(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(
                scan_all_dataset_ids=AsyncMock(return_value={"lerobot": ["lr1"], "hdf5": ["hd1"]}),
            )
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            service._datasets["lr1"] = DatasetInfo(id="lr1", name="lr1", total_episodes=0, fps=30.0)
            service._datasets["hd1"] = DatasetInfo(id="hd1", name="hd1", total_episodes=0, fps=30.0)
            await service.list_datasets()
            provider.get_info_json.assert_not_awaited()
            provider.count_hdf5_episodes.assert_not_awaited()

        asyncio.run(_run())

    def test_missing_base_returns_cached(self, tmp_path):
        async def _run() -> None:
            missing = tmp_path / "absent"
            service = DatasetService(base_path=str(missing))
            service._datasets["x"] = DatasetInfo(id="x", name="x", total_episodes=0, fps=30.0)
            result = await service.list_datasets()
            assert [d.id for d in result] == ["x"]

        asyncio.run(_run())

    def test_scan_oserror_returns_cached(self, tmp_path, monkeypatch):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["cached"] = DatasetInfo(id="cached", name="cached", total_episodes=0, fps=30.0)

            def boom(*_args: Any, **_kwargs: Any) -> None:
                raise OSError("permission denied")

            monkeypatch.setattr(service, "_scan_directory", boom)
            result = await service.list_datasets()
            assert [d.id for d in result] == ["cached"]

        asyncio.run(_run())

    def test_prune_evicts_missing_local(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["gone"] = DatasetInfo(id="gone", name="gone", total_episodes=0, fps=30.0)
            service._local_dataset_ids.add("gone")
            await service.list_datasets()
            assert "gone" not in service._datasets
            assert "gone" not in service._local_dataset_ids

        asyncio.run(_run())


class TestGetDatasetEdgeCases:
    def test_invalid_local_id_evicts(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=0, fps=30.0)
            service._local_dataset_ids.add("ds")
            # No filesystem dir → _get_dataset_path raises ValueError → evict
            result = await service.get_dataset("ds")
            assert result is None
            assert "ds" not in service._datasets

        asyncio.run(_run())

    def test_returns_cached(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            info = DatasetInfo(id="ds", name="ds", total_episodes=0, fps=30.0)
            service._datasets["ds"] = info
            service._blob_dataset_ids.add("ds")
            assert await service.get_dataset("ds") is info

        asyncio.run(_run())

    def test_blob_lerobot_then_hdf5_fallback(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(
                get_info_json=AsyncMock(return_value=None),
                count_hdf5_episodes=AsyncMock(return_value=5),
            )
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            result = await service.get_dataset("ds")
            assert result is not None
            assert result.total_episodes == 5

        asyncio.run(_run())

    def test_blob_lerobot_success(self, tmp_path):
        async def _run() -> None:
            provider = _make_provider(
                get_info_json=AsyncMock(return_value={"total_episodes": 7, "fps": 60}),
            )
            service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
            result = await service.get_dataset("ds")
            assert result is not None
            assert result.total_episodes == 7
            provider.count_hdf5_episodes.assert_not_awaited()

        asyncio.run(_run())


class TestRegisterAndCapabilities:
    def test_register_dataset_stores(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            info = DatasetInfo(id="x", name="x", total_episodes=0, fps=30.0)
            await service.register_dataset(info)
            assert service._datasets["x"] is info

        asyncio.run(_run())

    def test_has_blob_provider_false(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        assert service.has_blob_provider() is False

    def test_has_blob_provider_true(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path), blob_provider=_make_provider())
        assert service.has_blob_provider() is True


class TestListEpisodesFallbacks:
    def test_fallback_uses_dataset_total(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=3, fps=30.0)
            episodes = await service.list_episodes("ds")
            assert [e.index for e in episodes] == [0, 1, 2]

        asyncio.run(_run())

    def test_no_indices_returns_empty(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            # No dataset registered, no handler resolved → empty list.
            assert await service.list_episodes("ds") == []

        asyncio.run(_run())

    def test_pagination_and_filters(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=5, fps=30.0)
            # All have task_index=0 by default
            result = await service.list_episodes("ds", offset=1, limit=2, task_index=0)
            assert [e.index for e in result] == [1, 2]
            # Mismatched task filter returns nothing.
            assert await service.list_episodes("ds", task_index=99) == []

        asyncio.run(_run())

    def test_has_annotations_filter(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=2, fps=30.0)
            service._storage.list_annotated_episodes = AsyncMock(return_value=[0])  # type: ignore[method-assign]
            annotated = await service.list_episodes("ds", has_annotations=True)
            assert [e.index for e in annotated] == [0]
            unannotated = await service.list_episodes("ds", has_annotations=False)
            assert [e.index for e in unannotated] == [1]

        asyncio.run(_run())


class TestGetEpisodeBranches:
    def test_cached_returns_with_annotations(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            ep = EpisodeData(meta=EpisodeMeta(index=0, length=1, task_index=0))
            service._episode_cache.put("ds", 0, ep)
            service._storage.list_annotated_episodes = AsyncMock(return_value=[0])  # type: ignore[method-assign]
            result = await service.get_episode("ds", 0)
            assert result is ep
            assert result.meta.has_annotations is True

        asyncio.run(_run())

    def test_validate_index_out_of_range(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=2, fps=30.0)
            assert await service.get_episode("ds", 99) is None
            assert await service.get_episode("ds", -1) is None

        asyncio.run(_run())

    def test_unknown_dataset_returns_empty(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            result = await service.get_episode("ds", 0)
            assert result is not None
            assert result.meta.index == 0
            assert result.video_urls == {}
            assert result.trajectory_data == []

        asyncio.run(_run())


class TestGetEpisodeTrajectory:
    def test_cached_returns_trajectory(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            point = TrajectoryPoint(
                timestamp=0.0,
                frame=0,
                joint_positions=[0.0],
                joint_velocities=[0.0],
                end_effector_pose=[0.0],
                gripper_state=0.0,
            )
            ep = EpisodeData(meta=EpisodeMeta(index=0, length=1, task_index=0), trajectory_data=[point])
            service._episode_cache.put("ds", 0, ep)
            assert await service.get_episode_trajectory("ds", 0) == [point]

        asyncio.run(_run())

    def test_uncached_no_handler(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service.get_episode_trajectory("ds", 0) == []

        asyncio.run(_run())


class TestSchedulePrefetch:
    def test_skips_when_cache_disabled(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)
        # Cache disabled when capacity is 0; the call must not raise.
        service._schedule_prefetch("ds", 0)
        assert service._prefetch_tasks == set()

    def test_skips_when_total_le_one(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=1, fps=30.0)
        service._schedule_prefetch("ds", 0)
        assert service._prefetch_tasks == set()

    def test_skips_when_no_indices(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=4, fps=30.0)
        # Pre-cache the surrounding indices so the indices list becomes empty.
        for idx in range(4):
            service._episode_cache.put("ds", idx, EpisodeData(meta=EpisodeMeta(index=idx, length=1, task_index=0)))
        service._schedule_prefetch("ds", 0)
        assert service._prefetch_tasks == set()

    def test_runtime_error_swallowed(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=4, fps=30.0)
        # Outside an event loop asyncio.create_task raises RuntimeError → swallowed.
        service._schedule_prefetch("ds", 0)
        assert service._prefetch_tasks == set()

    def test_creates_task_when_loop_running(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        service._datasets["ds"] = DatasetInfo(id="ds", name="ds", total_episodes=4, fps=30.0)

        async def runner() -> None:
            service._schedule_prefetch("ds", 1)
            # Allow the prefetch coroutine to settle (no handler → returns quickly)
            await asyncio.sleep(0)
            for task in list(service._prefetch_tasks):
                if not task.done():
                    task.cancel()

        asyncio.run(runner())


class TestIsSafeVideoPath:
    def test_inside_base_is_safe(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        target = tmp_path / "video.mp4"
        target.write_bytes(b"")
        assert service.is_safe_video_path(str(target)) is True

    def test_equal_to_base_is_safe(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        assert service.is_safe_video_path(str(tmp_path)) is True

    def test_outside_base_not_safe(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path / "data"))
        (tmp_path / "data").mkdir()
        outside = tmp_path / "elsewhere.mp4"
        outside.write_bytes(b"")
        assert service.is_safe_video_path(str(outside)) is False

    def test_synced_dir_is_safe(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path / "data"))
        (tmp_path / "data").mkdir()
        synced = tmp_path / "synced"
        synced.mkdir()
        target = synced / "video.mp4"
        target.write_bytes(b"")
        service._blob_synced["ds"] = synced
        assert service.is_safe_video_path(str(target)) is True

    def test_hdf5_synced_dir_is_safe(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path / "data"))
        (tmp_path / "data").mkdir()
        synced = tmp_path / "hdf5synced"
        synced.mkdir()
        service._blob_hdf5_synced["ds"] = synced
        assert service.is_safe_video_path(str(synced)) is True


class TestUploadVideoToBlob:
    def test_upload_success_invokes_provider(self, tmp_path):
        provider = _make_provider()
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        cache = tmp_path / "v.mp4"
        cache.write_bytes(b"")
        service._upload_video_to_blob("ds", 1, "cam", cache)
        provider.upload_video.assert_awaited_once()


class TestGetDatasetPath:
    def test_traversal_rejected(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="Invalid dataset path"):
            service._get_dataset_path("../escape")

    def test_too_deep_rejected(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="too deep"):
            service._get_dataset_path("a--b--c--d--e--f")

    def test_missing_directory_raises(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            service._get_dataset_path("nonexistent")

    def test_missing_base_raises(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path / "absent"))
        with pytest.raises(ValueError, match="Base path not found"):
            service._get_dataset_path("anything")

    def test_resolves_existing(self, tmp_path):
        ds_dir = tmp_path / "ds"
        ds_dir.mkdir()
        service = DatasetService(base_path=str(tmp_path))
        assert service._get_dataset_path("ds") == ds_dir.resolve()


class TestInvalidateCache:
    def test_invalidate_returns_count(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        service._episode_cache.put("ds", 0, EpisodeData(meta=EpisodeMeta(index=0, length=1, task_index=0)))
        assert service.invalidate_episode_cache("ds", 0) == 1
        assert service._episode_cache.get("ds", 0) is None


class TestCapabilityFlags:
    def test_dataset_has_hdf5_default_false(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        assert service.dataset_has_hdf5("missing") is False
        assert service.dataset_is_lerobot("missing") is False

    def test_format_availability_flags(self, tmp_path):
        service = DatasetService(base_path=str(tmp_path))
        # Whatever the runtime says, both should be booleans
        assert isinstance(service.has_hdf5_support(), bool)
        assert isinstance(service.has_lerobot_support(), bool)


class TestGetVideoFilePath:
    def test_no_handler_returns_none(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert service.get_video_file_path("ds", 0, "cam") is None

        asyncio.run(_run())

    def test_lerobot_handler_returns_path(self, tmp_path, monkeypatch):
        service = DatasetService(base_path=str(tmp_path))
        monkeypatch.setattr(service, "_resolve_handler", lambda _ds: service._lerobot_handler)
        monkeypatch.setattr(service._lerobot_handler, "get_video_path", lambda *_a, **_kw: "/tmp/v.mp4")
        assert service.get_video_file_path("ds", 0, "cam") == "/tmp/v.mp4"

    def test_hdf5_handler_uploads_when_new(self, tmp_path, monkeypatch):
        provider = _make_provider()
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        monkeypatch.setattr(service, "_resolve_handler", lambda _ds: service._hdf5_handler)
        cache = tmp_path / "v.mp4"  # does not exist yet
        monkeypatch.setattr(service._hdf5_handler, "_video_cache_path", lambda *_a, **_kw: cache)

        def fake_get(*_a: Any, **_kw: Any) -> str:
            cache.write_bytes(b"")
            return str(cache)

        monkeypatch.setattr(service._hdf5_handler, "get_video_path", fake_get)
        uploads: list[Any] = []
        monkeypatch.setattr(service, "_upload_video_to_blob", lambda *args: uploads.append(args))
        result = service.get_video_file_path("ds", 0, "cam")
        assert result == str(cache)
        assert len(uploads) == 1

    def test_hdf5_handler_no_cache_path(self, tmp_path, monkeypatch):
        service = DatasetService(base_path=str(tmp_path))
        monkeypatch.setattr(service, "_resolve_handler", lambda _ds: service._hdf5_handler)
        monkeypatch.setattr(service._hdf5_handler, "_video_cache_path", lambda *_a, **_kw: None)
        assert service.get_video_file_path("ds", 0, "cam") is None


class TestFrameAndCameraDelegation:
    def test_get_frame_image_no_handler(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service.get_frame_image("missing", 0, 0, "cam") is None

        asyncio.run(_run())

    def test_get_episode_cameras_no_handler(self, tmp_path):
        async def _run() -> None:
            service = DatasetService(base_path=str(tmp_path))
            assert await service.get_episode_cameras("missing", 0) == []

        asyncio.run(_run())


class TestUploadVideoFailure:
    def test_upload_failure_logs_warning(self, tmp_path, caplog):
        provider = _make_provider(upload_video=AsyncMock(side_effect=RuntimeError("boom")))
        service = DatasetService(base_path=str(tmp_path), blob_provider=provider)
        cache = tmp_path / "v.mp4"
        cache.write_bytes(b"")
        with caplog.at_level("WARNING"):
            service._upload_video_to_blob("ds", 1, "cam", cache)
        assert any("Blob upload failed" in r.message for r in caplog.records)


class TestGetDatasetServiceSingleton:
    def test_singleton_creates_instance(self, monkeypatch, tmp_path):
        from src.api.services.dataset_service import service as svc_mod

        monkeypatch.setattr(svc_mod, "_dataset_service", None)

        class _Cfg:
            data_path = str(tmp_path)
            episode_cache_capacity = 4
            episode_cache_max_mb = 16

        # Stub the lazy imports inside get_dataset_service
        from src.api import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "get_app_config", lambda: _Cfg(), raising=False)
        monkeypatch.setattr(cfg_mod, "create_annotation_storage", lambda _c: None, raising=False)
        monkeypatch.setattr(cfg_mod, "create_blob_dataset_provider", lambda _c: None, raising=False)

        first = svc_mod.get_dataset_service()
        second = svc_mod.get_dataset_service()
        assert first is second
        assert isinstance(first, DatasetService)
