"""Unit tests for the pure detection helpers in ``metrics.detect_initial_objects``."""

from __future__ import annotations

import numpy as np
from metrics.detect_initial_objects import (
    Detection,
    _aggregate_centroids,
    _annotate,
    _detect_blue_bin,
    _detect_white_gear,
    _largest_blob_centroid,
)


def _mask_with_square(size: int, top: int, left: int, side: int) -> np.ndarray:
    mask = np.zeros((size, size), dtype=np.uint8)
    mask[top : top + side, left : left + side] = 255
    return mask


class TestLargestBlobCentroid:
    def test_finds_square_blob_centroid(self):
        mask = _mask_with_square(200, 70, 70, 60)
        det = _largest_blob_centroid(mask, min_area=100, max_area=100_000)
        assert det.found
        assert abs(det.cx - 99.5) < 2.0
        assert abs(det.cy - 99.5) < 2.0
        assert det.area > 3000

    def test_returns_not_found_for_empty_mask(self):
        det = _largest_blob_centroid(np.zeros((50, 50), dtype=np.uint8), min_area=10, max_area=1000)
        assert not det.found
        assert det.area == 0.0

    def test_rejects_thin_blobs_via_aspect_ratio(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[40:45, 10:90] = 255  # long thin strip, aspect ~16
        det = _largest_blob_centroid(mask, min_area=10, max_area=100_000, max_aspect_ratio=2.5)
        assert not det.found


class TestColorDetectors:
    def test_detects_blue_bin(self):
        rgb = np.zeros((200, 200, 3), dtype=np.uint8)
        rgb[50:150, 50:150] = (0, 0, 255)
        det = _detect_blue_bin(rgb)
        assert det.found
        assert 50 < det.cx < 150

    def test_detects_white_gear(self):
        rgb = np.zeros((200, 200, 3), dtype=np.uint8)
        rgb[80:120, 80:120] = (255, 255, 255)
        det = _detect_white_gear(rgb)
        assert det.found

    def test_blank_frame_yields_no_detection(self):
        rgb = np.zeros((200, 200, 3), dtype=np.uint8)
        assert not _detect_blue_bin(rgb).found
        assert not _detect_white_gear(rgb).found


class TestAnnotateAndAggregate:
    def test_annotate_preserves_shape(self):
        rgb = np.zeros((40, 40, 3), dtype=np.uint8)
        gear = Detection(found=True, cx=10.0, cy=10.0, area=100.0, bbox=(5, 5, 10, 10), angle_deg=0.0)
        bin_ = Detection(found=True, cx=30.0, cy=30.0, area=200.0, bbox=(25, 25, 10, 10), angle_deg=0.0)
        out = _annotate(rgb, gear, bin_)
        assert out.shape == rgb.shape

    def test_aggregate_centroids_with_detections(self):
        rows = [
            {"gear": {"found": True, "cx": 10.0, "cy": 20.0, "area": 100.0}},
            {"gear": {"found": True, "cx": 20.0, "cy": 30.0, "area": 200.0}},
            {"gear": {"found": False, "cx": float("nan"), "cy": float("nan"), "area": 0.0}},
        ]
        stats = _aggregate_centroids(rows, "gear")
        assert stats["detections"] == 2
        assert stats["missing"] == 1
        assert stats["cx"]["mean"] == 15.0

    def test_aggregate_centroids_empty(self):
        rows = [{"gear": {"found": False, "cx": float("nan"), "cy": float("nan"), "area": 0.0}}]
        assert _aggregate_centroids(rows, "gear") == {"detections": 0}
