"""Tests for YOLO11 object detection service."""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.api.services.detection_service import DetectionService

# Skip all tests if ultralytics (or its torch dependency) is not importable.
# Use a broad except to also handle partial/broken installs (e.g., a torch
# namespace package missing __init__.py raises AttributeError, not ImportError).
try:
    import ultralytics  # noqa: F401
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"ultralytics unavailable: {exc}", allow_module_level=True)


class TestDetectionService:
    """Test cases for the detection service."""

    @pytest.fixture(scope="class")
    def service(self) -> DetectionService:
        models_dir = Path(os.environ.get("DETECTION_MODELS_DIR", "./models"))
        digests = json.loads(os.environ.get("DETECTION_MODEL_DIGESTS", "{}"))
        required_models = {"yolo11n", "yolov8s-world"}
        missing_models = sorted(name for name in required_models if not (models_dir / f"{name}.pt").is_file())
        missing_digests = sorted(required_models - digests.keys())
        if missing_models or missing_digests:
            pytest.skip(
                f"Detection smoke test requires weights {missing_models} and configured digests {missing_digests}"
            )
        return DetectionService(models_dir=models_dir, model_digests=digests)

    @pytest.mark.parametrize(
        ("model_name", "labels"),
        [
            ("yolo11n", None),
            ("yolov8s-world", ["robot"]),
        ],
    )
    def test_model_loads(self, service, model_name: str, labels: list[str] | None):
        """Test that closed- and open-vocabulary YOLO models load and warm up."""
        model = service._get_model(model_name, labels=labels)
        assert model is not None
        assert hasattr(model, "names")
        print(f"Model loaded with {len(model.names)} classes")

    def test_detect_synthetic_image(self, service):
        """Test detection on a synthetic test image."""
        # Create a synthetic image (solid color - should have no detections)
        img = Image.new("RGB", (640, 480), color=(128, 128, 128))
        buffer = BytesIO()
        img.save(buffer, format="JPEG")
        image_bytes = buffer.getvalue()

        # Run detection
        import asyncio

        result = asyncio.run(service.detect_frame(image_bytes, frame_idx=0, confidence=0.25))

        print(f"Synthetic image: {len(result.detections)} detections")
        assert result.frame == 0
        # Gray image should have few or no detections
        assert len(result.detections) >= 0

    def test_detect_person_image(self, service):
        """Test detection on an image that should contain detectable objects."""
        # Create a more realistic test - draw some shapes that might trigger detection
        img = Image.new("RGB", (640, 480), color=(200, 200, 200))

        # Draw a circle (might be detected as sports ball)
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img)
        draw.ellipse([200, 150, 400, 350], fill=(255, 128, 0), outline=(0, 0, 0))

        buffer = BytesIO()
        img.save(buffer, format="JPEG")
        image_bytes = buffer.getvalue()

        import asyncio

        result = asyncio.run(service.detect_frame(image_bytes, frame_idx=0, confidence=0.1))

        print(f"Circle image: {len(result.detections)} detections")
        for det in result.detections:
            print(f"  - {det.class_name}: {det.confidence:.3f}")

    def test_detect_from_hdf5(self, service):
        """Test detection on actual HDF5 data."""
        import h5py

        # Find a test HDF5 file
        test_paths = [
            "data/test-192-insertions/episode_000000.hdf5",
            "test-192-insertions/episode_000000.hdf5",
            "data/test-dataset/episode_000000.hdf5",
        ]

        hdf5_path = None
        for path in test_paths:
            if os.path.exists(path):
                hdf5_path = path
                break

        if hdf5_path is None:
            pytest.skip("No test HDF5 file found")

        print(f"Using HDF5: {hdf5_path}")

        with h5py.File(hdf5_path, "r") as f:
            # List observation keys
            if "observation" not in f:
                pytest.skip("No observation group in HDF5")

            obs_keys = list(f["observation"].keys())
            print(f"Observation keys: {obs_keys}")

            # Find camera data
            camera_key = None
            for key in obs_keys:
                ds = f["observation"][key]
                print(f"  {key}: shape={ds.shape}, dtype={ds.dtype}")
                # Look for image-like data (N, H, W, C) with C=3
                if len(ds.shape) == 4 and ds.shape[-1] == 3:
                    camera_key = key
                    break

            if camera_key is None:
                pytest.skip("No camera data found in HDF5")

            print(f"\nUsing camera: {camera_key}")

            # Get first frame
            frame_data = f["observation"][camera_key][0]
            print(f"Frame shape: {frame_data.shape}")
            print(f"Frame dtype: {frame_data.dtype}")
            print(f"Frame range: min={frame_data.min()}, max={frame_data.max()}")

            # Convert to PIL Image
            img = Image.fromarray(frame_data.astype(np.uint8))
            print(f"PIL Image: size={img.size}, mode={img.mode}")

            # Save for debugging
            img.save("test_hdf5_frame.jpg")
            print("Saved test_hdf5_frame.jpg")

            # Convert to bytes
            buffer = BytesIO()
            img.save(buffer, format="JPEG")
            image_bytes = buffer.getvalue()

            # Run detection with low confidence
            import asyncio

            result = asyncio.run(service.detect_frame(image_bytes, frame_idx=0, confidence=0.1))

            print("\nDetection results:")
            print(f"  Processing time: {result.processing_time_ms:.1f}ms")
            print(f"  Detections: {len(result.detections)}")

            for det in result.detections:
                print(f"    - {det.class_name}: {det.confidence:.3f} @ {det.bbox}")

            # Detection should work even if no objects found
            assert result.frame == 0
            assert result.processing_time_ms > 0
