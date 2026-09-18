"""Behavior tests for episode-level object detection."""

from __future__ import annotations

import pytest

from src.api.models.detection import Detection, DetectionRequest, DetectionResult
from src.api.services import detection_service as detection_service_module
from src.api.services.detection_service import DetectionService


@pytest.mark.asyncio
async def test_detect_episode_forwards_request_and_preserves_frame_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DetectionService()
    fetched_indices: list[int] = []
    detection_calls: list[tuple[bytes, int, float, str, list[str] | None]] = []

    async def get_frame_image(frame_idx: int) -> bytes:
        fetched_indices.append(frame_idx)
        return f"frame-{frame_idx}".encode()

    async def detect_frame(
        image_bytes: bytes,
        frame_idx: int,
        confidence: float = 0.25,
        model_name: str = "yolo11n",
        labels: list[str] | None = None,
    ) -> DetectionResult:
        detection_calls.append((image_bytes, frame_idx, confidence, model_name, labels))
        return DetectionResult(frame=frame_idx, detections=[], processing_time_ms=1.0)

    monkeypatch.setattr(service, "detect_frame", detect_frame)
    request = DetectionRequest(
        frames=[1, 3],
        confidence=0.6,
        model="yolov8s-world",
        labels=["widget"],
    )

    summary = await service.detect_episode(
        dataset_id="dataset",
        episode_idx=0,
        request=request,
        get_frame_image=get_frame_image,
        total_frames=10,
    )

    assert fetched_indices == [1, 3]
    assert detection_calls == [
        (b"frame-1", 1, 0.6, "yolov8s-world", ["widget"]),
        (b"frame-3", 3, 0.6, "yolov8s-world", ["widget"]),
    ]
    assert summary.model_dump() == {
        "total_frames": 10,
        "processed_frames": 2,
        "total_detections": 0,
        "detections_by_frame": [
            {"frame": 1, "detections": [], "processing_time_ms": 1.0},
            {"frame": 3, "detections": [], "processing_time_ms": 1.0},
        ],
        "class_summary": {},
    }


@pytest.mark.asyncio
async def test_detect_episode_skips_missing_and_failed_frames_and_summarizes_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DetectionService()

    async def get_frame_image(frame_idx: int) -> bytes | None:
        if frame_idx in {1, 2, 3, 4}:
            return None
        if frame_idx == 6:
            raise RuntimeError("unreadable frame")
        return f"frame-{frame_idx}".encode()

    async def detect_frame(
        _image_bytes: bytes,
        frame_idx: int,
        confidence: float = 0.25,
        model_name: str = "yolo11n",
        labels: list[str] | None = None,
    ) -> DetectionResult:
        assert confidence == 0.1
        assert model_name == "yolo11n"
        assert labels is None
        detections = [
            Detection(
                class_id=0,
                class_name="person",
                confidence=0.8 if frame_idx == 0 else 0.6,
                bbox=(0.0, 0.0, 1.0, 1.0),
            )
        ]
        return DetectionResult(frame=frame_idx, detections=detections, processing_time_ms=2.0)

    monkeypatch.setattr(service, "detect_frame", detect_frame)

    summary = await service.detect_episode(
        dataset_id="dataset",
        episode_idx=4,
        request=DetectionRequest(),
        get_frame_image=get_frame_image,
        total_frames=7,
    )

    assert summary.model_dump() == {
        "total_frames": 7,
        "processed_frames": 2,
        "total_detections": 2,
        "detections_by_frame": [
            {
                "frame": 0,
                "detections": [
                    {
                        "class_id": 0,
                        "class_name": "person",
                        "confidence": 0.8,
                        "bbox": (0.0, 0.0, 1.0, 1.0),
                    }
                ],
                "processing_time_ms": 2.0,
            },
            {
                "frame": 5,
                "detections": [
                    {
                        "class_id": 0,
                        "class_name": "person",
                        "confidence": 0.6,
                        "bbox": (0.0, 0.0, 1.0, 1.0),
                    }
                ],
                "processing_time_ms": 2.0,
            },
        ],
        "class_summary": {
            "person": {
                "count": 2,
                "avg_confidence": pytest.approx(0.7),
            }
        },
    }
    assert service.get_cached("dataset", 4) is summary


@pytest.mark.asyncio
async def test_detect_episode_surfaces_missing_model_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DetectionService()

    async def get_frame_image(_frame_idx: int) -> bytes:
        return b"image"

    async def detect_frame(
        _image_bytes: bytes,
        _frame_idx: int,
        confidence: float = 0.25,
        model_name: str = "yolo11n",
        labels: list[str] | None = None,
    ) -> DetectionResult:
        raise ImportError("ultralytics unavailable")

    monkeypatch.setattr(service, "detect_frame", detect_frame)

    with pytest.raises(ImportError, match="ultralytics unavailable"):
        await service.detect_episode(
            dataset_id="dataset",
            episode_idx=0,
            request=DetectionRequest(frames=[0]),
            get_frame_image=get_frame_image,
            total_frames=1,
        )
    assert service.get_cached("dataset", 0) is None


@pytest.mark.asyncio
async def test_cache_can_be_read_and_cleared_through_public_methods() -> None:
    service = DetectionService()

    async def unexpected_frame_read(_frame_idx: int) -> bytes:
        raise AssertionError("No frames should be read")

    summary = await service.detect_episode(
        dataset_id="dataset",
        episode_idx=2,
        request=DetectionRequest(),
        get_frame_image=unexpected_frame_read,
        total_frames=0,
    )

    assert service.get_cached("dataset", 2) is summary
    assert service.clear_cache("dataset", 2) is True
    assert service.get_cached("dataset", 2) is None
    assert service.clear_cache("dataset", 2) is False


def test_get_detection_service_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection_service_module, "_detection_service", None)

    first = detection_service_module.get_detection_service()
    second = detection_service_module.get_detection_service()

    assert first is second
    assert isinstance(first, DetectionService)
