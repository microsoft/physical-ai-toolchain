"""Security posture tests for hardware-capable operator requests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.auth import reset_auth_provider
from src.api.operator.authorization import (
    require_hardware_access,
    require_operator_csrf,
)
from src.api.routers import operator
from src.api.services.operator_service import OperatorService
from tests.conftest import make_asgi_request


@pytest.fixture(autouse=True)
def _select_lerobot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_ADAPTER_MODE", "lerobot")


@pytest.mark.parametrize(
    ("headers", "server", "client"),
    [
        ({"host": "workstation:8000"}, ("127.0.0.1", 8000), ("127.0.0.1", 1234)),
        (
            {"host": "localhost:8000", "origin": "http://remote.test"},
            ("127.0.0.1", 8000),
            ("127.0.0.1", 1234),
        ),
        ({"host": "localhost:8000"}, ("0.0.0.0", 8000), ("127.0.0.1", 1234)),
        ({"host": "localhost:8000"}, ("127.0.0.1", 8000), ("10.0.0.2", 1234)),
    ],
)
async def test_auth_disabled_hardware_rejects_non_loopback_posture(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    server: tuple[str, int],
    client: tuple[str, int],
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.setenv("OPERATOR_ALLOW_UNAUTHENTICATED_LOOPBACK", "true")
    request = make_asgi_request("POST", "/api/operator/preflights", headers=headers)
    request.scope["server"] = server
    request.scope["client"] = client

    with pytest.raises(HTTPException) as error:
        await require_hardware_access(request, user=None)

    assert error.value.status_code == 403


async def test_auth_disabled_hardware_requires_explicit_loopback_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.delenv("OPERATOR_ALLOW_UNAUTHENTICATED_LOOPBACK", raising=False)
    request = make_asgi_request(
        "POST", "/api/operator/preflights", headers={"host": "localhost:8000"}
    )

    with pytest.raises(HTTPException) as error:
        await require_hardware_access(request, user=None)

    assert error.value.status_code == 403


async def test_auth_disabled_explicit_loopback_posture_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.setenv("OPERATOR_ALLOW_UNAUTHENTICATED_LOOPBACK", "true")
    request = make_asgi_request(
        "POST",
        "/api/operator/preflights",
        headers={"host": "localhost:8000", "origin": "http://localhost:5173"},
    )
    request.scope["server"] = ("127.0.0.1", 8000)

    await require_hardware_access(request, user=None)


async def test_api_key_requires_distinct_operator_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
    monkeypatch.setenv("DATAVIEWER_OPERATOR_API_KEY", "operator-secret")
    request = make_asgi_request(
        "POST",
        "/api/operator/preflights",
        headers={"X-Operator-API-Key": "operator-secret"},
    )
    user = {"auth_method": "apikey"}

    await require_hardware_access(request, user=user)

    request = make_asgi_request(
        "POST", "/api/operator/preflights", headers={"X-API-Key": "general-secret"}
    )
    with pytest.raises(HTTPException) as error:
        await require_hardware_access(request, user=user)
    assert error.value.status_code == 403


async def test_operator_csrf_never_bypasses_when_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    request = make_asgi_request("POST", "/api/operator/preflights")

    with pytest.raises(HTTPException) as error:
        await require_operator_csrf(request)

    assert error.value.status_code == 403


def test_real_router_requires_general_and_operator_api_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
    monkeypatch.setenv("DATAVIEWER_API_KEY", "general-secret")
    monkeypatch.setenv("DATAVIEWER_OPERATOR_API_KEY", "operator-secret")
    reset_auth_provider()

    app = FastAPI()
    app.state.operator_service = OperatorService(adapter_mode="lerobot")
    app.include_router(operator.router, prefix="/api/operator")
    with TestClient(app) as client:
        both = client.get(
            "/api/operator/capabilities",
            headers={
                "X-API-Key": "general-secret",
                "X-Operator-API-Key": "operator-secret",
            },
        )
        general_only = client.get(
            "/api/operator/capabilities", headers={"X-API-Key": "general-secret"}
        )
        operator_only = client.get(
            "/api/operator/capabilities",
            headers={"X-Operator-API-Key": "operator-secret"},
        )

    reset_auth_provider()
    assert both.status_code == 200
    assert general_only.status_code == 403
    assert operator_only.status_code == 401
