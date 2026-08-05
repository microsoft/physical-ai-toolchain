"""Operator capability, status, and session control routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

from ..csrf import require_csrf_token
from ..operator.authorization import (
    require_hardware_access,
    require_operator_access,
    require_operator_csrf,
)
from ..operator.models import (
    OperatorCapabilities,
    OperatorCommand,
    OperatorStatus,
    PreflightRequest,
    PreflightResult,
    StartSessionRequest,
)
from ..services.operator_preflight_service import (
    OperatorPreflightConflictError,
    OperatorPreflightNotFoundError,
    OperatorPreflightService,
)
from ..services.operator_service import (
    OperatorConflictError,
    OperatorDisabledError,
    OperatorPreconditionError,
    OperatorService,
)

router = APIRouter(
    dependencies=[Depends(require_csrf_token), Depends(require_operator_access)]
)


def get_operator_service(request: Request) -> OperatorService:
    """Resolve the lifespan-owned operator service."""
    service = getattr(request.app.state, "operator_service", None)
    if not isinstance(service, OperatorService):
        raise HTTPException(status_code=503, detail="Operator service is unavailable")
    return service


def get_preflight_service(request: Request) -> OperatorPreflightService:
    operator_service = getattr(request.app.state, "operator_service", None)
    if (
        operator_service is None
        or not operator_service.capabilities().preflight_enabled
    ):
        raise HTTPException(status_code=409, detail="Operator preflight is disabled")
    service = getattr(request.app.state, "operator_preflight_service", None)
    if service is None:
        raise HTTPException(
            status_code=503, detail="Operator preflight service is unavailable"
        )
    return service


@router.get("/capabilities", response_model=OperatorCapabilities)
async def get_capabilities(
    service: OperatorService = Depends(get_operator_service),
) -> OperatorCapabilities:
    """Return operator feature availability."""
    return service.capabilities()


@router.get("/status", response_model=OperatorStatus)
async def get_status(
    service: OperatorService = Depends(get_operator_service),
) -> OperatorStatus:
    """Return the authoritative operator status."""
    return service.status()


@router.get("/cameras/{camera}/frame")
async def get_camera_frame(
    camera: str,
    service: OperatorService = Depends(get_operator_service),
) -> Response:
    """Return the latest JPEG sampled from a worker-owned camera handle."""
    sanitized_camera = camera.replace("\r", "").replace("\n", "")
    try:
        frame = service.camera_frame(sanitized_camera)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Operator camera not found") from error
    except LookupError as error:
        raise HTTPException(
            status_code=404, detail="Operator camera frame unavailable"
        ) from error
    return Response(
        content=frame.jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Operator-Captured-At": str(frame.captured_at_s),
        },
    )


@router.get("/events")
async def stream_events(
    request: Request,
    once: bool = False,
    service: OperatorService = Depends(get_operator_service),
) -> StreamingResponse:
    """Stream authenticated status snapshots with revision replay."""
    last_event_id = request.headers.get("Last-Event-ID")

    async def event_source():
        async for event_type, snapshot in service.events(last_event_id, once=once):
            event_id = f"{snapshot.service_instance_id}:{snapshot.revision}"
            yield (
                f"id: {event_id}\nevent: {event_type}\ndata: {snapshot.model_dump_json()}\n\n"
            )

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post(
    "/preflights", response_model=PreflightResult, status_code=status.HTTP_202_ACCEPTED
)
async def create_preflight(
    request: PreflightRequest,
    _hardware_access: None = Depends(require_hardware_access),
    _operator_csrf: None = Depends(require_operator_csrf),
    service: OperatorPreflightService = Depends(get_preflight_service),
) -> PreflightResult:
    try:
        return service.create(request)
    except OperatorPreflightConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OperatorPreflightNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Operator profile not found"
        ) from error


@router.get("/preflights/{preflight_id}", response_model=PreflightResult)
async def get_preflight(
    preflight_id: str,
    service: OperatorPreflightService = Depends(get_preflight_service),
) -> PreflightResult:
    try:
        return service.get(preflight_id.replace("\r", "").replace("\n", ""))
    except OperatorPreflightNotFoundError as error:
        raise HTTPException(status_code=404, detail="Preflight not found") from error


@router.delete("/preflights/{preflight_id}", response_model=PreflightResult)
async def cancel_preflight(
    preflight_id: str,
    command_id: str = Query(min_length=1, max_length=128),
    _hardware_access: None = Depends(require_hardware_access),
    _operator_csrf: None = Depends(require_operator_csrf),
    service: OperatorPreflightService = Depends(get_preflight_service),
) -> PreflightResult:
    try:
        return service.cancel(
            preflight_id.replace("\r", "").replace("\n", ""),
            command_id=command_id.replace("\r", "").replace("\n", ""),
        )
    except OperatorPreflightNotFoundError as error:
        raise HTTPException(status_code=404, detail="Preflight not found") from error


@router.post(
    "/sessions", response_model=OperatorStatus, status_code=status.HTTP_201_CREATED
)
async def start_session(
    request: StartSessionRequest,
    _hardware_access: None = Depends(require_hardware_access),
    _operator_csrf: None = Depends(require_operator_csrf),
    service: OperatorService = Depends(get_operator_service),
) -> OperatorStatus:
    """Start one operator session."""
    try:
        return await service.start(request)
    except OperatorPreconditionError as error:
        raise HTTPException(status_code=412, detail=str(error)) from error
    except NotImplementedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    except (OperatorDisabledError, OperatorConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/sessions/{session_id}/commands", response_model=OperatorStatus)
async def send_command(
    session_id: str,
    command: OperatorCommand,
    service: OperatorService = Depends(get_operator_service),
) -> OperatorStatus:
    """Send an idempotent command to the named session."""
    sanitized_session_id = session_id.replace("\r", "").replace("\n", "")
    sanitized_command = command.model_copy(
        update={"command_id": command.command_id.replace("\r", "").replace("\n", "")}
    )
    try:
        return await service.command(sanitized_session_id, sanitized_command)
    except OperatorConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/sessions/{session_id}", response_model=OperatorStatus)
async def stop_session(
    session_id: str,
    command_id: str = Query(min_length=1, max_length=128),
    service: OperatorService = Depends(get_operator_service),
) -> OperatorStatus:
    """Cancel the named operator session."""
    sanitized_session_id = session_id.replace("\r", "").replace("\n", "")
    sanitized_command_id = command_id.replace("\r", "").replace("\n", "")
    try:
        return await service.stop(sanitized_session_id, command_id=sanitized_command_id)
    except OperatorConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
