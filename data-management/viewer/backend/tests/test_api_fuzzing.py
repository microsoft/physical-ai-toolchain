"""Schemathesis-based OpenAPI contract fuzzing for the FastAPI backend."""

import os

import pytest
import schemathesis
from hypothesis import HealthCheck, settings


@pytest.fixture(scope="session")
def api_schema(test_dataset_path):
    """Load OpenAPI schema from the in-process ASGI app used in tests."""
    os.environ["DATA_DIR"] = test_dataset_path

    import src.api.config as config_mod
    import src.api.services.annotation_service as ann_mod
    import src.api.services.dataset_service as ds_mod

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None

    from src.api.main import app

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    yield schema

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
def test_openapi_contract_fuzzing(case):
    """Generated cases should not trigger 5xx and must match declared schemas."""
    case.call_and_validate()
