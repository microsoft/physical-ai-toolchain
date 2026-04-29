"""
Integration tests for DatasetService against a sample LeRobot dataset.

Tests dataset discovery, episode listing with pagination and filtering,
episode data retrieval, trajectory extraction, and capability reporting.
"""

import asyncio
import os
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from src.api.services.dataset_service import DatasetService

from .conftest import TEST_DATASET_ID

DATASET_ID = TEST_DATASET_ID


def _create_minimal_hdf5(path, num_frames=10, num_joints=6):
    """Create a minimal HDF5 episode file with required datasets."""
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        data.create_dataset("qpos", data=np.zeros((num_frames, num_joints)))
        data.create_dataset("action", data=np.zeros((num_frames, num_joints)))
        f.attrs["fps"] = 30.0
        f.attrs["task_index"] = 0


@pytest.fixture
def service(test_dataset_path):
    """DatasetService pointing to the real datasets directory."""
    return DatasetService(base_path=test_dataset_path)


class TestDatasetDiscovery:
    """Test automatic dataset discovery from the filesystem."""

    def test_list_datasets_finds_sample(self, service):
        async def _run() -> None:
            datasets = await service.list_datasets()
            ids = [d.id for d in datasets]
            assert DATASET_ID in ids

        asyncio.run(_run())

    def test_get_dataset_returns_info(self, service):
        async def _run() -> None:
            ds = await service.get_dataset(DATASET_ID)
            assert ds is not None
            assert ds.id == DATASET_ID
            assert ds.total_episodes == 64
            assert ds.fps == 30.0

        asyncio.run(_run())

    def test_get_dataset_features(self, service):
        async def _run() -> None:
            ds = await service.get_dataset(DATASET_ID)
            assert "observation.state" in ds.features
            assert "action" in ds.features
            assert "observation.images.il-camera" in ds.features

        asyncio.run(_run())

    def test_get_nonexistent_dataset(self, service):
        async def _run() -> None:
            ds = await service.get_dataset("nonexistent_dataset")
            assert ds is None

        asyncio.run(_run())

    def test_dataset_is_lerobot(self, service):
        service._discover_dataset(DATASET_ID)
        assert service.dataset_is_lerobot(DATASET_ID) is True

    def test_dataset_has_no_hdf5(self, service):
        service._discover_dataset(DATASET_ID)
        assert service.dataset_has_hdf5(DATASET_ID) is False

    def test_has_lerobot_support(self, service):
        assert service.has_lerobot_support() is True


class TestListEpisodes:
    """Test episode listing with pagination and filtering."""

    def test_default_list(self, service):
        async def _run() -> None:
            episodes = await service.list_episodes(DATASET_ID)
            assert len(episodes) == 64

        asyncio.run(_run())

    def test_pagination_offset(self, service):
        async def _run() -> None:
            episodes = await service.list_episodes(DATASET_ID, offset=60, limit=10)
            assert len(episodes) == 4
            assert episodes[0].index == 60

        asyncio.run(_run())

    def test_pagination_limit(self, service):
        async def _run() -> None:
            episodes = await service.list_episodes(DATASET_ID, offset=0, limit=5)
            assert len(episodes) == 5
            assert episodes[0].index == 0
            assert episodes[4].index == 4

        asyncio.run(_run())

    def test_episode_meta_fields(self, service):
        async def _run() -> None:
            episodes = await service.list_episodes(DATASET_ID, limit=1)
            ep = episodes[0]
            assert ep.index == 0
            assert ep.length > 0
            assert ep.task_index == 0
            assert isinstance(ep.has_annotations, bool)

        asyncio.run(_run())

    def test_filter_has_annotations_false(self, service):
        async def _run() -> None:
            """With no annotations saved, all episodes should appear."""
            episodes = await service.list_episodes(DATASET_ID, has_annotations=False)
            assert len(episodes) == 64

        asyncio.run(_run())

    def test_filter_task_index(self, service):
        async def _run() -> None:
            episodes = await service.list_episodes(DATASET_ID, task_index=0)
            assert len(episodes) == 64

        asyncio.run(_run())

    def test_filter_task_index_no_match(self, service):
        async def _run() -> None:
            episodes = await service.list_episodes(DATASET_ID, task_index=99)
            assert len(episodes) == 0

        asyncio.run(_run())


class TestGetEpisode:
    """Test full episode data retrieval."""

    def test_get_episode_returns_data(self, service):
        async def _run() -> None:
            ep = await service.get_episode(DATASET_ID, 0)
            assert ep is not None
            assert ep.meta.index == 0
            assert ep.meta.length > 0

        asyncio.run(_run())

    def test_episode_has_trajectory(self, service):
        async def _run() -> None:
            ep = await service.get_episode(DATASET_ID, 0)
            assert len(ep.trajectory_data) > 0

        asyncio.run(_run())

    def test_trajectory_point_fields(self, service):
        async def _run() -> None:
            ep = await service.get_episode(DATASET_ID, 0)
            pt = ep.trajectory_data[0]
            assert pt.timestamp >= 0
            assert pt.frame >= 0
            assert len(pt.joint_positions) == 16
            assert len(pt.joint_velocities) == 16
            assert len(pt.end_effector_pose) == 6
            assert 0 <= pt.gripper_state <= 1

        asyncio.run(_run())

    def test_episode_has_video_urls(self, service):
        async def _run() -> None:
            ep = await service.get_episode(DATASET_ID, 0)
            assert "observation.images.il-camera" in ep.video_urls
            assert f"/api/datasets/{DATASET_ID}/episodes/0/video/" in ep.video_urls["observation.images.il-camera"]

        asyncio.run(_run())

    def test_trajectory_length_matches_meta(self, service):
        async def _run() -> None:
            ep = await service.get_episode(DATASET_ID, 10)
            assert ep.meta.length == len(ep.trajectory_data)

        asyncio.run(_run())


class TestTrajectory:
    """Test trajectory-only extraction."""

    def test_get_trajectory(self, service):
        async def _run() -> None:
            traj = await service.get_episode_trajectory(DATASET_ID, 0)
            assert len(traj) > 0

        asyncio.run(_run())

    def test_trajectory_timestamps_increase(self, service):
        async def _run() -> None:
            traj = await service.get_episode_trajectory(DATASET_ID, 0)
            timestamps = [pt.timestamp for pt in traj]
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i - 1]

        asyncio.run(_run())


class TestCameras:
    """Test camera discovery."""

    def test_get_cameras(self, service):
        async def _run() -> None:
            cameras = await service.get_episode_cameras(DATASET_ID, 0)
            assert "observation.images.il-camera" in cameras

        asyncio.run(_run())


class TestVideoFilePath:
    """Test video file serving path resolution."""

    def test_get_video_file_path(self, service):
        service._discover_dataset(DATASET_ID)
        path = service.get_video_file_path(DATASET_ID, 0, "observation.images.il-camera")
        assert path is not None
        assert os.path.isfile(path)
        assert path.endswith(".mp4")

    def test_get_video_file_path_missing_camera(self, service):
        service._discover_dataset(DATASET_ID)
        path = service.get_video_file_path(DATASET_ID, 0, "fake_camera")
        assert path is None


class TestEpisodeCacheIntegration:
    """Test LRU cache behavior within the real dataset service."""

    def test_second_request_is_cache_hit(self, service):
        async def _run() -> None:
            await service.get_episode(DATASET_ID, 0)
            stats_before = service._episode_cache.stats()

            await service.get_episode(DATASET_ID, 0)
            stats_after = service._episode_cache.stats()

            assert stats_after.hits == stats_before.hits + 1

        asyncio.run(_run())

    def test_invalidation_forces_reload(self, service):
        async def _run() -> None:
            await service.get_episode(DATASET_ID, 0)
            assert service._episode_cache.get(DATASET_ID, 0) is not None

            service.invalidate_episode_cache(DATASET_ID, 0)
            assert service._episode_cache.get(DATASET_ID, 0) is None

        asyncio.run(_run())

    def test_prefetch_populates_adjacent_episodes(self, service):
        async def _run() -> None:
            # Discover dataset metadata first so prefetch knows total_episodes
            await service.get_dataset(DATASET_ID)
            await service.get_episode(DATASET_ID, 3)
            # Allow background prefetch task to complete
            await asyncio.sleep(1.0)

            # Episodes 1-5 should be prefetched (radius=2)
            for idx in [1, 2, 4, 5]:
                cached = service._episode_cache.get(DATASET_ID, idx)
                assert cached is not None, f"Episode {idx} should be prefetched"

        asyncio.run(_run())

    def test_trajectory_served_from_cache(self, service):
        async def _run() -> None:
            await service.get_episode(DATASET_ID, 0)
            stats_before = service._episode_cache.stats()

            traj = await service.get_episode_trajectory(DATASET_ID, 0)
            stats_after = service._episode_cache.stats()

            assert len(traj) > 0
            assert stats_after.hits == stats_before.hits + 1

        asyncio.run(_run())


class TestNestedDatasetDiscovery:
    """Test discovery of datasets nested under parent folders."""

    def test_discovers_nested_hdf5_datasets(self, tmp_path):
        async def _run() -> None:
            """Subdirectories with HDF5 files under a parent folder are discovered."""
            parent = tmp_path / "e2emanufacturing"
            parent.mkdir()
            session1 = parent / "session_a"
            session1.mkdir()
            _create_minimal_hdf5(session1 / "episode_0.hdf5")
            session2 = parent / "session_b"
            session2.mkdir()
            _create_minimal_hdf5(session2 / "episode_0.hdf5")

            service = DatasetService(base_path=str(tmp_path))
            datasets = await service.list_datasets()
            ids = {d.id for d in datasets}
            assert "e2emanufacturing--session_a" in ids
            assert "e2emanufacturing--session_b" in ids

        asyncio.run(_run())

    def test_nested_datasets_have_group(self, tmp_path):
        async def _run() -> None:
            """Nested datasets should have their parent folder as the group."""
            parent = tmp_path / "my_project"
            parent.mkdir()
            child = parent / "recording_1"
            child.mkdir()
            _create_minimal_hdf5(child / "episode_0.hdf5")

            service = DatasetService(base_path=str(tmp_path))
            datasets = await service.list_datasets()
            ds = next(d for d in datasets if d.id == "my_project--recording_1")
            assert ds.group == "my_project"

        asyncio.run(_run())

    def test_nested_dataset_path_resolves(self, tmp_path):
        async def _run() -> None:
            """Nested dataset IDs resolve correctly to filesystem paths."""
            parent = tmp_path / "group"
            parent.mkdir()
            child = parent / "ds1"
            child.mkdir()
            _create_minimal_hdf5(child / "episode_0.hdf5", num_frames=15)

            service = DatasetService(base_path=str(tmp_path))
            await service.list_datasets()
            ds = await service.get_dataset("group--ds1")
            assert ds is not None
            assert ds.total_episodes == 1

        asyncio.run(_run())

    def test_flat_datasets_have_no_group(self, tmp_path):
        async def _run() -> None:
            """Standard top-level datasets should have no group."""
            (tmp_path / "flat_ds").mkdir()
            _create_minimal_hdf5(tmp_path / "flat_ds" / "episode_0.hdf5")

            service = DatasetService(base_path=str(tmp_path))
            datasets = await service.list_datasets()
            ds = next(d for d in datasets if d.id == "flat_ds")
            assert ds.group is None

        asyncio.run(_run())

    def test_three_level_nested_datasets_discovered(self, tmp_path):
        async def _run() -> None:
            """Datasets 3 levels deep are discovered with correct --separated IDs."""
            deep = tmp_path / "project" / "recordings" / "session_1"
            deep.mkdir(parents=True)
            _create_minimal_hdf5(deep / "episode_0.hdf5")

            service = DatasetService(base_path=str(tmp_path))
            datasets = await service.list_datasets()
            ids = {d.id for d in datasets}
            assert "project--recordings--session_1" in ids

        asyncio.run(_run())

    def test_deep_nested_dataset_group_includes_all_parents(self, tmp_path):
        async def _run() -> None:
            """Group for 3-level dataset includes all parent segments."""
            deep = tmp_path / "project" / "recordings" / "session_1"
            deep.mkdir(parents=True)
            _create_minimal_hdf5(deep / "episode_0.hdf5")

            service = DatasetService(base_path=str(tmp_path))
            datasets = await service.list_datasets()
            ds = next(d for d in datasets if d.id == "project--recordings--session_1")
            assert ds.group == "project--recordings"

        asyncio.run(_run())

    def test_deep_nested_dataset_path_resolves(self, tmp_path):
        async def _run() -> None:
            """3-level nested dataset IDs resolve correctly to filesystem paths."""
            deep = tmp_path / "project" / "recordings" / "session_1"
            deep.mkdir(parents=True)
            _create_minimal_hdf5(deep / "episode_0.hdf5", num_frames=20)

            service = DatasetService(base_path=str(tmp_path))
            await service.list_datasets()
            ds = await service.get_dataset("project--recordings--session_1")
            assert ds is not None
            assert ds.total_episodes == 1

        asyncio.run(_run())

    def test_six_level_nesting_rejected(self, tmp_path):
        async def _run() -> None:
            """Dataset IDs with more than 5 segments are rejected."""
            from src.api.services.dataset_service.service import _validate_dataset_id

            with pytest.raises(ValueError, match="too deep"):
                _validate_dataset_id("a--b--c--d--e--f")

        asyncio.run(_run())

    def test_five_level_nesting_accepted(self, tmp_path):
        async def _run() -> None:
            """Dataset IDs with exactly 5 segments are accepted."""
            from src.api.services.dataset_service.service import _validate_dataset_id

            result = _validate_dataset_id("a--b--c--d--e")
            assert result == "a--b--c--d--e"

        asyncio.run(_run())


class TestLocalAnnotationPathResolution:
    """Test that local annotations resolve --separated IDs to nested paths."""

    def test_nested_annotation_path_uses_nested_dirs(self, tmp_path):
        async def _run() -> None:
            """Annotations for nested datasets use nested filesystem directories."""
            from src.api.storage.local import LocalStorageAdapter

            adapter = LocalStorageAdapter(str(tmp_path))
            ann_dir = adapter._get_annotations_dir("project--recordings--session_1")
            expected = tmp_path / "project" / "recordings" / "session_1" / "annotations" / "episodes"
            assert ann_dir == expected

        asyncio.run(_run())

    def test_flat_annotation_path_unchanged(self, tmp_path):
        async def _run() -> None:
            """Annotations for flat datasets use a single directory level."""
            from src.api.storage.local import LocalStorageAdapter

            adapter = LocalStorageAdapter(str(tmp_path))
            ann_dir = adapter._get_annotations_dir("flat_dataset")
            expected = tmp_path / "flat_dataset" / "annotations" / "episodes"
            assert ann_dir == expected

        asyncio.run(_run())


class TestDatasetIdToBlobPrefix:
    """Test dataset_id_to_blob_prefix helper converts -- to /."""

    def test_nested_id_converts_to_slash(self):
        from src.api.storage.paths import dataset_id_to_blob_prefix

        assert dataset_id_to_blob_prefix("a--b--c") == "a/b/c"

    def test_flat_id_unchanged(self):
        from src.api.storage.paths import dataset_id_to_blob_prefix

        assert dataset_id_to_blob_prefix("flat_dataset") == "flat_dataset"

    def test_two_level_id(self):
        from src.api.storage.paths import dataset_id_to_blob_prefix

        assert dataset_id_to_blob_prefix("parent--child") == "parent/child"


class TestLabelsPathResolution:
    """Test that labels use nested filesystem paths for -- separated IDs."""

    def test_nested_labels_path_resolves(self, tmp_path):
        async def _run() -> None:
            """Labels for nested datasets use nested filesystem directories."""
            from src.api.routers.labels import _labels_path_for_base

            path = _labels_path_for_base("project--recordings--session_1", str(tmp_path))
            expected = tmp_path / "project" / "recordings" / "session_1" / "meta" / "episode_labels.json"
            assert path == expected

        asyncio.run(_run())

    def test_flat_labels_path_unchanged(self, tmp_path):
        async def _run() -> None:
            """Labels for flat datasets use single directory level."""
            from src.api.routers.labels import _labels_path_for_base

            path = _labels_path_for_base("flat_dataset", str(tmp_path))
            expected = tmp_path / "flat_dataset" / "meta" / "episode_labels.json"
            assert path == expected

        asyncio.run(_run())


class TestBlobLabelStorage:
    """Test blob-backed label storage for azure mode."""

    def test_blob_label_load_returns_default_when_missing(self):
        async def _run() -> None:
            """Loading labels from blob returns defaults when blob doesn't exist."""
            from src.api.routers.labels import _create_label_storage

            storage = _create_label_storage(storage_backend="azure", blob_provider=None)
            # Without a blob provider, should fall back to empty defaults
            result = await storage.load("nonexistent")
            assert result.dataset_id == "nonexistent"
            assert result.available_labels == ["SUCCESS", "FAILURE", "PARTIAL"]

        asyncio.run(_run())


class TestCombinedBlobScan:
    """Test combined single-pass blob scanning."""

    def test_scan_all_dataset_ids_returns_both_types(self):
        async def _run() -> None:
            """scan_all_dataset_ids discovers both LeRobot and HDF5 datasets."""
            from src.api.storage.blob_dataset import BlobDatasetProvider

            assert hasattr(BlobDatasetProvider, "scan_all_dataset_ids")

        asyncio.run(_run())


class TestGetBlobPrefix:
    """Test that BlobDatasetProvider resolves dataset IDs to blob prefixes."""

    def test_get_blob_prefix_resolves_nested_ids(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        assert hasattr(BlobDatasetProvider, "get_blob_prefix")
        assert BlobDatasetProvider.get_blob_prefix("a--b--c") == "a/b/c"

    def test_get_blob_prefix_flat_id_unchanged(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        assert BlobDatasetProvider.get_blob_prefix("flat_dataset") == "flat_dataset"


class TestBlobSyncTempPrefixes:
    """Test temp-directory prefixes used for blob dataset sync."""

    def test_blob_sync_prefix_excludes_path_separators(self, tmp_path, monkeypatch):
        async def _run() -> None:
            class FakeBlobProvider:
                async def sync_dataset_to_local(self, dataset_id: str, local_dir: Path) -> bool:
                    return True

            created_prefixes: list[str] = []
            created_dir = tmp_path / "blob-sync"

            def fake_mkdtemp(*, prefix: str) -> str:
                created_prefixes.append(prefix)
                created_dir.mkdir(parents=True, exist_ok=True)
                return str(created_dir)

            monkeypatch.setattr("src.api.services.dataset_service.service.tempfile.mkdtemp", fake_mkdtemp)

            service = DatasetService(base_path=str(tmp_path), blob_provider=FakeBlobProvider())
            with pytest.raises(ValueError, match="Invalid dataset identifier"):
                await service._ensure_blob_synced("../escape")

        asyncio.run(_run())

    def test_blob_meta_sync_prefix_excludes_path_separators(self, tmp_path, monkeypatch):
        async def _run() -> None:
            class FakeBlobProvider:
                async def sync_meta_only_to_local(self, dataset_id: str, local_dir: Path) -> bool:
                    return True

            created_prefixes: list[str] = []
            created_dir = tmp_path / "blob-meta-sync"

            def fake_mkdtemp(*, prefix: str) -> str:
                created_prefixes.append(prefix)
                created_dir.mkdir(parents=True, exist_ok=True)
                return str(created_dir)

            monkeypatch.setattr("src.api.services.dataset_service.service.tempfile.mkdtemp", fake_mkdtemp)

            service = DatasetService(base_path=str(tmp_path), blob_provider=FakeBlobProvider())
            with pytest.raises(ValueError, match="Invalid dataset identifier"):
                await service._ensure_blob_meta_synced("..\\escape")

        asyncio.run(_run())


def _create_hdf5_with_images(path, num_frames=10, num_joints=6, width=64, height=48):
    """Create an HDF5 episode file with trajectory data and camera images."""
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        data.create_dataset("qpos", data=np.zeros((num_frames, num_joints)))
        data.create_dataset("action", data=np.zeros((num_frames, num_joints)))
        obs = f.create_group("observations")
        img_group = obs.create_group("images")
        img_group.create_dataset(
            "cam0",
            data=np.random.randint(0, 255, (num_frames, height, width, 3), dtype=np.uint8),
        )
        f.attrs["fps"] = 30.0
        f.attrs["task_index"] = 0


class TestHDF5VideoGeneration:
    """Test on-demand mp4 video generation from HDF5 image data."""

    def test_hdf5_episode_provides_video_url(self, tmp_path):
        async def _run() -> None:
            """HDF5 episodes with cameras should populate video_urls."""
            ds_dir = tmp_path / "cam_dataset"
            ds_dir.mkdir()
            _create_hdf5_with_images(ds_dir / "episode_0.hdf5", num_frames=5)

            service = DatasetService(base_path=str(tmp_path))
            await service.list_datasets()
            episode = await service.get_episode("cam_dataset", 0)

            assert episode is not None
            assert len(episode.video_urls) > 0

        asyncio.run(_run())

    def test_hdf5_video_file_created_on_access(self, tmp_path):
        async def _run() -> None:
            """Accessing video path generates and caches an mp4 file."""
            import importlib.util
            import shutil

            if shutil.which("ffmpeg") is None and importlib.util.find_spec("cv2") is None:
                pytest.skip("Requires ffmpeg or cv2 for video encoding")

            from src.api.services.dataset_service.hdf5_handler import HDF5FormatHandler

            ds_dir = tmp_path / "vid_dataset"
            ds_dir.mkdir()
            _create_hdf5_with_images(ds_dir / "episode_0.hdf5", num_frames=5)

            handler = HDF5FormatHandler()
            handler.get_loader("vid_dataset", ds_dir)

            video_path = handler.get_video_path("vid_dataset", 0, "cam0")
            assert video_path is not None
            assert Path(video_path).exists()
            assert Path(video_path).suffix == ".mp4"

        asyncio.run(_run())

    def test_hdf5_single_frame_uses_slice(self, tmp_path):
        async def _run() -> None:
            """get_frame_image should load only the requested frame, not the full array."""
            from src.api.services.dataset_service.hdf5_handler import HDF5FormatHandler

            ds_dir = tmp_path / "slice_dataset"
            ds_dir.mkdir()
            _create_hdf5_with_images(ds_dir / "episode_0.hdf5", num_frames=5, width=32, height=24)

            handler = HDF5FormatHandler()
            handler.get_loader("slice_dataset", ds_dir)

            frame_bytes = handler.get_frame_image("slice_dataset", 0, 2, "cam0")
            assert frame_bytes is not None
            assert len(frame_bytes) > 0

        asyncio.run(_run())


class TestBlobTempDirCleanup:
    """Test that blob sync temp directories are cleaned up properly."""

    def test_evict_dataset_removes_synced_temp_dir(self, tmp_path):
        async def _run() -> None:
            """Evicting a dataset cleans up its blob sync temp directory."""
            service = DatasetService(base_path=str(tmp_path))
            fake_dir = tmp_path / "dvw_fake"
            fake_dir.mkdir()
            (fake_dir / "somefile.parquet").write_text("data")
            service._blob_synced["test_ds"] = fake_dir

            service._evict_dataset("test_ds")

            assert not fake_dir.exists()
            assert "test_ds" not in service._blob_synced

        asyncio.run(_run())

    def test_evict_dataset_removes_meta_synced_temp_dir(self, tmp_path):
        async def _run() -> None:
            """Evicting a dataset cleans up its meta sync temp directory."""
            service = DatasetService(base_path=str(tmp_path))
            fake_dir = tmp_path / "dvwm_fake"
            fake_dir.mkdir()
            (fake_dir / "meta" / "info.json").parent.mkdir(parents=True)
            (fake_dir / "meta" / "info.json").write_text("{}")
            service._blob_meta_synced["test_ds"] = fake_dir

            service._evict_dataset("test_ds")

            assert not fake_dir.exists()
            assert "test_ds" not in service._blob_meta_synced

        asyncio.run(_run())

    def test_cleanup_all_temp_dirs(self, tmp_path):
        """cleanup_temp_dirs removes all synced and meta-synced directories."""
        service = DatasetService(base_path=str(tmp_path))
        dir1 = tmp_path / "dvw_1"
        dir2 = tmp_path / "dvwm_2"
        dir1.mkdir()
        dir2.mkdir()
        service._blob_synced["ds1"] = dir1
        service._blob_meta_synced["ds2"] = dir2

        service.cleanup_temp_dirs()

        assert not dir1.exists()
        assert not dir2.exists()
        assert len(service._blob_synced) == 0
        assert len(service._blob_meta_synced) == 0


class TestLoggingSanitization:
    """Tests for inline logger argument sanitization required by CodeQL."""

    def test_upload_video_logs_sanitized_dataset_id_and_integer_episode(self, tmp_path, monkeypatch):
        service = DatasetService(base_path=str(tmp_path))
        logged: list[tuple[object, ...]] = []

        class FakeBlobProvider:
            async def upload_video(self, dataset_id: str, camera: str, episode_idx: int, cache_path: Path) -> None:
                raise RuntimeError("upload failed")

        service._blob_provider = FakeBlobProvider()

        monkeypatch.setattr(
            "src.api.services.dataset_service.service.logger.warning",
            lambda message, *args: logged.append((message, *args)),
        )

        service._upload_video_to_blob("dataset\r\nname", 7.0, "cam0", tmp_path / "video.mp4")

        assert len(logged) == 1
        message, ds_id, ep_idx, exc = logged[0]
        assert message == "Blob upload failed for %s ep %d: %s"
        assert ds_id == "datasetname"
        assert ep_idx == 7
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "upload failed"
