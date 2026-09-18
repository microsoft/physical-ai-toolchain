"""Public behavior tests for DatasetService with synthetic datasets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.services.dataset_service import DatasetService

_DATASET_ID = "synthetic"
_CAMERA = "observation.images.front"


def _write_dataset(base_path: Path, dataset_id: str = _DATASET_ID) -> Path:
    dataset = base_path.joinpath(*dataset_id.split("--"))
    (dataset / "meta").mkdir(parents=True)
    (dataset / "data" / "chunk-000").mkdir(parents=True)
    (dataset / "videos" / "chunk-000" / _CAMERA).mkdir(parents=True)
    info = {
        "codebase_version": "v2.1",
        "robot_type": "synthetic-arm",
        "total_episodes": 3,
        "total_frames": 9,
        "total_tasks": 2,
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": 20.0,
        "splits": {"train": "0:3"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": {
            "observation.state": {"dtype": "float32", "shape": [3], "names": ["a", "b", "c"]},
            "action": {"dtype": "float32", "shape": [3]},
            _CAMERA: {"dtype": "video", "shape": [48, 64, 3]},
        },
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    with (dataset / "meta" / "episodes.jsonl").open("w", encoding="utf-8") as episodes_file:
        for episode_index, task_index in ((0, 0), (1, 1), (2, 0)):
            episodes_file.write(
                json.dumps(
                    {
                        "episode_index": episode_index,
                        "length": 3,
                        "task_index": task_index,
                    }
                )
                + "\n"
            )
    with (dataset / "meta" / "tasks.jsonl").open("w", encoding="utf-8") as tasks_file:
        tasks_file.write(json.dumps({"task_index": 0, "task": "pick"}) + "\n")
        tasks_file.write(json.dumps({"task_index": 1, "task": "place"}) + "\n")

    for episode_index, task_index in ((0, 0), (1, 1), (2, 0)):
        table = pa.table(
            {
                "frame_index": [0, 1, 2],
                "timestamp": [0.0, 0.05, 0.1],
                "episode_index": [episode_index] * 3,
                "task_index": [task_index] * 3,
                "observation.state": [
                    [float(episode_index), 1.0, 2.0],
                    [float(episode_index + 1), 2.0, 3.0],
                    [float(episode_index + 2), 3.0, 4.0],
                ],
                "action": [[0.4, 0.5, 0.6]] * 3,
            }
        )
        pq.write_table(table, dataset / "data" / "chunk-000" / f"episode_{episode_index:06d}.parquet")
        video_path = dataset / "videos" / "chunk-000" / _CAMERA / f"episode_{episode_index:06d}.mp4"
        video_path.write_bytes(f"video-{episode_index}".encode())
        os.utime(video_path, (1, 1))
    return dataset


@pytest.fixture
def service(tmp_path: Path) -> DatasetService:
    _write_dataset(tmp_path)
    return DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)


class TestDatasetDiscovery:
    @pytest.mark.asyncio
    async def test_lists_synthetic_dataset(self, service):
        datasets = await service.list_datasets()

        assert len(datasets) == 1
        assert datasets[0].model_dump() == {
            "id": _DATASET_ID,
            "name": "synthetic (synthetic-arm)",
            "group": None,
            "total_episodes": 3,
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

    @pytest.mark.asyncio
    async def test_gets_dataset_without_prior_listing(self, service):
        dataset = await service.get_dataset(_DATASET_ID)

        assert dataset is not None
        assert dataset.id == _DATASET_ID
        assert dataset.total_episodes == 3

    @pytest.mark.asyncio
    async def test_missing_dataset_returns_none(self, service):
        assert await service.get_dataset("missing") is None

    @pytest.mark.asyncio
    async def test_rejects_traversal_through_public_lookup(self, service):
        assert await service.get_dataset("../escape") is None
        assert await service.get_dataset("a--b--c--d--e--f") is None

    @pytest.mark.asyncio
    async def test_reports_format_capabilities_after_discovery(self, service):
        await service.get_dataset(_DATASET_ID)

        assert service.dataset_is_lerobot(_DATASET_ID) is True
        assert service.dataset_has_hdf5(_DATASET_ID) is False
        assert service.has_lerobot_support() is True


class TestNestedDatasetDiscovery:
    @pytest.mark.asyncio
    async def test_discovers_nested_dataset_and_group(self, tmp_path):
        _write_dataset(tmp_path, "project--recordings--session")
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)

        datasets = await service.list_datasets()

        assert [dataset.id for dataset in datasets] == ["project--recordings--session"]
        assert datasets[0].group == "project--recordings"

    @pytest.mark.asyncio
    async def test_ignores_dataset_beyond_maximum_depth(self, tmp_path):
        _write_dataset(tmp_path, "a--b--c--d--e--f")
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)

        assert await service.list_datasets() == []


class TestEpisodeListing:
    @pytest.mark.asyncio
    async def test_lists_exact_episode_metadata(self, service):
        await service.get_dataset(_DATASET_ID)

        episodes = await service.list_episodes(_DATASET_ID)

        assert [episode.model_dump() for episode in episodes] == [
            {"index": 0, "length": 3, "task_index": 0, "has_annotations": False},
            {"index": 1, "length": 3, "task_index": 1, "has_annotations": False},
            {"index": 2, "length": 3, "task_index": 0, "has_annotations": False},
        ]

    @pytest.mark.asyncio
    async def test_applies_pagination(self, service):
        await service.get_dataset(_DATASET_ID)

        episodes = await service.list_episodes(_DATASET_ID, offset=1, limit=1)

        assert [episode.index for episode in episodes] == [1]

    @pytest.mark.asyncio
    async def test_filters_by_task_and_annotation_state(self, service):
        await service.get_dataset(_DATASET_ID)

        task_episodes = await service.list_episodes(_DATASET_ID, task_index=0)
        annotated_episodes = await service.list_episodes(_DATASET_ID, has_annotations=True)

        assert [episode.index for episode in task_episodes] == [0, 2]
        assert annotated_episodes == []


class TestEpisodeData:
    @pytest.mark.asyncio
    async def test_gets_complete_episode(self, service):
        await service.get_dataset(_DATASET_ID)

        episode = await service.get_episode(_DATASET_ID, 1)

        assert episode is not None
        assert episode.meta.model_dump() == {
            "index": 1,
            "length": 3,
            "task_index": 1,
            "has_annotations": False,
        }
        assert episode.cameras == [_CAMERA]
        assert episode.video_urls == {_CAMERA: f"/api/datasets/{_DATASET_ID}/episodes/1/video/{_CAMERA}?v=1"}
        assert [point.frame for point in episode.trajectory_data] == [0, 1, 2]
        assert [point.timestamp for point in episode.trajectory_data] == pytest.approx([0.0, 0.05, 0.1])
        assert episode.trajectory_data[0].joint_positions == [1.0, 1.0, 2.0]
        assert episode.trajectory_data[0].joint_velocities == pytest.approx([20.0, 20.0, 20.0])
        assert episode.trajectory_data[0].end_effector_pose == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_out_of_range_episode_returns_none(self, service):
        await service.get_dataset(_DATASET_ID)

        assert await service.get_episode(_DATASET_ID, 3) is None

    @pytest.mark.asyncio
    async def test_gets_trajectory_and_cameras(self, service):
        await service.get_dataset(_DATASET_ID)

        trajectory = await service.get_episode_trajectory(_DATASET_ID, 0)
        cameras = await service.get_episode_cameras(_DATASET_ID, 0)

        assert len(trajectory) == 3
        assert [point.frame for point in trajectory] == [0, 1, 2]
        assert cameras == [_CAMERA]

    @pytest.mark.asyncio
    async def test_public_cache_invalidation_forces_reload(self, tmp_path):
        dataset_path = _write_dataset(tmp_path)
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=4)
        await service.get_dataset(_DATASET_ID)
        first = await service.get_episode(_DATASET_ID, 0)
        assert first is not None
        parquet_path = dataset_path / "data" / "chunk-000" / "episode_000000.parquet"
        table = pq.read_table(parquet_path)
        table = table.set_column(
            table.schema.get_field_index("observation.state"),
            "observation.state",
            pa.array([[9.0, 9.0, 9.0]] * 3),
        )
        pq.write_table(table, parquet_path)

        cached = await service.get_episode(_DATASET_ID, 0)
        invalidated = service.invalidate_episode_cache(_DATASET_ID, 0)
        reloaded = await service.get_episode(_DATASET_ID, 0)

        assert cached is first
        assert invalidated == 1
        assert reloaded is not None
        assert reloaded.trajectory_data[0].joint_positions == [9.0, 9.0, 9.0]


class TestMediaPaths:
    @pytest.mark.asyncio
    async def test_resolves_video_path_inside_dataset(self, service):
        await service.get_dataset(_DATASET_ID)

        video_path = service.get_video_file_path(_DATASET_ID, 0, _CAMERA)

        assert video_path is not None
        assert Path(video_path).read_bytes() == b"video-0"
        assert service.is_safe_video_path(video_path) is True

    @pytest.mark.asyncio
    async def test_missing_camera_returns_none(self, service):
        await service.get_dataset(_DATASET_ID)

        assert service.get_video_file_path(_DATASET_ID, 0, "missing") is None

    def test_rejects_video_path_outside_base(self, service, tmp_path):
        outside_path = tmp_path.parent / "outside.mp4"

        assert service.is_safe_video_path(str(outside_path)) is False
