"""
Integration tests for DatasetService against a sample LeRobot dataset.

Tests dataset discovery, episode listing with pagination and filtering,
episode data retrieval, trajectory extraction, and capability reporting.
"""

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

    @pytest.mark.asyncio
    async def test_list_datasets_finds_sample(self, service):
        datasets = await service.list_datasets()
        ids = [d.id for d in datasets]
        assert DATASET_ID in ids

    @pytest.mark.asyncio
    async def test_get_dataset_returns_info(self, service):
        ds = await service.get_dataset(DATASET_ID)
        assert ds is not None
        assert ds.id == DATASET_ID
        assert ds.total_episodes == 64
        assert ds.fps == 30.0

    @pytest.mark.asyncio
    async def test_get_dataset_features(self, service):
        ds = await service.get_dataset(DATASET_ID)
        assert "observation.state" in ds.features
        assert "action" in ds.features
        assert "observation.images.il-camera" in ds.features

    @pytest.mark.asyncio
    async def test_get_nonexistent_dataset(self, service):
        ds = await service.get_dataset("nonexistent_dataset")
        assert ds is None

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

    @pytest.mark.asyncio
    async def test_default_list(self, service):
        episodes = await service.list_episodes(DATASET_ID)
        assert len(episodes) == 64

    @pytest.mark.asyncio
    async def test_pagination_offset(self, service):
        episodes = await service.list_episodes(DATASET_ID, offset=60, limit=10)
        assert len(episodes) == 4
        assert episodes[0].index == 60

    @pytest.mark.asyncio
    async def test_pagination_limit(self, service):
        episodes = await service.list_episodes(DATASET_ID, offset=0, limit=5)
        assert len(episodes) == 5
        assert episodes[0].index == 0
        assert episodes[4].index == 4

    @pytest.mark.asyncio
    async def test_episode_meta_fields(self, service):
        episodes = await service.list_episodes(DATASET_ID, limit=1)
        ep = episodes[0]
        assert ep.index == 0
        assert ep.length > 0
        assert ep.task_index == 0
        assert isinstance(ep.has_annotations, bool)

    @pytest.mark.asyncio
    async def test_filter_has_annotations_false(self, service):
        """With no annotations saved, all episodes should appear."""
        episodes = await service.list_episodes(DATASET_ID, has_annotations=False)
        assert len(episodes) == 64

    @pytest.mark.asyncio
    async def test_filter_task_index(self, service):
        episodes = await service.list_episodes(DATASET_ID, task_index=0)
        assert len(episodes) == 64

    @pytest.mark.asyncio
    async def test_filter_task_index_no_match(self, service):
        episodes = await service.list_episodes(DATASET_ID, task_index=99)
        assert len(episodes) == 0


class TestGetEpisode:
    """Test full episode data retrieval."""

    @pytest.mark.asyncio
    async def test_get_episode_returns_data(self, service):
        ep = await service.get_episode(DATASET_ID, 0)
        assert ep is not None
        assert ep.meta.index == 0
        assert ep.meta.length > 0

    @pytest.mark.asyncio
    async def test_episode_has_trajectory(self, service):
        ep = await service.get_episode(DATASET_ID, 0)
        assert len(ep.trajectory_data) > 0

    @pytest.mark.asyncio
    async def test_trajectory_point_fields(self, service):
        ep = await service.get_episode(DATASET_ID, 0)
        pt = ep.trajectory_data[0]
        assert pt.timestamp >= 0
        assert pt.frame >= 0
        assert len(pt.joint_positions) == 16
        assert len(pt.joint_velocities) == 16
        assert len(pt.end_effector_pose) == 6
        assert 0 <= pt.gripper_state <= 1

    @pytest.mark.asyncio
    async def test_episode_has_video_urls(self, service):
        ep = await service.get_episode(DATASET_ID, 0)
        assert "observation.images.il-camera" in ep.video_urls
        assert f"/api/datasets/{DATASET_ID}/episodes/0/video/" in ep.video_urls["observation.images.il-camera"]

    @pytest.mark.asyncio
    async def test_trajectory_length_matches_meta(self, service):
        ep = await service.get_episode(DATASET_ID, 10)
        assert ep.meta.length == len(ep.trajectory_data)


class TestTrajectory:
    """Test trajectory-only extraction."""

    @pytest.mark.asyncio
    async def test_get_trajectory(self, service):
        traj = await service.get_episode_trajectory(DATASET_ID, 0)
        assert len(traj) > 0

    @pytest.mark.asyncio
    async def test_trajectory_timestamps_increase(self, service):
        traj = await service.get_episode_trajectory(DATASET_ID, 0)
        timestamps = [pt.timestamp for pt in traj]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


class TestCameras:
    """Test camera discovery."""

    @pytest.mark.asyncio
    async def test_get_cameras(self, service):
        cameras = await service.get_episode_cameras(DATASET_ID, 0)
        assert "observation.images.il-camera" in cameras


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

    @pytest.mark.asyncio
    async def test_second_request_is_cache_hit(self, service):
        await service.get_episode(DATASET_ID, 0)
        stats_before = service._episode_cache.stats()

        await service.get_episode(DATASET_ID, 0)
        stats_after = service._episode_cache.stats()

        assert stats_after.hits == stats_before.hits + 1

    @pytest.mark.asyncio
    async def test_invalidation_forces_reload(self, service):
        await service.get_episode(DATASET_ID, 0)
        assert service._episode_cache.get(DATASET_ID, 0) is not None

        service.invalidate_episode_cache(DATASET_ID, 0)
        assert service._episode_cache.get(DATASET_ID, 0) is None

    @pytest.mark.asyncio
    async def test_prefetch_populates_adjacent_episodes(self, service):
        import asyncio

        # Discover dataset metadata first so prefetch knows total_episodes
        await service.get_dataset(DATASET_ID)
        await service.get_episode(DATASET_ID, 3)
        # Allow background prefetch task to complete
        await asyncio.sleep(1.0)

        # Episodes 1-5 should be prefetched (radius=2)
        for idx in [1, 2, 4, 5]:
            cached = service._episode_cache.get(DATASET_ID, idx)
            assert cached is not None, f"Episode {idx} should be prefetched"

    @pytest.mark.asyncio
    async def test_trajectory_served_from_cache(self, service):
        await service.get_episode(DATASET_ID, 0)
        stats_before = service._episode_cache.stats()

        traj = await service.get_episode_trajectory(DATASET_ID, 0)
        stats_after = service._episode_cache.stats()

        assert len(traj) > 0
        assert stats_after.hits == stats_before.hits + 1


class TestNestedDatasetDiscovery:
    """Test discovery of datasets nested under parent folders."""

    @pytest.mark.asyncio
    async def test_discovers_nested_hdf5_datasets(self, tmp_path):
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

    @pytest.mark.asyncio
    async def test_nested_datasets_have_group(self, tmp_path):
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

    @pytest.mark.asyncio
    async def test_nested_dataset_path_resolves(self, tmp_path):
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

    @pytest.mark.asyncio
    async def test_flat_datasets_have_no_group(self, tmp_path):
        """Standard top-level datasets should have no group."""
        (tmp_path / "flat_ds").mkdir()
        _create_minimal_hdf5(tmp_path / "flat_ds" / "episode_0.hdf5")

        service = DatasetService(base_path=str(tmp_path))
        datasets = await service.list_datasets()
        ds = next(d for d in datasets if d.id == "flat_ds")
        assert ds.group is None


class TestBlobSyncTempPrefixes:
    """Test temp-directory prefixes used for blob dataset sync."""

    @pytest.mark.asyncio
    async def test_blob_sync_prefix_excludes_path_separators(self, tmp_path, monkeypatch):
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

    @pytest.mark.asyncio
    async def test_blob_meta_sync_prefix_excludes_path_separators(self, tmp_path, monkeypatch):
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
