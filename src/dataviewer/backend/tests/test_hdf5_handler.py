"""
Unit tests for HDF5FormatHandler.

Tests handler detection, capability checks, and subdirectory episode
discovery for datasets with recording session subdirectories.
"""

import threading
from collections.abc import Callable

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


class TestVideoGenerationQueue:
    """Tests for priority-based video generation queue."""

    def test_processes_higher_priority_first(self, tmp_path):
        """Priority 0 items are processed before priority 2 items."""
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        results: list[int] = []
        barrier = threading.Event()

        def blocking_gen() -> bool:
            barrier.wait(timeout=5)
            return True

        def make_recorder(idx: int) -> Callable[[], bool]:
            def gen() -> bool:
                results.append(idx)
                return True

            return gen

        block_path = tmp_path / "block.mp4"
        q.submit("ds", 99, "cam", VideoGenerationQueue.PRIORITY_USER, block_path, blocking_gen)

        p2_path = tmp_path / "bulk.mp4"
        q.submit("ds", 0, "cam", VideoGenerationQueue.PRIORITY_BULK, p2_path, make_recorder(2))

        p0_path = tmp_path / "user.mp4"
        q.submit("ds", 1, "cam", VideoGenerationQueue.PRIORITY_USER, p0_path, make_recorder(0))

        barrier.set()

        import time

        deadline = time.monotonic() + 5
        while len(results) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert results == [0, 2]

    def test_duplicate_submissions_return_same_event(self, tmp_path):
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        path = tmp_path / "test.mp4"
        gen = lambda: True

        e1 = q.submit("ds", 0, "cam", 2, path, gen)
        e2 = q.submit("ds", 0, "cam", 0, path, gen)
        assert e1 is e2

    def test_returns_set_event_for_existing_file(self, tmp_path):
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        path = tmp_path / "cached.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake mp4")

        event = q.submit("ds", 0, "cam", 0, path, lambda: True)
        assert event.is_set()

    def test_cancel_dataset_skips_queued_items(self, tmp_path):
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        generated = []
        barrier = threading.Event()
        blocker_started = threading.Event()

        def blocking_gen() -> bool:
            blocker_started.set()
            barrier.wait(5)
            return True

        block_path = tmp_path / "block.mp4"
        q.submit("other", 0, "cam", 0, block_path, blocking_gen)

        blocker_started.wait(timeout=5)

        cancel_path = tmp_path / "cancel.mp4"
        q.submit("cancel_me", 0, "cam", 2, cancel_path, lambda: generated.append(1) or True)

        q.cancel_dataset("cancel_me")
        barrier.set()

        import time

        time.sleep(0.2)

        assert not cancel_path.exists()
        assert generated == []

    def test_cancel_dataset_blocks_on_in_progress(self, tmp_path):
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        gen_started = threading.Event()
        gen_gate = threading.Event()

        def slow_gen() -> bool:
            gen_started.set()
            gen_gate.wait(timeout=5)
            return True

        path = tmp_path / "slow.mp4"
        q.submit("slow_ds", 0, "cam", 0, path, slow_gen)

        gen_started.wait(timeout=5)
        assert q._in_progress_dataset == "slow_ds"

        cancel_done = threading.Event()

        def do_cancel():
            q.cancel_dataset("slow_ds")
            cancel_done.set()

        threading.Thread(target=do_cancel).start()

        import time

        time.sleep(0.1)
        assert not cancel_done.is_set()

        gen_gate.set()
        cancel_done.wait(timeout=5)
        assert cancel_done.is_set()

    def test_cancel_returns_immediately_when_not_in_progress(self):
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()

        import time

        start = time.monotonic()
        q.cancel_dataset("nonexistent")
        elapsed = time.monotonic() - start

        assert elapsed < 0.1

    def test_ensure_video_returns_cached_path(self, tmp_path):
        """_ensure_video returns immediately for already-cached videos."""
        _create_hdf5_with_images(tmp_path / "episode_0.hdf5", cameras=["il-camera"])
        handler = HDF5FormatHandler()
        handler._loaders["test"] = HDF5Loader(tmp_path)

        cache_dir = tmp_path / "meta" / "videos" / "il-camera"
        cache_dir.mkdir(parents=True)
        cached_file = cache_dir / "episode_000000.mp4"
        cached_file.write_bytes(b"fake mp4")

        result = handler._ensure_video("test", 0, "il-camera")
        assert result == str(cached_file)

    def test_schedule_bulk_skips_cached(self, tmp_path):
        """schedule_bulk_video_generation only enqueues uncached episodes."""
        _create_hdf5_with_images(tmp_path / "episode_0.hdf5", cameras=["il-camera"])
        _create_hdf5_with_images(tmp_path / "episode_1.hdf5", cameras=["il-camera"])

        handler = HDF5FormatHandler()
        handler._loaders["test"] = HDF5Loader(tmp_path)

        cache_dir = tmp_path / "meta" / "videos" / "il-camera"
        cache_dir.mkdir(parents=True)
        (cache_dir / "episode_000000.mp4").write_bytes(b"fake mp4")

        queued = handler.schedule_bulk_video_generation("test")
        assert queued == 1

    def test_on_generated_callback_fires_after_generation(self, tmp_path):
        """on_generated callback fires after successful video generation."""
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        uploaded = []

        path = tmp_path / "gen.mp4"

        def gen() -> bool:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake video")
            return True

        event = q.submit("ds", 0, "cam", 0, path, gen, on_generated=lambda: uploaded.append(str(path)))
        event.wait(timeout=5)

        assert uploaded == [str(path)]

    def test_on_generated_callback_not_called_on_failure(self, tmp_path):
        """on_generated callback does not fire when generation returns False."""
        from src.api.services.dataset_service.hdf5_handler import VideoGenerationQueue

        q = VideoGenerationQueue()
        uploaded = []

        path = tmp_path / "fail.mp4"
        event = q.submit("ds", 0, "cam", 0, path, lambda: False, on_generated=lambda: uploaded.append(1))
        event.wait(timeout=5)

        assert uploaded == []
