"""Curated dataset release API: capabilities, destinations, durable jobs, and preview."""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response
from physical_ai_curation.release.inputs import SavedSelection
from pydantic import BaseModel, ConfigDict, Field

from ..auth import PrincipalContext, require_principal_context
from ..csrf import require_csrf_token
from ..services.release_service import (
    ReleaseApiError,
    ReleaseService,
    ReleaseSpec,
    get_release_service,
)


class ReleaseErrorBody(BaseModel):
    """Release failures or FastAPI request-validation details."""

    detail: dict[str, str] | list[dict[str, Any]]


router = APIRouter(responses={422: {"model": ReleaseErrorBody}})

JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
MAX_EPISODE_SELECTION = 10_000


class ReleaseSpecBody(BaseModel):
    """Client release selection. Destinations are aliases; no filesystem paths are accepted."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1, max_length=63)
    package_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    dataset_name: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=255)
    episode_indices: list[int] = Field(min_length=1, max_length=MAX_EPISODE_SELECTION)
    resources: list[dict[str, Any]] = Field(default_factory=list, max_length=30000)
    finalized: bool = False
    preview_digest: str = Field(default="", max_length=64)
    processing_digest: str = Field(default="", max_length=64)
    local_disk_acknowledged: bool = False

    def to_spec(self) -> ReleaseSpec:
        return ReleaseSpec(
            destination=self.destination,
            package_id=self.package_id,
            attempt_id=self.attempt_id,
            dataset_name=self.dataset_name or self.package_id,
            dataset_id=self.dataset_id,
            episode_indices=tuple(self.episode_indices),
            resources=tuple(SavedSelection(**item) for item in self.resources),
            finalized=self.finalized,
        )


def _raise(error: ReleaseApiError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code, detail={"schema": "curated-release-api/v1", "code": error.detail}
    ) from error


def _sanitized(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r", "").replace("\n", "")
    if isinstance(value, list):
        return [_sanitized(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitized(item) for key, item in value.items()}
    return value


def _scope(principal: PrincipalContext) -> str:
    return principal.scope_id


@router.get("/capabilities")
async def get_capabilities(
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Report the release API schema and configured capabilities without filesystem paths."""
    return service.capabilities()


@router.get("/destinations")
async def list_destinations(
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> list[dict[str, str]]:
    """List destination aliases available to release jobs."""
    return service.destinations()


@router.get("/jobs")
async def list_jobs(
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """List release jobs owned by the caller, ordered by acceptance."""
    try:
        return service.jobs(_scope(principal), cursor=int(cursor), limit=int(limit))
    except ReleaseApiError as error:
        _raise(error)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: JobId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Return the status of one caller-owned release job."""
    try:
        return service.status(_scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.post("/jobs", status_code=202, dependencies=[Depends(require_csrf_token)])
async def submit_job(
    body: ReleaseSpecBody,
    response: Response,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Accept a release job. Replays with the same Idempotency-Key return the existing job."""
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        body = ReleaseSpecBody.model_validate(_sanitized(body.model_dump()))
        body.episode_indices = [int(index) for index in body.episode_indices]
        accepted = await service.submit(
            _scope(principal),
            body.to_spec(),
            idempotency_key.replace("\r", "").replace("\n", ""),
            preview_digest=body.preview_digest,
            processing_digest=body.processing_digest,
            local_disk_acknowledged=bool(body.local_disk_acknowledged),
        )
    except ReleaseApiError as error:
        _raise(error)
    except (ValueError, TypeError):
        _raise(ReleaseApiError(422, "invalid_release_selection"))
    response.headers["Location"] = f"/api/releases/v1/jobs/{accepted.job_id}"
    response.headers["Retry-After"] = "5"
    return {"job_id": accepted.job_id, "replayed": accepted.replayed}


@router.get("/preview")
async def preview_release(
    selection: str = Query(max_length=1024 * 1024),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Describe a release selection without accepting or reserving output."""
    try:
        body = ReleaseSpecBody.model_validate(_sanitized(json.loads(selection.replace("\r", "").replace("\n", ""))))
        body.episode_indices = [int(index) for index in body.episode_indices]
        return await service.preview(_scope(principal), body.to_spec())
    except ReleaseApiError as error:
        _raise(error)
    except (ValueError, TypeError):
        _raise(ReleaseApiError(422, "invalid_release_selection"))


@router.post("/jobs/{job_id}/cancel", status_code=202, dependencies=[Depends(require_csrf_token)])
async def cancel_job(
    job_id: JobId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Request cancellation of a caller-owned release job."""
    try:
        return service.cancel(_scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.get("/inventory/{dataset_id}")
async def source_inventory(
    dataset_id: str,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return await service.inventory(
            dataset_id.replace("\r", "").replace("\n", ""), cursor=int(cursor), limit=int(limit)
        )
    except ReleaseApiError as error:
        _raise(error)


@router.post("/jobs/{job_id}/recover", status_code=202, dependencies=[Depends(require_csrf_token)])
async def recover_job(
    job_id: JobId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return service.recover(_scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.get("/jobs/{job_id}/result")
async def get_result(
    job_id: JobId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return service.result(_scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases")
async def list_source_releases(
    dataset_id: str,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """List verified releases of a source visible to the caller. Read-only; no paths in output."""
    try:
        return service.source_releases(_scope(principal), dataset_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases/{handle}")
async def get_source_release(
    dataset_id: str,
    handle: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Read one verified source release by opaque handle. Read-only; 404 when not visible."""
    try:
        return service.source_release(_scope(principal), dataset_id.replace("\r", "").replace("\n", ""), handle)
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases/{handle}/episodes")
async def list_source_episodes(
    dataset_id: str,
    handle: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Page native episode metadata of a verified local release. Paths are never returned."""
    try:
        return service.source_episodes(
            _scope(principal),
            dataset_id.replace("\r", "").replace("\n", ""),
            handle,
            cursor=int(cursor),
            limit=int(limit),
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases/{handle}/episodes/{episode_index}/media/{camera}")
async def get_source_episode_media(
    dataset_id: str,
    handle: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    episode_index: Annotated[int, Path(ge=0)],
    camera: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> Response:
    """Return the MP4 container holding one episode's camera stream from a verified local release."""
    try:
        content = service.source_episode_media(
            _scope(principal),
            dataset_id.replace("\r", "").replace("\n", ""),
            handle,
            episode_index=int(episode_index),
            camera=camera,
        )
    except ReleaseApiError as error:
        _raise(error)
    return Response(content=content, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/artifacts/{artifact}")
async def get_artifact(
    job_id: JobId,
    artifact: str,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return service.result(
            _scope(principal), job_id.replace("\r", "").replace("\n", ""), artifact.replace("\r", "").replace("\n", "")
        )
    except ReleaseApiError as error:
        _raise(error)
