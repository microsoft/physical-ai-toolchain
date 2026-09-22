"""
YOLO11 object detection service.

Provides singleton model loading and frame-by-frame detection
for HDF5 episode data.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from cachetools import TTLCache
from PIL import Image

from ..config import get_app_config
from ..detection_constants import ALLOWED_DETECTION_MODELS, COCO_CLASSES
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

DEFAULT_OPEN_VOCAB_MODEL = "yolov8s-world"
_OPEN_VOCAB_PREFIXES: tuple[str, ...] = ("yolov8", "yoloe")
_OPEN_VOCAB_TOKENS: tuple[str, ...] = ("world", "worldv2", "yoloe")


def _is_open_vocab_model(model_name: str) -> bool:
    name = model_name.lower()
    if not name.startswith(_OPEN_VOCAB_PREFIXES):
        return False
    return any(token in name for token in _OPEN_VOCAB_TOKENS)


class DetectionModelError(ValueError):
    """Raised when a requested detection model is not an approved local weight file."""


class InvalidDetectionModelError(DetectionModelError):
    """Raised when a request names a model outside the approved identifier set."""


class DetectionModelUnavailableError(DetectionModelError):
    """Raised when an approved model is not safely available to the service."""


class DetectionService:
    """Object detection service supporting closed-vocabulary YOLO11 and open-vocabulary YOLO-World."""

    def __init__(
        self,
        models_dir: str | Path = "./models",
        cache_max_size: int = 100,
        cache_ttl_seconds: float = 3600,
        default_confidence: float = 0.1,
        model_digests: Mapping[str, str] | None = None,
        cache_timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model: YOLO | None = None
        self._model_name: str = ""
        self._model_classes: tuple[str, ...] = ()
        self._models_dir = Path(models_dir).expanduser().resolve()
        self._model_digests = dict(model_digests or {})
        self._verified_models_dir = tempfile.TemporaryDirectory(prefix="detection-models-")
        self._cache: TTLCache[str, EpisodeDetectionSummary] = TTLCache(
            maxsize=cache_max_size,
            ttl=cache_ttl_seconds,
            timer=cache_timer,
        )
        self._default_confidence = default_confidence

    def _resolve_model_path(self, model_name: str) -> Path:
        """Resolve and verify an approved local weight file."""
        self._validate_model_identifier(model_name)

        model_path = (self._models_dir / f"{model_name}.pt").resolve()
        if not model_path.is_relative_to(self._models_dir):
            raise DetectionModelUnavailableError("Approved model resolves outside configured models directory")
        if not model_path.is_file():
            raise DetectionModelUnavailableError(f"Approved model file not found for identifier '{model_name}'")
        expected_digest = self._model_digests.get(model_name)
        if expected_digest is None:
            raise DetectionModelUnavailableError(
                f"Approved model digest is not configured for identifier '{model_name}'"
            )
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != expected_digest:
            raise DetectionModelUnavailableError(f"Approved model integrity check failed for identifier '{model_name}'")
        return model_path

    def _stage_verified_model(self, model_name: str) -> Path:
        source_path = self._resolve_model_path(model_name)
        model_bytes = source_path.read_bytes()
        if hashlib.sha256(model_bytes).hexdigest() != self._model_digests[model_name]:
            raise DetectionModelUnavailableError(f"Approved model integrity check failed for identifier '{model_name}'")

        with tempfile.NamedTemporaryFile(
            dir=self._verified_models_dir.name,
            suffix=".pt",
            delete=False,
        ) as staged_file:
            staged_file.write(model_bytes)
            staged_path = Path(staged_file.name)
        staged_path.chmod(0o400)
        return staged_path

    @staticmethod
    def _validate_model_identifier(model_name: str) -> None:
        """Reject model identifiers outside the approved set."""
        if model_name not in ALLOWED_DETECTION_MODELS:
            raise InvalidDetectionModelError(
                f"Model must be an approved model identifier: {', '.join(sorted(ALLOWED_DETECTION_MODELS))}"
            )

    @staticmethod
    def _effective_model_name(model_name: str, labels: list[str] | None) -> str:
        model_name = model_name.replace("\r", "").replace("\n", "")
        DetectionService._validate_model_identifier(model_name)
        if labels and not _is_open_vocab_model(model_name):
            return DEFAULT_OPEN_VOCAB_MODEL
        return model_name

    def effective_confidence(self, request: DetectionRequest) -> float:
        """Resolve the request confidence against the configured service default."""
        return request.confidence if request.confidence is not None else self._default_confidence

    def _get_model(
        self,
        model_name: str = "yolo11n",
        labels: list[str] | None = None,
    ) -> YOLO:
        """Load or return cached YOLO model.

        When ``labels`` is provided, an open-vocabulary YOLO-World model is selected and its
        class vocabulary is set to the supplied labels. The default closed-vocabulary model
        is overridden to ``DEFAULT_OPEN_VOCAB_MODEL`` if a closed-vocab name is passed alongside labels.
        """
        model_name = self._effective_model_name(model_name, labels)

        if self._model is None or self._model_name != model_name:
            try:
                from ultralytics import YOLO
            except ImportError:
                logger.error("ultralytics not installed. Run: uv sync --extra yolo")
                raise
            try:
                model_path = self._stage_verified_model(model_name)
                logger.info("Loading YOLO model: %s", model_name.replace("\r", "").replace("\n", ""))
                model = YOLO(str(model_path))

                import numpy as np

                dummy = np.zeros((640, 640, 3), dtype=np.uint8)
                model(dummy, verbose=False)
            except DetectionModelError:
                raise
            except Exception as exc:
                raise DetectionModelUnavailableError("Configured detection model is unavailable") from exc

            self._model = model
            self._model_name = model_name
            self._model_classes = ()
            logger.info("YOLO model loaded and warmed up")

        if labels:
            label_tuple = tuple(labels)
            if self._model_classes != label_tuple:
                set_classes = getattr(self._model, "set_classes", None)
                if set_classes is None:
                    raise ValueError(
                        f"Model '{model_name}' does not support open-vocabulary labels; "
                        "use a YOLO-World variant such as yolov8s-world."
                    )
                set_classes(list(label_tuple))
                self._model_classes = label_tuple
                logger.info("Set open-vocabulary classes: %d label(s)", len(label_tuple))
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
        labels: list[str] | None = None,
    ) -> DetectionResult:
        """Run detection on a single frame.

        When ``labels`` is provided, an open-vocabulary YOLO-World model is used and class
        names are resolved against the supplied label list rather than the COCO vocabulary.
        """
        import sys

        model = self._get_model(model_name, labels=labels)
        # Resolve class-name lookup: the loaded model's `names` is authoritative for both
        # closed- and open-vocabulary models (YOLO-World updates it via ``set_classes``).
        model_names = getattr(model, "names", None)
        class_lookup: dict[int, str] = {}
        if isinstance(model_names, dict):
            class_lookup = {int(k): str(v) for k, v in model_names.items()}
        elif isinstance(model_names, list):
            class_lookup = {idx: str(name) for idx, name in enumerate(model_names)}

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
                    if class_id in class_lookup:
                        class_name = class_lookup[class_id]
                    elif class_id < len(COCO_CLASSES):
                        class_name = COCO_CLASSES[class_id]
                    else:
                        class_name = f"class_{class_id}"
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
        confidence = self.effective_confidence(request)
        model_name = self._effective_model_name(request.model, request.labels)
        labels = request.labels
        self._resolve_model_path(model_name)
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
                    labels=labels,
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

            except (DetectionModelError, ImportError):
                raise
            except Exception as e:
                print(f"[DETECT] Frame {frame_idx}: ERROR {e}", file=sys.stderr, flush=True)
                logger.warning(
                    "Failed to process frame %d: %s",
                    int(frame_idx),
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
        config = get_app_config()
        _detection_service = DetectionService(
            models_dir=config.detection_models_dir,
            model_digests=config.detection_model_digests,
            cache_max_size=config.detection_cache_max_size,
            cache_ttl_seconds=config.detection_cache_ttl_seconds,
            default_confidence=config.detection_confidence_threshold,
        )
    return _detection_service
