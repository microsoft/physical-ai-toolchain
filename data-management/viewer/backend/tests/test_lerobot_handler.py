"""
Integration tests for LeRobotFormatHandler against a sample LeRobot dataset.

Tests handler discovery, episode listing, episode loading,
trajectory extraction, camera discovery, and video path resolution.
"""

import pytest

from src.api.services.dataset_service.lerobot_handler import LeRobotFormatHandler

from .conftest import TEST_DATASET_ID, TEST_DATASET_PATH

DATASET_ID = TEST_DATASET_ID


@pytest.fixture(scope="module")
def dataset_path():
    """Path to the test dataset directory."""
    from pathlib import Path

    path = Path(TEST_DATASET_PATH) / DATASET_ID
    if not path.is_dir():
        pytest.skip(f"LeRobot dataset not found: {path}")
    return path


@pytest.fixture
def handler(dataset_path):
    """Fresh handler with loader initialized for the test dataset."""
    h = LeRobotFormatHandler()
    assert h.get_loader(DATASET_ID, dataset_path)
    return h


class TestHandlerDetection:
    """Test format detection and loader initialization."""

    def test_available(self):
        h = LeRobotFormatHandler()
        assert h.available is True

    def test_can_handle_lerobot(self, dataset_path):
        h = LeRobotFormatHandler()
        assert h.can_handle(dataset_path) is True

    def test_cannot_handle_missing(self, tmp_path):
        h = LeRobotFormatHandler()
        assert h.can_handle(tmp_path / "nonexistent") is False

    def test_get_loader_success(self, dataset_path):
        h = LeRobotFormatHandler()
        assert h.get_loader(DATASET_ID, dataset_path) is True
        assert h.has_loader(DATASET_ID) is True

    def test_get_loader_missing(self, tmp_path):
        h = LeRobotFormatHandler()
        assert h.get_loader("fake", tmp_path / "nonexistent") is False


class TestDiscover:
    """Test dataset discovery."""

    def test_discover_returns_info(self, handler, dataset_path):
        info = handler.discover(DATASET_ID, dataset_path)
        assert info is not None
        assert info.id == DATASET_ID
        assert info.total_episodes == 64
        assert info.fps == 30.0

    def test_discover_has_features(self, handler, dataset_path):
        info = handler.discover(DATASET_ID, dataset_path)
        assert "observation.state" in info.features
        assert "action" in info.features


class TestListEpisodes:
    """Test episode listing."""

    def test_returns_indices_and_meta(self, handler):
        indices, meta = handler.list_episodes(DATASET_ID)
        assert len(indices) == 64
        assert 0 in meta
        assert meta[0]["length"] > 0

    def test_indices_sorted(self, handler):
        indices, _ = handler.list_episodes(DATASET_ID)
        assert indices == sorted(indices)


class TestLoadEpisode:
    """Test full episode loading."""

    def test_returns_episode_data(self, handler):
        ep = handler.load_episode(DATASET_ID, 0)
        assert ep is not None
        assert ep.meta.index == 0
        assert ep.meta.length > 0

    def test_trajectory_populated(self, handler):
        ep = handler.load_episode(DATASET_ID, 0)
        assert len(ep.trajectory_data) == ep.meta.length

    def test_trajectory_point_fields(self, handler):
        ep = handler.load_episode(DATASET_ID, 0)
        pt = ep.trajectory_data[0]
        assert pt.timestamp >= 0
        assert pt.frame >= 0
        assert len(pt.joint_positions) > 0
        assert len(pt.end_effector_pose) == 6

    def test_video_urls(self, handler):
        ep = handler.load_episode(DATASET_ID, 0)
        assert "observation.images.il-camera" in ep.video_urls


class TestGetTrajectory:
    """Test trajectory-only extraction."""

    def test_returns_points(self, handler):
        traj = handler.get_trajectory(DATASET_ID, 0)
        assert len(traj) > 0

    def test_timestamps_non_decreasing(self, handler):
        traj = handler.get_trajectory(DATASET_ID, 0)
        timestamps = [pt.timestamp for pt in traj]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


class TestCamerasAndVideo:
    """Test camera and video path resolution."""

    def test_get_cameras(self, handler):
        cameras = handler.get_cameras(DATASET_ID, 0)
        assert "observation.images.il-camera" in cameras

    def test_get_video_path(self, handler):
        path = handler.get_video_path(DATASET_ID, 0, "observation.images.il-camera")
        assert path is not None
        assert path.endswith(".mp4")

    def test_get_video_path_missing_camera(self, handler):
        path = handler.get_video_path(DATASET_ID, 0, "fake_camera")
        assert path is None


class TestFfmpegExtraction:
    """Test ffmpeg-based frame extraction."""

    FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-data"
    FFMPEG_PATH = "/usr/bin/ffmpeg"

    def test_successful_extraction(self, monkeypatch):
        """Verify _extract_frame_ffmpeg returns stdout bytes on success."""
        import subprocess as sp

        monkeypatch.setattr(
            LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: self.FFMPEG_PATH)
        )

        def mock_run(cmd, *, capture_output=False, timeout=None):
            assert cmd[0] == self.FFMPEG_PATH
            assert "-ss" in cmd
            return sp.CompletedProcess(cmd, returncode=0, stdout=self.FAKE_JPEG, stderr=b"")

        monkeypatch.setattr(sp, "run", mock_run)

        result = LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 5, 30.0)
        assert result == self.FAKE_JPEG

    def test_returns_none_when_ffmpeg_missing(self, monkeypatch):
        monkeypatch.setattr(
            LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: None)
        )
        result = LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 0, 30.0)
        assert result is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: self.FFMPEG_PATH)
        )
        monkeypatch.setattr(
            sp,
            "run",
            lambda *a, **kw: sp.CompletedProcess(a[0], returncode=1, stdout=b"", stderr=b"error"),
        )

        result = LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 0, 30.0)
        assert result is None

    def test_seek_time_calculation(self, monkeypatch):
        """Verify frame_idx / fps produces correct -ss argument."""
        import subprocess as sp

        monkeypatch.setattr(
            LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: self.FFMPEG_PATH)
        )

        captured_cmd = []

        def mock_run(cmd, *, capture_output=False, timeout=None):
            captured_cmd.extend(cmd)
            return sp.CompletedProcess(cmd, returncode=0, stdout=self.FAKE_JPEG, stderr=b"")

        monkeypatch.setattr(sp, "run", mock_run)

        LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 90, 30.0)
        ss_idx = captured_cmd.index("-ss")
        assert captured_cmd[ss_idx + 1] == "3.000000"
