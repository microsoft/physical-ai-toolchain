"""Unit tests for the datasets router (`src/api/routers/datasets.py`).

Exercises listing, capabilities, episode metadata, trajectory, frame
image, camera, video file/blob streaming, cache stats, and cache
warmup endpoints with the dataset service mocked out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint


@pytest.fixture
def client() -> TestClient:
    from src.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_service() -> MagicMock:
    svc = MagicMock()
    svc.list_datasets = AsyncMock(return_value=[])
    svc.get_dataset = AsyncMock(return_value=None)
    svc.list_episodes = AsyncMock(return_value=[])
    svc.get_episode = AsyncMock(return_value=None)
    svc.get_episode_trajectory = AsyncMock(return_value=[])
    svc.get_frame_image = AsyncMock(return_value=None)
    svc.get_episode_cameras = AsyncMock(return_value=[])
    svc.get_video_file_path = MagicMock(return_value=None)
    svc.is_safe_video_path = MagicMock(return_value=True)
    svc.has_blob_provider = MagicMock(return_value=False)
    svc.get_blob_video_path = AsyncMock(return_value=None)
    svc.get_blob_video_stream = AsyncMock(return_value=None)
    svc.dataset_has_hdf5 = MagicMock(return_value=False)
    svc.dataset_is_lerobot = MagicMock(return_value=True)
    svc.has_hdf5_support = MagicMock(return_value=True)
    svc.has_lerobot_support = MagicMock(return_value=True)
    svc._episode_cache = MagicMock()
    return svc


@pytest.fixture
def override_service(mock_service: MagicMock):
    from src.api.main import app
    from src.api.services.dataset_service import get_dataset_service

    app.dependency_overrides[get_dataset_service] = lambda: mock_service
    try:
        yield mock_service
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)


def _make_dataset(dataset_id: str = "ds-1", total: int = 3) -> DatasetInfo:
    return DatasetInfo(id=dataset_id, name=dataset_id, total_episodes=total, fps=30.0)


def _make_episode(idx: int = 0, length: int = 10) -> EpisodeData:
    meta = EpisodeMeta(index=idx, length=length, task_index=0, has_annotations=False)
    return EpisodeData(meta=meta, video_urls={}, cameras=[], trajectory_data=[])


def _make_trajectory_point(frame: int = 0) -> TrajectoryPoint:
    return TrajectoryPoint(
        timestamp=float(frame),
        frame=frame,
        joint_positions=[0.0],
        joint_velocities=[0.0],
        end_effector_pose=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        gripper_state=0.0,
    )


# ---------------------------------------------------------------------------
# GET /api/datasets and GET /api/datasets/{id}
# ---------------------------------------------------------------------------


class TestListAndGetDataset:
    def test_list_datasets_returns_list(self, client: TestClient, override_service) -> None:
        override_service.list_datasets = AsyncMock(return_value=[_make_dataset("a"), _make_dataset("b")])
        resp = client.get("/api/datasets")
        assert resp.status_code == 200
        body = resp.json()
        assert [d["id"] for d in body] == ["a", "b"]

    def test_get_dataset_returns_metadata(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1"))
        resp = client.get("/api/datasets/ds-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "ds-1"

    def test_get_dataset_not_found_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/datasets/{id}/capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_capabilities_with_dataset(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1", total=7))
        override_service.dataset_has_hdf5 = MagicMock(return_value=True)
        override_service.dataset_is_lerobot = MagicMock(return_value=False)
        resp = client.get("/api/datasets/ds-1/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["episode_count"] == 7
        assert body["has_hdf5_files"] is True
        assert body["is_lerobot_dataset"] is False
        assert body["hdf5_support"] is True
        assert body["lerobot_support"] is True

    def test_capabilities_without_dataset_reports_zero_episodes(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/missing/capabilities")
        assert resp.status_code == 200
        assert resp.json()["episode_count"] == 0


# ---------------------------------------------------------------------------
# GET /api/datasets/{id}/episodes
# ---------------------------------------------------------------------------


class TestListEpisodes:
    def test_list_episodes_returns_metadata(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1"))
        override_service.list_episodes = AsyncMock(
            return_value=[EpisodeMeta(index=0, length=5, task_index=0, has_annotations=False)]
        )
        resp = client.get("/api/datasets/ds-1/episodes?offset=0&limit=10")
        assert resp.status_code == 200
        assert resp.json()[0]["index"] == 0

    def test_list_episodes_dataset_not_found(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/missing/episodes")
        assert resp.status_code == 404

    def test_list_episodes_passes_filters(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1"))
        override_service.list_episodes = AsyncMock(return_value=[])
        resp = client.get("/api/datasets/ds-1/episodes?offset=2&limit=5&has_annotations=true&task_index=3")
        assert resp.status_code == 200
        kwargs = override_service.list_episodes.await_args.kwargs
        assert kwargs == {"offset": 2, "limit": 5, "has_annotations": True, "task_index": 3}


# ---------------------------------------------------------------------------
# GET /api/datasets/{id}/episodes/{episode_idx}
# ---------------------------------------------------------------------------


class TestGetEpisode:
    def test_get_episode_returns_data_and_cache_header(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1"))
        override_service.get_episode = AsyncMock(return_value=_make_episode(0))
        resp = client.get("/api/datasets/ds-1/episodes/0")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "private, max-age=60"

    def test_get_episode_dataset_not_found(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/missing/episodes/0")
        assert resp.status_code == 404

    def test_get_episode_episode_not_found(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1"))
        override_service.get_episode = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/ds-1/episodes/9")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/datasets/{id}/episodes/{episode_idx}/trajectory
# ---------------------------------------------------------------------------


class TestGetTrajectory:
    def test_trajectory_returns_data(self, client: TestClient, override_service) -> None:
        override_service.get_episode_trajectory = AsyncMock(return_value=[_make_trajectory_point(0)])
        resp = client.get("/api/datasets/ds-1/episodes/0/trajectory")
        assert resp.status_code == 200
        assert resp.json()[0]["frame"] == 0
        assert resp.headers["cache-control"] == "private, max-age=60"

    def test_trajectory_empty_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_episode_trajectory = AsyncMock(return_value=[])
        resp = client.get("/api/datasets/ds-1/episodes/0/trajectory")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/datasets/{id}/episodes/{episode_idx}/frames/{frame_idx}
# ---------------------------------------------------------------------------


class TestGetFrame:
    def test_frame_returns_jpeg(self, client: TestClient, override_service) -> None:
        override_service.get_frame_image = AsyncMock(return_value=b"\xff\xd8jpeg")
        resp = client.get("/api/datasets/ds-1/episodes/0/frames/0?camera=il-camera")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.content == b"\xff\xd8jpeg"
        assert "max-age=3600" in resp.headers["cache-control"]

    def test_frame_missing_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_frame_image = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/ds-1/episodes/0/frames/99")
        assert resp.status_code == 404

    def test_frame_unexpected_error_returns_500(self, client: TestClient, override_service) -> None:
        override_service.get_frame_image = AsyncMock(side_effect=RuntimeError("decode boom"))
        resp = client.get("/api/datasets/ds-1/episodes/0/frames/0")
        assert resp.status_code == 500
        assert "decode boom" in resp.json()["detail"]

    def test_frame_http_exception_propagates(self, client: TestClient, override_service) -> None:
        override_service.get_frame_image = AsyncMock(side_effect=HTTPException(status_code=418, detail="teapot"))
        resp = client.get("/api/datasets/ds-1/episodes/0/frames/0")
        assert resp.status_code == 418


# ---------------------------------------------------------------------------
# GET /api/datasets/{id}/episodes/{episode_idx}/cameras
# ---------------------------------------------------------------------------


class TestGetCameras:
    def test_cameras_returned(self, client: TestClient, override_service) -> None:
        override_service.get_episode_cameras = AsyncMock(return_value=["cam-a", "cam-b"])
        resp = client.get("/api/datasets/ds-1/episodes/0/cameras")
        assert resp.status_code == 200
        assert resp.json() == ["cam-a", "cam-b"]


# ---------------------------------------------------------------------------
# GET/HEAD /api/datasets/{id}/episodes/{episode_idx}/video/{camera}
# ---------------------------------------------------------------------------


class TestGetVideo:
    def test_video_file_response(self, client: TestClient, override_service, tmp_path: Path) -> None:
        video = tmp_path / "ep0.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        override_service.get_video_file_path = MagicMock(return_value=str(video))
        override_service.is_safe_video_path = MagicMock(return_value=True)
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert "immutable" in resp.headers["cache-control"]

    def test_video_unsafe_path_returns_400(self, client: TestClient, override_service, tmp_path: Path) -> None:
        video = tmp_path / "ep0.mp4"
        video.write_bytes(b"x")
        override_service.get_video_file_path = MagicMock(return_value=str(video))
        override_service.is_safe_video_path = MagicMock(return_value=False)
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_video_missing_file_returns_404(self, client: TestClient, override_service, tmp_path: Path) -> None:
        override_service.get_video_file_path = MagicMock(return_value=str(tmp_path / "nope.mp4"))
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 404

    def test_video_unknown_suffix_defaults_mp4(self, client: TestClient, override_service, tmp_path: Path) -> None:
        video = tmp_path / "ep0.bin"
        video.write_bytes(b"x")
        override_service.get_video_file_path = MagicMock(return_value=str(video))
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"

    def test_video_blob_streaming_full(self, client: TestClient, override_service) -> None:
        override_service.get_video_file_path = MagicMock(return_value=None)
        override_service.has_blob_provider = MagicMock(return_value=True)
        override_service.get_blob_video_path = AsyncMock(return_value="blob/path/ep0.mp4")

        async def _stream():
            yield b"chunk-1"
            yield b"chunk-2"

        override_service.get_blob_video_stream = AsyncMock(
            return_value=({"Content-Length": "14"}, "video/mp4", _stream())
        )
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 200
        assert resp.content == b"chunk-1chunk-2"

    def test_video_blob_streaming_range(self, client: TestClient, override_service) -> None:
        override_service.get_video_file_path = MagicMock(return_value=None)
        override_service.has_blob_provider = MagicMock(return_value=True)
        override_service.get_blob_video_path = AsyncMock(return_value="blob/path/ep0.mp4")

        async def _stream():
            yield b"abc"

        override_service.get_blob_video_stream = AsyncMock(
            return_value=(
                {"Content-Range": "bytes 0-2/100", "Content-Length": "3"},
                "video/mp4",
                _stream(),
            )
        )
        resp = client.get(
            "/api/datasets/ds-1/episodes/0/video/il-camera",
            headers={"Range": "bytes=0-2"},
        )
        assert resp.status_code == 206

    def test_video_blob_head_returns_no_body(self, client: TestClient, override_service) -> None:
        override_service.get_video_file_path = MagicMock(return_value=None)
        override_service.has_blob_provider = MagicMock(return_value=True)
        override_service.get_blob_video_path = AsyncMock(return_value="blob/path/ep0.mp4")

        async def _stream():
            yield b"unused"

        override_service.get_blob_video_stream = AsyncMock(
            return_value=({"Content-Length": "10"}, "video/mp4", _stream())
        )
        resp = client.head("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 200
        assert resp.content == b""

    def test_video_blob_path_missing_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_video_file_path = MagicMock(return_value=None)
        override_service.has_blob_provider = MagicMock(return_value=True)
        override_service.get_blob_video_path = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 404
        assert "blob" in resp.json()["detail"].lower()

    def test_video_no_local_no_blob_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_video_file_path = MagicMock(return_value=None)
        override_service.has_blob_provider = MagicMock(return_value=False)
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 404

    def test_video_blob_stream_none_returns_outer_404(self, client: TestClient, override_service) -> None:
        override_service.get_video_file_path = MagicMock(return_value=None)
        override_service.has_blob_provider = MagicMock(return_value=True)
        override_service.get_blob_video_path = AsyncMock(return_value="blob/path/ep0.mp4")
        override_service.get_blob_video_stream = AsyncMock(return_value=None)
        resp = client.get("/api/datasets/ds-1/episodes/0/video/il-camera")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/datasets/cache/stats
# ---------------------------------------------------------------------------


class TestCacheStats:
    def test_cache_stats(self, client: TestClient, override_service) -> None:
        stats = MagicMock()
        stats.capacity = 100
        stats.size = 5
        stats.hits = 20
        stats.misses = 4
        stats.hit_rate = 0.83
        stats.total_bytes = 2048
        stats.max_memory_bytes = 1_048_576
        override_service._episode_cache.stats = MagicMock(return_value=stats)
        resp = client.get("/api/datasets/cache/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["capacity"] == 100
        assert body["size"] == 5
        assert body["hits"] == 20
        assert body["misses"] == 4
        assert body["hit_rate"] == 0.83
        assert body["total_bytes"] == 2048
        assert body["max_memory_bytes"] == 1_048_576


# ---------------------------------------------------------------------------
# POST /api/datasets/{id}/cache/warm
# ---------------------------------------------------------------------------


class TestWarmCache:
    def test_warm_cache_loads_capped_count(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1", total=2))

        async def _get_episode(_dataset_id: str, idx: int) -> Any:
            return _make_episode(idx)

        override_service.get_episode = AsyncMock(side_effect=_get_episode)
        resp = client.post("/api/datasets/ds-1/cache/warm?count=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"dataset_id": "ds-1", "loaded": 2, "requested": 2}

    def test_warm_cache_skips_missing_episodes(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=_make_dataset("ds-1", total=3))

        async def _get_episode(_dataset_id: str, idx: int) -> Any:
            return None if idx == 1 else _make_episode(idx)

        override_service.get_episode = AsyncMock(side_effect=_get_episode)
        resp = client.post("/api/datasets/ds-1/cache/warm?count=3")
        assert resp.status_code == 200
        assert resp.json() == {"dataset_id": "ds-1", "loaded": 2, "requested": 3}

    def test_warm_cache_dataset_not_found(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.post("/api/datasets/missing/cache/warm?count=1")
        assert resp.status_code == 404
