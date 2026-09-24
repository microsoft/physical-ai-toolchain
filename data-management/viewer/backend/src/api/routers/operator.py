"""Operator capability, calibration, preflight, and session routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ..csrf import require_csrf_token
from ..operator.authorization import require_hardware_access, require_operator_access, require_operator_csrf
from ..operator.calibration import inspect_operator_calibration
from ..operator.models import (
    OperatorCalibrationReport,
    OperatorCapabilities,
    OperatorCommand,
    OperatorStatus,
    PreflightRequest,
    PreflightResult,
    StartSessionRequest,
)
from ..services.operator_preflight_service import OperatorPreflightConflictError, OperatorPreflightNotFoundError
from ..services.operator_service import OperatorConflictError, OperatorDisabledError, OperatorPreconditionError

router = APIRouter(dependencies=[Depends(require_operator_access)])
_MUTATION_DEPENDENCIES = [
    Depends(require_csrf_token),
    Depends(require_operator_csrf),
    Depends(require_hardware_access),
]


def get_operator_service(request: Request) -> Any:
    service = getattr(request.app.state, "operator_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Operator service unavailable")
    return service


def get_preflight_service(request: Request) -> Any:
    service = getattr(request.app.state, "operator_preflight_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Operator preflight is disabled")
    return service


@router.get("/capabilities", response_model=OperatorCapabilities)
async def get_capabilities(service: Any = Depends(get_operator_service)) -> OperatorCapabilities:
    return service.capabilities()


@router.get("/status", response_model=OperatorStatus)
async def get_status(service: Any = Depends(get_operator_service)) -> OperatorStatus:
    return service.status()


@router.get("/cameras/{camera}/frame")
async def get_camera_frame(camera: str, service: Any = Depends(get_operator_service)) -> Response:
    sanitized = camera.replace("\r", "").replace("\n", "")
    try:
        frame = service.camera_frame(sanitized)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Operator camera not found") from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Operator camera frame unavailable") from error
    return Response(
        content=frame.jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Operator-Captured-At": str(frame.captured_at_s)},
    )


@router.get("/calibration", response_model=OperatorCalibrationReport)
async def get_calibration(request: Request, response: Response) -> OperatorCalibrationReport:
    response.headers["Cache-Control"] = "no-store"
    profiles = getattr(request.app.state, "operator_profiles", {})
    profile = profiles.get("so101")
    if profile is None:
        raise HTTPException(status_code=404, detail="Operator profile not found")
    return inspect_operator_calibration(profile)


@router.post(
    "/preflights",
    response_model=PreflightResult,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_MUTATION_DEPENDENCIES,
)
async def create_preflight(
    request_body: PreflightRequest,
    service: Any = Depends(get_preflight_service),
) -> PreflightResult:
    sanitized = request_body.model_copy(
        update={
            "command_id": request_body.command_id.replace("\r", "").replace("\n", ""),
            "profile": request_body.profile.replace("\r", "").replace("\n", ""),
        }
    )
    try:
        return service.create(sanitized)
    except OperatorPreflightNotFoundError as error:
        raise HTTPException(status_code=404, detail="Operator profile not found") from error
    except OperatorPreflightConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/preflights/{preflight_id}", response_model=PreflightResult)
async def get_preflight(preflight_id: str, service: Any = Depends(get_preflight_service)) -> PreflightResult:
    sanitized = preflight_id.replace("\r", "").replace("\n", "")
    try:
        return service.get(sanitized)
    except OperatorPreflightNotFoundError as error:
        raise HTTPException(status_code=404, detail="Operator preflight not found") from error


@router.delete(
    "/preflights/{preflight_id}",
    response_model=PreflightResult,
    dependencies=_MUTATION_DEPENDENCIES,
)
async def cancel_preflight(
    preflight_id: str,
    command_id: str = Query(min_length=1, max_length=128),
    service: Any = Depends(get_preflight_service),
) -> PreflightResult:
    sanitized_id = preflight_id.replace("\r", "").replace("\n", "")
    sanitized_command = command_id.replace("\r", "").replace("\n", "")
    try:
        return service.cancel(sanitized_id, command_id=sanitized_command)
    except OperatorPreflightNotFoundError as error:
        raise HTTPException(status_code=404, detail="Operator preflight not found") from error
    except OperatorPreflightConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/sessions",
    response_model=OperatorStatus,
    status_code=status.HTTP_201_CREATED,
    dependencies=_MUTATION_DEPENDENCIES,
)
async def start_session(
    request_body: StartSessionRequest,
    service: Any = Depends(get_operator_service),
) -> OperatorStatus:
    sanitized = request_body.model_copy(
        update={
            "command_id": request_body.command_id.replace("\r", "").replace("\n", ""),
            "profile": request_body.profile.replace("\r", "").replace("\n", "") if request_body.profile else None,
            "preflight_id": request_body.preflight_id.replace("\r", "").replace("\n", "")
            if request_body.preflight_id
            else None,
        }
    )
    try:
        return await service.start(sanitized)
    except OperatorPreconditionError as error:
        raise HTTPException(status_code=412, detail=str(error)) from error
    except (OperatorDisabledError, OperatorConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/commands",
    response_model=OperatorStatus,
    dependencies=_MUTATION_DEPENDENCIES,
)
async def issue_command(
    session_id: str,
    command: OperatorCommand,
    service: Any = Depends(get_operator_service),
) -> OperatorStatus:
    sanitized_session = session_id.replace("\r", "").replace("\n", "")
    sanitized_command = command.model_copy(
        update={"command_id": command.command_id.replace("\r", "").replace("\n", "")}
    )
    try:
        return await service.command(sanitized_session, sanitized_command)
    except OperatorConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete(
    "/sessions/{session_id}",
    response_model=OperatorStatus,
    dependencies=_MUTATION_DEPENDENCIES,
)
async def stop_session(
    session_id: str,
    command_id: str = Query(min_length=1, max_length=128),
    service: Any = Depends(get_operator_service),
) -> OperatorStatus:
    try:
        return await service.stop(
            session_id.replace("\r", "").replace("\n", ""),
            command_id=command_id.replace("\r", "").replace("\n", ""),
        )
    except OperatorConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/events")
async def get_events(
    request: Request,
    once: bool = False,
    service: Any = Depends(get_operator_service),
) -> StreamingResponse:
    last_event_id = request.headers.get("Last-Event-ID")
    sanitized = last_event_id.replace("\r", "").replace("\n", "") if last_event_id else None

    async def stream():
        async for event_type, snapshot in service.events(sanitized, once=bool(once)):
            event_id = f"{snapshot.service_instance_id}:{snapshot.revision}"
            payload = json.dumps(snapshot.model_dump(mode="json"), separators=(",", ":"))
            yield f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
