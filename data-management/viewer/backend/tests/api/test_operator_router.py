"""API contract tests for operator capabilities and session control."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import require_auth
from src.api.csrf import CSRF_COOKIE_NAME, generate_csrf_token
from src.api.routers import operator
from src.api.services.operator_service import OperatorCameraFrame, OperatorService


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(operator.router, prefix="/api/operator")
    return test_app


@pytest.fixture
def disabled_client(app: FastAPI) -> TestClient:
    app.state.operator_service = OperatorService(adapter_mode="disabled")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def simulated_client() -> TestClient:
    service = OperatorService(adapter_mode="simulated", command_timeout_s=2.0)

    @asynccontextmanager
    async def lifespan(test_app: FastAPI) -> AsyncIterator[None]:
        test_app.state.operator_service = service
        yield
        await service.shutdown()

    test_app = FastAPI(lifespan=lifespan)
    test_app.include_router(operator.router, prefix="/api/operator")
    with TestClient(test_app) as client:
        yield client


class TestDisabledOperatorApi:
    def test_capabilities_and_status_remain_available(self, disabled_client: TestClient) -> None:
        capabilities = disabled_client.get("/api/operator/capabilities")
        status = disabled_client.get("/api/operator/status")

        assert capabilities.status_code == 200
        assert capabilities.json()["enabled"] is False
        assert status.status_code == 200
        assert status.json()["state"] == "disabled"

    def test_start_fails_closed(self, disabled_client: TestClient) -> None:
        response = disabled_client.post(
            "/api/operator/sessions",
            json={"command_id": "disabled-start", "mode": "teleoperate"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Operator mode is disabled"

    def test_main_app_mounts_disabled_operator_status(self) -> None:
        from src.api.main import app as main_app

        with TestClient(main_app) as client:
            capabilities = client.get("/api/operator/capabilities")
            status = client.get("/api/operator/status")

        assert capabilities.status_code == 200
        assert capabilities.json()["adapter_mode"] == "disabled"
        assert status.status_code == 200
        assert status.json()["state"] == "disabled"

    def test_missing_operator_service_returns_unavailable(self, app: FastAPI) -> None:
        with TestClient(app) as client:
            response = client.get("/api/operator/status")

        assert response.status_code == 503


class TestSimulatedOperatorApi:
    def test_start_is_idempotent(self, simulated_client: TestClient) -> None:
        request = {"command_id": "idempotent-api-start", "mode": "teleoperate"}

        first = simulated_client.post("/api/operator/sessions", json=request)
        replay = simulated_client.post("/api/operator/sessions", json=request)

        assert first.status_code == 201
        assert replay.status_code == 201
        assert replay.json() == first.json()

    def test_session_commands_are_addressed_and_idempotent(self, simulated_client: TestClient) -> None:
        started_response = simulated_client.post(
            "/api/operator/sessions",
            json={"command_id": "api-start-record", "mode": "record"},
        )
        assert started_response.status_code == 201, started_response.text
        started = started_response.json()

        command = {
            "command_id": "save-api-episode",
            "action": "save",
            "expected_revision": started["revision"],
        }
        command_url = f"/api/operator/sessions/{started['session_id']}/commands"
        first = simulated_client.post(command_url, json=command)
        replay = simulated_client.post(command_url, json=command)

        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()

    def test_stale_session_is_rejected(self, simulated_client: TestClient) -> None:
        started = simulated_client.post(
            "/api/operator/sessions",
            json={"command_id": "api-start-teleoperate", "mode": "teleoperate"},
        )
        assert started.status_code == 201

        response = simulated_client.delete(
            "/api/operator/sessions/not-current",
            params={"command_id": "stale-stop"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Stale session ID"

    def test_stale_command_is_rejected(self, simulated_client: TestClient) -> None:
        response = simulated_client.post(
            "/api/operator/sessions/not-current/commands",
            json={"command_id": "stale\r\ncommand", "action": "save"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Stale session ID"


class TestOperatorSecurityBoundary:
    @pytest.fixture
    def secured_app(self, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        test_app = FastAPI()
        test_app.state.operator_service = OperatorService(adapter_mode="disabled")
        test_app.include_router(operator.router, prefix="/api/operator")
        return test_app

    def test_operator_role_is_required(self, secured_app: FastAPI) -> None:
        secured_app.dependency_overrides[require_auth] = lambda: {"roles": ["Viewer"]}
        token = generate_csrf_token()
        with TestClient(secured_app) as client:
            client.cookies.set(CSRF_COOKIE_NAME, token)
            response = client.post(
                "/api/operator/sessions",
                json={"command_id": "forbidden-start", "mode": "teleoperate"},
                headers={"X-CSRF-Token": token},
            )

        assert response.status_code == 403

    def test_mutations_require_csrf(self, secured_app: FastAPI) -> None:
        secured_app.dependency_overrides[require_auth] = lambda: {"roles": ["Operator"]}
        with TestClient(secured_app) as client:
            response = client.post(
                "/api/operator/sessions",
                json={"command_id": "csrf-start", "mode": "teleoperate"},
            )

        assert response.status_code == 403

    def test_operator_role_allows_authorized_request(self, secured_app: FastAPI) -> None:
        secured_app.dependency_overrides[require_auth] = lambda: {"roles": ["Operator"]}
        token = generate_csrf_token()
        with TestClient(secured_app) as client:
            client.cookies.set(CSRF_COOKIE_NAME, token)
            response = client.post(
                "/api/operator/sessions",
                json={"command_id": "authorized-start", "mode": "teleoperate"},
                headers={"X-CSRF-Token": token},
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "Operator mode is disabled"


class TestOperatorEvents:
    def test_replays_current_snapshot_after_service_instance_mismatch(self, simulated_client: TestClient) -> None:
        response = simulated_client.get(
            "/api/operator/events?once=true",
            headers={"Last-Event-ID": "old-service:99"},
        )

        assert response.status_code == 200
        assert "event: snapshot" in response.text
        assert '"state":"idle"' in response.text

    async def test_service_fans_out_revision_updates_to_multiple_subscribers(
        self,
    ) -> None:
        service = OperatorService(adapter_mode="simulated", command_timeout_s=1.0)
        first = service.events(None)
        second = service.events(None)
        await anext(first)
        await anext(second)

        await service.start(operator.StartSessionRequest(command_id="fanout-start", mode="teleoperate"))
        first_event = await anext(first)
        second_event = await anext(second)

        assert first_event[1].revision == second_event[1].revision
        assert first_event[1].service_instance_id == second_event[1].service_instance_id
        await first.aclose()
        await second.aclose()
        await service.shutdown()


class TestOperatorCameraFrames:
    def test_capabilities_describe_profile_hardware(self, app: FastAPI) -> None:
        profile = SimpleNamespace(
            model_dump=lambda mode: {
                "embodiment": "SO-101",
                "actuator_names": [
                    "shoulder_pan",
                    "shoulder_lift",
                    "elbow_flex",
                    "wrist_flex",
                    "wrist_roll",
                    "gripper",
                ],
                "leader": {"logical_id": "my_leader_arm"},
                "follower": {"logical_id": "my_follower_arm"},
                "wrist_camera": {"fps": 30},
                "front_camera": {"fps": 30},
            }
        )
        app.state.operator_service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=SimpleNamespace(profiles={"so101": profile}),
        )

        with TestClient(app) as client:
            response = client.get("/api/operator/capabilities")

        assert response.status_code == 200
        assert response.json()["robots"] == [
            {
                "role": "leader",
                "name": "my_leader_arm",
                "embodiment": "SO-101",
                "actuator_count": 6,
            },
            {
                "role": "follower",
                "name": "my_follower_arm",
                "embodiment": "SO-101",
                "actuator_count": 6,
            },
        ]
        assert response.json()["cameras"] == [
            {"name": "wrist", "default_fps": 30},
            {"name": "front", "default_fps": 30},
        ]

    def test_returns_latest_worker_owned_jpeg(self, app: FastAPI) -> None:
        profile = SimpleNamespace(
            model_dump=lambda mode: {
                "wrist_camera": {"fps": 30},
                "front_camera": {"fps": 30},
            }
        )
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=SimpleNamespace(profiles={"so101": profile}),
        )
        service._camera_frames["wrist"] = OperatorCameraFrame(jpeg=b"jpeg", captured_at_s=1.25)
        app.state.operator_service = service

        with TestClient(app) as client:
            response = client.get("/api/operator/cameras/wrist/frame")

        assert response.status_code == 200
        assert response.content == b"jpeg"
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-operator-captured-at"] == "1.25"

    def test_unknown_and_empty_camera_frames_return_not_found(self, app: FastAPI) -> None:
        profile = SimpleNamespace(
            model_dump=lambda mode: {
                "wrist_camera": {"fps": 30},
                "front_camera": {"fps": 30},
            }
        )
        app.state.operator_service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=SimpleNamespace(profiles={"so101": profile}),
        )

        with TestClient(app) as client:
            unknown = client.get("/api/operator/cameras/side/frame")
            unavailable = client.get("/api/operator/cameras/wrist/frame")

        assert unknown.status_code == 404
        assert unknown.json()["detail"] == "Operator camera not found"
        assert unavailable.status_code == 404
        assert unavailable.json()["detail"] == "Operator camera frame unavailable"
