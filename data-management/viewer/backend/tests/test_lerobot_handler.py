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

        monkeypatch.setattr(LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: self.FFMPEG_PATH))

        def mock_run(cmd, *, capture_output=False, timeout=None):
            assert cmd[0] == self.FFMPEG_PATH
            assert "-ss" in cmd
            return sp.CompletedProcess(cmd, returncode=0, stdout=self.FAKE_JPEG, stderr=b"")

        monkeypatch.setattr(sp, "run", mock_run)

        result = LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 5, 30.0)
        assert result == self.FAKE_JPEG

    def test_returns_none_when_ffmpeg_missing(self, monkeypatch):
        monkeypatch.setattr(LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: None))
        result = LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 0, 30.0)
        assert result is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: self.FFMPEG_PATH))
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

        monkeypatch.setattr(LeRobotFormatHandler, "_resolve_ffmpeg", staticmethod(lambda: self.FFMPEG_PATH))

        captured_cmd = []

        def mock_run(cmd, *, capture_output=False, timeout=None):
            captured_cmd.extend(cmd)
            return sp.CompletedProcess(cmd, returncode=0, stdout=self.FAKE_JPEG, stderr=b"")

        monkeypatch.setattr(sp, "run", mock_run)

        LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/video.mp4", 90, 30.0)
        ss_idx = captured_cmd.index("-ss")
        assert captured_cmd[ss_idx + 1] == "3.000000"

    def test_returns_none_on_subprocess_exception(self, monkeypatch):
        import shutil
        import subprocess as sp

        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/ffmpeg")

        def boom(*a, **kw):
            raise sp.TimeoutExpired(cmd="ffmpeg", timeout=10)

        monkeypatch.setattr(sp, "run", boom)
        assert LeRobotFormatHandler._extract_frame_ffmpeg("/tmp/v.mp4", 0, 30.0) is None


# ---------------------------------------------------------------------------
# Synthetic-loader tests (no real dataset required).
# A FakeLoader is injected directly into handler._loaders to exercise the
# handler's orchestration logic without filesystem fixtures.
# ---------------------------------------------------------------------------

import numpy as np

from src.api.services.dataset_service import lerobot_handler as lh_module


class FakeLRInfo:
    def __init__(self, *, total_episodes=2, fps=30.0, robot_type="ur10e", features=None):
        self.total_episodes = total_episodes
        self.fps = fps
        self.robot_type = robot_type
        self.features = features or {
            "observation.state": {"dtype": "float32", "shape": [6]},
            "action": {"dtype": "float32", "shape": [6]},
            "observation.images.cam0": {"dtype": "video", "shape": [480, 640, 3]},
        }


class FakeLREpisode:
    def __init__(self, length=4):
        self.length = length
        self.timestamps = np.arange(length, dtype=np.float64) / 30.0
        self.frame_indices = np.arange(length, dtype=np.int64)
        self.joint_positions = np.zeros((length, 6), dtype=np.float64)
        self.joint_velocities = np.zeros((length, 6), dtype=np.float64)
        self.actions = np.zeros((length, 6), dtype=np.float64)
        self.task_index = 0
        self.video_paths = {"observation.images.cam0": "/tmp/cam0.mp4"}


class FakeLoader:
    def __init__(self, *, episodes=None, info=None, raise_on=None):
        self._episodes = episodes if episodes is not None else {0: {"length": 4}, 1: {"length": 5}}
        self._info = info if info is not None else FakeLRInfo()
        self._raise_on = raise_on or set()

    def _maybe_raise(self, name):
        if name in self._raise_on:
            raise RuntimeError(f"boom-{name}")

    def get_dataset_info(self):
        self._maybe_raise("get_dataset_info")
        return self._info

    def list_episodes_with_meta(self):
        self._maybe_raise("list_episodes_with_meta")
        return self._episodes

    def load_episode(self, idx):
        self._maybe_raise("load_episode")
        return FakeLREpisode()

    def get_video_path(self, idx, camera):
        self._maybe_raise("get_video_path")
        if camera == "missing":
            return None
        return f"/tmp/{camera}.mp4"

    def get_cameras(self):
        self._maybe_raise("get_cameras")
        return ["observation.images.cam0"]

    def get_tasks(self):
        self._maybe_raise("get_tasks")
        return {0: "pick", 1: "place"}


def _inject(handler, loader, dataset_id="ds"):
    handler._loaders[dataset_id] = loader
    return dataset_id


class TestGetLoaderSynthetic:
    def test_returns_true_when_already_loaded(self, tmp_path):
        h = LeRobotFormatHandler()
        h._loaders["ds"] = FakeLoader()
        assert h.get_loader("ds", tmp_path) is True

    def test_returns_false_when_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lh_module, "LEROBOT_AVAILABLE", False)
        h = LeRobotFormatHandler()
        assert h.get_loader("ds", tmp_path) is False

    def test_returns_false_when_path_missing(self, tmp_path):
        h = LeRobotFormatHandler()
        assert h.get_loader("ds", tmp_path / "nope") is False

    def test_returns_false_when_not_lerobot(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lh_module, "is_lerobot_dataset", lambda p: False)
        h = LeRobotFormatHandler()
        assert h.get_loader("ds", tmp_path) is False

    def test_constructs_loader_on_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lh_module, "is_lerobot_dataset", lambda p: True)
        monkeypatch.setattr(lh_module, "LeRobotLoader", lambda p: FakeLoader())
        h = LeRobotFormatHandler()
        assert h.get_loader("ds", tmp_path) is True
        assert h.has_loader("ds")

    def test_returns_false_on_constructor_exception(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lh_module, "is_lerobot_dataset", lambda p: True)

        def boom(p):
            raise RuntimeError("nope")

        monkeypatch.setattr(lh_module, "LeRobotLoader", boom)
        h = LeRobotFormatHandler()
        assert h.get_loader("ds", tmp_path) is False


class TestListEpisodesFromPath:
    def test_returns_empty_when_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lh_module, "LEROBOT_AVAILABLE", False)
        h = LeRobotFormatHandler()
        assert h.list_episodes_from_path(tmp_path) == ([], {})

    def test_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lh_module, "LeRobotLoader", lambda p: FakeLoader())
        h = LeRobotFormatHandler()
        indices, meta = h.list_episodes_from_path(tmp_path)
        assert indices == [0, 1]
        assert meta[0]["length"] == 4

    def test_returns_empty_on_exception(self, monkeypatch, tmp_path):
        def boom(p):
            raise RuntimeError("boom")

        monkeypatch.setattr(lh_module, "LeRobotLoader", boom)
        h = LeRobotFormatHandler()
        assert h.list_episodes_from_path(tmp_path) == ([], {})


class TestDiscoverSynthetic:
    def test_returns_none_when_get_loader_fails(self, tmp_path):
        h = LeRobotFormatHandler()
        assert h.discover("ds", tmp_path / "nope") is None

    def test_discover_maps_features(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        info = h.discover("ds", None)
        assert info is not None
        assert info.id == "ds"
        assert info.total_episodes == 2
        assert info.fps == 30.0
        assert "observation.state" in info.features
        assert info.features["observation.images.cam0"].dtype == "video"

    def test_discover_handles_exception(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader(raise_on={"get_dataset_info"}))
        assert h.discover("ds", None) is None


class TestListEpisodesSynthetic:
    def test_no_loader_returns_empty(self):
        h = LeRobotFormatHandler()
        assert h.list_episodes("missing") == ([], {})

    def test_success(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        indices, meta = h.list_episodes("ds")
        assert indices == [0, 1]
        assert meta[1]["length"] == 5

    def test_exception_returns_empty(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader(raise_on={"list_episodes_with_meta"}))
        assert h.list_episodes("ds") == ([], {})


class TestLoadEpisodeSynthetic:
    def test_no_loader_returns_none(self):
        h = LeRobotFormatHandler()
        assert h.load_episode("missing", 0) is None

    def test_success_basic(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        ep = h.load_episode("ds", 0)
        assert ep is not None
        assert ep.meta.index == 0
        assert ep.meta.length == 4
        assert "observation.images.cam0" in ep.video_urls
        assert ep.video_urls["observation.images.cam0"].endswith("/observation.images.cam0")
        assert len(ep.trajectory_data) == 4

    def test_dataset_info_adds_blob_video_urls(self):
        from src.api.models.datasources import DatasetInfo, FeatureSchema

        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        ds_info = DatasetInfo(
            id="ds",
            name="ds",
            total_episodes=1,
            fps=30.0,
            features={
                "observation.images.cam0": FeatureSchema(dtype="video", shape=[480, 640, 3]),
                "observation.images.blob_only": FeatureSchema(dtype="video", shape=[480, 640, 3]),
                "action": FeatureSchema(dtype="float32", shape=[6]),
            },
            tasks=[],
        )
        ep = h.load_episode("ds", 0, dataset_info=ds_info)
        assert ep is not None
        assert "observation.images.blob_only" in ep.video_urls
        assert "action" not in ep.video_urls

    def test_exception_returns_none(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader(raise_on={"load_episode"}))
        assert h.load_episode("ds", 0) is None


class TestGetTrajectorySynthetic:
    def test_no_loader_returns_empty(self):
        h = LeRobotFormatHandler()
        assert h.get_trajectory("missing", 0) == []

    def test_success(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        traj = h.get_trajectory("ds", 0)
        assert len(traj) == 4

    def test_exception_returns_empty(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader(raise_on={"load_episode"}))
        assert h.get_trajectory("ds", 0) == []


class TestGetFrameImageSynthetic:
    def test_no_loader_returns_none(self):
        h = LeRobotFormatHandler()
        assert h.get_frame_image("missing", 0, 0, "cam0") is None

    def test_no_video_returns_none(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        assert h.get_frame_image("ds", 0, 0, "missing") is None

    def test_ffmpeg_path(self, monkeypatch):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        monkeypatch.setattr(
            LeRobotFormatHandler,
            "_extract_frame_ffmpeg",
            staticmethod(lambda *a, **kw: b"JPEG"),
        )
        assert h.get_frame_image("ds", 0, 0, "cam0") == b"JPEG"

    def test_cv2_fallback_path(self, monkeypatch):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        monkeypatch.setattr(
            LeRobotFormatHandler,
            "_extract_frame_ffmpeg",
            staticmethod(lambda *a, **kw: None),
        )
        monkeypatch.setattr(
            LeRobotFormatHandler,
            "_extract_frame_cv2",
            staticmethod(lambda *a, **kw: b"CV2"),
        )
        assert h.get_frame_image("ds", 0, 0, "cam0") == b"CV2"


class TestExtractFrameCv2:
    def test_returns_none_when_imports_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name in ("cv2", "PIL"):
                raise ImportError(name)
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert LeRobotFormatHandler._extract_frame_cv2("/tmp/v.mp4", 0) is None

    def test_returns_none_when_read_fails(self, monkeypatch):
        import sys
        import types

        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_POS_FRAMES=1,
            COLOR_BGR2RGB=4,
            cvtColor=lambda f, c: f,
            VideoCapture=lambda path: types.SimpleNamespace(
                set=lambda *a: None,
                read=lambda: (False, None),
                release=lambda: None,
            ),
        )
        fake_pil = types.ModuleType("PIL")
        fake_pil.Image = types.SimpleNamespace(fromarray=lambda x: None)

        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
        monkeypatch.setitem(sys.modules, "PIL", fake_pil)
        assert LeRobotFormatHandler._extract_frame_cv2("/tmp/v.mp4", 0) is None

    def test_returns_jpeg_on_success(self, monkeypatch):
        import sys
        import types

        frame = np.zeros((4, 4, 3), dtype=np.uint8)

        class FakeImg:
            def save(self, buf, format, quality):
                buf.write(b"JPEGBYTES")

        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_POS_FRAMES=1,
            COLOR_BGR2RGB=4,
            cvtColor=lambda f, c: f,
            VideoCapture=lambda path: types.SimpleNamespace(
                set=lambda *a: None,
                read=lambda: (True, frame),
                release=lambda: None,
            ),
        )
        fake_pil = types.ModuleType("PIL")
        fake_pil.Image = types.SimpleNamespace(fromarray=lambda x: FakeImg())

        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
        monkeypatch.setitem(sys.modules, "PIL", fake_pil)
        assert LeRobotFormatHandler._extract_frame_cv2("/tmp/v.mp4", 0) == b"JPEGBYTES"


class TestGetCamerasGetVideoPathSynthetic:
    def test_get_cameras_no_loader(self):
        h = LeRobotFormatHandler()
        assert h.get_cameras("missing", 0) == []

    def test_get_cameras_success(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        assert h.get_cameras("ds", 0) == ["observation.images.cam0"]

    def test_get_cameras_exception(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader(raise_on={"get_cameras"}))
        assert h.get_cameras("ds", 0) == []

    def test_get_video_path_no_loader(self):
        h = LeRobotFormatHandler()
        assert h.get_video_path("missing", 0, "cam0") is None

    def test_get_video_path_success(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        assert h.get_video_path("ds", 0, "cam0") == "/tmp/cam0.mp4"

    def test_get_video_path_returns_none_when_loader_returns_none(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader())
        assert h.get_video_path("ds", 0, "missing") is None

    def test_get_video_path_exception(self):
        h = LeRobotFormatHandler()
        _inject(h, FakeLoader(raise_on={"get_video_path"}))
        assert h.get_video_path("ds", 0, "cam0") is None


class TestResolveFfmpeg:
    """Cover the actual imageio_ffmpeg \u2192 shutil.which fallback chain."""

    IMAGEIO_BINARY = "/opt/imageio_ffmpeg/ffmpeg"
    SYSTEM_BINARY = "/usr/bin/ffmpeg"

    def test_prefers_imageio_ffmpeg(self, monkeypatch):
        """When imageio-ffmpeg is importable, its binary path wins."""
        import sys
        import types

        fake_module = types.ModuleType("imageio_ffmpeg")
        fake_module.get_ffmpeg_exe = lambda: self.IMAGEIO_BINARY
        monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_module)

        # shutil.which must not be consulted when imageio_ffmpeg succeeds.
        import shutil

        def fail_which(_name):  # pragma: no cover - guarded
            raise AssertionError("shutil.which should not be called when imageio_ffmpeg is available")

        monkeypatch.setattr(shutil, "which", fail_which)

        assert LeRobotFormatHandler._resolve_ffmpeg() == self.IMAGEIO_BINARY

    def test_falls_back_to_system_ffmpeg_when_imageio_missing(self, monkeypatch):
        """Import errors trigger the shutil.which fallback path."""
        import sys

        # Force ImportError without removing any pre-existing import.
        monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: self.SYSTEM_BINARY if name == "ffmpeg" else None)

        assert LeRobotFormatHandler._resolve_ffmpeg() == self.SYSTEM_BINARY

    def test_returns_none_when_no_binary_found(self, monkeypatch):
        """No imageio binary and no system ffmpeg yields None."""
        import sys

        monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)

        assert LeRobotFormatHandler._resolve_ffmpeg() is None
