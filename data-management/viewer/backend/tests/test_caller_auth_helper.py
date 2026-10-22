"""Behavior of the loopback-only workload proof supplier."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from azure.core.credentials import AccessToken

from src.caller_auth_helper import create_app

CLIENT_ID = "11111111-1111-4111-8111-111111111111"
API_ID = "22222222-2222-4222-8222-222222222222"
HEADER = "X-Dataviewer-Caller-Token"


class Credential:
    """Controllable SDK boundary retaining token expiry and cancellation behavior."""

    def __init__(self) -> None:
        self.calls = 0
        self.closed = False
        self.failure = False
        self.block = False
        self.expires_on = 2000
        self.scopes = []

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.calls += 1
        self.scopes.append(scopes)
        if self.block:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        if self.failure:
            raise RuntimeError("private credential payload")
        return AccessToken(f"private-proof-{self.calls}", self.expires_on)

    async def close(self) -> None:
        self.closed = True


@asynccontextmanager
async def helper(
    monkeypatch: pytest.MonkeyPatch, credential: Credential, clock: list[float], **kwargs: Any
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("DATAVIEWER_CALLER_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", API_ID)
    app = create_app(credential_factory=lambda **settings: credential, clock=lambda: clock[0], **kwargs)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://helper") as client,
    ):
        yield client


async def test_given_concurrent_requests_when_acquiring_then_one_token_is_returned_only_in_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = Credential()
    async with helper(monkeypatch, credential, [1000.0]) as client:
        responses = await asyncio.gather(*(client.get("/token") for _ in range(20)))
        assert all(response.status_code == 204 and response.content == b"" for response in responses)
        assert all(response.headers[HEADER] == "private-proof-1" for response in responses)
        assert all(response.headers["cache-control"] == "no-store" for response in responses)
        assert credential.calls == 1
        assert credential.scopes == [(f"api://{API_ID}/.default",)]
        ready = await client.get("/ready")
        assert ready.status_code == 204 and HEADER not in ready.headers
    assert credential.closed


async def test_given_refresh_failure_when_safety_deadline_reached_then_deny_and_recover(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    credential = Credential()
    clock = [1000.0]
    async with helper(monkeypatch, credential, clock) as client:
        assert (await client.get("/token")).status_code == 204
        clock[0] = 1700
        credential.failure = True
        assert (await client.get("/token")).headers[HEADER] == "private-proof-1"
        assert (await client.get("/token")).status_code == 204
        assert credential.calls == 2
        clock[0] = 1940
        denied = await client.get("/token")
        assert denied.status_code == 503 and HEADER not in denied.headers
        assert (await client.get("/ready")).status_code == 503
        assert (await client.get("/health")).status_code == 204
        credential.failure = False
        credential.expires_on = 4000
        clock[0] = 1951
        assert (await client.get("/token")).status_code == 204
    assert "private" not in caplog.text


@pytest.mark.parametrize("expiry", [999, 1000, 1060])
async def test_given_unusable_sdk_token_when_requested_then_fail_closed(
    monkeypatch: pytest.MonkeyPatch, expiry: int
) -> None:
    credential = Credential()
    credential.expires_on = expiry
    async with helper(monkeypatch, credential, [1000.0]) as client:
        response = await client.get("/token")
    assert response.status_code == 503 and HEADER not in response.headers and response.content == b""


async def test_given_acquisition_timeout_when_requested_then_bounded_denial_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = Credential()
    credential.block = True
    async with helper(monkeypatch, credential, [1000.0], acquisition_timeout=0.01) as client:
        response = await asyncio.wait_for(client.get("/token"), timeout=1)
        assert response.status_code == 503
        assert (await client.get("/health")).status_code == 204
    assert credential.closed


@pytest.mark.parametrize("setting", ["DATAVIEWER_CALLER_CLIENT_ID", "DATAVIEWER_AZURE_CLIENT_ID"])
@pytest.mark.parametrize("value", ["", "not-an-identity", "../resource"])
def test_given_invalid_configuration_when_created_then_startup_is_rejected(
    monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    monkeypatch.setenv("DATAVIEWER_CALLER_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", API_ID)
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError, match="caller helper configuration"):
        create_app()


async def test_given_request_body_or_browser_headers_when_fetching_proof_then_not_reflected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = Credential()
    async with helper(monkeypatch, credential, [1000.0]) as client:
        response = await client.get("/token", headers={"Authorization": "private-browser-token"})
        assert "private-browser-token" not in str(response.headers)
        assert (await client.post("/token", content=b"body")).status_code == 405
        assert (await client.get("/unknown")).status_code == 404


async def test_given_real_sdk_when_acquiring_then_explicit_identity_and_resource_reach_transport(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append((parse_qs(urlsplit(self.path).query), self.headers.get("X-IDENTITY-HEADER")))
            payload = json.dumps(
                {
                    "access_token": "private-sdk-proof",
                    "expires_on": str(int(time.time()) + 3600),
                    "resource": f"api://{API_ID}",
                    "token_type": "Bearer",
                    "client_id": CLIENT_ID,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("IDENTITY_ENDPOINT", f"http://127.0.0.1:{server.server_port}/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "private-endpoint-secret")
    monkeypatch.setenv("DATAVIEWER_CALLER_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", API_ID)
    app = create_app()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://helper") as client,
        ):
            response = await client.get("/token")
        assert response.status_code == 204 and response.headers[HEADER] == "private-sdk-proof"
        assert requests[0][0]["client_id"] == [CLIENT_ID]
        assert requests[0][0]["resource"] == [f"api://{API_ID}"]
        assert requests[0][1] == "private-endpoint-secret"
        assert "private" not in caplog.text
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
