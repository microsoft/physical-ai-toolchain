"""
Integration tests for LeRobotLoader against a sample LeRobot dataset.

Tests dataset info loading, episode listing, episode data loading,
video path resolution, and camera discovery.
"""

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.services.lerobot_loader import (
    LeRobotEpisodeData,
    LeRobotLoader,
    LeRobotLoaderError,
    _column_to_numpy,
    get_lerobot_loader,
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


# ---------------------------------------------------------------------------
# Synthetic dataset tests (no external sample required)
# ---------------------------------------------------------------------------


def _default_features(joint_dim: int = 6, include_velocity: bool = False, video_keys=()):
    features = {
        "observation.state": {"dtype": "float32", "shape": [joint_dim]},
        "action": {"dtype": "float32", "shape": [joint_dim]},
    }
    if include_velocity:
        features["observation.velocity"] = {"dtype": "float32", "shape": [joint_dim]}
    for key in video_keys:
        features[key] = {"dtype": "video", "shape": [3, 240, 320]}
    return features


def _write_info(
    base: Path,
    *,
    total_episodes: int = 1,
    total_chunks: int = 1,
    chunks_size: int = 1000,
    fps: float = 30.0,
    features: dict | None = None,
    extra: dict | None = None,
) -> None:
    info = {
        "codebase_version": "v2.0",
        "robot_type": "synthetic-arm",
        "total_episodes": total_episodes,
        "total_frames": 0,
        "total_tasks": 1,
        "total_chunks": total_chunks,
        "chunks_size": chunks_size,
        "fps": fps,
        "splits": {},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": features if features is not None else _default_features(),
    }
    if extra:
        info.update(extra)
    meta = base / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "info.json").write_text(json.dumps(info))


def _write_episode_parquet(
    base: Path,
    *,
    episode_index: int = 0,
    chunk_index: int = 0,
    file_index: int = 0,
    length: int = 4,
    joint_dim: int = 6,
    include_state: bool = True,
    state_column: str = "observation.state",
    include_action: bool = True,
    include_velocity: bool = False,
    velocity_column: str = "observation.velocity",
    task_index: int = 0,
    frame_indices: list[int] | None = None,
) -> Path:
    frames = frame_indices if frame_indices is not None else list(range(length))
    n = len(frames)
    columns: dict[str, list] = {
        "episode_index": [episode_index] * n,
        "frame_index": frames,
        "timestamp": [float(i) / 30.0 for i in frames],
        "task_index": [task_index] * n,
    }
    if include_state:
        columns[state_column] = [[float(i)] * joint_dim for i in range(n)]
    if include_action:
        columns["action"] = [[float(i) * 0.1] * joint_dim for i in range(n)]
    if include_velocity:
        columns[velocity_column] = [[float(i) * 0.01] * joint_dim for i in range(n)]
    table = pa.table(columns)
    out_dir = base / "data" / f"chunk-{chunk_index:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"file-{file_index:03d}.parquet"
    pq.write_table(table, out_path)
    return out_path


def _write_meta_episodes(
    base: Path,
    *,
    rows: list[dict],
    chunk_index: int = 0,
    file_index: int = 0,
) -> Path:
    columns = {
        "episode_index": [r["episode_index"] for r in rows],
        "length": [r["length"] for r in rows],
        "task_index": [r.get("task_index", 0) for r in rows],
    }
    table = pa.table(columns)
    out_dir = base / "meta" / "episodes" / f"chunk-{chunk_index:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"file-{file_index:03d}.parquet"
    pq.write_table(table, out_path)
    return out_path


class TestColumnToNumpy:
    def test_list_column(self):
        table = pa.table({"x": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]})
        arr = _column_to_numpy(table, "x")
        assert arr.shape == (2, 3)
        assert arr.dtype.kind == "f"

    def test_fixed_size_list_column(self):
        typ = pa.list_(pa.float32(), 3)
        table = pa.table({"x": pa.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], type=typ)})
        arr = _column_to_numpy(table, "x")
        assert arr.shape == (2, 3)

    def test_scalar_column(self):
        table = pa.table({"x": [1, 2, 3]})
        arr = _column_to_numpy(table, "x")
        assert arr.tolist() == [1, 2, 3]


class TestLeRobotLoaderError:
    def test_message_only(self):
        err = LeRobotLoaderError("boom")
        assert str(err) == "boom"
        assert err.cause is None

    def test_with_cause(self):
        cause = ValueError("inner")
        err = LeRobotLoaderError("outer", cause=cause)
        assert err.cause is cause


class TestLoadInfoSynthetic:
    def test_missing_info_json(self, tmp_path):
        loader = LeRobotLoader(str(tmp_path))
        with pytest.raises(LeRobotLoaderError):
            loader.list_episodes()

    def test_malformed_info_json(self, tmp_path):
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "info.json").write_text("{not json")
        loader = LeRobotLoader(str(tmp_path))
        with pytest.raises(LeRobotLoaderError) as excinfo:
            loader.list_episodes()
        assert excinfo.value.cause is not None

    def test_defaults_applied(self, tmp_path):
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "info.json").write_text(json.dumps({}))
        loader = LeRobotLoader(str(tmp_path))
        info = loader.get_dataset_info()
        assert info.codebase_version == "v2.0"
        assert info.robot_type == "unknown"
        assert info.total_episodes == 0
        assert info.fps == 30.0


class TestFactoryAndDetection:
    def test_get_lerobot_loader_factory(self, tmp_path):
        loader = get_lerobot_loader(str(tmp_path))
        assert isinstance(loader, LeRobotLoader)

    def test_is_lerobot_dataset_missing_info(self, tmp_path):
        (tmp_path / "data").mkdir()
        assert is_lerobot_dataset(str(tmp_path)) is False

    def test_is_lerobot_dataset_missing_data(self, tmp_path):
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "info.json").write_text("{}")
        assert is_lerobot_dataset(str(tmp_path)) is False

    def test_is_lerobot_dataset_valid(self, tmp_path):
        _write_info(tmp_path)
        _write_episode_parquet(tmp_path)
        assert is_lerobot_dataset(str(tmp_path)) is True


class TestFindEpisodeLocationSynthetic:
    def test_standard_layout(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_episode_parquet(tmp_path, episode_index=0, chunk_index=0, file_index=0)
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert data.episode_index == 0
        assert data.length == 4

    def test_scan_fallback_other_chunk(self, tmp_path):
        _write_info(tmp_path, total_episodes=2, total_chunks=2)
        # Episode 1 lives in chunk-001, but default lookup tries chunk=ep
        # which means chunk-001/file-000 — write it there to exercise scan.
        # Use chunk 0 to host episode 1 to force scan past initial guess.
        _write_episode_parquet(tmp_path, episode_index=1, chunk_index=0, file_index=1, length=2)
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(1)
        assert data.episode_index == 1
        assert data.length == 2

    def test_episode_not_found(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        # No parquet files written
        (tmp_path / "data").mkdir()
        loader = LeRobotLoader(str(tmp_path))
        with pytest.raises(LeRobotLoaderError):
            loader.load_episode(0)


class TestListEpisodesWithMetaSynthetic:
    def test_meta_episodes_read(self, tmp_path):
        _write_info(tmp_path, total_episodes=2)
        _write_meta_episodes(
            tmp_path,
            rows=[
                {"episode_index": 0, "length": 5, "task_index": 0},
                {"episode_index": 1, "length": 7, "task_index": 1},
            ],
        )
        loader = LeRobotLoader(str(tmp_path))
        meta = loader.list_episodes_with_meta()
        assert meta[0]["length"] == 5
        assert meta[1]["task_index"] == 1

    def test_zero_fill_fallback(self, tmp_path):
        _write_info(tmp_path, total_episodes=3)
        loader = LeRobotLoader(str(tmp_path))
        meta = loader.list_episodes_with_meta()
        assert set(meta.keys()) == {0, 1, 2}
        assert all(m["length"] == 0 for m in meta.values())

    def test_cache_reuse(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_meta_episodes(tmp_path, rows=[{"episode_index": 0, "length": 3, "task_index": 0}])
        loader = LeRobotLoader(str(tmp_path))
        first = loader.list_episodes_with_meta()
        second = loader.list_episodes_with_meta()
        assert first is second


class TestLoadEpisodeSynthetic:
    def test_happy_path_observation_state(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_episode_parquet(tmp_path, length=3)
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert isinstance(data, LeRobotEpisodeData)
        assert data.length == 3
        assert data.joint_positions.shape == (3, 6)
        assert data.actions.shape == (3, 6)
        assert data.joint_velocities is None

    def test_qpos_alias_when_state_missing(self, tmp_path):
        features = _default_features()
        features.pop("observation.state")
        features["qpos"] = {"dtype": "float32", "shape": [6]}
        _write_info(tmp_path, total_episodes=1, features=features)
        _write_episode_parquet(tmp_path, length=2, include_state=True, state_column="qpos")
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert data.joint_positions.shape == (2, 6)

    def test_default_zeros_when_no_state(self, tmp_path):
        features = _default_features()
        features.pop("observation.state")
        _write_info(tmp_path, total_episodes=1, features=features)
        _write_episode_parquet(tmp_path, length=4, include_state=False)
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert data.joint_positions.shape == (4, 6)
        assert np.all(data.joint_positions == 0)

    def test_velocity_attached(self, tmp_path):
        features = _default_features(include_velocity=True)
        _write_info(tmp_path, total_episodes=1, features=features)
        _write_episode_parquet(tmp_path, length=3, include_velocity=True)
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert data.joint_velocities is not None
        assert data.joint_velocities.shape == (3, 6)

    def test_qvel_alias_when_velocity_missing(self, tmp_path):
        features = _default_features(include_velocity=True)
        features.pop("observation.velocity")
        features["qvel"] = {"dtype": "float32", "shape": [6]}
        _write_info(tmp_path, total_episodes=1, features=features)
        _write_episode_parquet(
            tmp_path,
            length=2,
            include_velocity=True,
            velocity_column="qvel",
        )
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert data.joint_velocities is not None
        assert data.joint_velocities.shape == (2, 6)

    def test_frame_index_sorted(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_episode_parquet(tmp_path, frame_indices=[3, 0, 2, 1])
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert list(data.frame_indices) == [0, 1, 2, 3]

    def test_video_paths_attached(self, tmp_path):
        features = _default_features(video_keys=("observation.images.cam",))
        _write_info(tmp_path, total_episodes=1, features=features)
        _write_episode_parquet(tmp_path, length=2)
        # create the video file so get_video_path resolves
        vid_dir = tmp_path / "videos" / "observation.images.cam" / "chunk-000"
        vid_dir.mkdir(parents=True)
        (vid_dir / "file-000.mp4").write_bytes(b"\x00")
        loader = LeRobotLoader(str(tmp_path))
        data = loader.load_episode(0)
        assert "observation.images.cam" in data.video_paths

    def test_episode_not_found_in_parquet(self, tmp_path):
        _write_info(tmp_path, total_episodes=2)
        # Write parquet for episode 0 only, but request episode 1 in same file
        _write_episode_parquet(tmp_path, episode_index=0, length=2)
        # Episode 1 lookup will land in chunk=1 path that doesn't exist → error
        loader = LeRobotLoader(str(tmp_path))
        with pytest.raises(LeRobotLoaderError):
            loader.load_episode(1)


class TestGetEpisodeInfoSynthetic:
    def test_meta_path_success(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_meta_episodes(tmp_path, rows=[{"episode_index": 0, "length": 9, "task_index": 2}])
        loader = LeRobotLoader(str(tmp_path))
        info = loader.get_episode_info(0)
        assert info["length"] == 9
        assert info["task_index"] == 2

    def test_data_parquet_fallback(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_episode_parquet(tmp_path, length=4, task_index=1)
        loader = LeRobotLoader(str(tmp_path))
        info = loader.get_episode_info(0)
        assert info["length"] == 4


class TestGetVideoPathSynthetic:
    def test_returns_none_when_missing(self, tmp_path):
        _write_info(tmp_path, total_episodes=1)
        _write_episode_parquet(tmp_path)
        loader = LeRobotLoader(str(tmp_path))
        assert loader.get_video_path(0, "missing") is None

    def test_returns_path_when_present(self, tmp_path):
        features = _default_features(video_keys=("cam0",))
        _write_info(tmp_path, total_episodes=1, features=features)
        _write_episode_parquet(tmp_path)
        vid_dir = tmp_path / "videos" / "cam0" / "chunk-000"
        vid_dir.mkdir(parents=True)
        (vid_dir / "file-000.mp4").write_bytes(b"\x00")
        loader = LeRobotLoader(str(tmp_path))
        path = loader.get_video_path(0, "cam0")
        assert path is not None
        assert path.exists()


class TestGetCamerasSynthetic:
    def test_filter_by_video_dtype(self, tmp_path):
        features = _default_features(video_keys=("cam0", "cam1"))
        _write_info(tmp_path, total_episodes=1, features=features)
        loader = LeRobotLoader(str(tmp_path))
        cams = loader.get_cameras()
        assert sorted(cams) == ["cam0", "cam1"]


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

    def test_get_tasks_jsonl(self, v2_dataset):
        """v2.x stores task metadata as ``meta/tasks.jsonl``."""
        import json

        tasks_path = v2_dataset / "meta" / "tasks.jsonl"
        with tasks_path.open("w") as f:
            f.write(json.dumps({"task_index": 0, "task": "pick the block"}) + "\n")
            f.write(json.dumps({"task_index": 1, "task": "place the block"}) + "\n")

        loader = LeRobotLoader(v2_dataset)
        tasks = loader.get_tasks()

        assert tasks == {0: "pick the block", 1: "place the block"}

    def test_get_tasks_jsonl_skips_blank_lines(self, v2_dataset):
        """Blank lines in tasks.jsonl must not raise."""
        import json

        tasks_path = v2_dataset / "meta" / "tasks.jsonl"
        with tasks_path.open("w") as f:
            f.write("\n")
            f.write(json.dumps({"task_index": 0, "task": "lift"}) + "\n")
            f.write("\n")

        loader = LeRobotLoader(v2_dataset)
        assert loader.get_tasks() == {0: "lift"}

    def test_get_tasks_jsonl_malformed_returns_empty(self, v2_dataset):
        """Malformed jsonl falls back to an empty mapping rather than raising."""
        tasks_path = v2_dataset / "meta" / "tasks.jsonl"
        tasks_path.write_text("not-valid-json\n")

        loader = LeRobotLoader(v2_dataset)
        assert loader.get_tasks() == {}

    def test_get_tasks_parquet(self, v2_dataset):
        """v3 stores task metadata as ``meta/tasks.parquet``."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        # Remove the jsonl variant so the parquet branch is exercised.
        jsonl_path = v2_dataset / "meta" / "tasks.jsonl"
        if jsonl_path.exists():
            jsonl_path.unlink()

        table = pa.table(
            {
                "task_index": [0, 1, 2],
                "task": ["align", "grasp", "release"],
            }
        )
        pq.write_table(table, v2_dataset / "meta" / "tasks.parquet")

        loader = LeRobotLoader(v2_dataset)
        tasks = loader.get_tasks()

        assert tasks == {0: "align", 1: "grasp", 2: "release"}

    def test_get_tasks_returns_empty_when_missing(self, v2_dataset):
        """No tasks metadata files means an empty dict, not an exception."""
        for name in ("tasks.jsonl", "tasks.parquet"):
            candidate = v2_dataset / "meta" / name
            if candidate.exists():
                candidate.unlink()

        loader = LeRobotLoader(v2_dataset)
        assert loader.get_tasks() == {}


class TestV2LayoutDetection:
    """Verify _is_v2_layout requires both v2.x placeholders."""

    def _info(self, data_path: str):
        from src.api.services.lerobot_loader import LeRobotDatasetInfo

        return LeRobotDatasetInfo(
            codebase_version="v2.1",
            robot_type="r",
            total_episodes=1,
            total_frames=1,
            total_tasks=1,
            total_chunks=1,
            chunks_size=1000,
            fps=30.0,
            splits={},
            data_path=data_path,
            video_path="",
            features={},
        )

    def test_detects_v2_template(self, tmp_path):
        loader = LeRobotLoader.__new__(LeRobotLoader)
        loader.base_path = tmp_path
        info = self._info("data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet")
        assert loader._is_v2_layout(info) is True

    def test_rejects_v3_template(self, tmp_path):
        loader = LeRobotLoader.__new__(LeRobotLoader)
        loader.base_path = tmp_path
        info = self._info("data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet")
        assert loader._is_v2_layout(info) is False

    def test_rejects_partial_episode_index_only(self, tmp_path):
        """A future v3 template that uses {episode_index} alone must not flip to v2."""
        loader = LeRobotLoader.__new__(LeRobotLoader)
        loader.base_path = tmp_path
        info = self._info("data/chunk-{chunk_index:03d}/episode_{episode_index:06d}.parquet")
        assert loader._is_v2_layout(info) is False
