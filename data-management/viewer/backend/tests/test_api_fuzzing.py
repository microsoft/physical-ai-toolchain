"""Schemathesis-based OpenAPI contract fuzzing for the FastAPI backend."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.config import (
    GenerationConfig,
    ProjectConfig,
    ProjectsConfig,
    SchemathesisConfig,
)

from src.api.models.detection import EpisodeDetectionSummary
from src.api.services.detection_service import get_detection_service

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
    detection_service = MagicMock()
    detection_service.effective_confidence.return_value = 0.1
    detection_service.get_cached.return_value = None
    detection_service.clear_cache.return_value = False
    detection_service.detect_episode = AsyncMock(
        side_effect=lambda _dataset_id, _episode_idx, _request, _get_frame_image, total_frames: (
            EpisodeDetectionSummary(
                total_frames=total_frames,
                processed_frames=0,
                total_detections=0,
            )
        )
    )
    client.app.dependency_overrides[get_detection_service] = lambda: detection_service
    try:
        yield schemathesis.openapi.from_asgi(
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
    finally:
        client.app.dependency_overrides.pop(get_detection_service, None)


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
