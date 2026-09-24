"""API behavior tests for operator mutation safety and idempotency."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.operator.models import OperatorAdapterMode, OperatorCapabilities, OperatorStatus, SessionState
from src.api.routers import operator


class _DispatchProbe:
    def __init__(self) -> None:
        self.start_calls = 0

    def capabilities(self) -> OperatorCapabilities:
        return OperatorCapabilities(
            enabled=True,
            adapter_mode=OperatorAdapterMode.LEROBOT,
            adapter_version=1,
            protocol_version=2,
            modes=[],
            profiles=[],
        )

    def status(self) -> OperatorStatus:
        return OperatorStatus(service_instance_id="service-1", state=SessionState.IDLE)

    async def start(self, _request: object) -> OperatorStatus:
        self.start_calls += 1
        return self.status()

    def camera_frame(self, camera: str) -> object:
        if camera != "wrist":
            raise KeyError(camera)
        return type("Frame", (), {"jpeg": b"\xff\xd8jpeg\xff\xd9", "captured_at_s": 1.25})()


def test_given_auth_disabled_hardware_mode_when_start_requested_then_worker_is_not_dispatched(
    monkeypatch,
) -> None:
    # Arrange
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.setenv("OPERATOR_ADAPTER_MODE", "lerobot")
    service = _DispatchProbe()
    app = FastAPI()
    app.state.operator_service = service
    app.include_router(operator.router, prefix="/api/operator")

    # Act
    with TestClient(app) as client:
        response = client.post(
            "/api/operator/sessions",
            json={"command_id": "start-1", "mode": "teleoperate"},
        )

    # Assert
    assert response.status_code == 403
    assert service.start_calls == 0


def test_given_configured_camera_when_latest_frame_requested_then_jpeg_is_never_cached() -> None:
    # Arrange
    service = _DispatchProbe()
    app = FastAPI()
    app.state.operator_service = service
    app.include_router(operator.router, prefix="/api/operator")

    # Act
    with TestClient(app) as client:
        response = client.get("/api/operator/cameras/wrist/frame")
        missing = client.get("/api/operator/cameras/front/frame")

    # Assert
    assert (response.status_code, response.headers["content-type"], response.content) == (
        200,
        "image/jpeg",
        b"\xff\xd8jpeg\xff\xd9",
    )
    assert response.headers["cache-control"] == "no-store"
    assert missing.status_code == 404
