"""Public behavior tests for synthetic LeRobot datasets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.services.lerobot_loader import (
    LeRobotEpisodeData,
    LeRobotLoader,
    LeRobotLoaderError,
    get_lerobot_loader,
    is_lerobot_dataset,
)

_CAMERA = "observation.images.front"


def _write_v2_dataset(root: Path) -> Path:
    dataset = root / "synthetic-lerobot"
    (dataset / "meta").mkdir(parents=True)
    (dataset / "data" / "chunk-000").mkdir(parents=True)
    (dataset / "videos" / "chunk-000" / _CAMERA).mkdir(parents=True)

    info = {
        "codebase_version": "v2.1",
        "robot_type": "synthetic-arm",
        "total_episodes": 2,
        "total_frames": 5,
        "total_tasks": 2,
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": 20.0,
        "splits": {"train": "0:2"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": {
            "observation.state": {
                "dtype": "float32",
                "shape": [3],
                "names": ["shoulder", "elbow", "wrist"],
            },
            "observation.velocity": {"dtype": "float32", "shape": [3]},
            "action": {"dtype": "float32", "shape": [3]},
            "observation.gripper.is_closed": {"dtype": "bool", "shape": [1]},
            _CAMERA: {"dtype": "video", "shape": [48, 64, 3]},
        },
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")

    with (dataset / "meta" / "episodes.jsonl").open("w", encoding="utf-8") as episodes_file:
        episodes_file.write(json.dumps({"episode_index": 0, "length": 3, "task_index": 0}) + "\n")
        episodes_file.write(json.dumps({"episode_index": 1, "length": 2, "task_index": 1}) + "\n")

    with (dataset / "meta" / "tasks.jsonl").open("w", encoding="utf-8") as tasks_file:
        tasks_file.write(json.dumps({"task_index": 0, "task": "pick"}) + "\n")
        tasks_file.write(json.dumps({"task_index": 1, "task": "place"}) + "\n")

    for episode_index, length in ((0, 3), (1, 2)):
        frame_indices = list(reversed(range(length)))
        table = pa.table(
            {
                "frame_index": frame_indices,
                "timestamp": [frame / 20.0 for frame in frame_indices],
                "episode_index": [episode_index] * length,
                "task_index": [episode_index] * length,
                "observation.state": [[float(frame), float(frame + 1), float(frame + 2)] for frame in frame_indices],
                "observation.velocity": [[0.1, 0.2, 0.3] for _ in frame_indices],
                "action": [[0.4, 0.5, 0.6] for _ in frame_indices],
                "observation.gripper.is_closed": [frame % 2 == 1 for frame in frame_indices],
            }
        )
        pq.write_table(table, dataset / "data" / "chunk-000" / f"episode_{episode_index:06d}.parquet")
        video_path = dataset / "videos" / "chunk-000" / _CAMERA / f"episode_{episode_index:06d}.mp4"
        video_path.write_bytes(f"video-{episode_index}".encode())
        os.utime(video_path, (1, 1))

    return dataset


def _write_v3_dataset(root: Path) -> Path:
    dataset = root / "synthetic-lerobot-v3"
    (dataset / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (dataset / "data" / "chunk-002").mkdir(parents=True)
    (dataset / "videos" / _CAMERA / "chunk-007").mkdir(parents=True)

    info = {
        "codebase_version": "v3.0",
        "robot_type": "synthetic-v3-arm",
        "total_episodes": 6,
        "total_frames": 2,
        "total_tasks": 2,
        "total_chunks": 3,
        "chunks_size": 1000,
        "fps": 10.0,
        "splits": {"train": "0:6"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "qpos": {"dtype": "float32", "shape": [2]},
            "qvel": {"dtype": "float32", "shape": [2]},
            _CAMERA: {"dtype": "video", "shape": [48, 64, 3]},
        },
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")

    pq.write_table(
        pa.table(
            {
                "episode_index": [5, 5],
                "frame_index": [1, 0],
                "task_index": [3, 3],
                "qpos": [[3.0, 4.0], [1.0, 2.0]],
                "qvel": [[0.3, 0.4], [0.1, 0.2]],
            }
        ),
        dataset / "data" / "chunk-002" / "file-003.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [5],
                "length": [2],
                "tasks": [["place"]],
                f"videos/{_CAMERA}/chunk_index": [7],
                f"videos/{_CAMERA}/file_index": [4],
                f"videos/{_CAMERA}/from_timestamp": [12.5],
                f"videos/{_CAMERA}/to_timestamp": [12.7],
            }
        ),
        dataset / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    pq.write_table(
        pa.table({"task_index": [0, 3], "task": ["pick", "place"]}),
        dataset / "meta" / "tasks.parquet",
    )
    (dataset / "videos" / _CAMERA / "chunk-007" / "file-004.mp4").write_bytes(b"v3-video")
    return dataset


def _write_jsonl_dataset(root: Path) -> Path:
    dataset = root / "synthetic-lerobot-jsonl"
    (dataset / "meta").mkdir(parents=True)
    (dataset / "data" / "chunk-000").mkdir(parents=True)
    (dataset / "videos" / _CAMERA / "chunk-000").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "robot_type": "jsonl-arm",
        "total_episodes": 1,
        "total_frames": 2,
        "fps": 25.0,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.jsonl",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "qpos": {"dtype": "float32", "shape": [2]},
            "qvel": {"dtype": "float32", "shape": [2]},
            "action": {"dtype": "float32", "shape": [2]},
            "observation.force": {"dtype": "float32", "shape": [1]},
            _CAMERA: {"dtype": "video", "shape": [48, 64, 3]},
        },
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    rows = [
        {
            "episode_index": 0,
            "frame_index": 1,
            "timestamp": 0.04,
            "task_index": 2,
            "qpos": [3.0, 4.0],
            "qvel": [0.3, 0.4],
            "action": [0.7, 0.8],
            "observation.force": [6.0],
        },
        {
            "episode_index": 0,
            "frame_index": 0,
            "timestamp": 0.0,
            "task_index": 2,
            "qpos": [1.0, 2.0],
            "qvel": [0.1, 0.2],
            "action": [0.5, 0.6],
            "observation.force": [5.0],
        },
    ]
    data_path = dataset / "data" / "chunk-000" / "file-000.jsonl"
    data_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    (dataset / "videos" / _CAMERA / "chunk-000" / "file-000.mp4").write_bytes(b"jsonl-video")
    return dataset


@pytest.fixture
def dataset_path(tmp_path: Path) -> Path:
    return _write_v2_dataset(tmp_path)


@pytest.fixture
def loader(dataset_path: Path) -> LeRobotLoader:
    return LeRobotLoader(dataset_path)


class TestDatasetDetection:
    def test_detects_complete_dataset(self, dataset_path):
        assert is_lerobot_dataset(dataset_path) is True

    def test_rejects_missing_info(self, tmp_path):
        (tmp_path / "data").mkdir()

        assert is_lerobot_dataset(tmp_path) is False

    def test_rejects_missing_data_directory(self, tmp_path):
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "info.json").write_text("{}", encoding="utf-8")

        assert is_lerobot_dataset(tmp_path) is False

    def test_factory_returns_loader(self, dataset_path):
        assert isinstance(get_lerobot_loader(dataset_path), LeRobotLoader)


class TestDatasetInfo:
    def test_loads_exact_metadata(self, loader):
        info = loader.get_dataset_info()

        assert info.codebase_version == "v2.1"
        assert info.robot_type == "synthetic-arm"
        assert info.total_episodes == 2
        assert info.total_frames == 5
        assert info.total_tasks == 2
        assert info.total_chunks == 1
        assert info.chunks_size == 1000
        assert info.fps == 20.0
        assert info.splits == {"train": "0:2"}
        assert info.features["observation.state"]["names"] == ["shoulder", "elbow", "wrist"]

    def test_missing_info_raises_contextual_error(self, tmp_path):
        with pytest.raises(LeRobotLoaderError, match=r"info\.json not found"):
            LeRobotLoader(tmp_path).get_dataset_info()

    def test_malformed_info_preserves_cause(self, tmp_path):
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "info.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(LeRobotLoaderError, match=r"Invalid info\.json") as error:
            LeRobotLoader(tmp_path).get_dataset_info()

        assert isinstance(error.value.cause, json.JSONDecodeError)


class TestEpisodeListing:
    def test_lists_declared_episode_indices(self, loader):
        assert loader.list_episodes() == [0, 1]

    def test_lists_exact_episode_metadata(self, loader):
        metadata = loader.list_episodes_with_meta()

        assert metadata == {
            0: {
                "length": 3,
                "task_index": 0,
                "cameras": [_CAMERA],
                "fps": 20.0,
                "robot_type": "synthetic-arm",
            },
            1: {
                "length": 2,
                "task_index": 1,
                "cameras": [_CAMERA],
                "fps": 20.0,
                "robot_type": "synthetic-arm",
            },
        }

    def test_episode_info_matches_metadata(self, loader):
        assert loader.get_episode_info(1) == {
            "length": 2,
            "task_index": 1,
            "cameras": [_CAMERA],
            "fps": 20.0,
            "robot_type": "synthetic-arm",
            "episode_index": 1,
        }


class TestEpisodeLoading:
    def test_loads_sorted_typed_episode(self, loader):
        episode = loader.load_episode(0)

        assert isinstance(episode, LeRobotEpisodeData)
        assert episode.episode_index == 0
        assert episode.length == 3
        assert episode.frame_indices.tolist() == [0, 1, 2]
        assert episode.timestamps.tolist() == pytest.approx([0.0, 0.05, 0.1])
        assert episode.joint_positions.tolist() == [
            [0.0, 1.0, 2.0],
            [1.0, 2.0, 3.0],
            [2.0, 3.0, 4.0],
        ]
        assert episode.joint_velocities is not None
        np.testing.assert_allclose(episode.joint_velocities, [[0.1, 0.2, 0.3]] * 3)
        np.testing.assert_allclose(episode.actions, [[0.4, 0.5, 0.6]] * 3)
        assert episode.additional_features["observation.gripper.is_closed"].tolist() == [False, True, False]
        assert episode.task_index == 0
        assert episode.metadata == {
            "robot_type": "synthetic-arm",
            "fps": 20.0,
            "codebase_version": "v2.1",
        }

    def test_loads_last_episode(self, loader):
        episode = loader.load_episode(1)

        assert episode.episode_index == 1
        assert episode.length == 2
        assert episode.task_index == 1

    def test_missing_episode_raises(self, loader):
        with pytest.raises(LeRobotLoaderError, match="Failed to load episode 9"):
            loader.load_episode(9)


class TestTasksAndVideo:
    def test_returns_tasks(self, loader):
        assert loader.get_tasks() == {0: "pick", 1: "place"}

    def test_skips_blank_task_lines(self, dataset_path):
        tasks_path = dataset_path / "meta" / "tasks.jsonl"
        tasks_path.write_text(
            "\n" + json.dumps({"task_index": 0, "task": "pick"}) + "\n\n",
            encoding="utf-8",
        )

        assert LeRobotLoader(dataset_path).get_tasks() == {0: "pick"}

    def test_malformed_tasks_return_empty_mapping(self, dataset_path):
        (dataset_path / "meta" / "tasks.jsonl").write_text("not-json\n", encoding="utf-8")

        assert LeRobotLoader(dataset_path).get_tasks() == {}

    def test_returns_camera_names(self, loader):
        assert loader.get_cameras() == [_CAMERA]

    def test_resolves_existing_video(self, loader, dataset_path):
        assert loader.get_video_path(1, _CAMERA) == (
            dataset_path / "videos" / "chunk-000" / _CAMERA / "episode_000001.mp4"
        )

    def test_returns_none_for_unknown_camera(self, loader):
        assert loader.get_video_path(0, "observation.images.missing") is None


class TestV3DatasetBehavior:
    def test_resolves_scanned_data_and_independent_video_chunks(self, tmp_path: Path) -> None:
        dataset = _write_v3_dataset(tmp_path)
        loader = LeRobotLoader(dataset)

        episode = loader.load_episode(5)

        assert episode.episode_index == 5
        assert episode.length == 2
        assert episode.frame_indices.tolist() == [0, 1]
        assert episode.timestamps.tolist() == pytest.approx([0.0, 0.1])
        assert episode.joint_positions.tolist() == [[1.0, 2.0], [3.0, 4.0]]
        assert episode.joint_velocities is not None
        np.testing.assert_allclose(episode.joint_velocities, [[0.1, 0.2], [0.3, 0.4]])
        assert episode.actions.tolist() == [[0.0, 0.0], [0.0, 0.0]]
        assert episode.task_index == 3
        assert episode.video_paths == {_CAMERA: dataset / "videos" / _CAMERA / "chunk-007" / "file-004.mp4"}

    def test_reads_episode_metadata_tasks_and_video_window(self, tmp_path: Path) -> None:
        dataset = _write_v3_dataset(tmp_path)
        loader = LeRobotLoader(dataset)

        assert loader.list_episodes_with_meta() == {
            5: {
                "length": 2,
                "task_index": 3,
                "cameras": [_CAMERA],
                "fps": 10.0,
                "robot_type": "synthetic-v3-arm",
            }
        }
        assert loader.get_episode_info(5) == {
            "length": 2,
            "task_index": 3,
            "cameras": [_CAMERA],
            "fps": 10.0,
            "robot_type": "synthetic-v3-arm",
            "episode_index": 5,
        }
        assert loader.get_tasks() == {0: "pick", 3: "place"}
        assert loader.get_video_path(5, _CAMERA) == (dataset / "videos" / _CAMERA / "chunk-007" / "file-004.mp4")
        assert loader.get_video_time_window(5, _CAMERA) == (12.5, 12.7)
        assert loader.get_video_time_window(4, _CAMERA) is None


class TestJsonlDatasetBehavior:
    def test_loads_sorted_alias_columns_and_additional_features(self, tmp_path: Path) -> None:
        dataset = _write_jsonl_dataset(tmp_path)
        loader = LeRobotLoader(dataset)

        episode = loader.load_episode(0)

        assert episode.frame_indices.tolist() == [0, 1]
        assert episode.timestamps.tolist() == pytest.approx([0.0, 0.04])
        assert episode.joint_positions.tolist() == [[1.0, 2.0], [3.0, 4.0]]
        assert episode.joint_velocities is not None
        np.testing.assert_allclose(episode.joint_velocities, [[0.1, 0.2], [0.3, 0.4]])
        assert episode.actions.tolist() == [[0.5, 0.6], [0.7, 0.8]]
        assert episode.additional_features["observation.force"].tolist() == [[5.0], [6.0]]
        assert episode.task_index == 2
        assert episode.video_paths == {_CAMERA: dataset / "videos" / _CAMERA / "chunk-000" / "file-000.mp4"}

    def test_gets_episode_info_without_loading_parquet(self, tmp_path: Path) -> None:
        dataset = _write_jsonl_dataset(tmp_path)

        assert LeRobotLoader(dataset).get_episode_info(0) == {
            "episode_index": 0,
            "length": 2,
            "fps": 25.0,
            "cameras": [_CAMERA],
            "task_index": 2,
            "robot_type": "jsonl-arm",
        }

    def test_malformed_jsonl_preserves_cause(self, tmp_path: Path) -> None:
        dataset = _write_jsonl_dataset(tmp_path)
        (dataset / "data" / "chunk-000" / "file-000.jsonl").write_text("{bad json\n", encoding="utf-8")

        with pytest.raises(LeRobotLoaderError, match="Failed to read") as error:
            LeRobotLoader(dataset).load_episode(0)

        assert isinstance(error.value.cause, json.JSONDecodeError)
