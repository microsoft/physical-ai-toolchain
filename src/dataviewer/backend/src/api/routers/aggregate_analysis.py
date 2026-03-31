"""
Aggregate analysis API endpoints.

Provides endpoints for computing dataset-level statistics,
joint state occupancy maps, temporal visitation maps, and
object detection summaries across nested HDF5 datasets.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from ..csrf import require_csrf_token
from ..models.aggregate_analysis import (
    AggregateAnalysisResult,
    DatasetStatistics,
    DetectionSummary,
    JointOccupancyMap,
    TemporalVisitationMap,
)
from ..services.aggregate_analysis import AggregateAnalyzer, get_aggregate_analyzer
from ..services.dataset_service import DatasetService, get_dataset_service
from ..validation import validated_dataset_id

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_parent_path(dataset_id: str, dataset_service: DatasetService) -> Path:
    """Resolve a dataset_id to the parent directory containing sessions.

    For nested IDs like ``hexagon_episodes--session_a``, returns the parent
    ``hexagon_episodes/`` path. For flat IDs, returns the dataset path directly.
    """
    base = Path(dataset_service.base_path)
    # Strip the nested child portion if present
    parent_id = dataset_id.split("--")[0]
    parent_path = base / parent_id
    if not parent_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset directory not found: '{parent_id}'")
    return parent_path


@router.get(
    "/{dataset_id}/aggregate",
    response_model=AggregateAnalysisResult,
)
async def get_aggregate_analysis(
    dataset_id: str = Depends(validated_dataset_id),
    dataset_service: DatasetService = Depends(get_dataset_service),
    analyzer: AggregateAnalyzer = Depends(get_aggregate_analyzer),
    include_detection: bool = Query(default=False, description="Run object detection on sampled frames"),
) -> AggregateAnalysisResult:
    """Run full aggregate analysis on a nested HDF5 dataset."""
    dataset_path = _resolve_parent_path(dataset_id, dataset_service)
    try:
        return analyzer.analyze(
            dataset_id=dataset_id,
            dataset_path=dataset_path,
            include_detection=include_detection,
        )
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e


@router.get(
    "/{dataset_id}/aggregate/statistics",
    response_model=DatasetStatistics,
)
async def get_aggregate_statistics(
    dataset_id: str = Depends(validated_dataset_id),
    dataset_service: DatasetService = Depends(get_dataset_service),
    analyzer: AggregateAnalyzer = Depends(get_aggregate_analyzer),
) -> DatasetStatistics:
    """Get dataset-level statistics (episode counts, lengths, dimensions)."""
    dataset_path = _resolve_parent_path(dataset_id, dataset_service)
    try:
        bundle = analyzer.load_dataset(dataset_path)
        return analyzer.compute_statistics(dataset_id, bundle)
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e


@router.get(
    "/{dataset_id}/aggregate/occupancy",
    response_model=list[JointOccupancyMap],
)
async def get_joint_occupancy(
    dataset_id: str = Depends(validated_dataset_id),
    dataset_service: DatasetService = Depends(get_dataset_service),
    analyzer: AggregateAnalyzer = Depends(get_aggregate_analyzer),
) -> list[JointOccupancyMap]:
    """Get joint state occupancy (2D histograms) for default joint pairs."""
    dataset_path = _resolve_parent_path(dataset_id, dataset_service)
    try:
        bundle = analyzer.load_dataset(dataset_path)
        return analyzer.compute_joint_occupancy(bundle)
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e


@router.get(
    "/{dataset_id}/aggregate/temporal",
    response_model=list[TemporalVisitationMap],
)
async def get_temporal_visitation(
    dataset_id: str = Depends(validated_dataset_id),
    dataset_service: DatasetService = Depends(get_dataset_service),
    analyzer: AggregateAnalyzer = Depends(get_aggregate_analyzer),
) -> list[TemporalVisitationMap]:
    """Get time-resolved joint value distributions across all episodes."""
    dataset_path = _resolve_parent_path(dataset_id, dataset_service)
    try:
        bundle = analyzer.load_dataset(dataset_path)
        return analyzer.compute_temporal_visitation(bundle)
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e


@router.post(
    "/{dataset_id}/aggregate/detect",
    response_model=DetectionSummary,
    dependencies=[Depends(require_csrf_token)],
)
async def run_detection_analysis(
    dataset_id: str = Depends(validated_dataset_id),
    dataset_service: DatasetService = Depends(get_dataset_service),
    analyzer: AggregateAnalyzer = Depends(get_aggregate_analyzer),
    n_samples: int = Query(default=20, ge=1, le=100, description="Number of frames to sample"),
) -> DetectionSummary:
    """Sample frames across the dataset and run YOLO detection for annotation suggestions."""
    dataset_path = _resolve_parent_path(dataset_id, dataset_service)
    try:
        analyzer.n_detection_samples = n_samples
        bundle = analyzer.load_dataset(dataset_path)
        frames = analyzer.sample_frames_for_detection(dataset_path, bundle)
        if not frames:
            return DetectionSummary(total_frames_sampled=0, total_detections=0)
        return analyzer.run_detection_summary(frames)
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
