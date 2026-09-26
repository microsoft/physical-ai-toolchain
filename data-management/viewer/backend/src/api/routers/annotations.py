"""
Annotation API endpoints for LeRobot annotation system.

Provides CRUD endpoints for episode annotations and aggregated
annotation summaries.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response

from ..auth import PrincipalContext, require_principal_context
from ..csrf import require_csrf_token
from ..models.annotations import (
    AnnotationSummary,
    AutoQualityAnalysis,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
)
from ..services.annotation_service import AnnotationService, get_annotation_service
from ..services.dataset_service import DatasetService, get_dataset_service
from ..validation import SAFE_DATASET_ID_PATTERN, path_int_param, path_string_param

router = APIRouter()


# ============================================================================
# Episode Annotation CRUD
# ============================================================================


@router.get(
    "/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
    response_model=EpisodeAnnotationFile,
)
async def get_annotations(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    service: AnnotationService = Depends(get_annotation_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeAnnotationFile:
    """
    Get annotations for a specific episode.

    Returns the complete annotation file including all annotator
    contributions and consensus if available.
    """
    # Verify dataset exists
    dataset = await dataset_service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    versioned = await service.get_annotation_versioned(dataset_id, episode_idx)
    if versioned.value is None:
        # Return empty annotation file if none exists
        return EpisodeAnnotationFile(
            episode_index=episode_idx,
            dataset_id=dataset_id,
        )
    if versioned.etag:
        response.headers["ETag"] = versioned.etag
    return versioned.value


@router.put(
    "/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
    response_model=EpisodeAnnotationFile,
    dependencies=[Depends(require_csrf_token)],
)
async def save_annotations(
    response: Response,
    annotation: EpisodeAnnotation = ...,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    principal: PrincipalContext = Depends(require_principal_context),
    if_match: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
    service: AnnotationService = Depends(get_annotation_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeAnnotationFile:
    """
    Save or update annotations for an episode.

    Adds or updates the annotation for the current user. If an annotation
    from the same user already exists, it will be replaced.
    """
    # Verify dataset exists
    dataset = await dataset_service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    # Verify episode exists
    if episode_idx < 0 or episode_idx >= dataset.total_episodes:
        raise HTTPException(
            status_code=404,
            detail=f"Episode {episode_idx} not found in dataset '{dataset_id}'",
        )

    if if_match is None and if_none_match is None:
        raise HTTPException(status_code=428, detail="If-Match or If-None-Match is required")
    if if_match is not None and if_none_match is not None:
        raise HTTPException(status_code=400, detail="Specify only one revision precondition")
    if if_none_match is not None and if_none_match != "*":
        raise HTTPException(status_code=400, detail="If-None-Match must be '*'")

    owned_annotation = annotation.model_copy(update={"annotator_id": principal.scope_id})
    result = await service.save_annotation(
        dataset_id,
        episode_idx,
        owned_annotation,
        if_match=if_match,
        if_none_match=if_none_match == "*",
    )
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)
    if result.etag:
        response.headers["ETag"] = result.etag
    if result.value is None:
        raise HTTPException(status_code=500, detail="Saved annotation is unavailable")
    return result.value


@router.delete(
    "/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
    response_model=dict,
    dependencies=[Depends(require_csrf_token)],
)
async def delete_annotations(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    principal: PrincipalContext = Depends(require_principal_context),
    if_match: str | None = Header(default=None),
    service: AnnotationService = Depends(get_annotation_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> dict:
    """
    Delete annotations for an episode.

    Removes only the authenticated principal's contribution.
    """
    # Verify dataset exists
    dataset = await dataset_service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    if if_match is None:
        raise HTTPException(status_code=428, detail="If-Match is required")

    deleted = await service.delete_annotation(
        dataset_id,
        episode_idx,
        principal.scope_id,
        if_match=if_match,
    )
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)
    return {"deleted": deleted, "episode_index": episode_idx}


# ============================================================================
# Auto-Analysis
# ============================================================================


@router.post(
    "/datasets/{dataset_id}/episodes/{episode_idx}/annotations/auto",
    response_model=AutoQualityAnalysis,
    dependencies=[Depends(require_csrf_token)],
)
async def trigger_auto_analysis(
    background_tasks: BackgroundTasks,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    service: AnnotationService = Depends(get_annotation_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> AutoQualityAnalysis:
    """
    Trigger automatic quality analysis for an episode.

    Runs trajectory analysis to compute quality metrics and detect anomalies.
    Returns suggested ratings based on computed metrics.
    """
    # Verify dataset exists
    dataset = await dataset_service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    # Get episode data for analysis
    episode = await dataset_service.get_episode(dataset_id, episode_idx)
    if episode is None:
        raise HTTPException(
            status_code=404,
            detail=f"Episode {episode_idx} not found in dataset '{dataset_id}'",
        )

    return await service.run_auto_analysis(dataset_id, episode_idx, episode)


# ============================================================================
# Annotation Summary
# ============================================================================


@router.get(
    "/datasets/{dataset_id}/annotations/summary",
    response_model=AnnotationSummary,
)
async def get_annotation_summary(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    service: AnnotationService = Depends(get_annotation_service),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> AnnotationSummary:
    """
    Get aggregated annotation metrics for a dataset.

    Returns summary statistics including task completeness distribution,
    quality score distribution, and anomaly type counts.
    """
    # Verify dataset exists
    dataset = await dataset_service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    return await service.get_summary(dataset_id, dataset.total_episodes)
