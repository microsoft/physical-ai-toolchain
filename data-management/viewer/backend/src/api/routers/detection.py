"""
Detection API endpoints for YOLO11 object detection.

Provides endpoints for running detection on episode frames
and retrieving cached results.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..csrf import require_csrf_token
from ..models.detection import DetectionRequest, EpisodeDetectionSummary
from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.detection_service import DetectionService, get_detection_service
from ..validation import SAFE_DATASET_ID_PATTERN, path_int_param, path_string_param

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

RATE_LIMIT_DETECT = os.environ.get("RATE_LIMIT_DETECT", "10/minute")
RATE_LIMIT_DETECTIONS = os.environ.get("RATE_LIMIT_DETECTIONS", "120/minute")


@router.post(
    "/{dataset_id}/episodes/{episode_idx}/detect",
    response_model=EpisodeDetectionSummary,
    dependencies=[Depends(require_csrf_token)],
)
@limiter.limit(RATE_LIMIT_DETECT)
async def run_detection(
    request: Request,
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    request_body: DetectionRequest = DetectionRequest(),
    detection_service: DetectionService = Depends(get_detection_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeDetectionSummary:
    """
    Run YOLO11 object detection on episode frames.

    Processes specified frames (or all frames if not specified) and
    returns detection results with bounding boxes and class labels.
    Results are cached for subsequent retrieval.
    """
    logger.info(
        "POST /detect: dataset=%s, episode=%d, model=%s, confidence=%s",
        dataset_id,
        episode_idx,
        request_body.model,
        request_body.confidence,
    )

    # Validate episode exists
    episode = await dataset_service.get_episode(dataset_id, episode_idx)
    if episode is None:
        logger.warning("Episode %d not found in dataset %s", episode_idx, dataset_id)
        raise HTTPException(
            status_code=404,
            detail=f"Episode {episode_idx} not found in dataset '{dataset_id}'",
        )

    total_frames = episode.meta.length
    logger.info("Episode has %d frames", total_frames)

    # Create frame image getter
    async def get_frame_image(frame_idx: int) -> bytes | None:
        return await dataset_service.get_frame_image(dataset_id, episode_idx, frame_idx, "il-camera")

    try:
        summary = await detection_service.detect_episode(
            dataset_id,
            episode_idx,
            request_body,
            get_frame_image,
            total_frames,
        )
        return summary
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="YOLO dependencies not installed. Run: uv sync --extra yolo",
        )
    except Exception:
        logger.exception("Detection failed")
        raise HTTPException(
            status_code=500,
            detail="Detection failed",
        )


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/detections",
    response_model=EpisodeDetectionSummary | None,
)
@limiter.limit(RATE_LIMIT_DETECTIONS)
async def get_detections(
    request: Request,
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    detection_service: DetectionService = Depends(get_detection_service),
) -> EpisodeDetectionSummary | None:
    """
    Get cached detection results for an episode.

    Returns None if no detection has been run yet.
    """
    return detection_service.get_cached(dataset_id, episode_idx)


@router.delete(
    "/{dataset_id}/episodes/{episode_idx}/detections",
    dependencies=[Depends(require_csrf_token)],
)
@limiter.limit(RATE_LIMIT_DETECTIONS)
async def clear_detections(
    request: Request,
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    detection_service: DetectionService = Depends(get_detection_service),
) -> dict[str, bool]:
    """
    Clear cached detection results for an episode.

    Use this after frame edits to force re-detection.
    """
    cleared = detection_service.clear_cache(dataset_id, episode_idx)
    return {"cleared": cleared}
