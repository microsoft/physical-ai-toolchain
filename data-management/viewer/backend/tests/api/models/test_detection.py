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
        assert req.frames is None
        assert req.confidence == 0.25
        assert req.model == "yolo11n"

    def test_validate_frames_none_returns_none(self):
        req = DetectionRequest(frames=None)
        assert req.frames is None

    def test_validate_frames_valid_list(self):
        req = DetectionRequest(frames=[0, 1, 5, 10])
        assert req.frames == [0, 1, 5, 10]

    def test_validate_frames_negative_raises(self):
        with pytest.raises(ValidationError, match="non-negative"):
            DetectionRequest(frames=[0, -1, 2])

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            DetectionRequest(confidence=1.5)


class TestDetectionModels:
    def test_detection_instantiation(self):
        det = Detection(class_id=0, class_name="person", confidence=0.9, bbox=(0.0, 0.0, 10.0, 20.0))
        assert det.class_id == 0
        assert det.bbox == (0.0, 0.0, 10.0, 20.0)

    def test_detection_result_defaults(self):
        result = DetectionResult(frame=3, processing_time_ms=12.5)
        assert result.detections == []

    def test_class_summary(self):
        summary = ClassSummary(count=4, avg_confidence=0.75)
        assert summary.count == 4

    def test_episode_summary_defaults(self):
        summary = EpisodeDetectionSummary(total_frames=10, processed_frames=5, total_detections=2)
        assert summary.detections_by_frame == []
        assert summary.class_summary == {}
