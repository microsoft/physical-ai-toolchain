"""Public behavior tests for the LeRobot dataset format handler."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.api.models.datasources import DatasetInfo, FeatureSchema
from src.api.services.dataset_service import lerobot_handler as handler_module
from src.api.services.dataset_service.lerobot_handler import LeRobotFormatHandler
from src.api.services.lerobot_loader import LeRobotDatasetInfo, LeRobotEpisodeData

_CAMERA = "observation.images.cam0"


class FakeLoader:
    def __init__(
        self,
        base_path: Path,
        *,
        failures: set[str] | None = None,
        video_window: tuple[float, float] | None = None,
    ) -> None:
        self.base_path = base_path
        self.failures = failures or set()
        self.video_window = video_window
        self.video_path = base_path / "source.mp4"
        self.video_path.write_bytes(b"video")
        os.utime(self.video_path, (1, 1))

    def _raise_if_requested(self, operation: str) -> None:
        if operation in self.failures:
            raise RuntimeError(operation)

    def get_dataset_info(self) -> LeRobotDatasetInfo:
        self._raise_if_requested("get_dataset_info")
        return LeRobotDatasetInfo(
            codebase_version="v3.0",
            robot_type="synthetic-arm",
            total_episodes=2,
            total_frames=7,
            total_tasks=2,
            total_chunks=1,
            chunks_size=1000,
            fps=20.0,
            splits={"train": "0:2"},
            data_path="data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            features={
                "observation.state": {"dtype": "float32", "shape": [3], "names": ["a", "b", "c"]},
                "action": {"dtype": "float32", "shape": [3]},
                _CAMERA: {"dtype": "video", "shape": [48, 64, 3]},
            },
        )

    def list_episodes_with_meta(self) -> dict[int, dict[str, int]]:
        self._raise_if_requested("list_episodes_with_meta")
        return {1: {"length": 3, "task_index": 1}, 0: {"length": 4, "task_index": 0}}

    def load_episode(self, episode_index: int) -> LeRobotEpisodeData:
        self._raise_if_requested("load_episode")
        length = 4
        return LeRobotEpisodeData(
            episode_index=episode_index,
            length=length,
            timestamps=np.arange(length, dtype=np.float64) / 20.0,
            frame_indices=np.arange(length, dtype=np.int64),
            joint_positions=np.array([[0.0, 1.0, 2.0]] * length),
            joint_velocities=np.array([[0.1, 0.2, 0.3]] * length),
            actions=np.array([[0.4, 0.5, 0.6]] * length),
            additional_features={},
            task_index=1,
            video_paths={_CAMERA: self.video_path},
            metadata={"robot_type": "synthetic-arm"},
        )

    def get_video_path(self, episode_index: int, camera: str) -> Path | None:
        self._raise_if_requested("get_video_path")
        return self.video_path if camera == _CAMERA else None

    def get_video_time_window(self, episode_index: int, camera: str) -> tuple[float, float] | None:
        self._raise_if_requested("get_video_time_window")
        return self.video_window

    def get_cameras(self) -> list[str]:
        self._raise_if_requested("get_cameras")
        return [_CAMERA]

    def get_tasks(self) -> dict[int, str]:
        self._raise_if_requested("get_tasks")
        return {0: "pick", 1: "place"}


def _configured_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    failures: set[str] | None = None,
    video_window: tuple[float, float] | None = None,
) -> tuple[LeRobotFormatHandler, FakeLoader]:
    loader = FakeLoader(tmp_path, failures=failures, video_window=video_window)
    monkeypatch.setattr(handler_module, "is_lerobot_dataset", lambda path: True)
    monkeypatch.setattr(handler_module, "LeRobotLoader", lambda path: loader)
    handler = LeRobotFormatHandler()
    assert handler.get_loader("dataset", tmp_path) is True
    return handler, loader


class TestDetectionAndDiscovery:
    def test_reports_availability(self):
        assert LeRobotFormatHandler().available is True

    def test_can_handle_detected_dataset(self, monkeypatch, tmp_path):
        monkeypatch.setattr(handler_module, "is_lerobot_dataset", lambda path: path == tmp_path)

        assert LeRobotFormatHandler().can_handle(tmp_path) is True

    def test_rejects_missing_dataset(self, tmp_path):
        assert LeRobotFormatHandler().get_loader("dataset", tmp_path / "missing") is False

    def test_initializes_loader_once(self, monkeypatch, tmp_path):
        loader = FakeLoader(tmp_path)
        constructor = MagicMock(return_value=loader)
        monkeypatch.setattr(handler_module, "is_lerobot_dataset", lambda path: True)
        monkeypatch.setattr(handler_module, "LeRobotLoader", constructor)
        handler = LeRobotFormatHandler()

        assert handler.get_loader("dataset", tmp_path) is True
        assert handler.get_loader("dataset", tmp_path) is True
        assert handler.has_loader("dataset") is True
        constructor.assert_called_once_with(tmp_path)

    def test_rejects_loader_constructor_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(handler_module, "is_lerobot_dataset", lambda path: True)
        constructor = MagicMock(side_effect=RuntimeError("invalid dataset"))
        monkeypatch.setattr(handler_module, "LeRobotLoader", constructor)
        handler = LeRobotFormatHandler()

        assert handler.get_loader("dataset", tmp_path) is False
        assert handler.has_loader("dataset") is False
        constructor.assert_called_once_with(tmp_path)

    def test_lists_episodes_without_registering_loader(self, monkeypatch, tmp_path):
        loader = FakeLoader(tmp_path)
        constructor = MagicMock(return_value=loader)
        monkeypatch.setattr(handler_module, "LeRobotLoader", constructor)

        assert LeRobotFormatHandler().list_episodes_from_path(tmp_path) == (
            [0, 1],
            {1: {"length": 3, "task_index": 1}, 0: {"length": 4, "task_index": 0}},
        )
        constructor.assert_called_once_with(tmp_path)

    def test_list_from_path_failure_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            handler_module,
            "LeRobotLoader",
            MagicMock(side_effect=RuntimeError("unreadable dataset")),
        )

        assert LeRobotFormatHandler().list_episodes_from_path(tmp_path) == ([], {})

    def test_discovers_metadata_features_and_tasks(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        dataset = handler.discover("dataset", tmp_path)

        assert dataset is not None
        assert dataset.model_dump() == {
            "id": "dataset",
            "name": "dataset (synthetic-arm)",
            "group": None,
            "total_episodes": 2,
            "fps": 20.0,
            "features": {
                "observation.state": {
                    "dtype": "float32",
                    "shape": [3],
                    "names": ["a", "b", "c"],
                },
                "action": {"dtype": "float32", "shape": [3], "names": None},
                _CAMERA: {"dtype": "video", "shape": [48, 64, 3], "names": None},
            },
            "tasks": [
                {"task_index": 0, "description": "pick"},
                {"task_index": 1, "description": "place"},
            ],
        }

    def test_discovery_failure_returns_none(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, failures={"get_dataset_info"})

        assert handler.discover("dataset", tmp_path) is None


class TestEpisodeBehavior:
    def test_lists_sorted_episodes_with_metadata(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        assert handler.list_episodes("dataset") == (
            [0, 1],
            {1: {"length": 3, "task_index": 1}, 0: {"length": 4, "task_index": 0}},
        )

    def test_loads_episode_as_public_model(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        episode = handler.load_episode("dataset", 1)

        assert episode is not None
        assert episode.meta.model_dump() == {
            "index": 1,
            "length": 4,
            "task_index": 1,
            "has_annotations": False,
        }
        assert episode.cameras == [_CAMERA]
        assert episode.video_urls == {_CAMERA: "/api/datasets/dataset/episodes/1/video/observation.images.cam0?v=1"}
        assert [variable.key for variable in episode.trajectory_variables] == [
            "observation.state[0]",
            "observation.state[1]",
            "observation.state[2]",
            "action[0]",
            "action[1]",
            "action[2]",
        ]
        assert len(episode.trajectory_data) == 4
        assert episode.trajectory_data[0].model_dump() == {
            "timestamp": 0.0,
            "frame": 0,
            "joint_positions": [0.0, 1.0, 2.0],
            "joint_velocities": [0.1, 0.2, 0.3],
            "end_effector_pose": [0.4, 0.5, 0.6],
            "gripper_state": 0.0,
            "variables": {
                "observation.state[0]": 0.0,
                "observation.state[1]": 1.0,
                "observation.state[2]": 2.0,
                "action[0]": 0.4,
                "action[1]": 0.5,
                "action[2]": 0.6,
            },
        }

    def test_blob_only_video_feature_gets_url(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)
        dataset = DatasetInfo(
            id="dataset",
            name="dataset",
            total_episodes=2,
            fps=20.0,
            features={
                _CAMERA: FeatureSchema(dtype="video", shape=[48, 64, 3]),
                "observation.images.blob": FeatureSchema(dtype="video", shape=[48, 64, 3]),
            },
        )

        episode = handler.load_episode("dataset", 0, dataset_info=dataset)

        assert episode is not None
        assert episode.video_urls["observation.images.blob"] == (
            "/api/datasets/dataset/episodes/0/video/observation.images.blob"
        )

    def test_returns_empty_public_outcomes_without_loader(self):
        handler = LeRobotFormatHandler()

        assert handler.list_episodes("missing") == ([], {})
        assert handler.load_episode("missing", 0) is None
        assert handler.get_trajectory("missing", 0) == []
        assert handler.get_cameras("missing", 0) == []
        assert handler.get_video_path("missing", 0, _CAMERA) is None

    def test_trajectory_matches_loaded_episode(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        trajectory = handler.get_trajectory("dataset", 0)

        assert len(trajectory) == 4
        assert [point.frame for point in trajectory] == [0, 1, 2, 3]
        assert [point.timestamp for point in trajectory] == pytest.approx([0.0, 0.05, 0.1, 0.15])

    @pytest.mark.parametrize(
        ("operation", "expected"),
        [
            ("list_episodes_with_meta", ([], {})),
            ("load_episode", None),
        ],
    )
    def test_loader_failures_return_public_empty_outcomes(self, monkeypatch, tmp_path, operation, expected):
        handler, _ = _configured_handler(monkeypatch, tmp_path, failures={operation})

        if operation == "list_episodes_with_meta":
            result = handler.list_episodes("dataset")
        else:
            result = handler.load_episode("dataset", 0)

        assert result == expected

    def test_trajectory_failure_returns_empty(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, failures={"load_episode"})

        assert handler.get_trajectory("dataset", 0) == []

    def test_exposes_video_time_windows(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, video_window=(2.25, 3.75))

        episode = handler.load_episode("dataset", 0)

        assert episode is not None
        assert episode.video_time_windows == {_CAMERA: [2.25, 3.75]}

    def test_ignores_video_time_window_failure(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, failures={"get_video_time_window"})

        episode = handler.load_episode("dataset", 0)

        assert episode is not None
        assert episode.video_time_windows == {}


class TestVideoAndFrames:
    def test_returns_source_video_without_time_window(self, monkeypatch, tmp_path):
        handler, loader = _configured_handler(monkeypatch, tmp_path)

        assert handler.get_video_path("dataset", 0, _CAMERA) == str(loader.video_path)

    def test_generates_episode_clip_through_ffmpeg_boundary(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, video_window=(0.0, 1.0))
        cached_clip = tmp_path / "meta" / "videos" / _CAMERA / "episode_000000.mp4"
        commands: list[list[str]] = []

        def run_ffmpeg(command, *, capture_output, timeout):
            commands.append(command)
            Path(command[-1]).write_bytes(b"valid-clip")
            return subprocess.CompletedProcess(command, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setitem(
            sys.modules,
            "imageio_ffmpeg",
            SimpleNamespace(get_ffmpeg_exe=lambda: "/fake/ffmpeg"),
        )
        monkeypatch.setattr(handler_module.subprocess, "run", run_ffmpeg)

        assert handler.get_video_path("dataset", 0, _CAMERA) == str(cached_clip)
        assert cached_clip.read_bytes() == b"valid-clip"
        assert commands == [
            [
                "/fake/ffmpeg",
                "-y",
                "-ss",
                "0.000000",
                "-i",
                str(tmp_path / "source.mp4"),
                "-t",
                "1.000000",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-movflags",
                "+faststart",
                str(cached_clip.with_suffix(".tmp.mp4")),
            ]
        ]

    def test_retains_decodable_cached_clip(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, video_window=(0.0, 1.0))
        cached_clip = tmp_path / "meta" / "videos" / _CAMERA / "episode_000000.mp4"
        cached_clip.parent.mkdir(parents=True)
        cached_clip.write_bytes(b"valid")
        run_ffmpeg = MagicMock(return_value=subprocess.CompletedProcess([], returncode=0, stdout=b"", stderr=b""))
        monkeypatch.setitem(
            sys.modules,
            "imageio_ffmpeg",
            SimpleNamespace(get_ffmpeg_exe=lambda: "/fake/ffmpeg"),
        )
        monkeypatch.setattr(handler_module.subprocess, "run", run_ffmpeg)

        assert handler.get_video_path("dataset", 0, _CAMERA) == str(cached_clip)
        run_ffmpeg.assert_called_once()
        assert run_ffmpeg.call_args.args[0][-1] == "-"

    def test_returns_source_when_clip_generation_fails(self, monkeypatch, tmp_path):
        handler, loader = _configured_handler(monkeypatch, tmp_path, video_window=(0.0, 1.0))
        monkeypatch.setitem(
            sys.modules,
            "imageio_ffmpeg",
            SimpleNamespace(get_ffmpeg_exe=lambda: "/fake/ffmpeg"),
        )
        monkeypatch.setattr(
            handler_module.subprocess,
            "run",
            MagicMock(return_value=subprocess.CompletedProcess([], returncode=1, stdout=b"", stderr=b"encode failed")),
        )

        assert handler.get_video_path("dataset", 0, _CAMERA) == str(loader.video_path)

    def test_returns_none_for_unknown_video(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        assert handler.get_video_path("dataset", 0, "unknown") is None

    def test_returns_empty_camera_outcome_on_loader_failure(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path, failures={"get_cameras"})

        assert handler.get_cameras("dataset", 0) == []

    def test_returns_available_cameras(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        assert handler.get_cameras("dataset", 0) == [_CAMERA]

    def test_frame_uses_ffmpeg_boundary(self, monkeypatch, tmp_path):
        handler, loader = _configured_handler(monkeypatch, tmp_path)
        run_ffmpeg = MagicMock(return_value=subprocess.CompletedProcess([], returncode=0, stdout=b"jpeg", stderr=b""))
        monkeypatch.setitem(
            sys.modules,
            "imageio_ffmpeg",
            SimpleNamespace(get_ffmpeg_exe=lambda: "/fake/ffmpeg"),
        )
        monkeypatch.setattr(handler_module.subprocess, "run", run_ffmpeg)

        assert handler.get_frame_image("dataset", 0, 3, _CAMERA) == b"jpeg"
        assert run_ffmpeg.call_args.args[0] == [
            "/fake/ffmpeg",
            "-ss",
            "0.150000",
            "-i",
            str(loader.video_path),
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-c:v",
            "mjpeg",
            "-q:v",
            "2",
            "pipe:1",
        ]

    def test_frame_falls_back_to_cv2_at_external_boundaries(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)
        capture = MagicMock()
        capture.read.return_value = (True, np.zeros((2, 2, 3), dtype=np.uint8))
        fake_cv2 = SimpleNamespace(
            CAP_PROP_POS_FRAMES=1,
            COLOR_BGR2RGB=2,
            VideoCapture=MagicMock(return_value=capture),
            cvtColor=MagicMock(side_effect=lambda frame, conversion: frame),
        )
        monkeypatch.setitem(
            sys.modules,
            "imageio_ffmpeg",
            SimpleNamespace(get_ffmpeg_exe=lambda: "/fake/ffmpeg"),
        )
        monkeypatch.setitem(sys.modules, "av", None)
        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
        monkeypatch.setattr(
            handler_module.subprocess,
            "run",
            MagicMock(return_value=subprocess.CompletedProcess([], returncode=1, stdout=b"", stderr=b"")),
        )

        image = handler.get_frame_image("dataset", 0, 2, _CAMERA)

        assert image is not None
        assert image[:2] == b"\xff\xd8"
        capture.set.assert_called_once_with(1, 2)
        capture.release.assert_called_once_with()

    def test_frame_returns_none_without_video(self, monkeypatch, tmp_path):
        handler, _ = _configured_handler(monkeypatch, tmp_path)

        assert handler.get_frame_image("dataset", 0, 0, "unknown") is None
