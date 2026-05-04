"""
Unit tests for HDF5FormatHandler.

Tests handler detection, capability checks, and subdirectory episode
discovery for datasets with recording session subdirectories.
"""

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from src.api.services.dataset_service.hdf5_handler import HDF5FormatHandler
from src.api.services.hdf5_loader import HDF5Loader


def _create_minimal_hdf5(path, num_frames=10, num_joints=6):
    """Create a minimal HDF5 episode file with required datasets."""
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        data.create_dataset("qpos", data=np.zeros((num_frames, num_joints)))
        data.create_dataset("action", data=np.zeros((num_frames, num_joints)))
        f.attrs["fps"] = 30.0
        f.attrs["task_index"] = 0


def _create_hdf5_with_images(path, num_frames=10, num_joints=6, cameras=None):
    """Create an HDF5 file with image data under observations/images/."""
    cameras = cameras or ["il-camera"]
    with h5py.File(path, "w") as f:
        obs = f.create_group("observations")
        obs.create_dataset("qpos", data=np.zeros((num_frames, num_joints)))
        imgs = obs.create_group("images")
        for cam in cameras:
            imgs.create_dataset(cam, data=np.zeros((num_frames, 48, 64, 3), dtype=np.uint8))
        f.create_dataset("action", data=np.zeros((num_frames, num_joints)))
        f.attrs["fps"] = 30.0
        f.attrs["task_index"] = 0


class TestHandlerDetection:
    """Test format detection and capability."""

    def test_available_matches_import(self):
        from src.api.services.dataset_service.hdf5_handler import HDF5_AVAILABLE

        h = HDF5FormatHandler()
        assert h.available is HDF5_AVAILABLE

    def test_cannot_handle_empty_dir(self, tmp_path):
        h = HDF5FormatHandler()
        assert h.can_handle(tmp_path) is False

    def test_cannot_handle_nonexistent(self, tmp_path):
        h = HDF5FormatHandler()
        assert h.can_handle(tmp_path / "nonexistent") is False

    def test_cannot_handle_lerobot_dataset(self, tmp_path):
        """A LeRobot dataset without .hdf5 files should not match."""
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "info.json").write_text("{}")
        (tmp_path / "data").mkdir()
        h = HDF5FormatHandler()
        assert h.can_handle(tmp_path) is False

    def test_get_loader_nonexistent(self, tmp_path):
        h = HDF5FormatHandler()
        assert h.get_loader("fake", tmp_path / "nonexistent") is False


class TestListEpisodesNoData:
    """Test list_episodes when no loader is initialized."""

    def test_returns_empty(self):
        h = HDF5FormatHandler()
        indices, meta = h.list_episodes("unknown_dataset")
        assert indices == []
        assert meta == {}


class TestLoadEpisodeNoData:
    """Test load_episode when no loader is initialized."""

    def test_returns_none(self):
        h = HDF5FormatHandler()
        assert h.load_episode("unknown", 0) is None


class TestTrajectoryNoData:
    """Test get_trajectory when no loader is initialized."""

    def test_returns_empty(self):
        h = HDF5FormatHandler()
        assert h.get_trajectory("unknown", 0) == []


class TestCamerasNoData:
    """Test cameras when no loader is initialized."""

    def test_returns_empty(self):
        h = HDF5FormatHandler()
        assert h.get_cameras("unknown", 0) == []

    def test_video_path_returns_none(self):
        h = HDF5FormatHandler()
        assert h.get_video_path("unknown", 0, "cam") is None


class TestBuildTrajectory:
    """Test the shared build_trajectory utility used by both handlers."""

    def test_basic_conversion(self):
        from src.api.services.dataset_service.base import build_trajectory

        length = 3
        timestamps = np.array([0.0, 0.033, 0.066])
        joint_positions = np.zeros((3, 6))
        joint_positions[1, 0] = 1.5

        points = build_trajectory(
            length=length,
            timestamps=timestamps,
            joint_positions=joint_positions,
        )

        assert len(points) == 3
        assert points[0].timestamp == 0.0
        assert points[1].joint_positions[0] == 1.5
        assert points[0].frame == 0
        assert points[2].frame == 2

    def test_with_frame_indices(self):
        from src.api.services.dataset_service.base import build_trajectory

        points = build_trajectory(
            length=2,
            timestamps=np.array([0.0, 0.5]),
            frame_indices=np.array([10, 20]),
            joint_positions=np.zeros((2, 6)),
        )

        assert points[0].frame == 10
        assert points[1].frame == 20

    def test_optional_arrays(self):
        from src.api.services.dataset_service.base import build_trajectory

        points = build_trajectory(
            length=1,
            timestamps=np.array([0.0]),
            joint_positions=np.ones((1, 4)),
            joint_velocities=np.full((1, 4), 2.0),
            end_effector_poses=np.full((1, 6), 0.5),
            gripper_states=np.array([0.7]),
        )

        assert points[0].joint_velocities == [2.0, 2.0, 2.0, 2.0]
        assert points[0].end_effector_pose == [0.5] * 6
        assert points[0].gripper_state == pytest.approx(0.7)

    def test_clamp_gripper(self):
        from src.api.services.dataset_service.base import build_trajectory

        points = build_trajectory(
            length=2,
            timestamps=np.array([0.0, 1.0]),
            joint_positions=np.zeros((2, 6)),
            gripper_states=np.array([-0.5, 1.5]),
            clamp_gripper=True,
        )

        assert points[0].gripper_state == 0.0
        assert points[1].gripper_state == 1.0

    def test_defaults_for_missing_arrays(self):
        from src.api.services.dataset_service.base import build_trajectory

        points = build_trajectory(
            length=1,
            timestamps=np.array([0.0]),
            joint_positions=np.ones((1, 6)),
        )

        assert points[0].joint_velocities == [0.0] * 6
        assert points[0].end_effector_pose == [0.0] * 6
        assert points[0].gripper_state == 0.0

    def test_velocity_estimated_from_finite_differences(self):
        """When velocities are missing, estimate via dq/dt and pad final sample."""
        from src.api.services.dataset_service.base import build_trajectory

        length = 3
        timestamps = np.array([0.0, 1.0, 2.0])
        joint_positions = np.array(
            [
                [0.0, 0.0],
                [1.0, 2.0],
                [3.0, 6.0],
            ]
        )

        points = build_trajectory(
            length=length,
            timestamps=timestamps,
            joint_positions=joint_positions,
        )

        # Forward differences for the first two samples; last sample repeats
        # the previous diff so the array stays the same length.
        assert points[0].joint_velocities == pytest.approx([1.0, 2.0])
        assert points[1].joint_velocities == pytest.approx([2.0, 4.0])
        assert points[2].joint_velocities == pytest.approx([2.0, 4.0])

    def test_velocity_estimation_clamps_zero_dt(self):
        """Non-monotonic timestamps must not produce NaN/Inf velocities."""
        from src.api.services.dataset_service.base import build_trajectory

        timestamps = np.array([0.0, 0.0, 0.0])
        joint_positions = np.array(
            [
                [0.0, 0.0],
                [1.0, -1.0],
                [2.0, -2.0],
            ]
        )

        points = build_trajectory(
            length=3,
            timestamps=timestamps,
            joint_positions=joint_positions,
        )

        for point in points:
            for value in point.joint_velocities:
                assert np.isfinite(value)

    def test_velocity_estimation_skipped_for_single_row(self):
        """Single-frame trajectories must not crash the velocity estimator."""
        from src.api.services.dataset_service.base import build_trajectory

        points = build_trajectory(
            length=1,
            timestamps=np.array([0.0]),
            joint_positions=np.zeros((1, 6)),
        )

        assert len(points) == 1
        assert points[0].joint_velocities == [0.0] * 6

    def test_velocity_guard_handles_length_gt_positions_shape(self):
        """If length is overstated relative to joint_positions, no diff crash."""
        from src.api.services.dataset_service.base import build_trajectory

        # length>1 but joint_positions has only one row: guard must skip
        # estimation rather than IndexError on the diff-padding step.
        points = build_trajectory(
            length=1,
            timestamps=np.array([0.0]),
            joint_positions=np.ones((1, 4)),
        )

        assert points[0].joint_velocities == [0.0] * 4


class TestSubdirectoryEpisodeDiscovery:
    """
    Test that HDF5Loader does NOT merge subdirectories into a single dataset.
    Each recording session directory is its own dataset — nested discovery
    is handled at the service layer, not the loader.
    """

    def test_loader_ignores_subdirectory_files(self, tmp_path):
        """HDF5Loader should only find episodes in its base path, not subdirs."""
        session = tmp_path / "session_a"
        session.mkdir()
        _create_minimal_hdf5(session / "episode_0.hdf5", num_frames=5)

        loader = HDF5Loader(tmp_path)
        episodes = loader.list_episodes()
        assert episodes == []

    def test_loader_finds_episodes_when_pointed_at_session(self, tmp_path):
        """HDF5Loader pointed at a session directory finds its episodes."""
        _create_minimal_hdf5(tmp_path / "episode_0.hdf5", num_frames=5)
        _create_minimal_hdf5(tmp_path / "episode_1.hdf5", num_frames=8)

        loader = HDF5Loader(tmp_path)
        episodes = loader.list_episodes()
        assert episodes == [0, 1]

    def test_handler_can_handle_session_directory(self, tmp_path):
        """HDF5FormatHandler.can_handle recognizes a direct session dir."""
        _create_minimal_hdf5(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.can_handle(tmp_path) is True

    def test_handler_cannot_handle_parent_of_sessions(self, tmp_path):
        """Parent folder with only subdirectory HDF5 files should not match."""
        session = tmp_path / "session_a"
        session.mkdir()
        _create_minimal_hdf5(session / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.can_handle(tmp_path) is False

    def test_standard_layout_still_works(self, tmp_path):
        """Standard flat layout episodes should still be discovered."""
        _create_minimal_hdf5(tmp_path / "episode_0.hdf5", num_frames=10)
        _create_minimal_hdf5(tmp_path / "episode_1.hdf5", num_frames=20)

        loader = HDF5Loader(tmp_path)
        episodes = loader.list_episodes()
        assert episodes == [0, 1]

        ep = loader.load_episode(0)
        assert ep.length == 10


class TestEpisodeCameraMetadata:
    """Verify that load_episode includes camera names in metadata."""

    def test_cameras_in_metadata(self, tmp_path):
        """Episode metadata must include cameras discovered from image groups."""
        _create_hdf5_with_images(tmp_path / "episode_0.hdf5", cameras=["il-camera", "wrist-camera"])

        loader = HDF5Loader(tmp_path)
        ep = loader.load_episode(0)
        assert sorted(ep.metadata.get("cameras", [])) == ["il-camera", "wrist-camera"]

    def test_cameras_populated_for_hdf5(self, tmp_path):
        """HDF5FormatHandler.load_episode should return cameras and video_urls."""
        _create_hdf5_with_images(tmp_path / "episode_0.hdf5", cameras=["il-camera"])

        handler = HDF5FormatHandler()
        handler._loaders["test"] = HDF5Loader(tmp_path)

        episode = handler.load_episode("test", 0)
        assert episode is not None
        assert "il-camera" in episode.cameras
        assert "il-camera" in episode.video_urls


class TestVideoGeneration:
    """Tests for synchronous video generation."""

    def test_get_video_path_returns_cached(self, tmp_path):
        """get_video_path returns immediately for already-cached videos."""
        _create_hdf5_with_images(tmp_path / "episode_0.hdf5", cameras=["il-camera"])
        handler = HDF5FormatHandler()
        handler._loaders["test"] = HDF5Loader(tmp_path)

        cache_dir = tmp_path / "meta" / "videos" / "il-camera"
        cache_dir.mkdir(parents=True)
        cached_file = cache_dir / "episode_000000.mp4"
        cached_file.write_bytes(b"fake mp4")

        result = handler.get_video_path("test", 0, "il-camera")
        assert result == str(cached_file)

    def test_get_video_path_returns_none_no_loader(self):
        handler = HDF5FormatHandler()
        assert handler.get_video_path("unknown", 0, "cam") is None

    def test_video_cache_path_structure(self, tmp_path):
        _create_minimal_hdf5(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        handler._loaders["test"] = HDF5Loader(tmp_path)

        path = handler._video_cache_path("test", 0, "il-camera")
        assert path == tmp_path / "meta" / "videos" / "il-camera" / "episode_000000.mp4"


class TestVideoGenerationFallback:
    """Test video generation when cv2 is unavailable."""

    def test_cv2_fallback_returns_false_without_opencv(self, tmp_path, monkeypatch):
        """_generate_video_cv2 returns False when cv2 is not installed."""
        import builtins
        import sys

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("No module named 'cv2'")
            return real_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "cv2", raising=False)
        monkeypatch.setattr(builtins, "__import__", mock_import)

        from src.api.services.dataset_service.hdf5_handler import _generate_video_cv2

        images = np.zeros((5, 48, 64, 3), dtype=np.uint8)
        result = _generate_video_cv2(images, tmp_path / "out.mp4", 30.0)
        assert result is False
