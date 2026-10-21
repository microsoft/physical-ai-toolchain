# cspell:ignore cobject nject tject Dple CCESS ntial
"""Schemathesis-based OpenAPI contract fuzzing for the FastAPI backend."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote, unquote

import pytest
import schemathesis
from fastapi import Depends
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from jsonschema_rs import Draft202012Validator
from physical_ai_curation.api import JobRequest
from pydantic import ValidationError
from requests.structures import CaseInsensitiveDict
from schemathesis.config import (
    GenerationConfig,
    ProjectConfig,
    ProjectsConfig,
    SchemathesisConfig,
)
from schemathesis.core.transport import Response
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase
from schemathesis.specs.openapi.schemas import OpenApiSchema

from src.api.auth import require_auth
from src.api.models.annotations import EpisodeAnnotation
from src.api.models.detection import DetectionRequest, EpisodeDetectionSummary
from src.api.rate_limiter import limiter
from src.api.routers import vlm_judge
from src.api.routers.labels import DEFAULT_LABELS
from src.api.services.detection_service import get_detection_service
from src.api.services.vlm_judge_service import reset_vlm_judge_service
from src.api.validation import sanitize_user_string

from .api.test_annotations import sample_annotation as sample_annotation
from .conftest import _reset_singletons
from .lerobot_sources import CAMERA, write_source

schemathesis.checks.load_all_checks()


@st.composite
def _byte_range_headers(draw: st.DrawFn) -> str:
    start = draw(st.integers(min_value=0))
    span = draw(st.none() | st.integers(min_value=0))
    return f"bytes={start}-{'' if span is None else start + span}"


schemathesis.openapi.format("http-byte-range", _byte_range_headers())


@pytest.fixture(params=[False, True], ids=["default", "vlm"])
def api_schema(
    client: TestClient, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[OpenApiSchema]:
    """Load OpenAPI schema from the in-process ASGI app used in tests."""
    original_routes = list(client.app.router.routes)
    original_lifespan = client.app.router.lifespan_context
    if request.param:
        monkeypatch.setenv("VLM_JUDGE_ENABLED", "true")
        monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
        client.app.include_router(
            vlm_judge.router, prefix="/api/datasets", tags=["vlm-judge"], dependencies=[Depends(require_auth)]
        )
        client.app.include_router(
            vlm_judge.build_optional_job_router(), prefix="/api/judge", dependencies=[Depends(require_auth)]
        )
    monkeypatch.setattr(client.app.state, "judge_jobs", None, raising=False)
    client.app.openapi_schema = None
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
        client.app.state.judge_jobs = None
        reset_vlm_judge_service()
        limiter.reset()
        client.app.dependency_overrides.pop(get_detection_service, None)
        client.app.router.routes[:] = original_routes
        client.app.router.lifespan_context = original_lifespan
        client.app.openapi_schema = None


schema = schemathesis.pytest.from_fixture("api_schema")


def _resize[T](values: list[T], length: int, fill: T) -> list[T]:
    return (values + [values[-1] if values else fill] * length)[:length]


def _align_matrix(values: list[list[float]], length: int | None = None) -> list[list[float]]:
    width = max(map(len, values))
    rows = [_resize(row, width, 0.0) for row in values]
    return _resize(rows, length, [0.0] * width) if length is not None else rows


def prepare_positive_case(case: schemathesis.Case, client: TestClient) -> None:
    """Establish dependent inputs without changing any negative generated case."""
    if case.meta is None or case.meta.generation.mode != schemathesis.GenerationMode.POSITIVE:
        return
    for parameter in case.operation.iter_parameters():
        values = {"path": case.path_parameters, "query": case.query}.get(parameter.location)
        if values is None:
            continue
        value = values.get(parameter.name)
        definition = parameter.definition.get("schema", {})
        alternatives = [definition, *definition.get("anyOf", [])]
        if (
            isinstance(value, float)
            and value.is_integer()
            and any(option.get("type") == "integer" for option in alternatives)
        ):
            values[parameter.name] = int(value)
    path = case.operation.path
    if path.endswith("/annotations") and isinstance(case.body, dict):
        body = deepcopy(case.body)
        instruction = body.get("language_instruction")
        allowed = set()
        if isinstance(instruction, dict):
            subtasks = instruction.get("subtask_instructions", [])
            for index, subtask in enumerate(subtasks):
                subtask["id"] = f"subtask-{index}"
            allowed = {"instruction", *(f"subtasks/{subtask['id']}" for subtask in subtasks)}
        if "instruction_adoption" in body:
            body["instruction_adoption"] = {
                key: value for key, value in body["instruction_adoption"].items() if key in allowed
            }
        case.body = body
    if path.endswith("/edits") and isinstance(case.body, dict):
        body = deepcopy(case.body)
        for subtask in body["operations"].get("subtasks") or []:
            subtask["frameRange"] = sorted(subtask["frameRange"])
        case.body = body
    if path.startswith("/api/ai/") and isinstance(case.body, dict):
        body = deepcopy(case.body)
        if path != "/api/ai/cluster":
            body["positions"] = _align_matrix(body["positions"])
            length = len(body["positions"])
            for key in ("timestamps", "gripper_states", "gripper_commands"):
                if body.get(key) is not None:
                    body[key] = _resize(body[key], length, 0.0)
            if body.get("forces") is not None:
                body["forces"] = _align_matrix(body["forces"], length)
        elif "trajectories" in body:
            body["trajectories"] = [_align_matrix(trajectory) for trajectory in body["trajectories"]]
            if body.get("num_clusters") is not None:
                body["num_clusters"] = min(body["num_clusters"], len(body["trajectories"]))
        case.body = body

    parameter_names = {parameter.name.lower() for parameter in case.operation.iter_parameters()}
    if "if-none-match" in parameter_names:
        parameters = case.path_parameters or {}
        if path.endswith("/joint-config") or path == "/api/joint-config/defaults":
            read_path = case.formatted_path
        else:
            dataset_id = quote(unquote(str(parameters["dataset_id"])), safe="")
            if path.endswith(("/annotations", "/edits")):
                read_path = f"/api/datasets/{dataset_id}/episodes/{parameters['episode_idx']}/{path.rsplit('/', 1)[1]}"
            else:
                read_path = f"/api/datasets/{dataset_id}/labels"
        response = client.get(read_path)
        assert response.status_code in (200, 404), response.text
        headers = CaseInsensitiveDict(case.headers or {})
        headers.pop("if-match", None)
        headers.pop("if-none-match", None)
        etag = response.headers.get("ETag")
        headers["if-match" if etag else "if-none-match"] = etag or "*"
        case.headers = headers


@schema.parametrize()
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
def test_openapi_contract_fuzzing(
    case: schemathesis.Case, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated cases must not trigger server errors or violate the OpenAPI contract."""
    with TemporaryDirectory(dir=tmp_path) as data_dir, monkeypatch.context() as patch:
        patch.setenv("DATA_DIR", data_dir)
        _reset_singletons()
        limiter.reset()
        reset_vlm_judge_service()
        client.app.state.judge_jobs = None
        try:
            prepare_positive_case(case, client)
            case.call_and_validate()
        finally:
            _reset_singletons()
            limiter.reset()
            reset_vlm_judge_service()
            client.app.state.judge_jobs = None


def _generation_meta(mode: schemathesis.GenerationMode) -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={},
        phase=PhaseInfo(TestPhase.FUZZING, FuzzingPhaseData(None, None, None, None)),
    )


def _call_body_contract(
    api_schema: OpenApiSchema,
    path: str,
    method: str,
    body: dict[str, Any],
    expected_status: int,
    headers: dict[str, str] | None = None,
) -> Response:
    parameters = {
        name: value for name, value in {"dataset_id": "sample", "episode_idx": 0}.items() if f"{{{name}}}" in path
    }
    accepted = 200 <= expected_status < 300
    case = api_schema.make_case(
        operation=api_schema[path][method],
        path_parameters=parameters,
        body=body,
        headers=headers,
        meta=_generation_meta(
            schemathesis.GenerationMode.POSITIVE if accepted else schemathesis.GenerationMode.NEGATIVE
        ),
    )
    response = case.call_and_validate()
    assert response.status_code == expected_status, response.text
    document = api_schema.raw_schema
    body_schema = document["paths"][path][method.lower()]["requestBody"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator({**body_schema, "components": document["components"]})
    assert validator.is_valid(body) == accepted, body
    return response


@pytest.mark.parametrize(
    ("language", "expected_status"),
    [
        ("en", 200),
        (" en ", 200),
        ("\u001cen\u0085", 200),
        ("\r\nzh-Hant-TW\t", 200),
        ("en-x-abcdefgh-abcdefgh-abcdefgh-abc", 200),
        (" en-x-abcdefgh-abcdefgh-abcdefgh-abc", 422),
        (" e\rn ", 422),
        (" en_US ", 422),
        ("\u200ben", 422),
        (" \r\n ", 422),
    ],
)
def test_language_body_normalization_contract(
    api_schema: OpenApiSchema,
    tmp_path: Path,
    sample_annotation: EpisodeAnnotation,
    language: str,
    expected_status: int,
) -> None:
    write_source(tmp_path / "sample")
    body = sample_annotation.model_dump(mode="json")
    body["language_instruction"] = {"instruction": "Pick the cube", "source": "human", "language": language}
    response = _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
        "PUT",
        body,
        expected_status,
        headers={"If-None-Match": "*"},
    )
    if expected_status == 200:
        assert response.json()["annotations"][0]["language_instruction"]["language"] == language.strip()


@pytest.fixture
def annotation_contract_body(sample_annotation: EpisodeAnnotation, tmp_path: Path) -> dict[str, Any]:
    write_source(tmp_path / "sample")
    body = sample_annotation.model_dump(mode="json")
    body["data_quality"]["issues"] = [{"type": "frame-drop", "severity": "minor", "affected_frames": [0, 1]}]
    body["anomalies"]["anomalies"] = [
        {
            "id": "force-spike",
            "type": "force-spike",
            "severity": "high",
            "frame_range": [0, 1],
            "timestamp": [0.0, 0.1],
            "description": "Force spike",
            "auto_detected": True,
            "verified": False,
        }
    ]
    body["object_detections"] = [
        {
            "frame_index": 0,
            "camera": CAMERA,
            "detections": [{"label": "cube", "confidence": 0.5, "bbox": [0.0, 0.0, 16.0, 16.0]}],
        }
    ]
    return body


_ANNOTATION_SCALAR_PATHS = [
    ("task_completeness", "confidence"),
    ("task_completeness", "completion_percentage"),
    ("trajectory_quality", "overall_score"),
    *(("trajectory_quality", "metrics", metric) for metric in ("smoothness", "efficiency", "safety", "precision")),
    *(("data_quality", "issues", 0, "affected_frames", index) for index in range(2)),
    *(("anomalies", "anomalies", 0, "frame_range", index) for index in range(2)),
    *(("anomalies", "anomalies", 0, "timestamp", index) for index in range(2)),
    ("anomalies", "anomalies", 0, "auto_detected"),
    ("anomalies", "anomalies", 0, "verified"),
    ("object_detections", 0, "frame_index"),
    ("object_detections", 0, "detections", 0, "confidence"),
    *(("object_detections", 0, "detections", 0, "bbox", index) for index in range(4)),
]


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize(
    ("field_path", "text", "expected_status"),
    [
        pytest.param(field_path, text, status, id=f"{field_name}-{case}")
        for field_name, field_path, limit in [
            ("instruction", ("language_instruction", "instruction"), 1000),
            ("detection-label", ("object_detections", 0, "detections", 0, "label"), 100),
        ]
        for case, text, status in [
            ("empty", "", 422),
            ("cr", "\r", 422),
            ("lf", "\n", 422),
            ("crlf", "\r\n\r\n", 422),
            ("plain", "cube", 200),
            ("normalized", "\rcu\r\nbe\n", 200),
            ("space", " ", 200),
            ("tab", "\t", 200),
            ("unicode-separator", "\u2028", 200),
            ("padded", " \r\n ", 200),
            ("raw-limit", "x" * limit, 200),
            ("normalized-limit", "x" * (limit - 1) + "\r", 200),
            ("over-limit", "x" * (limit + 1), 422),
            ("normalized-over-limit", "x" * limit + "\r", 422),
        ]
    ],
)
def test_annotation_text_normalization_contract(
    api_schema: OpenApiSchema,
    client: TestClient,
    annotation_contract_body: dict[str, Any],
    field_path: tuple[str | int, ...],
    text: str,
    expected_status: int,
    existing: bool,
) -> None:
    path = "/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations"
    url = "/api/datasets/sample/episodes/0/annotations"
    annotation_contract_body["language_instruction"] = {"instruction": "Pick the cube", "source": "human"}
    headers = {"If-None-Match": "*"}
    if existing:
        _call_body_contract(api_schema, path, "PUT", annotation_contract_body, 200, headers=headers)
    before = client.get(url)
    assert before.status_code == 200
    if existing:
        headers = {"If-Match": before.headers["ETag"]}
    body = deepcopy(annotation_contract_body)
    container = body
    for key in field_path[:-1]:
        container = container[key]
    container[field_path[-1]] = text

    response = _call_body_contract(api_schema, path, "PUT", body, expected_status, headers=headers)
    after = client.get(url)
    assert after.status_code == 200
    if expected_status == 200:
        assert after.json() == response.json()
        assert [after.headers["ETag"]] == response.headers["etag"]
        saved = after.json()["annotations"][0]
        for key in field_path:
            saved = saved[key]
        assert saved == text.replace("\r", "").replace("\n", "")
    else:
        assert after.json() == before.json()
        assert after.headers.get("ETag") == before.headers.get("ETag")
        _call_body_contract(api_schema, path, "PUT", annotation_contract_body, 200, headers=headers)
        assert client.get(url).status_code == 200


@pytest.mark.parametrize("field_path", _ANNOTATION_SCALAR_PATHS, ids=lambda path: ".".join(map(str, path)))
@pytest.mark.parametrize("representation", ["string", "wrong-type"])
def test_annotation_scalar_contract(
    api_schema: OpenApiSchema,
    client: TestClient,
    annotation_contract_body: dict[str, Any],
    field_path: tuple[str | int, ...],
    representation: str,
) -> None:
    container = annotation_contract_body
    for key in field_path[:-1]:
        container = container[key]
    value = container[field_path[-1]]
    container[field_path[-1]] = (
        str(value).lower() if representation == "string" else (1 if isinstance(value, bool) else True)
    )
    _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
        "PUT",
        annotation_contract_body,
        422,
        headers={"If-None-Match": "*"},
    )
    assert client.get("/api/datasets/sample/episodes/0/annotations").json()["annotations"] == []


@pytest.mark.parametrize("integral_floats", [False, True])
@pytest.mark.parametrize("nullable", [False, True])
def test_annotation_numeric_controls(
    api_schema: OpenApiSchema,
    client: TestClient,
    annotation_contract_body: dict[str, Any],
    integral_floats: bool,
    nullable: bool,
) -> None:
    if integral_floats:
        for field_path in _ANNOTATION_SCALAR_PATHS:
            container = annotation_contract_body
            for key in field_path[:-1]:
                container = container[key]
            value = container[field_path[-1]]
            if type(value) is int:
                container[field_path[-1]] = float(value)
    if nullable:
        annotation_contract_body["task_completeness"]["completion_percentage"] = None
        annotation_contract_body["data_quality"]["issues"][0]["affected_frames"] = None
    response = _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
        "PUT",
        annotation_contract_body,
        200,
        headers={"If-None-Match": "*"},
    )
    saved = response.json()["annotations"][0]
    expected = {**annotation_contract_body, "annotator_id": saved["annotator_id"]}
    assert saved == EpisodeAnnotation.model_validate(expected).model_dump(mode="json")
    assert client.get("/api/datasets/sample/episodes/0/annotations").json()["annotations"] == [saved]


@pytest.mark.parametrize("timestamp", [0, 0.0, True])
def test_annotation_timestamp_type_contract(
    api_schema: OpenApiSchema, client: TestClient, annotation_contract_body: dict[str, Any], timestamp: object
) -> None:
    annotation_contract_body["timestamp"] = timestamp
    _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
        "PUT",
        annotation_contract_body,
        422,
        headers={"If-None-Match": "*"},
    )
    assert client.get("/api/datasets/sample/episodes/0/annotations").json()["annotations"] == []


@pytest.mark.parametrize(
    ("field_path", "value", "expected_status"),
    [
        (("task_completeness", "completion_percentage"), 0.0, 200),
        (("task_completeness", "completion_percentage"), 100.0, 200),
        (("task_completeness", "completion_percentage"), -1, 422),
        (("task_completeness", "completion_percentage"), 101, 422),
        (("task_completeness", "completion_percentage"), 0.5, 422),
        (("task_completeness", "confidence"), 1.0, 200),
        (("task_completeness", "confidence"), 5.0, 200),
        (("task_completeness", "confidence"), 0, 422),
        (("task_completeness", "confidence"), 6, 422),
        (("task_completeness", "confidence"), 1.5, 422),
        (("trajectory_quality", "overall_score"), 1.0, 200),
        (("trajectory_quality", "overall_score"), 5.0, 200),
        (("trajectory_quality", "overall_score"), 0, 422),
        (("trajectory_quality", "overall_score"), 6, 422),
        (("trajectory_quality", "overall_score"), 1.5, 422),
        (("object_detections", 0, "frame_index"), -1, 422),
        (("object_detections", 0, "frame_index"), 0.5, 422),
    ],
)
def test_annotation_numeric_boundaries(
    api_schema: OpenApiSchema,
    annotation_contract_body: dict[str, Any],
    field_path: tuple[str | int, ...],
    value: float,
    expected_status: int,
) -> None:
    container = annotation_contract_body
    for key in field_path[:-1]:
        container = container[key]
    container[field_path[-1]] = value
    _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations",
        "PUT",
        annotation_contract_body,
        expected_status,
        headers={"If-None-Match": "*"},
    )


@pytest.mark.parametrize("overwrite", [True, False, "true", "false", 1, 0, None])
def test_import_scalar_contract(
    api_schema: OpenApiSchema, client: TestClient, tmp_path: Path, overwrite: object
) -> None:
    write_source(tmp_path / "sample")
    analysis = client.put(
        "/api/datasets/sample/episodes/0/analysis", json={"object": "cube"}, headers={"If-None-Match": "*"}
    )
    assert analysis.status_code == 200
    assigned = client.put(
        "/api/datasets/sample/episodes/0/labels",
        json={"labels": ["OBJECT: BALL"]},
        headers={"If-Match": analysis.headers["ETag"]},
    )
    assert assigned.status_code == 200
    before = client.get("/api/datasets/sample/labels")
    accepted = isinstance(overwrite, bool)
    _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/labels/import-from-analysis",
        "POST",
        {"field": "object", "overwrite": overwrite},
        200 if accepted else 422,
        headers={"If-Match": before.headers["ETag"]},
    )
    after = client.get("/api/datasets/sample/labels")
    if accepted:
        assert after.json()["episodes"]["0"] == (["OBJECT: CUBE"] if overwrite else ["OBJECT: BALL", "OBJECT: CUBE"])
    else:
        assert after.json() == before.json()
        assert after.headers["ETag"] == before.headers["ETag"]


@pytest.mark.parametrize("api_schema", [True], indirect=True, ids=["vlm"])
@pytest.mark.parametrize("force", [True, False, "true", "false", 1, 0, None])
def test_judge_scalar_contract(api_schema: OpenApiSchema, client: TestClient, tmp_path: Path, force: object) -> None:
    write_source(tmp_path / "sample")
    path = "/api/datasets/{dataset_id}/episodes/{episode_idx}/judge"
    initial = _finish_judge_contract(api_schema, client, _call_body_contract(api_schema, path, "POST", {}, 202))
    assert initial["cached"] is False
    accepted = isinstance(force, bool)
    response = _call_body_contract(api_schema, path, "POST", {"force": force}, 202 if accepted else 422)
    if accepted:
        assert _finish_judge_contract(api_schema, client, response)["cached"] is not force
    repeated = _finish_judge_contract(api_schema, client, _call_body_contract(api_schema, path, "POST", {}, 202))
    assert repeated["cached"] is True


def _finish_judge_contract(api_schema: OpenApiSchema, client: TestClient, response: Response) -> dict[str, Any]:
    assert response.status_code == 202
    assert client.portal.call(client.app.state.judge_jobs.run_once)
    case = api_schema.make_case(
        operation=api_schema["/api/judge/jobs/{job_id}"]["GET"],
        path_parameters={"job_id": response.json()["id"]},
    )
    completed = case.call_and_validate()
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded", completed.text
    return completed.json()["targets"][0]


@pytest.mark.parametrize("mapping", ["samples", "snapshot_ids"])
@pytest.mark.parametrize("key", ["0", "00", "-1", "+0", "0.0", "0_0", "\u0660", "0\n"])
def test_judge_job_map_keys_match_schema(mapping: str, key: str) -> None:
    body = {
        "dataset_id": "sample",
        "episode_indices": [0],
        mapping: {key: {"snapshot_id": "snapshot"} if mapping == "samples" else "snapshot"},
    }
    accepted = key in ("0", "00", "-1")
    assert Draft202012Validator(JobRequest.model_json_schema()).is_valid(body) == accepted
    if accepted:
        assert list(getattr(JobRequest.model_validate(body), mapping)) == [int(key)]
    else:
        with pytest.raises(ValidationError):
            JobRequest.model_validate(body)


@pytest.mark.parametrize("index", [0, 1, 1.0, "1", True, -1, 0.5])
def test_judge_job_indices_match_schema(index: object) -> None:
    body = {"dataset_id": "sample", "episode_indices": [index]}
    accepted = type(index) in (int, float) and index in (0, 1)
    assert Draft202012Validator(JobRequest.model_json_schema()).is_valid(body) == accepted
    if accepted:
        assert JobRequest.model_validate(body).episode_indices == [int(index)]
    else:
        with pytest.raises(ValidationError):
            JobRequest.model_validate(body)


@pytest.mark.parametrize(
    ("view", "accepted"),
    [
        ("front", True),
        ("fr\r\nont", True),
        ("", False),
        ("../front", False),
        ("front\nextra", True),
        ("x" * 129, False),
    ],
)
def test_judge_job_views_match_schema(view: str, accepted: bool) -> None:
    body = {"dataset_id": "sample", "episode_indices": [0], "options": {"views": [view]}}
    assert Draft202012Validator(JobRequest.model_json_schema()).is_valid(body) == accepted
    if accepted:
        assert JobRequest.model_validate(body).options.views == [sanitize_user_string(view)]
    else:
        with pytest.raises(ValidationError):
            JobRequest.model_validate(body)


@pytest.mark.parametrize("api_schema", [True], indirect=True, ids=["vlm"])
@pytest.mark.parametrize(
    "dataset_id", ["sample", "robot--sample", "sample--", "sample--..", "sample----other", "sample-\r\n-.."]
)
def test_judge_dataset_path_schema_preserves_traversal_rejection(
    api_schema: OpenApiSchema, client: TestClient, dataset_id: str
) -> None:
    operation = api_schema.raw_schema["paths"]["/api/datasets/{dataset_id}/episodes/{episode_idx}/judge"]["get"]
    parameter = next(item["schema"] for item in operation["parameters"] if item["name"] == "dataset_id")
    accepted = dataset_id in ("sample", "robot--sample")
    assert Draft202012Validator(parameter).is_valid(dataset_id) == accepted
    response = client.get(f"/api/datasets/{quote(dataset_id, safe='')}/episodes/0/judge")
    assert response.status_code == (404 if accepted else 400)


@pytest.mark.parametrize("api_schema", [True], indirect=True, ids=["vlm"])
@pytest.mark.parametrize(
    ("method", "expected_status"),
    [
        ("gvl", 202),
        ("chronological", 202),
        ("g\rvl", 202),
        ("\r\nchronological\r\n", 202),
        ("\n".join("chronological"), 202),
        (None, 202),
        ("", 422),
        ("reverse", 422),
        ("GVL", 422),
        (" gvl ", 422),
        ("g\tvl", 422),
        ("gvl\nextra", 422),
        ("gvl\u2028", 422),
    ],
)
def test_judge_method_normalization_contract(
    api_schema: OpenApiSchema, client: TestClient, tmp_path: Path, method: str | None, expected_status: int
) -> None:
    write_source(tmp_path / "sample")
    path = "/api/datasets/{dataset_id}/episodes/{episode_idx}/judge"
    response = _call_body_contract(api_schema, path, "POST", {"process_method": method}, expected_status)
    if expected_status == 202:
        _finish_judge_contract(api_schema, client, response)
        configured_method = response.json()["config"]["process_method"]
        canonical = sanitize_user_string(method) if method is not None else configured_method
        assert configured_method == canonical
        cached = _call_body_contract(api_schema, path, "POST", {"process_method": canonical}, 202)
        assert _finish_judge_contract(api_schema, client, cached)["cached"] is True


@pytest.mark.parametrize(
    ("field", "expected_status"),
    [
        ("object", 200),
        (" object ", 200),
        ("\u001cobject\u0085", 200),
        (" ob\r\nject ", 200),
        ("object" + " " * 58, 200),
        ("object" + " " * 59, 422),
        ("ob\tject", 400),
        ("OBJECT", 400),
        ("object\nextra", 400),
        (" \r\n ", 400),
    ],
)
def test_import_body_normalization_contract(
    api_schema: OpenApiSchema, client: TestClient, tmp_path: Path, field: str, expected_status: int
) -> None:
    write_source(tmp_path / "sample")
    analysis = client.put(
        "/api/datasets/sample/episodes/0/analysis", json={"object": "cube"}, headers={"If-None-Match": "*"}
    )
    assert analysis.status_code == 200, analysis.text
    response = _call_body_contract(
        api_schema,
        "/api/datasets/{dataset_id}/labels/import-from-analysis",
        "POST",
        {"field": field},
        expected_status,
        headers={"If-Match": analysis.headers["ETag"]},
    )
    if expected_status == 200:
        assert response.json()["field"] == "object"
        assert response.json()["episodes"]["0"] == ["OBJECT: CUBE"]
        assert client.get("/api/datasets/sample/labels").json()["episodes"]["0"] == ["OBJECT: CUBE"]


@pytest.mark.parametrize(
    ("label", "normalized", "expected_status"),
    [
        ("cube", ["cube"], 200),
        ("", None, 200),
        (" " * 101, None, 200),
        (" " * 55 + "cube" + " " * 55, ["cube"], 200),
        ("\u001c" * 101 + "cube" + "\u0085" * 101, ["cube"], 200),
        ("x" * 100, ["x" * 100], 200),
        ("\r" + "x" * 100 + "\n", ["x" * 100], 200),
        ("cu\r\nbe", ["cube"], 200),
        ("x" * 101, None, 422),
        ("x" * 50 + "\r" + "x" * 50, None, 422),
        ("\u200b" * 101, None, 422),
    ],
)
def test_detection_body_normalization_contract(
    api_schema: OpenApiSchema, tmp_path: Path, label: str, normalized: list[str] | None, expected_status: int
) -> None:
    write_source(tmp_path / "sample")
    body = {"labels": [label]}
    _call_body_contract(
        api_schema, "/api/datasets/{dataset_id}/episodes/{episode_idx}/detect", "POST", body, expected_status
    )
    if expected_status == 200:
        assert DetectionRequest.model_validate(body).labels == normalized


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ({"frames": [0], "confidence": 0.5}, 200),
        ({"frames": [0.0], "confidence": 1}, 200),
        ({"frames": None, "confidence": None}, 200),
        ({"frames": ["0"]}, 422),
        ({"frames": [False]}, 422),
        ({"frames": [0.5]}, 422),
        ({"confidence": "0.5"}, 422),
        ({"confidence": True}, 422),
    ],
)
def test_detection_scalar_contract(
    api_schema: OpenApiSchema, tmp_path: Path, body: dict[str, Any], expected_status: int
) -> None:
    write_source(tmp_path / "sample")
    _call_body_contract(
        api_schema, "/api/datasets/{dataset_id}/episodes/{episode_idx}/detect", "POST", body, expected_status
    )


@pytest.mark.parametrize(
    ("count", "expected_status"),
    [(2, 200), (2.0, 200), (None, 200), ("2", 422), (2.5, 422), (1, 422), (21, 422), (True, 422)],
)
def test_cluster_scalar_contract(api_schema: OpenApiSchema, count: object, expected_status: int) -> None:
    body = {
        "trajectories": [[[0.0], [1.0], [2.0]], [[0.0], [2.0], [4.0]]],
        "num_clusters": count,
    }
    response = _call_body_contract(api_schema, "/api/ai/cluster", "POST", body, expected_status)
    if expected_status == 200 and count is not None:
        assert response.json()["num_clusters"] == 2


@pytest.fixture
def export_contract_body(tmp_path: Path) -> dict[str, Any]:
    write_source(tmp_path / "sample")
    return {
        "episodeIndices": [0],
        "outputPath": str(tmp_path / "exported"),
        "applyEdits": True,
        "includeLanguageInstructions": True,
        "edits": {
            "0": {
                "episodeIndex": 0,
                "globalTransform": {
                    "crop": {"x": 0, "y": 0, "width": 16, "height": 16},
                    "resize": {"width": 16, "height": 16},
                },
                "removedFrames": [1],
                "insertedFrames": [{"afterFrameIndex": 3, "interpolationFactor": 0.5}],
                "subtasks": [{"id": "pick", "label": "Pick", "frameRange": [0, 4], "color": "#00ff00"}],
                "trajectoryAdjustments": [{"frameIndex": 2, "channelDeltas": {"0": 0.5}, "channelValues": {"1": 2}}],
            }
        },
    }


_EXPORT_SCALAR_PATHS = [
    ("episodeIndices", 0),
    ("applyEdits",),
    ("includeLanguageInstructions",),
    ("edits", "0", "episodeIndex"),
    ("edits", "0", "globalTransform", "crop", "x"),
    ("edits", "0", "globalTransform", "resize", "width"),
    ("edits", "0", "removedFrames", 0),
    ("edits", "0", "insertedFrames", 0, "afterFrameIndex"),
    ("edits", "0", "insertedFrames", 0, "interpolationFactor"),
    ("edits", "0", "subtasks", 0, "frameRange", 0),
    ("edits", "0", "trajectoryAdjustments", 0, "frameIndex"),
    ("edits", "0", "trajectoryAdjustments", 0, "channelDeltas", "0"),
    ("edits", "0", "trajectoryAdjustments", 0, "channelValues", "1"),
]


def _assert_export_contract(api_schema: OpenApiSchema, body: dict[str, Any], route: str, expected_status: int) -> None:
    response = _call_body_contract(api_schema, route, "POST", body, expected_status)
    output = Path(body["outputPath"])
    if expected_status == 200:
        if route.endswith("/stream"):
            assert "event: complete" in response.text
            assert "event: error" not in response.text
        else:
            assert response.json()["success"] is True
        assert (output / "meta/info.json").is_file()
    else:
        assert not output.exists()


@pytest.mark.parametrize("route", ["/api/datasets/{dataset_id}/export", "/api/datasets/{dataset_id}/export/stream"])
@pytest.mark.parametrize("field_path", _EXPORT_SCALAR_PATHS, ids=lambda path: ".".join(map(str, path)))
@pytest.mark.parametrize("representation", ["string", "wrong-type"])
def test_export_scalar_contract(
    api_schema: OpenApiSchema,
    export_contract_body: dict[str, Any],
    field_path: tuple[str | int, ...],
    representation: str,
    route: str,
) -> None:
    container = export_contract_body
    for key in field_path[:-1]:
        container = container[key]
    value = container[field_path[-1]]
    container[field_path[-1]] = (
        str(value).lower() if representation == "string" else (1 if isinstance(value, bool) else False)
    )
    _assert_export_contract(api_schema, export_contract_body, route, 422)


@pytest.mark.parametrize("route", ["/api/datasets/{dataset_id}/export", "/api/datasets/{dataset_id}/export/stream"])
@pytest.mark.parametrize("integral_floats", [False, True])
def test_export_numeric_controls(
    api_schema: OpenApiSchema, export_contract_body: dict[str, Any], integral_floats: bool, route: str
) -> None:
    if integral_floats:
        for field_path in _EXPORT_SCALAR_PATHS:
            container = export_contract_body
            for key in field_path[:-1]:
                container = container[key]
            value = container[field_path[-1]]
            if type(value) is int:
                container[field_path[-1]] = float(value)
    _assert_export_contract(api_schema, export_contract_body, route, 200)


@pytest.mark.parametrize("route", ["/api/datasets/{dataset_id}/export", "/api/datasets/{dataset_id}/export/stream"])
@pytest.mark.parametrize("mapping", ["edits", "channelDeltas", "channelValues"])
@pytest.mark.parametrize("key", ["0", "00", "-0", "-1", "+0", "0.0", " 0 ", "0_0", "\u0660", "0\n"])
def test_export_key_contract(
    api_schema: OpenApiSchema, export_contract_body: dict[str, Any], mapping: str, key: str, route: str
) -> None:
    if mapping == "edits":
        export_contract_body["edits"] = {key: export_contract_body["edits"]["0"]}
    else:
        export_contract_body["edits"]["0"]["trajectoryAdjustments"][0][mapping] = {key: 0.5}
    accepted = key in ("0", "00") or (mapping == "edits" and key in ("-0", "-1"))
    _assert_export_contract(api_schema, export_contract_body, route, 200 if accepted else 422)


@pytest.mark.parametrize("mode", list(schemathesis.GenerationMode))
def test_integer_wire_preparation_preserves_values_and_negative_cases(
    api_schema: OpenApiSchema, client: TestClient, mode: schemathesis.GenerationMode
) -> None:
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes"]["GET"],
        path_parameters={"dataset_id": "sample"},
        query={"offset": 100.0, "limit": 100.0, "task_index": 1.0},
        meta=_generation_meta(mode),
    )
    prepare_positive_case(case, client)
    assert case.query == {"offset": 100, "limit": 100, "task_index": 1}
    expected_type = int if mode == schemathesis.GenerationMode.POSITIVE else float
    assert all(type(value) is expected_type for value in case.query.values())
    if mode == schemathesis.GenerationMode.POSITIVE:
        assert case.call_and_validate().status_code == 404

    path_case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes/{episode_idx}/detections"]["DELETE"],
        path_parameters={"dataset_id": "sample", "episode_idx": 0.0},
        meta=_generation_meta(mode),
    )
    prepare_positive_case(path_case, client)
    assert path_case.path_parameters["episode_idx"] == 0
    assert type(path_case.path_parameters["episode_idx"]) is expected_type
    if mode == schemathesis.GenerationMode.POSITIVE:
        assert path_case.call_and_validate().status_code == 200

    invalid = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes/{episode_idx}/detections"]["DELETE"],
        path_parameters={"dataset_id": "sample", "episode_idx": "+0.0"},
        meta=_generation_meta(schemathesis.GenerationMode.NEGATIVE),
    )
    prepare_positive_case(invalid, client)
    assert invalid.path_parameters["episode_idx"] == "+0.0"
    assert invalid.call_and_validate().status_code == 422


@pytest.mark.parametrize("mode", list(schemathesis.GenerationMode))
def test_dependent_input_preparation_preserves_negative_cases_and_values(
    api_schema: OpenApiSchema, client: TestClient, mode: schemathesis.GenerationMode
) -> None:
    body = {
        "positions": [[2.0], [4.0, 6.0], [8.0]],
        "timestamps": [0.0, 0.1, 0.2, 0.3],
        "forces": [[1.0, 3.0]],
        "gripper_states": [],
    }
    case = api_schema.make_case(
        operation=api_schema["/api/ai/anomaly-detection"]["POST"], body=deepcopy(body), meta=_generation_meta(mode)
    )
    prepare_positive_case(case, client)
    if mode == schemathesis.GenerationMode.NEGATIVE:
        assert case.body == body
    else:
        assert case.body["positions"] == [[2.0, 2.0], [4.0, 6.0], [8.0, 8.0]]
        assert case.body["timestamps"] == [0.0, 0.1, 0.2]
        assert case.body["forces"] == [[1.0, 3.0]] * 3
        assert case.body["gripper_states"] == [0.0] * 3
        assert case.call_and_validate().status_code == 200


def test_revision_preparation_preserves_negative_headers(api_schema: OpenApiSchema, client: TestClient) -> None:
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/labels/save"]["POST"],
        path_parameters={"dataset_id": "sample"},
        headers={"If-Match": '"stale"', "If-None-Match": "invalid"},
        meta=_generation_meta(schemathesis.GenerationMode.NEGATIVE),
    )
    headers = deepcopy(case.headers)
    prepare_positive_case(case, client)
    assert case.headers == headers
    assert case.call_and_validate().status_code == 400


def test_revision_preparation_preserves_encoded_dataset_path(api_schema: OpenApiSchema, client: TestClient) -> None:
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/labels/save"]["POST"],
        path_parameters={"dataset_id": "sam%0Dple"},
        meta=_generation_meta(schemathesis.GenerationMode.POSITIVE),
    )
    with patch.object(client, "get", wraps=client.get) as read:
        prepare_positive_case(case, client)
    read.assert_called_once_with("/api/datasets/sam%0Dple/labels")
    assert case.path_parameters == {"dataset_id": "sam%0Dple"}
    assert case.call_and_validate().status_code == 200


@pytest.mark.parametrize("mode", list(schemathesis.GenerationMode))
def test_annotation_preparation_preserves_negative_subtask_relations(
    api_schema: OpenApiSchema,
    client: TestClient,
    annotation_contract_body: dict[str, Any],
    mode: schemathesis.GenerationMode,
) -> None:
    annotation_contract_body["language_instruction"] = {
        "instruction": "Pick and place",
        "source": "human",
        "subtask_instructions": [
            {"id": "duplicate", "text": "Pick"},
            {"id": "duplicate", "text": "Place"},
        ],
    }
    annotation_contract_body["instruction_adoption"] = {"instruction": "template", "subtasks/missing": "template"}
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations"]["PUT"],
        path_parameters={"dataset_id": "sample", "episode_idx": 0},
        body=deepcopy(annotation_contract_body),
        meta=_generation_meta(mode),
    )
    prepare_positive_case(case, client)
    if mode == schemathesis.GenerationMode.NEGATIVE:
        assert case.body == annotation_contract_body
    else:
        assert [item["id"] for item in case.body["language_instruction"]["subtask_instructions"]] == [
            "subtask-0",
            "subtask-1",
        ]
        assert case.body["instruction_adoption"] == {"instruction": "template"}
        assert case.call_and_validate().status_code == 200


@pytest.mark.parametrize("mode", list(schemathesis.GenerationMode))
def test_edit_preparation_preserves_negative_frame_order(
    api_schema: OpenApiSchema, client: TestClient, mode: schemathesis.GenerationMode
) -> None:
    body = {"operations": {"subtasks": [{"frameRange": [4, 1]}]}}
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes/{episode_idx}/edits"]["PUT"],
        path_parameters={"dataset_id": "sample", "episode_idx": 0},
        body=deepcopy(body),
        meta=_generation_meta(mode),
    )
    prepare_positive_case(case, client)
    assert case.body["operations"]["subtasks"][0]["frameRange"] == (
        [1, 4] if mode == schemathesis.GenerationMode.POSITIVE else [4, 1]
    )


@pytest.mark.parametrize("label", ["SUCCESS", " SU\rCCESS ", "FAI\nLURE", "par\r\ntial", "custom"])
@pytest.mark.parametrize("mode", list(schemathesis.GenerationMode))
def test_reserved_label_preparation_preserves_original_cases(
    api_schema: OpenApiSchema, client: TestClient, label: str, mode: schemathesis.GenerationMode
) -> None:
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/labels/options/{label}"]["DELETE"],
        path_parameters={"dataset_id": "sample", "label": label},
        headers={"If-None-Match": "*"},
        meta=_generation_meta(mode),
    )
    parameters = deepcopy(case.path_parameters)
    headers = deepcopy(case.headers)
    prepare_positive_case(case, client)
    assert case.path_parameters == parameters
    if mode == schemathesis.GenerationMode.NEGATIVE:
        assert case.headers == headers
    else:
        immutable = sanitize_user_string(label).strip().upper() in DEFAULT_LABELS
        assert case.call_and_validate().status_code == (409 if immutable else 200)
    assert client.get("/api/datasets/sample/labels/options").json() == DEFAULT_LABELS


@pytest.mark.parametrize("mode", list(schemathesis.GenerationMode))
@pytest.mark.parametrize("value", ["bytes=4-1", "bytes=4--1", "bytes= +02 - +04 ", "bytes=0-"])
def test_range_preparation_preserves_original_cases(
    api_schema: OpenApiSchema,
    client: TestClient,
    mode: schemathesis.GenerationMode,
    value: str,
) -> None:
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"]["GET"],
        path_parameters={"dataset_id": "sample", "episode_idx": 0, "camera": "front"},
        headers={"Range": value},
        meta=_generation_meta(mode),
    )
    prepare_positive_case(case, client)
    assert case.headers["Range"] == value
    if mode == schemathesis.GenerationMode.POSITIVE and not value.startswith("bytes=4-"):
        assert case.call_and_validate().status_code == 404


@pytest.mark.parametrize("value", ["items=0-3", "bytes=-2", "bytes=abc", "bytes=4-1", "bytes=0-1,3-4"])
def test_original_invalid_video_ranges_satisfy_response_contract(
    api_schema: OpenApiSchema,
    value: str,
) -> None:
    # Endpoint ordering is a relational constraint, not expressible by the header's string schema.
    meta = None if value == "bytes=4-1" else _generation_meta(schemathesis.GenerationMode.NEGATIVE)
    case = api_schema.make_case(
        operation=api_schema["/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"]["GET"],
        path_parameters={"dataset_id": "sample", "episode_idx": 0, "camera": "front"},
        headers={"Range": value},
        meta=meta,
    )
    response = case.call_and_validate()
    assert case.headers["Range"] == value
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Range header"}


def test_existing_resources_satisfy_complete_contracts(
    api_schema: OpenApiSchema, client: TestClient, tmp_path: Path, sample_annotation: EpisodeAnnotation
) -> None:
    """Require successful real storage, media, export, and revision operations, not just acceptable 404s."""
    write_source(tmp_path / "sample")
    parameters = {"dataset_id": "sample", "episode_idx": 0}

    def call(path: str, method: str = "GET", expected_status: int = 200, **kwargs: Any) -> Response:
        operation = api_schema[path][method]
        path_parameters = {
            parameter.name: parameters[parameter.name]
            for parameter in operation.iter_parameters()
            if parameter.location == "path" and parameter.name in parameters
        }
        case = api_schema.make_case(
            operation=operation,
            path_parameters=path_parameters,
            meta=_generation_meta(schemathesis.GenerationMode.POSITIVE),
            **kwargs,
        )
        prepare_positive_case(case, client)
        response = case.call_and_validate()
        assert response.status_code == expected_status, response.text
        return response

    base = "/api/datasets/{dataset_id}"
    episode = base + "/episodes/{episode_idx}"
    for path in (base, base + "/episodes", episode, episode + "/cameras", episode + "/annotations"):
        call(path)
    call(episode + "/annotations", "PUT", body=sample_annotation.model_dump(mode="json"))
    call(episode + "/annotations", "PUT", body=sample_annotation.model_dump(mode="json"))
    annotation = call(episode + "/annotations")
    call(episode + "/annotations", "DELETE", headers={"If-Match": annotation.headers["etag"][0]})
    call(base + "/labels/options", "POST", body={"label": "custom"})
    call(episode + "/labels", "PUT", body={"labels": ["custom"]})
    call(episode + "/analysis", "PUT", body={"object": "cube"})
    call(base + "/labels/import-from-analysis", "POST", body={"field": "object"})
    labels = call(base + "/labels/save", "POST")
    assert labels.json()["episodes"]["0"] == ["CUSTOM", "OBJECT: CUBE"]
    parameters.update(camera=CAMERA, frame_idx=0)
    call(episode + "/frames/{frame_idx}", query={"camera": CAMERA})
    video = call(episode + "/video/{camera}")
    assert video.content
    partial_video = call(episode + "/video/{camera}", expected_status=206, headers={"Range": "bytes=0-3"})
    assert partial_video.content == video.content[:4]
    limiter.reset()
    call(episode + "/detect", "POST", body={})
    call(base + "/export/preview", query={"episode_indices": "0"})
    exported = call(base + "/export", "POST", body={"episodeIndices": [0], "outputPath": str(tmp_path / "exported")})
    assert exported.json()["success"] is True
    assert (tmp_path / "exported/meta/info.json").is_file()
    streamed = call(
        base + "/export/stream",
        "POST",
        body={"episodeIndices": [0], "outputPath": str(tmp_path / "streamed")},
    )
    assert "event: complete" in streamed.text
    assert "event: error" not in streamed.text
    assert (tmp_path / "streamed/meta/info.json").is_file()
    if episode + "/judge" in api_schema.raw_schema["paths"]:
        call(episode + "/judge")
        queued = call(episode + "/judge", "POST", expected_status=202, body={})
        _finish_judge_contract(api_schema, client, queued)


def test_ai_routes_document_bad_request_responses(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    for path in (
        "/api/ai/trajectory-analysis",
        "/api/ai/anomaly-detection",
        "/api/ai/cluster",
        "/api/ai/suggest-annotation",
    ):
        assert "400" in schema["paths"][path]["post"]["responses"]


def test_contract_fuzzing_uses_all_defaults(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = MagicMock()

    test_openapi_contract_fuzzing.hypothesis.inner_test(case, client, tmp_path, monkeypatch)

    case.call_and_validate.assert_called_once_with()


@pytest.mark.parametrize(
    ("path", "methods"),
    [
        ("/api/joint-config/defaults", {"GET", "PUT"}),
        ("/api/datasets/test-dataset/joint-config", {"GET", "PUT"}),
        ("/api/datasets/test-dataset/episodes/0/annotations", {"GET", "PUT", "DELETE"}),
    ],
)
def test_unsupported_method_lists_all_route_methods(client: TestClient, path: str, methods: set[str]) -> None:
    response = client.request("TRACE", path)

    assert response.status_code == 405
    assert {method.strip() for method in response.headers["Allow"].split(",")} == methods
