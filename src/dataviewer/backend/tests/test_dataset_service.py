"""
Integration tests for DatasetService against a sample LeRobot dataset.

Tests dataset discovery, episode listing with pagination and filtering,
episode data retrieval, trajectory extraction, and capability reporting.
"""

import os

import pytest

from src.api.services.dataset_service import DatasetService

from .conftest import TEST_DATASET_ID

DATASET_ID = TEST_DATASET_ID


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
