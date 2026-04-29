"""
Integration tests for LeRobotLoader against a sample LeRobot dataset.

Tests dataset info loading, episode listing, episode data loading,
video path resolution, and camera discovery.
"""

import numpy as np
import pytest

from src.api.services.lerobot_loader import (
    LeRobotLoader,
    LeRobotLoaderError,
    is_lerobot_dataset,
)

from .conftest import TEST_DATASET_ID, TEST_DATASET_PATH


@pytest.fixture(scope="module")
def dataset_dir():
    """Path to the test LeRobot dataset directory."""
    import os

    path = os.path.join(TEST_DATASET_PATH, TEST_DATASET_ID)
    if not os.path.isdir(path):
        pytest.skip(f"LeRobot dataset not found: {path}")
    return path


@pytest.fixture(scope="module")
def loader(dataset_dir):
    return LeRobotLoader(dataset_dir)


class TestIsLerobotDataset:
    """Validate the format detection helper."""

    def test_valid_dataset(self, dataset_dir):
        assert is_lerobot_dataset(dataset_dir) is True

    def test_invalid_path(self, tmp_path):
        assert is_lerobot_dataset(tmp_path / "nonexistent") is False

    def test_missing_info_json(self, tmp_path):
        (tmp_path / "data").mkdir()
        assert is_lerobot_dataset(tmp_path) is False

    def test_missing_data_dir(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "info.json").write_text("{}")
        assert is_lerobot_dataset(tmp_path) is False


class TestDatasetInfo:
    """Test metadata loading from info.json."""

    def test_load_info(self, loader):
        info = loader.get_dataset_info()
        assert info.codebase_version == "v3.0"
        assert isinstance(info.robot_type, str) and info.robot_type
        assert info.total_episodes == 64
        assert info.total_frames == 20251
        assert info.fps == 30.0
        assert info.total_tasks == 1
        assert info.total_chunks == 64

    def test_features_contains_state(self, loader):
        info = loader.get_dataset_info()
        assert "observation.state" in info.features
        state = info.features["observation.state"]
        assert state["dtype"] == "float32"
        assert state["shape"] == [16]

    def test_features_contains_action(self, loader):
        info = loader.get_dataset_info()
        assert "action" in info.features
        action = info.features["action"]
        assert action["dtype"] == "float32"
        assert action["shape"] == [16]

    def test_features_contains_video(self, loader):
        info = loader.get_dataset_info()
        assert "observation.images.il-camera" in info.features
        cam = info.features["observation.images.il-camera"]
        assert cam["dtype"] == "video"
        assert cam["shape"] == [480, 640, 3]

    def test_data_and_video_path_templates(self, loader):
        info = loader.get_dataset_info()
        assert "{chunk_index" in info.data_path
        assert "{video_key}" in info.video_path


class TestListEpisodes:
    """Test episode enumeration."""

    def test_returns_64_episodes(self, loader):
        episodes = loader.list_episodes()
        assert episodes == list(range(64))

    def test_returns_sorted_list(self, loader):
        episodes = loader.list_episodes()
        assert episodes == sorted(episodes)


class TestLoadEpisode:
    """Test loading full episode data."""

    def test_load_first_episode(self, loader):
        ep = loader.load_episode(0)
        assert ep.episode_index == 0
        assert ep.length > 0

    def test_load_last_episode(self, loader):
        ep = loader.load_episode(63)
        assert ep.episode_index == 63
        assert ep.length > 0

    def test_timestamps_are_monotonic(self, loader):
        ep = loader.load_episode(0)
        diffs = np.diff(ep.timestamps)
        assert np.all(diffs >= 0), "timestamps must be non-decreasing"

    def test_frame_indices_sequential(self, loader):
        ep = loader.load_episode(0)
        assert ep.frame_indices[0] == 0
        diffs = np.diff(ep.frame_indices)
        assert np.all(diffs == 1), "frame indices must increment by 1"

    def test_joint_positions_shape(self, loader):
        ep = loader.load_episode(0)
        assert ep.joint_positions.ndim == 2
        assert ep.joint_positions.shape[0] == ep.length
        assert ep.joint_positions.shape[1] == 16

    def test_actions_shape(self, loader):
        ep = loader.load_episode(0)
        assert ep.actions.ndim == 2
        assert ep.actions.shape[0] == ep.length
        assert ep.actions.shape[1] == 16

    def test_task_index_is_zero(self, loader):
        ep = loader.load_episode(0)
        assert ep.task_index == 0

    def test_metadata_fields(self, loader):
        ep = loader.load_episode(0)
        assert isinstance(ep.metadata["robot_type"], str)
        assert ep.metadata["fps"] == 30.0

    def test_video_paths_resolved(self, loader):
        ep = loader.load_episode(0)
        assert "observation.images.il-camera" in ep.video_paths
        path = ep.video_paths["observation.images.il-camera"]
        assert path.exists(), f"Video file not found: {path}"
        assert path.suffix == ".mp4"

    def test_invalid_episode_raises(self, loader):
        with pytest.raises(LeRobotLoaderError):
            loader.load_episode(9999)

    def test_multiple_episodes_consistent_shapes(self, loader):
        """All episodes should have 16-dim state and action."""
        for idx in [0, 10, 30, 63]:
            ep = loader.load_episode(idx)
            assert ep.joint_positions.shape[1] == 16
            assert ep.actions.shape[1] == 16


class TestGetEpisodeInfo:
    """Test lightweight episode info retrieval."""

    def test_returns_length(self, loader):
        info = loader.get_episode_info(0)
        assert info["length"] > 0
        assert info["episode_index"] == 0

    def test_returns_fps(self, loader):
        info = loader.get_episode_info(0)
        assert info["fps"] == 30.0

    def test_returns_cameras(self, loader):
        info = loader.get_episode_info(0)
        assert "observation.images.il-camera" in info["cameras"]

    def test_consistent_with_load_episode(self, loader):
        info = loader.get_episode_info(5)
        ep = loader.load_episode(5)
        assert info["length"] == ep.length


class TestVideoAndCameras:
    """Test video path and camera discovery."""

    def test_get_video_path_existing(self, loader):
        path = loader.get_video_path(0, "observation.images.il-camera")
        assert path is not None
        assert path.exists()

    def test_get_video_path_missing_camera(self, loader):
        path = loader.get_video_path(0, "nonexistent_camera")
        assert path is None

    def test_get_cameras(self, loader):
        cameras = loader.get_cameras()
        assert cameras == ["observation.images.il-camera"]


class TestV2EpisodeLayout:
    """Validate v2.x layout (episode-per-parquet, episodes.jsonl) handling."""

    @pytest.fixture
    def v2_dataset(self, tmp_path):
        import json

        import pyarrow as pa
        import pyarrow.parquet as pq

        root = tmp_path / "v2-dataset"
        (root / "meta").mkdir(parents=True)
        (root / "data" / "chunk-000").mkdir(parents=True)
        (root / "videos" / "chunk-000" / "observation.images.front").mkdir(parents=True)

        info = {
            "codebase_version": "v2.1",
            "robot_type": "so101_follower",
            "total_episodes": 2,
            "total_frames": 5,
            "total_tasks": 1,
            "total_chunks": 1,
            "chunks_size": 1000,
            "fps": 30,
            "splits": {"train": "0:2"},
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
            "features": {
                "observation.state": {"dtype": "float32", "shape": [6]},
                "action": {"dtype": "float32", "shape": [6]},
                "observation.images.front": {"dtype": "video", "shape": [480, 640, 3]},
            },
        }
        (root / "meta" / "info.json").write_text(json.dumps(info))

        with (root / "meta" / "episodes.jsonl").open("w") as f:
            f.write(json.dumps({"episode_index": 0, "tasks": ["t"], "length": 3}) + "\n")
            f.write(json.dumps({"episode_index": 1, "tasks": ["t"], "length": 2}) + "\n")

        for ep_idx, length in [(0, 3), (1, 2)]:
            table = pa.table(
                {
                    "frame_index": list(range(length)),
                    "timestamp": [i / 30.0 for i in range(length)],
                    "episode_index": [ep_idx] * length,
                    "task_index": [0] * length,
                    "observation.state": [[0.0] * 6 for _ in range(length)],
                    "action": [[0.0] * 6 for _ in range(length)],
                }
            )
            pq.write_table(table, root / "data" / "chunk-000" / f"episode_{ep_idx:06d}.parquet")
            (root / "videos" / "chunk-000" / "observation.images.front" / f"episode_{ep_idx:06d}.mp4").write_bytes(b"")

        return root

    def test_episode_lengths_from_jsonl(self, v2_dataset):
        loader = LeRobotLoader(v2_dataset)
        meta = loader.list_episodes_with_meta()
        assert meta[0]["length"] == 3
        assert meta[1]["length"] == 2
        assert meta[0]["cameras"] == ["observation.images.front"]

    def test_load_episode_resolves_v2_paths(self, v2_dataset):
        loader = LeRobotLoader(v2_dataset)
        ep = loader.load_episode(1)
        assert ep.length == 2
        assert "observation.images.front" in ep.video_paths

    def test_get_video_path_v2(self, v2_dataset):
        loader = LeRobotLoader(v2_dataset)
        path = loader.get_video_path(0, "observation.images.front")
        assert path is not None and path.name == "episode_000000.mp4"
