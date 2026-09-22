"""Unit tests for the detection router (`src/api/routers/detection.py`).

Exercises run-detection (404 + happy path) and clear-detections cache
endpoints with the dataset and detection services mocked out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.models.datasources import EpisodeData, EpisodeMeta
from src.api.models.detection import EpisodeDetectionSummary


@pytest.fixture
def client() -> TestClient:
    from src.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def override_services():
    from src.api.main import app
    from src.api.services.dataset_service import get_dataset_service
    from src.api.services.detection_service import get_detection_service

    dataset_service = MagicMock()
    dataset_service.get_episode = AsyncMock(return_value=None)
    dataset_service.get_frame_image = AsyncMock(return_value=b"jpeg-bytes")

    detection_service = MagicMock()
    detection_service.effective_confidence.return_value = 0.1
    detection_service.detect_episode = AsyncMock()
    detection_service.clear_cache = MagicMock(return_value=False)

    app.dependency_overrides[get_dataset_service] = lambda: dataset_service
    app.dependency_overrides[get_detection_service] = lambda: detection_service
    try:
        yield dataset_service, detection_service
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)
        app.dependency_overrides.pop(get_detection_service, None)


def test_run_detection_episode_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_episode.return_value = None

    response = client.post("/api/datasets/ds-1/episodes/0/detect", json={})

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_run_detection_success_returns_summary(client: TestClient, override_services) -> None:
    dataset_service, detection_service = override_services
    episode = EpisodeData(
        meta=EpisodeMeta(index=0, length=5, task_index=0, has_annotations=False),
        video_urls={},
        cameras=["il-camera"],
        trajectory_data=[],
    )
    dataset_service.get_episode.return_value = episode
    summary = EpisodeDetectionSummary(
        total_frames=5,
        processed_frames=5,
        total_detections=0,
    )

    async def _fake_detect(dataset_id, episode_idx, body, get_frame_image, total):
        # Exercise the inner get_frame_image closure (line 76).
        await get_frame_image(0)
        return summary

    detection_service.detect_episode.side_effect = _fake_detect

    response = client.post("/api/datasets/ds-1/episodes/0/detect", json={})

    assert response.status_code == 200
    assert response.json()["total_frames"] == 5
    dataset_service.get_frame_image.assert_awaited_once_with("ds-1", 0, 0, "il-camera")


def test_run_detection_unexpected_error_returns_500(client: TestClient, override_services) -> None:
    dataset_service, detection_service = override_services
    dataset_service.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=1, task_index=0, has_annotations=False),
        video_urls={},
        cameras=["il-camera"],
        trajectory_data=[],
    )
    detection_service.detect_episode.side_effect = RuntimeError("boom")

    response = client.post("/api/datasets/ds-1/episodes/0/detect", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == "Detection failed"


def test_run_detection_invalid_model_returns_400(client: TestClient, override_services) -> None:
    from src.api.services.detection_service import InvalidDetectionModelError

    dataset_service, detection_service = override_services
    dataset_service.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=1, task_index=0, has_annotations=False),
        video_urls={},
        cameras=["il-camera"],
        trajectory_data=[],
    )
    detection_service.detect_episode.side_effect = InvalidDetectionModelError(
        "Model must be an approved model identifier"
    )

    response = client.post("/api/datasets/ds-1/episodes/0/detect", json={"model": "../unsafe"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Model must be an approved model identifier"


def test_run_detection_unavailable_model_returns_503(client: TestClient, override_services) -> None:
    from src.api.services.detection_service import DetectionModelUnavailableError

    dataset_service, detection_service = override_services
    dataset_service.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=1, task_index=0, has_annotations=False),
        video_urls={},
        cameras=["il-camera"],
        trajectory_data=[],
    )
    detection_service.detect_episode.side_effect = DetectionModelUnavailableError(
        "Approved model file not found: /private/models/yolo11n.pt"
    )

    response = client.post("/api/datasets/ds-1/episodes/0/detect", json={"model": "yolo11n"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Configured detection model is unavailable"


def test_run_detection_logs_unavailable_model(client: TestClient, override_services, caplog) -> None:
    from src.api.services.detection_service import DetectionModelUnavailableError

    dataset_service, detection_service = override_services
    dataset_service.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=1, task_index=0, has_annotations=False),
        video_urls={},
        cameras=["il-camera"],
        trajectory_data=[],
    )
    detection_service.detect_episode.side_effect = DetectionModelUnavailableError("private path")

    with caplog.at_level("ERROR"):
        response = client.post("/api/datasets/ds-1/episodes/0/detect", json={"model": "yolo11n"})

    assert response.status_code == 503
    assert "Configured detection model is unavailable for model yolo11n" in caplog.text


def test_run_detection_model_load_failure_returns_503_without_cache_write(
    client: TestClient,
    override_services,
    monkeypatch,
    tmp_path,
) -> None:
    import hashlib
    import sys
    import types

    from src.api.main import app
    from src.api.services.detection_service import DetectionService, get_detection_service

    dataset_service, _ = override_services
    dataset_service.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=1, task_index=0, has_annotations=False),
        video_urls={},
        cameras=["il-camera"],
        trajectory_data=[],
    )
    model_path = tmp_path / "yolo11n.pt"
    model_path.write_bytes(b"corrupt checkpoint")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    service = DetectionService(models_dir=tmp_path, model_digests={"yolo11n": digest})

    class FailingYOLO:
        def __init__(self, _model_path: str):
            raise RuntimeError("incompatible checkpoint")

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FailingYOLO))
    app.dependency_overrides[get_detection_service] = lambda: service

    response = client.post("/api/datasets/ds-1/episodes/0/detect", json={"model": "yolo11n"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Configured detection model is unavailable"
    assert service.get_cached("ds-1", 0) is None


def test_get_detections_returns_cached_summary(client: TestClient, override_services) -> None:
    _, detection_service = override_services
    summary = EpisodeDetectionSummary(total_frames=3, processed_frames=3, total_detections=0)
    detection_service.get_cached = MagicMock(return_value=summary)

    response = client.get("/api/datasets/ds-1/episodes/1/detections")

    assert response.status_code == 200
    assert response.json()["total_frames"] == 3
    detection_service.get_cached.assert_called_once_with("ds-1", 1)


def test_clear_detections_returns_cleared_status(client: TestClient, override_services) -> None:
    _, detection_service = override_services
    detection_service.clear_cache.return_value = True

    response = client.delete("/api/datasets/ds-1/episodes/2/detections")

    assert response.status_code == 200
    assert response.json() == {"cleared": True}
    detection_service.clear_cache.assert_called_once_with("ds-1", 2)
