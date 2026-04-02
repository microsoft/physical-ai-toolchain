"""Schemathesis contract fuzzing with authentication and CSRF enforcement enabled."""

import os

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings


@pytest.fixture(scope="session")
def api_schema_auth(tmp_path_factory):
    """Load OpenAPI schema from ASGI app while auth and CSRF checks are active."""
    data_path = tmp_path_factory.mktemp("fuzz-auth-data")

    import src.api.auth as auth_mod
    import src.api.config as config_mod

    original_auth_disabled = os.environ.get("DATAVIEWER_AUTH_DISABLED")
    original_auth_provider = os.environ.get("DATAVIEWER_AUTH_PROVIDER")
    original_api_key = os.environ.get("DATAVIEWER_API_KEY")

    os.environ["HMI_DATA_PATH"] = str(data_path)
    os.environ["DATAVIEWER_AUTH_DISABLED"] = "false"
    os.environ["DATAVIEWER_AUTH_PROVIDER"] = "apikey"
    os.environ["DATAVIEWER_API_KEY"] = "test-secret-key"

    config_mod._app_config = None
    auth_mod.reset_auth_provider()

    try:
        from src.api.main import app
    except ModuleNotFoundError as exc:
        if exc.name == "numpy":
            pytest.skip("Auth-enabled API fuzzing requires optional dependency 'numpy'")
        raise

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    yield schema

    config_mod._app_config = None
    auth_mod.reset_auth_provider()

    if original_auth_disabled is None:
        os.environ.pop("DATAVIEWER_AUTH_DISABLED", None)
    else:
        os.environ["DATAVIEWER_AUTH_DISABLED"] = original_auth_disabled

    if original_auth_provider is None:
        os.environ.pop("DATAVIEWER_AUTH_PROVIDER", None)
    else:
        os.environ["DATAVIEWER_AUTH_PROVIDER"] = original_auth_provider

    if original_api_key is None:
        os.environ.pop("DATAVIEWER_API_KEY", None)
    else:
        os.environ["DATAVIEWER_API_KEY"] = original_api_key


schema = schemathesis.pytest.from_fixture("api_schema_auth")


def _request_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/csrf-token").json()["csrf_token"]
    return {
        "X-API-Key": "test-secret-key",
        "X-CSRF-Token": token,
    }


@pytest.fixture
def auth_client(api_schema_auth):
    """Create a client with auth/CSRF settings prepared by the schema fixture."""
    from src.api.main import app

    with TestClient(app) as client:
        yield client


@schema.parametrize()
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
def test_openapi_contract_fuzzing_with_auth(case, auth_client):
    """Generated schema-valid requests should satisfy contract checks with auth enabled."""
    case.call_and_validate(session=auth_client, headers=_request_headers(auth_client))
