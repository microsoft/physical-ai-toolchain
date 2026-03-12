"""
YOLO11 object detection service.

Provides singleton model loading and frame-by-frame detection
for HDF5 episode data.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image

from ..models.detection import (
    ClassSummary,
    Detection,
    DetectionRequest,
    DetectionResult,
    EpisodeDetectionSummary,
)

if TYPE_CHECKING:
    from ultralytics import YOLO

logger = logging.getLogger(__name__)

# COCO class names for YOLO models
COCO_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]


class DetectionService:
    """YOLO11 object detection service with caching."""

    def __init__(self) -> None:
        self._model: YOLO | None = None
        self._model_name: str = ""
        self._cache: dict[str, EpisodeDetectionSummary] = {}

    def _get_model(self, model_name: str = "yolo11n") -> "YOLO":
        """Load or return cached YOLO model."""
        if self._model is None or self._model_name != model_name:
            try:
                from ultralytics import YOLO

                logger.info("Loading YOLO model: %s", model_name)
                self._model = YOLO(f"{model_name}.pt")
                self._model_name = model_name
                # Warmup with dummy inference
                import numpy as np

                dummy = np.zeros((640, 640, 3), dtype=np.uint8)
                self._model(dummy, verbose=False)
                logger.info("YOLO model loaded and warmed up")
            except ImportError:
                logger.error("ultralytics not installed. Run: uv sync --extra yolo")
                raise
        return self._model

    def _cache_key(self, dataset_id: str, episode_idx: int) -> str:
        """Generate cache key for detection results."""
        return f"{dataset_id}:{episode_idx}"

    def get_cached(self, dataset_id: str, episode_idx: int) -> EpisodeDetectionSummary | None:
        """Get cached detection results if available."""
        key = self._cache_key(dataset_id, episode_idx)
        return self._cache.get(key)

    def clear_cache(self, dataset_id: str, episode_idx: int) -> bool:
        """Clear cached detection results."""
        key = self._cache_key(dataset_id, episode_idx)
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    async def detect_frame(
        self,
        image_bytes: bytes,
        frame_idx: int,
        confidence: float = 0.25,
        model_name: str = "yolo11n",
    ) -> DetectionResult:
        """Run detection on a single frame."""
        import sys

        model = self._get_model(model_name)

        # Load image
        image = Image.open(BytesIO(image_bytes))
        print(
            f"[DETECT] Frame {frame_idx}: size={image.size}, mode={image.mode}, bytes={len(image_bytes)}",
            file=sys.stderr,
            flush=True,
        )

        # Run inference
        start_time = time.perf_counter()
        results = model(image, conf=confidence, verbose=False)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        print(
            f"[DETECT] Frame {frame_idx}: model returned "
            f"{len(results) if results else 0} result(s) in {elapsed_ms:.1f}ms",
            file=sys.stderr,
            flush=True,
        )

        # Parse results
        detections: list[Detection] = []
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes
            print(
                f"[DETECT] Frame {frame_idx}: boxes={boxes is not None}, "
                f"num_boxes={len(boxes) if boxes is not None else 0}",
                file=sys.stderr,
                flush=True,
            )

            if boxes is not None and len(boxes) > 0:
                classes = [int(c.item()) for c in boxes.cls]
                confs = [float(c.item()) for c in boxes.conf]
                print(
                    f"[DETECT] Frame {frame_idx}: classes={classes}, confidences={[f'{c:.3f}' for c in confs]}",
                    file=sys.stderr,
                    flush=True,
                )

                for i in range(len(boxes)):
                    class_id = int(boxes.cls[i].item())
                    class_name = COCO_CLASSES[class_id] if class_id < len(COCO_CLASSES) else f"class_{class_id}"
                    conf = float(boxes.conf[i].item())
                    x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                    detections.append(
                        Detection(
                            class_id=class_id,
                            class_name=class_name,
                            confidence=conf,
                            bbox=(x1, y1, x2, y2),
                        )
                    )
        else:
            print(f"[DETECT] Frame {frame_idx}: no results from model", file=sys.stderr, flush=True)

        print(
            f"[DETECT] Frame {frame_idx}: returning {len(detections)} detections",
            file=sys.stderr,
            flush=True,
        )
        return DetectionResult(
            frame=frame_idx,
            detections=detections,
            processing_time_ms=elapsed_ms,
        )

    async def detect_episode(
        self,
        dataset_id: str,
        episode_idx: int,
        request: DetectionRequest,
        get_frame_image: Callable[[int], Awaitable[bytes | None]],
        total_frames: int,
    ) -> EpisodeDetectionSummary:
        """Run detection on episode frames."""
        import sys

        print(
            f"[DETECT] Starting: dataset={dataset_id}, episode={episode_idx}, frames={total_frames}",
            file=sys.stderr,
            flush=True,
        )

        # Determine frames to process
        confidence = request.confidence
        model_name = request.model
        frames_to_process = request.frames if request.frames else list(range(total_frames))
        print(f"[DETECT] Will process {len(frames_to_process)} frames", file=sys.stderr, flush=True)

        results_by_frame: list[DetectionResult] = []
        class_counts: dict[str, list[float]] = {}
        skipped_frames = 0

        for frame_idx in frames_to_process:
            try:
                image_bytes = await get_frame_image(frame_idx)
                if image_bytes is None:
                    skipped_frames += 1
                    if skipped_frames <= 3:
                        print(
                            f"[DETECT] Frame {frame_idx}: image_bytes is None",
                            file=sys.stderr,
                            flush=True,
                        )
                    continue

                if frame_idx == 0:
                    print(
                        f"[DETECT] Frame 0: got {len(image_bytes)} bytes",
                        file=sys.stderr,
                        flush=True,
                    )

                result = await self.detect_frame(
                    image_bytes,
                    frame_idx,
                    confidence=confidence,
                    model_name=model_name,
                )

                if frame_idx == 0:
                    print(
                        f"[DETECT] Frame 0: found {len(result.detections)} detections",
                        file=sys.stderr,
                        flush=True,
                    )

                results_by_frame.append(result)

                # Accumulate class statistics
                for det in result.detections:
                    if det.class_name not in class_counts:
                        class_counts[det.class_name] = []
                    class_counts[det.class_name].append(det.confidence)

            except Exception as e:
                print(f"[DETECT] Frame {frame_idx}: ERROR {e}", file=sys.stderr, flush=True)
                logger.warning(
                    "Failed to process frame %s: %s",
                    str(frame_idx),
                    type(e).__name__,
                )
                continue

        total_dets = sum(len(r.detections) for r in results_by_frame)
        print(
            f"[DETECT] Complete: processed={len(results_by_frame)}, skipped={skipped_frames}, detections={total_dets}",
            file=sys.stderr,
            flush=True,
        )

        # Build class summary
        class_summary = {
            name: ClassSummary(
                count=len(confs),
                avg_confidence=sum(confs) / len(confs) if confs else 0.0,
            )
            for name, confs in class_counts.items()
        }

        total_detections = sum(len(r.detections) for r in results_by_frame)

        summary = EpisodeDetectionSummary(
            total_frames=total_frames,
            processed_frames=len(results_by_frame),
            total_detections=total_detections,
            detections_by_frame=results_by_frame,
            class_summary=class_summary,
        )

        # Cache results
        key = self._cache_key(dataset_id, episode_idx)
        self._cache[key] = summary

        return summary


# Singleton instance
_detection_service: DetectionService | None = None


def get_detection_service() -> DetectionService:
    """Get the singleton detection service instance."""
    global _detection_service
    if _detection_service is None:
        _detection_service = DetectionService()
    return _detection_service
