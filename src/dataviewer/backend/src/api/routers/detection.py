"""
Detection API endpoints for YOLO11 object detection.

Provides endpoints for running detection on episode frames
and retrieving cached results.
"""

import logging
import sys

from fastapi import APIRouter, Depends, HTTPException

from ..models.detection import DetectionRequest, EpisodeDetectionSummary
from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.detection_service import DetectionService, get_detection_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/{dataset_id}/episodes/{episode_idx}/detect",
    response_model=EpisodeDetectionSummary,
)
async def run_detection(
    dataset_id: str,
    episode_idx: int,
    request: DetectionRequest = DetectionRequest(),
    detection_service: DetectionService = Depends(get_detection_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeDetectionSummary:
    """
    Run YOLO11 object detection on episode frames.

    Processes specified frames (or all frames if not specified) and
    returns detection results with bounding boxes and class labels.
    Results are cached for subsequent retrieval.
    """
    print(f"\n{'=' * 60}", file=sys.stderr, flush=True)
    print(
        f"[API] POST /detect called: dataset={dataset_id}, episode={episode_idx}",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"[API] Request: model={request.model}, confidence={request.confidence}",
        file=sys.stderr,
        flush=True,
    )
    print(f"{'=' * 60}", file=sys.stderr, flush=True)

    # Validate episode exists
    episode = await dataset_service.get_episode(dataset_id, episode_idx)
    if episode is None:
        print("[API] ERROR: Episode not found", file=sys.stderr, flush=True)
        raise HTTPException(
            status_code=404,
            detail=f"Episode {episode_idx} not found in dataset '{dataset_id}'",
        )

    total_frames = episode.meta.length
    print(f"[API] Episode has {total_frames} frames", file=sys.stderr, flush=True)

    # Create frame image getter
    async def get_frame_image(frame_idx: int) -> bytes | None:
        return await dataset_service.get_frame_image(dataset_id, episode_idx, frame_idx, "il-camera")

    try:
        summary = await detection_service.detect_episode(
            dataset_id,
            episode_idx,
            request,
            get_frame_image,
            total_frames,
        )
        return summary
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="YOLO dependencies not installed. Run: uv sync --extra yolo",
        )
    except Exception as e:
        logger.exception("Detection failed")
        raise HTTPException(
            status_code=500,
            detail=f"Detection failed: {e!s}",
        )


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/detections",
    response_model=EpisodeDetectionSummary | None,
)
async def get_detections(
    dataset_id: str,
    episode_idx: int,
    detection_service: DetectionService = Depends(get_detection_service),
) -> EpisodeDetectionSummary | None:
    """
    Get cached detection results for an episode.

    Returns None if no detection has been run yet.
    """
    return detection_service.get_cached(dataset_id, episode_idx)


@router.delete("/{dataset_id}/episodes/{episode_idx}/detections")
async def clear_detections(
    dataset_id: str,
    episode_idx: int,
    detection_service: DetectionService = Depends(get_detection_service),
) -> dict[str, bool]:
    """
    Clear cached detection results for an episode.

    Use this after frame edits to force re-detection.
    """
    cleared = detection_service.clear_cache(dataset_id, episode_idx)
    return {"cleared": cleared}
