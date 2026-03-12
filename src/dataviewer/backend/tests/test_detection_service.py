"""Unit tests for detection service episode processing behavior."""

import pytest

from src.api.models.detection import DetectionRequest, DetectionResult
from src.api.services.detection_service import DetectionService


class TestDetectionEpisodeProcessing:
    """Tests for frame index handling in episode detection."""

    @pytest.mark.asyncio
    async def test_detect_episode_preserves_integer_frame_indices(self, monkeypatch):
        service = DetectionService()
        observed_indices: list[int] = []

        async def get_frame_image(frame_idx: int) -> bytes:
            assert isinstance(frame_idx, int)
            observed_indices.append(frame_idx)
            return b"image-bytes"

        async def fake_detect_frame(
            self,
            image_bytes: bytes,
            frame_idx: int,
            confidence: float = 0.25,
            model_name: str = "yolo11n",
        ) -> DetectionResult:
            assert image_bytes == b"image-bytes"
            assert isinstance(frame_idx, int)
            observed_indices.append(frame_idx)
            return DetectionResult(frame=frame_idx, detections=[], processing_time_ms=1.0)

        monkeypatch.setattr(DetectionService, "detect_frame", fake_detect_frame)

        summary = await service.detect_episode(
            dataset_id="dataset",
            episode_idx=0,
            request=DetectionRequest(frames=[1, 3]),
            get_frame_image=get_frame_image,
            total_frames=10,
        )

        assert observed_indices == [1, 1, 3, 3]
        assert [result.frame for result in summary.detections_by_frame] == [1, 3]
        assert summary.processed_frames == 2
