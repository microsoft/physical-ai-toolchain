"""HTTP contract tests for dataset endpoints using synthetic data."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from src.api.services.dataset_service import DatasetService, get_dataset_service

_DATASET_ID = "synthetic"
_CAMERA = "observation.images.front"


def _write_dataset(base_path: Path) -> None:
    dataset = base_path / _DATASET_ID
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
                json.dumps({"episode_index": episode_index, "length": 3, "task_index": task_index}) + "\n"
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
                "observation.state": [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [2.0, 3.0, 4.0]],
                "action": [[0.4, 0.5, 0.6]] * 3,
            }
        )
        pq.write_table(table, dataset / "data" / "chunk-000" / f"episode_{episode_index:06d}.parquet")
        (dataset / "videos" / "chunk-000" / _CAMERA / f"episode_{episode_index:06d}.mp4").write_bytes(
            bytes(range(256)) * 8
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    _write_dataset(tmp_path)
    service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)

    async def keep_source_video(path: Path) -> Path:
        return path

    async def synthetic_frame(
        dataset_id: str,
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes:
        assert (dataset_id, episode_idx, frame_idx, camera) == (_DATASET_ID, 0, 0, _CAMERA)
        return b"\xff\xd8synthetic-jpeg\xff\xd9"

    monkeypatch.setattr("src.api.routers.datasets.ensure_browser_compatible", keep_source_video)
    monkeypatch.setattr(service, "get_frame_image", synthetic_frame)

    from src.api.main import app

    app.dependency_overrides[get_dataset_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_dataset_service, None)


class TestDatasets:
    def test_lists_dataset_with_complete_schema(self, client):
        response = client.get("/api/datasets")

        assert response.status_code == 200
        assert response.json() == [
            {
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
        ]

    def test_gets_dataset(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}")

        assert response.status_code == 200
        assert response.json()["id"] == _DATASET_ID
        assert response.json()["total_episodes"] == 3

    def test_missing_dataset_returns_404(self, client):
        response = client.get("/api/datasets/missing")

        assert response.status_code == 404
        assert response.json() == {"detail": "Dataset 'missing' not found"}

    def test_capabilities_reflect_detected_format(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/capabilities")

        assert response.status_code == 200
        assert response.json() == {
            "hdf5_support": True,
            "has_hdf5_files": False,
            "lerobot_support": True,
            "is_lerobot_dataset": True,
            "episode_count": 3,
        }


class TestEpisodes:
    def test_lists_episode_metadata(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes")

        assert response.status_code == 200
        assert response.json() == [
            {"index": 0, "length": 3, "task_index": 0, "has_annotations": False},
            {"index": 1, "length": 3, "task_index": 1, "has_annotations": False},
            {"index": 2, "length": 3, "task_index": 0, "has_annotations": False},
        ]

    def test_applies_pagination(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes?offset=1&limit=1")

        assert response.status_code == 200
        assert response.json() == [{"index": 1, "length": 3, "task_index": 1, "has_annotations": False}]

    def test_filters_by_task_index(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes?task_index=0")

        assert response.status_code == 200
        assert [episode["index"] for episode in response.json()] == [0, 2]

    def test_offset_beyond_range_returns_empty_list(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes?offset=100")

        assert response.status_code == 200
        assert response.json() == []

    def test_missing_dataset_episodes_return_404(self, client):
        response = client.get("/api/datasets/missing/episodes")

        assert response.status_code == 404
        assert response.json() == {"detail": "Dataset 'missing' not found"}

    def test_gets_complete_episode(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes/1")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, max-age=60"
        data = response.json()
        assert data["meta"] == {
            "index": 1,
            "length": 3,
            "task_index": 1,
            "has_annotations": False,
        }
        assert data["cameras"] == [_CAMERA]
        assert len(data["trajectory_data"]) == 3
        assert data["trajectory_data"][0]["joint_positions"] == [0.0, 1.0, 2.0]

    def test_out_of_range_episode_returns_404(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes/3")

        assert response.status_code == 404
        assert response.json() == {"detail": f"Episode 3 not found in dataset '{_DATASET_ID}'"}

    def test_gets_trajectory(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes/0/trajectory")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, max-age=60"
        assert [point["frame"] for point in response.json()] == [0, 1, 2]
        assert [point["timestamp"] for point in response.json()] == pytest.approx([0.0, 0.05, 0.1])

    def test_gets_cameras(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes/0/cameras")

        assert response.status_code == 200
        assert response.json() == [_CAMERA]


class TestMedia:
    def test_streams_video_with_headers(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes/0/video/{_CAMERA}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "video/mp4"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.content == bytes(range(256)) * 8

    def test_supports_video_byte_range(self, client):
        response = client.get(
            f"/api/datasets/{_DATASET_ID}/episodes/0/video/{_CAMERA}",
            headers={"Range": "bytes=10-19"},
        )

        assert response.status_code == 206
        assert response.headers["content-range"] == "bytes 10-19/2048"
        assert response.content == bytes(range(10, 20))

    def test_missing_camera_returns_404(self, client):
        response = client.get(f"/api/datasets/{_DATASET_ID}/episodes/0/video/missing")

        assert response.status_code == 404
        assert response.json() == {"detail": "Video not found for episode 0, camera 'missing'"}

    def test_frame_camera_strips_crlf(self, client):
        response = client.get(
            f"/api/datasets/{_DATASET_ID}/episodes/0/frames/0",
            params={"camera": f"{_CAMERA}\r\n"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "public, max-age=3600"
        assert response.content == b"\xff\xd8synthetic-jpeg\xff\xd9"
