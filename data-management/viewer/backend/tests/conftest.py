"""Shared pytest configuration and fixtures for backend tests."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import os
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request


def make_asgi_request(
    method: str = "POST",
    path: str = "/api/x",
    headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
) -> Request:
    """Build a minimal Starlette `Request` for unit-testing dependency callables.

    Pass a sequence of pairs to send duplicate header names.
    """
    items = headers.items() if isinstance(headers, Mapping) else (headers or ())
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in items]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "app": FastAPI(),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


# Load .env from the backend directory so TEST_DATASET_ID and other
# settings can be configured without hardcoding.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TEST_DATASET_PATH = os.environ.get(
    "TEST_DATASET_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "datasets"),
)

TEST_DATASET_ID = os.environ.get("TEST_DATASET_ID", "lerobot")


@pytest.fixture
def held_locks() -> Iterator[contextlib.ExitStack]:
    """Export locks a test holds as another export would, released when the test ends."""
    with contextlib.ExitStack() as stack:
        yield stack


@pytest.fixture
def accepted_dataset_path(tmp_path: Path) -> Path:
    """Create an accepted dataset descriptor with matching artifact hashes."""
    dataset_path = tmp_path / "accepted-dataset"
    dataset_path.mkdir()
    artifacts = {}
    for name, content in {
        "capture_provenance": b'{"profile_id":"profile-alpha"}',
        "export_validation": b'{"status":"pass"}',
    }.items():
        filename = f"{name.replace('_', '-')}.json"
        (dataset_path / filename).write_bytes(content)
        artifacts[name] = {"file": filename, "sha256": hashlib.sha256(content).hexdigest()}
    descriptor = {
        "schema_version": 1,
        "dataset_id": dataset_path.name,
        "output_adapter_id": "lerobot_v3",
        "output_adapter_version": "0.6.0",
        "viewer_adapter_id": "dataviewer_v1",
        "profile_id": "profile-alpha",
        "profile_sha256": "a" * 64,
        "capture_features": [{"feature_id": "state-alpha", "kind": "observation_state"}],
        "sensors": [{"sensor_id": "view-alpha", "media_kind": "rgb"}],
        "artifacts": artifacts,
    }
    (dataset_path / "accepted-dataset.json").write_text(json.dumps(descriptor), encoding="utf-8")
    return dataset_path


def _write_accessibility_episode(path: Path, *, length: int, phase: float) -> None:
    """Write one deterministic synthetic HDF5 episode."""
    h5py = pytest.importorskip("h5py")
    numpy = pytest.importorskip("numpy")

    timeline = numpy.linspace(0.0, 1.0, length, dtype=numpy.float32)
    joint_offsets = numpy.linspace(0.0, 0.5, 6, dtype=numpy.float32)
    positions = numpy.sin((timeline[:, None] + phase) * numpy.pi) + joint_offsets
    actions = positions + 0.05

    row = numpy.arange(48, dtype=numpy.uint8)[:, None]
    column = numpy.arange(64, dtype=numpy.uint8)[None, :]
    frames = numpy.empty((length, 48, 64, 3), dtype=numpy.uint8)
    for frame_index in range(length):
        frames[frame_index, :, :, 0] = (row + frame_index * 7) % 255
        frames[frame_index, :, :, 1] = (column + frame_index * 11) % 255
        frames[frame_index, :, :, 2] = (row + column + frame_index * 13) % 255

    with h5py.File(path, "w") as file:
        observations = file.create_group("observations")
        observations.create_dataset("qpos", data=positions)
        images = observations.create_group("images")
        images.create_dataset("front", data=frames)
        images.create_dataset("wrist", data=numpy.flip(frames, axis=2))
        file.create_dataset("action", data=actions)
        file.attrs["fps"] = 12.0
        file.attrs["task_index"] = 0


@pytest.fixture(scope="session")
def accessibility_dataset_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the deterministic synthetic dataset used by accessibility runners."""
    root = tmp_path_factory.mktemp("a11y-datasets")
    dataset = root / "a11y-synthetic"
    dataset.mkdir()
    _write_accessibility_episode(dataset / "episode_0.hdf5", length=12, phase=0.0)
    _write_accessibility_episode(dataset / "episode_1.hdf5", length=18, phase=0.25)
    return root


@pytest.fixture(autouse=True, scope="session")
def configure_test_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Configure authentication and writable local storage for tests."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
        monkeypatch.setenv("DATA_DIR", str(tmp_path_factory.mktemp("default-datasets")))

        import src.api.config as config_mod
        import src.api.main as main_mod

        previous_config = main_mod._config
        config_mod._app_config = None
        main_mod._config = config_mod.load_config()
        yield
        config_mod._app_config = None
        main_mod._config = previous_config


@pytest.fixture(scope="session")
def test_dataset_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return an isolated dataset root for tests that need session scope."""
    return tmp_path_factory.mktemp("datasets")


@pytest.fixture(scope="session")
def test_dataset_id() -> str:
    return "test-dataset"


def _reset_singletons() -> None:
    import src.api.config as config_mod
    import src.api.routers.labels as labels_mod
    import src.api.services.annotation_service as ann_mod
    import src.api.services.dataset_service.service as ds_mod

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None
    labels_mod._label_storage = None


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Create an isolated FastAPI client backed by a temporary data directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    _reset_singletons()

    import src.api.main as main_mod

    with TestClient(main_mod.app) as c:
        yield c

    main_mod.app.dependency_overrides.clear()
    _reset_singletons()


# ============================================================================
# Hosted easy_auth caller proof
# ============================================================================

CALLER_TENANT_ID = "00000000-0000-0000-0000-000000000001"
CALLER_API_AUDIENCE = "00000000-0000-0000-0000-000000000002"
CALLER_OBJECT_ID = "00000000-0000-0000-0000-000000000003"
CALLER_CLIENT_ID = "00000000-0000-0000-0000-000000000004"
CALLER_ISSUER = f"https://login.microsoftonline.com/{CALLER_TENANT_ID}/v2.0"
CALLER_KEY_ID = "caller-test-key"
CALLER_TOKEN_HEADER = "X-Dataviewer-Caller-Token"
PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"
NAMEID_CLAIM = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"


def encode_bytes(data: bytes) -> str:
    """Base64-encode raw bytes as they appear in a forwarded principal header."""
    return base64.b64encode(data).decode("ascii")


def encode_principal(payload: object) -> str:
    """Encode a principal payload the way Easy Auth forwards it."""
    return encode_bytes(json.dumps(payload).encode("utf-8"))


DEFAULT_PRINCIPAL = encode_principal(
    {
        "claims": [
            {"typ": NAMEID_CLAIM, "val": "user-1"},
            {"typ": "name", "val": "Alice"},
            {"typ": "roles", "val": "Dataviewer.Admin"},
        ]
    }
)


def set_hosted_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply canonical hosted easy_auth settings for the current test."""
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
    monkeypatch.setenv("DATAVIEWER_AZURE_TENANT_ID", CALLER_TENANT_ID)
    monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", CALLER_API_AUDIENCE)
    monkeypatch.setenv("DATAVIEWER_CALLER_OBJECT_ID", CALLER_OBJECT_ID)
    monkeypatch.setenv("DATAVIEWER_CALLER_CLIENT_ID", CALLER_CLIENT_ID)


@dataclass(frozen=True)
class CallerKeys:
    """Trusted signing key plus an untrusted key for forged-signature cases."""

    primary: Any
    rogue: Any


def _jwks_document(public_key: Any, kid: str) -> bytes:
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return json.dumps({"keys": [jwk]}).encode("utf-8")


class _JwksHandler(http.server.BaseHTTPRequestHandler):
    server: JwksServer

    def do_GET(self) -> None:
        self.server.requests += 1
        if self.path != "/keys":
            self.send_error(404)
            return
        if self.server.status != 200:
            self.send_error(self.server.status)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.document)))
        self.end_headers()
        self.wfile.write(self.server.document)

    def log_message(self, format: str, *args: object) -> None:
        return


class JwksServer(http.server.ThreadingHTTPServer):
    """Loopback JWKS endpoint whose status tests can change between requests."""

    daemon_threads = True

    def __init__(self, document: bytes) -> None:
        super().__init__(("127.0.0.1", 0), _JwksHandler)
        self.document = document
        self.status = 200
        self.requests = 0

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/keys"


class HostedCaller:
    """Signs app-only caller proofs and builds request headers for the hosted easy_auth path."""

    def __init__(self, provider: Any, keys: CallerKeys) -> None:
        self.provider = provider
        self.keys = keys

    def token(
        self,
        claims: Mapping[str, object] | None = None,
        *,
        omit: Iterable[str] = (),
        algorithm: str = "RS256",
        key: Any = None,
        kid: str | None = CALLER_KEY_ID,
    ) -> str:
        """Return a caller proof with valid defaults, optionally overridden or with claims removed."""
        import jwt

        now = int(time.time())
        payload: dict[str, object] = {
            "ver": "2.0",
            "tid": CALLER_TENANT_ID,
            "idtyp": "app",
            "oid": CALLER_OBJECT_ID,
            "sub": CALLER_OBJECT_ID,
            "azp": CALLER_CLIENT_ID,
            "aud": CALLER_API_AUDIENCE,
            "iss": CALLER_ISSUER,
            "iat": now,
            "nbf": now - 10,
            "exp": now + 300,
        }
        payload.update(claims or {})
        for name in omit:
            payload.pop(name, None)
        signing_key = None if algorithm == "none" else (self.keys.primary if key is None else key)
        headers = {"kid": kid} if kid is not None else None
        return jwt.encode(payload, signing_key, algorithm=algorithm, headers=headers)

    def request_headers(
        self,
        *,
        include_caller: bool = True,
        caller_token: str | None = None,
        principal: str | None = DEFAULT_PRINCIPAL,
        extra: Sequence[tuple[str, str]] = (),
    ) -> list[tuple[str, str]]:
        """Build ordered header pairs; ``principal=None`` omits the forwarded user header."""
        headers: list[tuple[str, str]] = []
        if include_caller:
            headers.append((CALLER_TOKEN_HEADER, caller_token if caller_token is not None else self.token()))
        if principal is not None:
            headers.append((PRINCIPAL_HEADER, principal))
        headers.extend(extra)
        return headers


@pytest.fixture(scope="session")
def caller_keys() -> CallerKeys:
    from cryptography.hazmat.primitives.asymmetric import rsa

    return CallerKeys(
        primary=rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rogue=rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


@pytest.fixture(scope="session")
def jwks_server(caller_keys: CallerKeys) -> Iterator[JwksServer]:
    server = JwksServer(_jwks_document(caller_keys.primary.public_key(), CALLER_KEY_ID))
    thread = threading.Thread(target=server.serve_forever, name="jwks-test-server", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def hosted_caller(
    monkeypatch: pytest.MonkeyPatch,
    jwks_server: JwksServer,
    caller_keys: CallerKeys,
) -> Iterator[HostedCaller]:
    """Hosted provider whose verifier fetches keys from the loopback JWKS server."""
    import jwt

    from src.api import auth as auth_mod

    jwks_server.status = 200
    jwks_server.requests = 0
    set_hosted_environment(monkeypatch)
    auth_mod.reset_auth_provider()
    provider = auth_mod._get_provider()
    provider.verifier._jwks_client = jwt.PyJWKClient(jwks_server.url, cache_keys=False, lifespan=300, timeout=5)
    try:
        yield HostedCaller(provider, caller_keys)
    finally:
        auth_mod.reset_auth_provider()


@pytest.fixture
def hosted_client(hosted_caller: HostedCaller) -> TestClient:
    """Unauthenticated-by-default client for the hosted application."""
    from src.api.main import app

    return TestClient(app)
