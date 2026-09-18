"""Schemathesis-based OpenAPI contract fuzzing for the FastAPI backend."""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.config import (
    GenerationConfig,
    ProjectConfig,
    ProjectsConfig,
    SchemathesisConfig,
)

schemathesis.checks.load_all_checks()
_STATUS_CODE_CONFORMANCE = schemathesis.checks.CHECKS.get_one("status_code_conformance")
_RESPONSE_CONTRACT_CHECKS = schemathesis.checks.CHECKS.get_by_names(
    [
        "not_a_server_error",
        "content_type_conformance",
        "response_headers_conformance",
        "response_schema_conformance",
    ]
)


def _successful_status_code_conformance(
    ctx: schemathesis.CheckContext,
    response: schemathesis.Response,
    case: schemathesis.Case,
) -> bool | None:
    """Require successful responses to use a status documented by the operation."""
    if response.status_code < 400:
        return _STATUS_CODE_CONFORMANCE(ctx, response, case)
    return None


_RESPONSE_CONTRACT_CHECKS.append(_successful_status_code_conformance)


@pytest.fixture
def api_schema(client):
    """Load OpenAPI schema from the in-process ASGI app used in tests."""
    return schemathesis.openapi.from_asgi(
        "/openapi.json",
        client.app,
        config=SchemathesisConfig(
            projects=ProjectsConfig(
                default=ProjectConfig(
                    generation=GenerationConfig(
                        modes=[schemathesis.GenerationMode.POSITIVE],
                        deterministic=True,
                    )
                )
            )
        ),
    )


schema = schemathesis.pytest.from_fixture("api_schema").exclude(path_regex=r"^/api/(ai/.+|datasets/.+/judge)$")


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
    """Generated cases must not trigger server errors or violate the OpenAPI contract."""
    case.call_and_validate(checks=_RESPONSE_CONTRACT_CHECKS)
