"""Tests for detection Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.models.detection import (
    ClassSummary,
    Detection,
    DetectionRequest,
    DetectionResult,
    EpisodeDetectionSummary,
)


class TestDetectionRequest:
    def test_defaults(self):
        req = DetectionRequest()
        assert req.model_dump() == {
            "frames": None,
            "confidence": None,
            "model": "yolo11n",
            "labels": None,
            "camera": None,
        }

    def test_validate_frames_none_returns_none(self):
        req = DetectionRequest(frames=None)
        assert req.frames is None

    def test_validate_frames_valid_list(self):
        req = DetectionRequest(frames=[0, 1, 5, 10])
        assert req.frames == [0, 1, 5, 10]

    def test_validate_frames_negative_raises(self):
        with pytest.raises(ValidationError, match="non-negative"):
            DetectionRequest(frames=[0, -1, 2])

    @pytest.mark.parametrize("confidence", [-0.01, 1.01])
    def test_confidence_out_of_range(self, confidence: float):
        with pytest.raises(ValidationError, match=r"less than or equal|greater than or equal"):
            DetectionRequest(confidence=confidence)

    def test_labels_are_trimmed_and_empty_values_removed(self):
        req = DetectionRequest(labels=[" person ", "", "  ", "robot"])
        assert req.labels == ["person", "robot"]

    def test_empty_labels_normalize_to_none(self):
        req = DetectionRequest(labels=["", "  "])
        assert req.labels is None

    def test_overlong_label_raises(self):
        with pytest.raises(ValidationError, match="at most 100 characters"):
            DetectionRequest(labels=["x" * 101])

    def test_nested_strings_are_sanitized(self):
        req = DetectionRequest(
            model="yolo11n\r\n",
            labels=["pa\r\nrt"],
            camera="front\r\n",
        )
        assert req.model == "yolo11n"
        assert req.labels == ["part"]
        assert req.camera == "front"


class TestDetectionModels:
    def test_detection_instantiation(self):
        det = Detection(class_id=0, class_name="person", confidence=0.9, bbox=(0.0, 0.0, 10.0, 20.0))
        assert det.model_dump() == {
            "class_id": 0,
            "class_name": "person",
            "confidence": 0.9,
            "bbox": (0.0, 0.0, 10.0, 20.0),
        }

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("class_id", -1),
            ("confidence", -0.01),
            ("confidence", 1.01),
        ],
    )
    def test_detection_rejects_invalid_bounds(self, field: str, value: int | float):
        values = {
            "class_id": 0,
            "class_name": "person",
            "confidence": 0.9,
            "bbox": (0.0, 0.0, 10.0, 20.0),
            field: value,
        }
        with pytest.raises(ValidationError):
            Detection(**values)

    def test_detection_result_defaults(self):
        result = DetectionResult(frame=3, processing_time_ms=12.5)
        assert result.model_dump() == {
            "frame": 3,
            "detections": [],
            "processing_time_ms": 12.5,
        }

    def test_class_summary(self):
        summary = ClassSummary(count=4, avg_confidence=0.75)
        assert summary.model_dump() == {"count": 4, "avg_confidence": 0.75}

    def test_episode_summary_defaults(self):
        summary = EpisodeDetectionSummary(total_frames=10, processed_frames=5, total_detections=2)
        assert summary.model_dump() == {
            "total_frames": 10,
            "processed_frames": 5,
            "total_detections": 2,
            "detections_by_frame": [],
            "class_summary": {},
        }
