"""Curated dataset release API: capabilities, destinations, durable jobs, and preview."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from physical_ai_curation.release.inputs import SavedSelection
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from ..auth import PrincipalContext, require_principal_context
from ..csrf import require_csrf_token
from ..models.errors import ErrorResponse
from ..services.release_service import (
    ReleaseApiError,
    ReleaseService,
    ReleaseSpec,
    get_release_service,
)
from ..validation import DATASET_ID_INPUT_PATTERN, JsonInteger, path_int_param, query_int_param


class ReleaseErrorBody(BaseModel):
    """Release failures or FastAPI request-validation details."""

    detail: dict[str, Any] | list[dict[str, Any]]


router = APIRouter(
    responses={
        status: {"model": ReleaseErrorBody | ErrorResponse} for status in (400, 404, 409, 422, 499, 503, 504, 507)
    }
)

JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
MAX_EPISODE_SELECTION = 10_000
_IDENTITY_PATTERN = r"^[\r\n]*[A-Za-z0-9](?:[\r\n]*[A-Za-z0-9_-]){0,127}[\r\n]*$"
_NAME_PATTERN = r"^(?![\s\S]*-[\r\n]*-)[\r\n]*[A-Za-z0-9](?:[\r\n]*[A-Za-z0-9._-]){0,127}[\r\n]*$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
ReleaseDatasetId = Annotated[str, Path(pattern=DATASET_ID_INPUT_PATTERN)]


class SavedResourceBody(BaseModel):
    """Persisted resource identity; content is read from storage rather than supplied by callers."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {"properties": {"kind": {"const": "annotation"}}},
            "else": {"properties": {"annotation_author_id": {"type": "null"}}},
        },
    )

    kind: Literal["annotation", "edits", "labels", "ledger", "evidence", "recording_receipt", "import_mapping"]
    episode_index: Annotated[JsonInteger, Field(ge=0, json_schema_extra={"minimum": 0})]
    expected_revision: str = Field(min_length=1, pattern=r"[^\r\n]")
    annotation_author_id: str | None = Field(default=None, min_length=1, pattern=r"[^\r\n]")


class ReleaseSpecBody(BaseModel):
    """Client release selection. Destinations are aliases; no filesystem paths are accepted."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {"properties": {"dataset_name": {"type": "null"}}},
            "then": {"properties": {"package_id": {"pattern": r"^(?![\s\S]*-[\r\n]*-)[\s\S]*$"}}},
        },
    )

    destination: str = Field(min_length=1, max_length=63, pattern=r"^[\r\n]*[a-z](?:[\r\n]*[a-z0-9-]){0,62}[\r\n]*$")
    package_id: str = Field(min_length=1, max_length=128, pattern=_IDENTITY_PATTERN)
    attempt_id: str = Field(min_length=1, max_length=128, pattern=_IDENTITY_PATTERN)
    dataset_name: str | None = Field(default=None, min_length=1, max_length=128, pattern=re.compile(_NAME_PATTERN))
    dataset_id: str = Field(min_length=1, max_length=255, pattern=DATASET_ID_INPUT_PATTERN)
    episode_indices: list[Annotated[JsonInteger, Field(ge=0, json_schema_extra={"minimum": 0})]] = Field(
        min_length=1, max_length=MAX_EPISODE_SELECTION, json_schema_extra={"uniqueItems": True}
    )
    resources: list[SavedResourceBody] = Field(default_factory=list, max_length=30000)
    finalized: StrictBool = False
    description: str = Field(default="", max_length=2000)
    preview_digest: str = Field(default="", max_length=64)
    processing_digest: str = Field(default="", max_length=64)
    local_disk_acknowledged: StrictBool = False

    @field_validator("episode_indices")
    @classmethod
    def unique_episodes(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("episode selection contains duplicates")
        return value

    def to_spec(self) -> ReleaseSpec:
        return ReleaseSpec(
            destination=self.destination,
            package_id=self.package_id,
            attempt_id=self.attempt_id,
            dataset_name=self.dataset_name or self.package_id,
            dataset_id=self.dataset_id,
            episode_indices=tuple(self.episode_indices),
            resources=tuple(SavedSelection(**item.model_dump()) for item in self.resources),
            finalized=self.finalized,
            description=self.description,
        )


def _raise(error: ReleaseApiError) -> NoReturn:
    detail: dict[str, Any] = {"schema": "curated-release-api/v1", "code": error.detail}
    if error.nonretryable:
        detail["nonretryable"] = True
        detail["message"] = error.message
    raise HTTPException(status_code=error.status_code, detail=detail) from error


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


async def _while_connected[Result](request: Request, operation: Awaitable[Result]) -> Result:
    """Cancel request work on disconnect after FastAPI has consumed the request body."""

    async def disconnected() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    pending = asyncio.ensure_future(operation)
    disconnect = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({pending, disconnect}, return_when=asyncio.FIRST_COMPLETED)
        if pending in done:
            return pending.result()
        disconnect.result()
        raise ReleaseApiError(499, "request_disconnected")
    finally:
        pending.cancel()
        disconnect.cancel()
        await asyncio.gather(pending, disconnect, return_exceptions=True)


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
) -> list[dict[str, str | bool]]:
    """List destination aliases available to release jobs."""
    return service.destinations()


@router.get("/jobs")
async def list_jobs(
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
    cursor: int = Depends(query_int_param("cursor", default=0, ge=0)),
    limit: int = Depends(query_int_param("limit", default=50, ge=1, le=100)),
    dataset_id: str | None = Query(default=None, max_length=255),
) -> dict[str, Any]:
    """List release jobs owned by the caller, ordered by acceptance."""
    try:
        return await service.read(
            service.jobs,
            _scope(principal),
            cursor=int(cursor),
            limit=int(limit),
            dataset_id=_sanitized(dataset_id),
            response_bytes=service.metadata_bytes,
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/requests")
async def lookup_request(
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=_IDEMPOTENCY_PATTERN),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Resolve a previously accepted request without repeating selection or source access."""
    try:
        return await service.control(
            service.accepted_request, _scope(principal), idempotency_key.replace("\r", "").replace("\n", "")
        )
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
        return await service.control(service.status, _scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.post("/jobs", status_code=202, dependencies=[Depends(require_csrf_token)])
async def submit_job(
    body: ReleaseSpecBody,
    response: Response,
    request: Request,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
    idempotency_key: str = Header(alias="Idempotency-Key", pattern=_IDEMPOTENCY_PATTERN),
) -> dict[str, Any]:
    """Accept a release job. Replays with the same Idempotency-Key return the existing job."""
    try:
        body = ReleaseSpecBody.model_validate(_sanitized(body.model_dump()))
        body.episode_indices = [int(index) for index in body.episode_indices]
        accepted = await _while_connected(
            request,
            service.submit(
                _scope(principal),
                body.to_spec(),
                idempotency_key.replace("\r", "").replace("\n", ""),
                preview_digest=body.preview_digest,
                processing_digest=body.processing_digest,
                local_disk_acknowledged=bool(body.local_disk_acknowledged),
            ),
        )
    except ReleaseApiError as error:
        _raise(error)
    except (ValueError, TypeError):
        _raise(ReleaseApiError(422, "invalid_release_selection"))
    response.headers["Location"] = f"/api/releases/v1/jobs/{accepted.job_id}"
    response.headers["Retry-After"] = "5"
    return {"job_id": accepted.job_id, "replayed": accepted.replayed}


@router.post("/preview", dependencies=[Depends(require_csrf_token)])
async def preview_release_body(
    body: ReleaseSpecBody,
    request: Request,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Preview a bounded body without accepting a job or reserving output."""
    try:
        body = ReleaseSpecBody.model_validate(_sanitized(body.model_dump()))
        body.episode_indices = [int(index) for index in body.episode_indices]
        return await _while_connected(request, service.preview(_scope(principal), body.to_spec()))
    except ReleaseApiError as error:
        _raise(error)
    except (ValueError, TypeError):
        _raise(ReleaseApiError(422, "invalid_release_selection"))


@router.get("/preview")
async def preview_release(
    request: Request,
    selection: str = Query(
        max_length=1024 * 1024,
        json_schema_extra={
            "format": "release-selection",
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/components/schemas/ReleaseSpecBody"},
        },
    ),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Describe a release selection without accepting or reserving output."""
    try:
        body = ReleaseSpecBody.model_validate(_sanitized(json.loads(selection.replace("\r", "").replace("\n", ""))))
        body.episode_indices = [int(index) for index in body.episode_indices]
        return await _while_connected(request, service.preview(_scope(principal), body.to_spec()))
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
        return await service.control(service.cancel, _scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.post("/jobs/{job_id}/registration", dependencies=[Depends(require_csrf_token)])
async def retry_registration(
    job_id: JobId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Retry registration of a caller-owned committed release without republishing it."""
    try:
        return await service.control(
            service.retry_registration, _scope(principal), job_id.replace("\r", "").replace("\n", "")
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/inventory/{dataset_id}")
async def source_inventory(
    dataset_id: ReleaseDatasetId,
    cursor: int = Depends(query_int_param("cursor", default=0, ge=0)),
    limit: int = Depends(query_int_param("limit", default=50, ge=1, le=100)),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return await service.inventory(
            dataset_id.replace("\r", "").replace("\n", ""),
            cursor=int(cursor),
            limit=int(limit),
            scope=_scope(principal),
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/inventory/{dataset_id}/details")
async def source_inventory_details(
    dataset_id: ReleaseDatasetId,
    episodes: str = Query(
        max_length=1024,
        description="Comma-separated list of 1 to 100 distinct integer episode indices.",
        json_schema_extra={"format": "release-episodes"},
    ),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Return full saved evidence for up to 100 explicitly listed episode indices."""
    try:
        indices = [int(part) for part in episodes.split(",")]
    except ValueError:
        _raise(ReleaseApiError(422, "invalid_inventory_details"))
    try:
        return await service.inventory_details(
            dataset_id.replace("\r", "").replace("\n", ""),
            episodes=indices,
            scope=_scope(principal),
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
        return await service.control(service.recover, _scope(principal), job_id.replace("\r", "").replace("\n", ""))
    except ReleaseApiError as error:
        _raise(error)


@router.get("/jobs/{job_id}/result")
async def get_result(
    job_id: JobId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return await service.read(
            service.result,
            _scope(principal),
            job_id.replace("\r", "").replace("\n", ""),
            response_bytes=service.metadata_bytes,
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases")
async def list_source_releases(
    dataset_id: ReleaseDatasetId,
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """List verified releases of a source visible to the caller. Read-only; no paths in output."""
    try:
        return await service.read(
            service.source_releases,
            _scope(principal),
            dataset_id.replace("\r", "").replace("\n", ""),
            response_bytes=service.metadata_bytes,
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases/{handle}")
async def get_source_release(
    dataset_id: ReleaseDatasetId,
    handle: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Read one verified source release by opaque handle. Read-only; 404 when not visible."""
    try:
        return await service.read(
            service.source_release,
            _scope(principal),
            dataset_id.replace("\r", "").replace("\n", ""),
            handle,
            response_bytes=service.metadata_bytes,
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get("/sources/{dataset_id}/releases/{handle}/episodes")
async def list_source_episodes(
    dataset_id: ReleaseDatasetId,
    handle: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    cursor: int = Depends(query_int_param("cursor", default=0, ge=0)),
    limit: int = Depends(query_int_param("limit", default=50, ge=1, le=100)),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    """Page native episode metadata of a verified local release. Paths are never returned."""
    try:
        return await service.read(
            service.source_episodes,
            _scope(principal),
            dataset_id.replace("\r", "").replace("\n", ""),
            handle,
            cursor=int(cursor),
            limit=int(limit),
            response_bytes=service.metadata_bytes,
        )
    except ReleaseApiError as error:
        _raise(error)


@router.get(
    "/sources/{dataset_id}/releases/{handle}/episodes/{episode_index}/media/{camera}",
    response_class=Response,
    responses={200: {"content": {"video/mp4": {}}}},
)
async def get_source_episode_media(
    request: Request,
    dataset_id: ReleaseDatasetId,
    handle: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    camera: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,128}$")],
    episode_index: int = Depends(path_int_param("episode_index", ge=0)),
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> Response:
    """Return the MP4 container holding one episode's camera stream from a verified local release."""
    try:
        content = await _while_connected(
            request,
            service.read(
                service.source_episode_media,
                _scope(principal),
                dataset_id.replace("\r", "").replace("\n", ""),
                handle,
                episode_index=int(episode_index),
                camera=camera,
                response_bytes=service.media_bytes,
            ),
        )
    except ReleaseApiError as error:
        _raise(error)
    return Response(content=content, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/artifacts/{artifact}")
async def get_artifact(
    job_id: JobId,
    artifact: Annotated[
        str, Path(description="Supported artifacts: manifest, inventory, receipt, checksums. Others return 404.")
    ],
    principal: PrincipalContext = Depends(require_principal_context),
    service: ReleaseService = Depends(get_release_service),
) -> dict[str, Any]:
    try:
        return await service.read(
            service.result,
            _scope(principal),
            job_id.replace("\r", "").replace("\n", ""),
            artifact.replace("\r", "").replace("\n", ""),
            response_bytes=service.metadata_bytes,
        )
    except ReleaseApiError as error:
        _raise(error)
