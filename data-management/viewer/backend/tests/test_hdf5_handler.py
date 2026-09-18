"""Behavior tests for the HDF5 dataset format handler."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from src.api.services.dataset_service import hdf5_handler as handler_module
from src.api.services.dataset_service.hdf5_handler import HDF5FormatHandler


def _create_episode(
    path: Path,
    *,
    length: int = 4,
    cameras: tuple[str, ...] = ("cam0",),
    fps: float = 20.0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as file:
        data = file.create_group("data")
        positions = np.arange(length * 2, dtype=np.float64).reshape(length, 2)
        data.create_dataset("qpos", data=positions)
        data.create_dataset("qvel", data=np.ones_like(positions))
        data.create_dataset("action", data=positions + 10)
        data.create_dataset("timestamps", data=np.arange(length, dtype=np.float64) / fps)
        images = file.create_group("observations/images")
        for camera in cameras:
            images.create_dataset(camera, data=np.zeros((length, 8, 8, 3), dtype=np.uint8))
        file.attrs["fps"] = fps
        file.attrs["task_index"] = 2


def _episode_data(*, length: int = 3, cameras: list[str] | None = None) -> SimpleNamespace:
    positions = np.arange(length * 2, dtype=np.float64).reshape(length, 2)
    return SimpleNamespace(
        length=length,
        timestamps=np.arange(length, dtype=np.float64) / 30.0,
        joint_positions=positions,
        joint_velocities=np.ones_like(positions),
        actions=positions + 10,
        end_effector_pose=np.zeros((length, 6)),
        gripper_states=np.linspace(0.0, 1.0, length),
        task_index=2,
        metadata={"cameras": cameras or ["cam0"], "fps": 30.0},
    )


def _handler_with_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    loader: MagicMock,
) -> HDF5FormatHandler:
    (tmp_path / "episode_0.hdf5").touch()
    monkeypatch.setattr(handler_module, "HDF5Loader", lambda _path: loader)
    handler = HDF5FormatHandler()
    assert handler.get_loader("dataset", tmp_path) is True
    return handler


def _install_cv2_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    writer_error: bool = False,
) -> SimpleNamespace:
    state = SimpleNamespace(
        fourcc_calls=[],
        writer_calls=[],
        converted_frames=[],
        written_frames=[],
        release_count=0,
    )

    class FakeVideoWriter:
        def __init__(self, path: str, fourcc: int, fps: float, size: tuple[int, int]) -> None:
            state.writer_calls.append((path, fourcc, fps, size))
            if writer_error:
                raise OSError("video writer unavailable")
            self._path = Path(path)

        def write(self, frame: np.ndarray) -> None:
            state.written_frames.append(frame.copy())

        def release(self) -> None:
            state.release_count += 1
            self._path.write_bytes(b"cv2 video")

    def video_writer_fourcc(*codec: str) -> int:
        state.fourcc_calls.append(codec)
        return 42

    def cvt_color(frame: np.ndarray, code: int) -> np.ndarray:
        state.converted_frames.append((frame.copy(), code))
        return frame[..., ::-1]

    fake_cv2 = SimpleNamespace(
        COLOR_RGB2BGR=7,
        VideoWriter=FakeVideoWriter,
        VideoWriter_fourcc=video_writer_fourcc,
        cvtColor=cvt_color,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    return state


def _expected_ffmpeg_command(output_path: Path, fps: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        "8x8",
        "-r",
        str(fps),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


class TestHandlerDetection:
    """Tests for recognizing and initializing HDF5 datasets."""

    def test_available_reports_optional_dependency_state(self) -> None:
        assert HDF5FormatHandler().available is handler_module.HDF5_AVAILABLE

    @pytest.mark.parametrize("subdirectory", [None, "data", "episodes"])
    def test_can_handle_supported_layouts(self, tmp_path: Path, subdirectory: str | None) -> None:
        dataset_path = tmp_path if subdirectory is None else tmp_path / subdirectory
        _create_episode(dataset_path / "episode_0.hdf5")

        assert HDF5FormatHandler().can_handle(tmp_path) is True

    def test_rejects_missing_empty_and_nested_session_paths(self, tmp_path: Path) -> None:
        handler = HDF5FormatHandler()
        empty_path = tmp_path / "empty"
        empty_path.mkdir()
        _create_episode(tmp_path / "session" / "episode_0.hdf5")

        assert handler.can_handle(tmp_path / "missing") is False
        assert handler.can_handle(empty_path) is False
        assert handler.can_handle(tmp_path) is False

    def test_get_loader_reports_initialization_outcomes(self, tmp_path: Path) -> None:
        handler = HDF5FormatHandler()
        empty_path = tmp_path / "empty"
        empty_path.mkdir()

        assert handler.get_loader("missing", tmp_path / "missing") is False
        assert handler.get_loader("empty", empty_path) is False

        _create_episode(tmp_path / "episode_0.hdf5")
        assert handler.get_loader("dataset", tmp_path) is True
        assert handler.has_loader("dataset") is True
        assert handler.has_loader("other") is False
        assert handler.get_loader("dataset", tmp_path / "missing") is True

    def test_rejects_dataset_when_hdf5_support_is_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        monkeypatch.setattr(handler_module, "HDF5_AVAILABLE", False)
        handler = HDF5FormatHandler()

        assert handler.available is False
        assert handler.can_handle(tmp_path) is False
        assert handler.get_loader("dataset", tmp_path) is False


class TestDatasetDiscovery:
    """Tests for dataset and episode discovery through public methods."""

    def test_discover_reports_episode_count_and_fps(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", fps=15.0)
        _create_episode(tmp_path / "episode_1.hdf5", fps=15.0)
        handler = HDF5FormatHandler()

        info = handler.discover("dataset", tmp_path)

        assert info is not None
        assert info.id == "dataset"
        assert info.total_episodes == 2
        assert info.fps == 15.0

    def test_discover_returns_none_for_non_hdf5_directory(self, tmp_path: Path) -> None:
        assert HDF5FormatHandler().discover("dataset", tmp_path) is None

    def test_discover_returns_none_when_frame_loading_is_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        monkeypatch.setattr(handler_module, "load_single_frame", None)

        assert HDF5FormatHandler().discover("dataset", tmp_path) is None

    def test_discover_reports_empty_dataset_for_unrecognized_filenames(self, tmp_path: Path) -> None:
        with h5py.File(tmp_path / "recording.hdf5", "w") as file:
            file.create_dataset("data/qpos", data=np.zeros((2, 2), dtype=np.float64))

        info = HDF5FormatHandler().discover("dataset", tmp_path)

        assert info is not None
        assert info.total_episodes == 0
        assert info.fps == 30.0

    def test_discover_counts_files_when_loader_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        _create_episode(tmp_path / "episode_1.hdf5")
        loader = MagicMock()
        loader.list_episodes.side_effect = RuntimeError("unreadable index")
        handler = _handler_with_loader(monkeypatch, tmp_path, loader)

        info = handler.discover("dataset", tmp_path)

        assert info is not None
        assert info.total_episodes == 2
        assert info.fps == 30.0

    def test_list_episodes_returns_public_metadata(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=3)
        _create_episode(tmp_path / "episode_1.hdf5", length=5)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        indices, metadata = handler.list_episodes("dataset")

        assert indices == [0, 1]
        assert metadata == {
            0: {"length": 3, "task_index": 2},
            1: {"length": 5, "task_index": 2},
        }

    def test_list_episodes_handles_loader_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        loader = MagicMock()
        loader.list_episodes.side_effect = RuntimeError("unreadable index")
        handler = _handler_with_loader(monkeypatch, tmp_path, loader)

        assert handler.list_episodes("dataset") == ([], {})
        assert handler.list_episodes("unknown") == ([], {})

    def test_list_episodes_defaults_unreadable_episode_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        loader = MagicMock()
        loader.list_episodes.return_value = [4]
        loader.get_episode_info.side_effect = RuntimeError("unreadable episode")
        handler = _handler_with_loader(monkeypatch, tmp_path, loader)

        assert handler.list_episodes("dataset") == ([4], {4: {"length": 0, "task_index": 0}})


class TestEpisodeAccess:
    """Tests for public episode, trajectory, camera, and frame behavior."""

    def test_load_episode_exposes_trajectory_and_camera_data(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=3, cameras=("cam0", "cam1"))
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        episode = handler.load_episode("dataset", 0)

        assert episode is not None
        assert episode.meta.index == 0
        assert episode.meta.length == 3
        assert episode.meta.task_index == 2
        assert episode.cameras == ["cam0", "cam1"]
        assert episode.video_urls == {
            "cam0": "/api/datasets/dataset/episodes/0/video/cam0",
            "cam1": "/api/datasets/dataset/episodes/0/video/cam1",
        }
        assert [point.frame for point in episode.trajectory_data] == [0, 1, 2]
        assert episode.trajectory_data[1].joint_positions == [2.0, 3.0]

    def test_load_episode_returns_none_when_loader_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        loader = MagicMock()
        loader.load_episode.side_effect = RuntimeError("unreadable episode")
        handler = _handler_with_loader(monkeypatch, tmp_path, loader)

        assert handler.load_episode("dataset", 0) is None
        assert handler.load_episode("unknown", 0) is None

    def test_get_trajectory_returns_episode_points(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=4)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        trajectory = handler.get_trajectory("dataset", 0)

        assert [point.frame for point in trajectory] == [0, 1, 2, 3]
        assert trajectory[2].joint_positions == [4.0, 5.0]

    def test_get_trajectory_handles_loader_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        loader = MagicMock()
        loader.load_episode.side_effect = RuntimeError("unreadable episode")
        handler = _handler_with_loader(monkeypatch, tmp_path, loader)

        assert handler.get_trajectory("dataset", 0) == []
        assert handler.get_trajectory("unknown", 0) == []

    def test_get_cameras_reports_available_cameras(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", cameras=("cam0", "wrist"))
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        assert handler.get_cameras("dataset", 0) == ["cam0", "wrist"]
        assert handler.get_cameras("unknown", 0) == []

    def test_get_cameras_handles_loader_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        loader = MagicMock()
        loader.get_episode_info.side_effect = RuntimeError("unreadable episode")
        handler = _handler_with_loader(monkeypatch, tmp_path, loader)

        assert handler.get_cameras("dataset", 0) == []

    def test_get_frame_image_returns_jpeg_for_valid_frame(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        image = handler.get_frame_image("dataset", 0, 1, "cam0")

        assert image is not None
        assert image.startswith(b"\xff\xd8\xff")

    def test_get_frame_image_returns_none_for_unavailable_frame(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        assert handler.get_frame_image("dataset", 0, 99, "cam0") is None
        assert handler.get_frame_image("dataset", 0, 0, "missing") is None
        assert handler.get_frame_image("unknown", 0, 0, "cam0") is None

    def test_get_frame_image_returns_none_when_frame_loading_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        def fail_frame_load(_path: Path, _camera: str, _frame_idx: int) -> None:
            raise OSError("unreadable frame")

        monkeypatch.setattr(handler_module, "load_single_frame", fail_frame_load)

        assert handler.get_frame_image("dataset", 0, 0, "cam0") is None


class TestVideoAccess:
    """Tests for cached and generated video paths through the handler API."""

    def test_get_video_path_returns_cached_video(self, tmp_path: Path) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        cached_file = tmp_path / "meta" / "videos" / "cam0" / "episode_000000.mp4"
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"cached video")

        assert handler.get_video_path("dataset", 0, "cam0") == str(cached_file)

    def test_get_video_path_generates_video_with_ffmpeg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=2, fps=12.5)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        expected_path = tmp_path / "meta" / "videos" / "cam0" / "episode_000000.mp4"
        which_calls: list[str] = []
        popen_calls: list[tuple[list[str], dict[str, object]]] = []
        stdin_writes: list[bytes] = []
        process_events: list[str] = []

        def fake_which(executable: str) -> str:
            which_calls.append(executable)
            return "/usr/bin/ffmpeg"

        class FakeStdin:
            def write(self, data: bytes) -> None:
                stdin_writes.append(data)
                process_events.append("write")

            def close(self) -> None:
                process_events.append("close")

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()

            def wait(self) -> int:
                process_events.append("wait")
                assert expected_path.exists() is False
                expected_path.write_bytes(b"ffmpeg video")
                return 0

        def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
            popen_calls.append((command, kwargs))
            process_events.append("popen")
            return FakeProcess()

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        result = handler.get_video_path("dataset", 0, "cam0")

        assert result == str(expected_path)
        assert expected_path.read_bytes() == b"ffmpeg video"
        assert which_calls == ["ffmpeg"]
        assert process_events == ["popen", "write", "write", "close", "wait"]
        assert stdin_writes == [bytes(8 * 8 * 3), bytes(8 * 8 * 3)]
        assert popen_calls == [
            (
                _expected_ffmpeg_command(expected_path, 12.5),
                {
                    "stdin": subprocess.PIPE,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                },
            )
        ]

    def test_get_video_path_falls_back_to_cv2_when_ffmpeg_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=2, fps=15.0)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        expected_path = tmp_path / "meta" / "videos" / "cam0" / "episode_000000.mp4"
        which_calls: list[str] = []
        cv2_state = _install_cv2_fake(monkeypatch)

        def fake_which(executable: str) -> None:
            which_calls.append(executable)
            return None

        monkeypatch.setattr(shutil, "which", fake_which)

        result = handler.get_video_path("dataset", 0, "cam0")

        assert result == str(expected_path)
        assert expected_path.read_bytes() == b"cv2 video"
        assert which_calls == ["ffmpeg"]
        assert cv2_state.fourcc_calls == [("a", "v", "c", "1")]
        assert cv2_state.writer_calls == [(str(expected_path), 42, 15.0, (8, 8))]
        assert [code for _, code in cv2_state.converted_frames] == [7, 7]
        assert len(cv2_state.written_frames) == 2
        assert cv2_state.release_count == 1

    def test_get_video_path_falls_back_to_cv2_when_ffmpeg_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=1, fps=24.0)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        expected_path = tmp_path / "meta" / "videos" / "cam0" / "episode_000000.mp4"
        cv2_state = _install_cv2_fake(monkeypatch)
        popen_calls: list[list[str]] = []

        def fail_popen(command: list[str], **_kwargs: object) -> None:
            popen_calls.append(command)
            raise OSError("ffmpeg launch failed")

        monkeypatch.setattr(shutil, "which", lambda _executable: "/usr/bin/ffmpeg")
        monkeypatch.setattr(subprocess, "Popen", fail_popen)

        result = handler.get_video_path("dataset", 0, "cam0")

        assert result == str(expected_path)
        assert expected_path.read_bytes() == b"cv2 video"
        assert popen_calls == [_expected_ffmpeg_command(expected_path, 24.0)]
        assert cv2_state.writer_calls == [(str(expected_path), 42, 24.0, (8, 8))]
        assert cv2_state.release_count == 1

    def test_get_video_path_returns_none_when_frames_are_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        monkeypatch.setattr(handler_module, "load_all_frames", lambda *_args: None)

        assert handler.get_video_path("dataset", 0, "cam0") is None
        assert handler.get_video_path("unknown", 0, "cam0") is None

    def test_get_video_path_returns_none_when_cv2_writer_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=2, fps=10.0)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        expected_path = tmp_path / "meta" / "videos" / "cam0" / "episode_000000.mp4"
        cv2_state = _install_cv2_fake(monkeypatch, writer_error=True)
        monkeypatch.setattr(shutil, "which", lambda _executable: None)

        assert handler.get_video_path("dataset", 0, "cam0") is None
        assert expected_path.exists() is False
        assert cv2_state.fourcc_calls == [("a", "v", "c", "1")]
        assert cv2_state.writer_calls == [(str(expected_path), 42, 10.0, (8, 8))]
        assert cv2_state.converted_frames == []
        assert cv2_state.written_frames == []
        assert cv2_state.release_count == 0

    def test_get_video_path_returns_none_when_video_encoders_are_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5", length=1)
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        expected_path = tmp_path / "meta" / "videos" / "cam0" / "episode_000000.mp4"
        monkeypatch.setattr(shutil, "which", lambda _executable: None)
        monkeypatch.setitem(sys.modules, "cv2", None)

        assert handler.get_video_path("dataset", 0, "cam0") is None
        assert expected_path.exists() is False

    def test_get_video_path_returns_none_when_frame_loader_is_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True
        monkeypatch.setattr(handler_module, "load_all_frames", None)

        assert handler.get_video_path("dataset", 0, "cam0") is None

    def test_get_video_path_returns_none_when_frame_loading_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _create_episode(tmp_path / "episode_0.hdf5")
        handler = HDF5FormatHandler()
        assert handler.get_loader("dataset", tmp_path) is True

        def fail_frame_load(_path: Path, _camera: str) -> None:
            raise OSError("unreadable frames")

        monkeypatch.setattr(handler_module, "load_all_frames", fail_frame_load)

        assert handler.get_video_path("dataset", 0, "cam0") is None
