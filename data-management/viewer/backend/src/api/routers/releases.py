"""Sanitized API routes for durable release jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..csrf import require_csrf_token
from ..models.release_workflow import ReleaseEligibility, ReleaseSubmitRequest, ReleaseWorkflowResponse
from ..release.jobs import JobConflictError
from ..services.release_workflow_service import (
    NoEligibleEpisodesError,
    ReleaseWorkflowService,
    get_release_workflow_service,
)
from ..validation import path_string_param, query_string_param, sanitize_user_string, validate_safe_string

router = APIRouter()

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,254}$"


def _sanitize_request(request: ReleaseSubmitRequest) -> ReleaseSubmitRequest:
    return request.model_copy(
        update={
            "release_id": validate_safe_string(request.release_id, pattern=_ID_PATTERN, label="release ID"),
            "dataset_id": validate_safe_string(request.dataset_id, pattern=_ID_PATTERN, label="dataset ID"),
            "actor_id": validate_safe_string(request.actor_id, pattern=_ID_PATTERN, label="actor ID"),
            "reason": sanitize_user_string(request.reason),
            "destination_kind": validate_safe_string(request.destination_kind, label="destination kind"),
            "idempotency_key": validate_safe_string(
                request.idempotency_key,
                pattern=_ID_PATTERN,
                label="idempotency key",
            ),
        }
    )


@router.post(
    "/eligibility",
    response_model=ReleaseEligibility,
    dependencies=[Depends(require_csrf_token)],
)
async def evaluate_release(
    request: ReleaseSubmitRequest,
    service: ReleaseWorkflowService = Depends(get_release_workflow_service),
) -> ReleaseEligibility:
    return await service.evaluate(_sanitize_request(request))


@router.post(
    "",
    response_model=ReleaseWorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf_token)],
)
async def submit_release(
    request: ReleaseSubmitRequest,
    service: ReleaseWorkflowService = Depends(get_release_workflow_service),
) -> ReleaseWorkflowResponse:
    request = _sanitize_request(request)
    try:
        return await service.submit(request)
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoEligibleEpisodesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs/{job_id}", response_model=ReleaseWorkflowResponse)
async def get_release_status(
    job_id: str = Depends(path_string_param("job_id", pattern=_ID_PATTERN, label="release job ID")),
    service: ReleaseWorkflowService = Depends(get_release_workflow_service),
) -> ReleaseWorkflowResponse:
    try:
        return service.get_status(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Release job not found") from exc


@router.get("/jobs", response_model=list[ReleaseWorkflowResponse])
async def list_release_statuses(
    dataset_id: str = Depends(query_string_param("datasetId", default=None, pattern=_ID_PATTERN, label="dataset ID")),
    service: ReleaseWorkflowService = Depends(get_release_workflow_service),
) -> list[ReleaseWorkflowResponse]:
    if dataset_id is None:
        raise HTTPException(status_code=422, detail="datasetId is required")
    return service.list_statuses(dataset_id)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=ReleaseWorkflowResponse,
    dependencies=[Depends(require_csrf_token)],
)
async def cancel_release(
    job_id: str = Depends(path_string_param("job_id", pattern=_ID_PATTERN, label="release job ID")),
    service: ReleaseWorkflowService = Depends(get_release_workflow_service),
) -> ReleaseWorkflowResponse:
    try:
        return service.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Release job not found") from exc


@router.get("/{release_id}", response_model=ReleaseWorkflowResponse)
async def inspect_release(
    release_id: str = Depends(path_string_param("release_id", pattern=_ID_PATTERN, label="release ID")),
    service: ReleaseWorkflowService = Depends(get_release_workflow_service),
) -> ReleaseWorkflowResponse:
    try:
        return service.inspect(release_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
