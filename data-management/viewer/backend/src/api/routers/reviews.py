"""Sanitized API routes for immutable review and quality evidence."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..csrf import require_csrf_token
from ..models.review_workflow import QualityRunRequest
from ..models.reviews import AnnotationRevision, EditRevision, QualityReport, ReviewDecision
from ..services.review_service import ReviewIntegrityError
from ..services.review_workflow_service import (
    ReviewRouteMismatchError,
    ReviewWorkflowService,
    get_review_workflow_service,
)
from ..storage.review_base import DuplicateReviewRecordError, ReviewStorageError
from ..validation import SAFE_DATASET_ID_PATTERN, path_int_param, path_string_param, sanitize_user_string

router = APIRouter()

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,254}$"


@router.post(
    "/datasets/{dataset_id}/episodes/{episode_idx}/review/annotation-revisions",
    response_model=AnnotationRevision,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf_token)],
)
async def create_annotation_revision(
    revision: AnnotationRevision,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: ReviewWorkflowService = Depends(get_review_workflow_service),
) -> AnnotationRevision:
    revision = revision.model_copy(update={"actor_id": sanitize_user_string(revision.actor_id)})
    return await _create(service.create_annotation_revision(dataset_id, episode_idx, revision))


@router.post(
    "/datasets/{dataset_id}/episodes/{episode_idx}/review/edit-revisions",
    response_model=EditRevision,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf_token)],
)
async def create_edit_revision(
    revision: EditRevision,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: ReviewWorkflowService = Depends(get_review_workflow_service),
) -> EditRevision:
    revision = revision.model_copy(update={"actor_id": sanitize_user_string(revision.actor_id)})
    return await _create(service.create_edit_revision(dataset_id, episode_idx, revision))


@router.post(
    "/datasets/{dataset_id}/episodes/{episode_idx}/review/quality-reports",
    response_model=QualityReport,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf_token)],
)
async def create_quality_report(
    report: QualityReport,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: ReviewWorkflowService = Depends(get_review_workflow_service),
) -> QualityReport:
    report = report.model_copy(update={"actor_id": sanitize_user_string(report.actor_id)})
    return await _create(service.create_quality_report(dataset_id, episode_idx, report))


@router.post(
    "/datasets/{dataset_id}/episodes/{episode_idx}/review/quality-runs",
    response_model=QualityReport,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf_token)],
)
async def run_quality(
    request: QualityRunRequest,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: ReviewWorkflowService = Depends(get_review_workflow_service),
) -> QualityReport:
    request = request.model_copy(
        update={
            "actor_id": sanitize_user_string(request.actor_id),
            "reason": sanitize_user_string(request.reason) if request.reason is not None else None,
        }
    )
    return await _create(service.run_quality(dataset_id, episode_idx, request))


@router.get(
    "/datasets/{dataset_id}/episodes/{episode_idx}/review/quality-reports/{run_id}",
    response_model=QualityReport,
)
async def get_quality_report(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    run_id: str = Depends(path_string_param("run_id", pattern=_ID_PATTERN, label="quality run ID")),
    service: ReviewWorkflowService = Depends(get_review_workflow_service),
) -> QualityReport:
    try:
        report = await service.get_quality_report(dataset_id, episode_idx, run_id)
    except (ReviewRouteMismatchError, ReviewStorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(status_code=404, detail="Quality report not found")
    return report


@router.post(
    "/datasets/{dataset_id}/episodes/{episode_idx}/review/decisions",
    response_model=ReviewDecision,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf_token)],
)
async def create_decision(
    decision: ReviewDecision,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: ReviewWorkflowService = Depends(get_review_workflow_service),
) -> ReviewDecision:
    decision = decision.model_copy(
        update={
            "actor_id": sanitize_user_string(decision.actor_id),
            "notes": sanitize_user_string(decision.notes) if decision.notes is not None else None,
            "reason_codes": tuple(sanitize_user_string(value) for value in decision.reason_codes),
        }
    )
    return await _create(service.create_decision(dataset_id, episode_idx, decision))


async def _create(operation):
    try:
        return await operation
    except DuplicateReviewRecordError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ReviewIntegrityError, ReviewRouteMismatchError, ReviewStorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
