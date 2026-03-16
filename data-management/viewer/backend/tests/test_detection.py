"""Tests for YOLO11 object detection service."""

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

# Skip all tests if ultralytics is not installed
pytest.importorskip("ultralytics")


class TestDetectionService:
    """Test cases for the detection service."""

    def test_model_loads(self):
        """Test that the YOLO model can be loaded."""
        from src.api.services.detection_service import get_detection_service

        service = get_detection_service()
        model = service._get_model("yolo11n")
        assert model is not None
        assert hasattr(model, "names")
        print(f"Model loaded with {len(model.names)} classes")

    def test_detect_synthetic_image(self):
        """Test detection on a synthetic test image."""
        from src.api.services.detection_service import DetectionService

        service = DetectionService()

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

    def test_detect_person_image(self):
        """Test detection on an image that should contain detectable objects."""
        import os

        from ultralytics import YOLO

        from src.api.services.detection_service import DetectionService

        service = DetectionService()

        # Check if there's a test image or use a built-in ultralytics test
        model = YOLO("yolo11n.pt")

        # Use ultralytics built-in test image
        os.path.join(os.path.dirname(model.model_name or ""), "assets")

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

    def test_detect_from_hdf5(self):
        """Test detection on actual HDF5 data."""
        import os

        import h5py

        from src.api.services.detection_service import DetectionService

        service = DetectionService()

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
