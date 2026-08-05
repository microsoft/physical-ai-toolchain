"""API contract tests for operator preflight resources and start gating."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.operator.models import OperatorMode, PreflightLifecycle, PreflightResult
from src.api.routers import operator
from src.api.services.operator_service import OperatorService


class _StubPreflightService:
    def __init__(self) -> None:
        self.result = PreflightResult(
            preflight_id="preflight-1",
            lifecycle=PreflightLifecycle.COMPLETED,
            profile="so101",
            mode=OperatorMode.RECORD,
            profile_fingerprint="profile-fingerprint",
            resource_fingerprint="resource-fingerprint",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
            expires_at=datetime(2026, 7, 22, tzinfo=UTC) + timedelta(seconds=30),
            checks=[],
            ownership_complete=True,
            start_eligible=True,
        )

    def create(self, _request):
        return self.result

    def get(self, _preflight_id: str):
        return self.result

    def cancel(self, _preflight_id: str, *, command_id: str):
        return self.result.model_copy(
            update={"lifecycle": PreflightLifecycle.CANCELLED, "start_eligible": False}
        )


def test_preflight_routes_and_lerobot_start_gate() -> None:
    app = FastAPI()
    app.state.operator_service = OperatorService(adapter_mode="lerobot")
    app.state.operator_preflight_service = _StubPreflightService()
    app.include_router(operator.router, prefix="/api/operator")
    app.dependency_overrides[operator.require_operator_access] = lambda: None
    app.dependency_overrides[operator.require_hardware_access] = lambda: None
    app.dependency_overrides[operator.require_operator_csrf] = lambda: None
    with TestClient(app) as client:
        created = client.post(
            "/api/operator/preflights",
            json={
                "command_id": "preflight-command",
                "profile": "so101",
                "mode": "record",
            },
        )
        fetched = client.get("/api/operator/preflights/preflight-1")
        cancelled = client.delete(
            "/api/operator/preflights/preflight-1",
            params={"command_id": "cancel-preflight"},
        )

    assert created.status_code == 202
    assert fetched.status_code == 200
    assert cancelled.json()["lifecycle"] == "cancelled"


def test_disabled_operator_rejects_preflight_without_service() -> None:
    app = FastAPI()
    app.state.operator_service = type(
        "DisabledService",
        (),
        {"capabilities": lambda self: type("Caps", (), {"preflight_enabled": False})()},
    )()
    app.include_router(operator.router, prefix="/api/operator")
    app.dependency_overrides[operator.require_operator_access] = lambda: None
    app.dependency_overrides[operator.require_hardware_access] = lambda: None
    app.dependency_overrides[operator.require_operator_csrf] = lambda: None

    with TestClient(app) as client:
        response = client.post(
            "/api/operator/preflights",
            json={"command_id": "disabled", "profile": "so101", "mode": "record"},
        )

    assert response.status_code == 409
