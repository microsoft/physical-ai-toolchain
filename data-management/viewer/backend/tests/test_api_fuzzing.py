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
_BASE_EXCLUDED_CONTRACT_CHECKS = [
    schemathesis.checks.positive_data_acceptance,
    schemathesis.checks.status_code_conformance,
    schemathesis.checks.allow_header_conformance,
]
_NEGATIVE_DATA_ACCEPTANCE_PATHS = {
    "/api/datasets/{dataset_id}/joint-config",
    "/api/joint-config/defaults",
}


@pytest.fixture
def api_schema(client):
    """Load OpenAPI schema from the in-process ASGI app used in tests."""
    detection_service = MagicMock()
    detection_service.effective_confidence.return_value = 0.1
    detection_service.get_cached.return_value = None
    detection_service.clear_cache.return_value = False
    detection_service.detect_episode = AsyncMock(
        side_effect=lambda _dataset_id, _episode_idx, _request, _get_frame_image, total_frames: EpisodeDetectionSummary(
            total_frames=total_frames,
            processed_frames=0,
            total_detections=0,
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
                            modes=[
                                schemathesis.GenerationMode.POSITIVE,
                                schemathesis.GenerationMode.NEGATIVE,
                            ],
                            deterministic=True,
                        )
                    )
                )
            ),
        )
    finally:
        client.app.dependency_overrides.pop(get_detection_service, None)


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
    """Generated cases must not trigger server errors or violate the OpenAPI contract."""
    excluded_checks = list(_BASE_EXCLUDED_CONTRACT_CHECKS)
    if case.operation.path in _NEGATIVE_DATA_ACCEPTANCE_PATHS:
        excluded_checks.append(schemathesis.checks.negative_data_rejection)
    case.call_and_validate(excluded_checks=excluded_checks)


def test_ai_routes_document_bad_request_responses(client) -> None:
    schema = client.get("/openapi.json").json()

    for path in (
        "/api/ai/trajectory-analysis",
        "/api/ai/anomaly-detection",
        "/api/ai/cluster",
        "/api/ai/suggest-annotation",
    ):
        assert "400" in schema["paths"][path]["post"]["responses"]


@pytest.mark.parametrize(
    ("path", "additional_exclusions"),
    [
        ("/api/ai/trajectory-analysis", []),
        ("/health", []),
        ("/api/datasets/{dataset_id}/joint-config", [schemathesis.checks.negative_data_rejection]),
        ("/api/joint-config/defaults", [schemathesis.checks.negative_data_rejection]),
    ],
)
def test_contract_fuzzing_uses_defaults_with_bounded_exclusions(path, additional_exclusions) -> None:
    case = MagicMock()
    case.operation.path = path

    test_openapi_contract_fuzzing.hypothesis.inner_test(case)

    kwargs = case.call_and_validate.call_args.kwargs
    assert "checks" not in kwargs
    assert kwargs["excluded_checks"] == [*_BASE_EXCLUDED_CONTRACT_CHECKS, *additional_exclusions]
