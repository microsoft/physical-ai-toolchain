# cspell:ignore rtes codepoint nple
"""Behavioral regressions for published request and response contracts."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from jsonschema_rs import Draft202012Validator
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from src.api.main import api_http_exception_handler
from src.api.models.episode_edits import SaveEpisodeEditsRequest
from src.api.validation import path_int_param, query_int_param, range_header_param

from .lerobot_sources import write_source


@pytest.mark.parametrize(
    ("value", "valid", "expected"),
    [
        (None, True, (None, None)),
        ("bytes=2-4", True, (2, 3)),
        ("bytes= +02 - +04 ", True, (2, 3)),
        ("bytes=0_2-0_4", True, (2, 3)),
        ("by\rtes=0\r2-0\n4", True, (2, 3)),
        (" BYTES =2-4", True, (2, 3)),
        ("bytes=٢-٤", False, None),
        ("bytes=0--0", True, (0, 1)),
        ("bytes=0-", True, (0, None)),
        ("bytes=0-1\njunk", False, None),
        ("bytes=0-\x1c1", False, None),
        ("bytes=0- ", False, None),
        ("", False, None),
        ("null", False, None),
        ("items=0-3", False, None),
        ("bytes=-2", False, None),
        ("bytes=0-1,3-4", False, None),
        ("bytes=0-1.0", False, None),
    ],
)
def test_video_range_syntax_matches_schema(
    client: TestClient,
    value: str | None,
    valid: bool,
    expected: tuple[int | None, int | None] | None,
) -> None:
    document = client.get("/openapi.json").json()
    operation = document["paths"]["/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"]["get"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "Range")
    validator = Draft202012Validator(parameter["schema"])
    if value is None:
        assert parameter["required"] is False
        assert not validator.is_valid(None)
    else:
        assert validator.is_valid(value) == valid
    dependency = range_header_param()
    if valid:
        assert dependency(header_value=value) == expected
    else:
        with pytest.raises(HTTPException) as error:
            dependency(header_value=value)
        assert error.value.status_code == 400
        error_schema = operation["responses"]["400"]["content"]["application/json"]["schema"]
        Draft202012Validator({**error_schema, "components": document["components"]}).validate(
            {"detail": error.value.detail}
        )


@pytest.mark.parametrize("operation", ["read-stale", "write-stale", "invalid-context"])
def test_saved_edit_error_responses_match_schema(client: TestClient, tmp_path: Path, operation: str) -> None:
    source = write_source(tmp_path / "sample")
    path = "/api/datasets/sample/episodes/0/edits"
    state = client.get(path).json()
    body = {
        "source_id": state["source_id"],
        "source_revision": state["source_revision"],
        "operations": {"datasetId": "sample", "episodeIndex": 0, "removedFrames": []},
    }
    saved = client.put(path, json=body, headers={"If-None-Match": "*"})
    assert saved.status_code == 200
    if operation == "invalid-context":
        body["operations"]["removedFrames"] = [999]
        expected_status = 422
    else:
        info = source / "meta/info.json"
        info.write_text(info.read_text() + "\n")
        expected_status = 409
    method = "get" if operation == "read-stale" else "put"
    response = (
        client.get(path)
        if method == "get"
        else client.put(path, json=body, headers={"If-Match": saved.headers["ETag"]})
    )
    assert response.status_code == expected_status
    document = client.get("/openapi.json").json()
    responses = document["paths"]["/api/datasets/{dataset_id}/episodes/{episode_idx}/edits"][method]["responses"]
    assert str(expected_status) in responses
    response_schema = responses[str(expected_status)]["content"]["application/json"]["schema"]
    Draft202012Validator({**response_schema, "components": document["components"]}).validate(response.json())


@pytest.mark.parametrize("dimension", [1, 1.0, 0, -1, "1", True, 0.5, None])
def test_saved_edit_dimensions_match_schema(dimension: object) -> None:
    body = {
        "source_id": "source",
        "source_revision": "revision",
        "operations": {
            "datasetId": "sample",
            "episodeIndex": 0.0,
            "globalTransform": {"resize": {"width": dimension, "height": 1}},
        },
    }
    accepted = type(dimension) in (int, float) and dimension == 1
    assert Draft202012Validator(SaveEpisodeEditsRequest.model_json_schema()).is_valid(body) == accepted
    if accepted:
        request = SaveEpisodeEditsRequest.model_validate(body)
        assert request.operations.globalTransform.resize.width == 1
        assert request.operations.episodeIndex == 0
    else:
        with pytest.raises(ValidationError):
            SaveEpisodeEditsRequest.model_validate(body)


@pytest.mark.parametrize(
    "whitespace",
    [
        chr(codepoint)
        for codepoint in [
            *range(9, 14),
            *range(28, 33),
            133,
            160,
            5760,
            *range(8192, 8203),
            8232,
            8233,
            8239,
            8287,
            12288,
        ]
    ],
)
def test_nonempty_string_schemas_match_unicode_trimming(client: TestClient, whitespace: str) -> None:
    schema = client.get("/openapi.json").json()
    label_schema = schema["components"]["schemas"]["AddLabelOption"]["properties"]["label"]
    operation = schema["paths"]["/api/datasets/{dataset_id}/labels/options/{label}"]["delete"]
    path_schema = next(parameter["schema"] for parameter in operation["parameters"] if parameter["name"] == "label")
    for value_schema in (label_schema, path_schema):
        validator = Draft202012Validator(value_schema)
        assert not validator.is_valid(whitespace), repr(whitespace)
        assert validator.is_valid(f"{whitespace}custom{whitespace}"), repr(whitespace)
    response = client.post(
        "/api/datasets/sample/labels/options", json={"label": whitespace}, headers={"If-None-Match": "*"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("value", ["0.0", "+0.0", "1.0", "1e0", "true"])
def test_integer_parameter_strings_reject_non_integer_notation(client: TestClient, value: str) -> None:
    response = client.delete(f"/api/datasets/sample/episodes/{quote(value, safe='')}/detections")
    assert response.status_code == 422
    response = client.get("/api/datasets/sample/episodes", params={"offset": value})
    assert response.status_code == 422


def test_integer_parameter_dependencies_preserve_schema_bounds_and_omission() -> None:
    app = FastAPI()

    @app.get("/items/{index}")
    def read(
        index: int = Depends(path_int_param("index", ge=0, le=10)),
        limit: int | None = Depends(query_int_param("limit", default=None, ge=1, le=10)),
    ) -> dict[str, int | None]:
        return {"index": index, "limit": limit}

    with TestClient(app) as client:
        assert client.get("/items/0").json() == {"index": 0, "limit": None}
        assert client.get("/items/+2", params={"limit": "+3"}).json() == {"index": 2, "limit": 3}
        for path, params in [
            ("/items/-1", {}),
            ("/items/11", {}),
            ("/items/0", {"limit": "0"}),
            ("/items/0", {"limit": "11"}),
            ("/items/0", {"limit": "1.0"}),
        ]:
            assert client.get(path, params=params).status_code == 422
    parameters = app.openapi()["paths"]["/items/{index}"]["get"]["parameters"]
    assert parameters[0]["schema"]["type"] == "integer"
    assert parameters[0]["schema"]["minimum"] == 0
    assert parameters[0]["schema"]["maximum"] == 10


@pytest.mark.parametrize("field", ["hesitation_count", "correction_count", "motion_score"])
@pytest.mark.parametrize("value", [True, "1", 1.5, 1, 1.0, None])
def test_analysis_integer_fields_match_schema(client: TestClient, field: str, value: object) -> None:
    response = client.put(
        "/api/datasets/sample/episodes/0/analysis", json={field: value}, headers={"If-None-Match": "*"}
    )
    accepted = value is None or (type(value) in (int, float) and value == 1)
    assert response.status_code == (200 if accepted else 422), response.text


@pytest.mark.parametrize("field", ["duration_s", "smoothness", "normalized_smoothness", "efficiency", "jitter"])
@pytest.mark.parametrize("value", [True, "1", 1, 1.5, None])
def test_analysis_numeric_fields_match_schema(client: TestClient, field: str, value: object) -> None:
    response = client.put(
        "/api/datasets/sample/episodes/0/analysis", json={field: value}, headers={"If-None-Match": "*"}
    )
    accepted = value is None or type(value) in (int, float)
    assert response.status_code == (200 if accepted else 422), response.text


@pytest.mark.parametrize("field", ["grasp_success", "place_success"])
@pytest.mark.parametrize("value", [0, "false", False, True, None])
def test_analysis_boolean_fields_match_schema(client: TestClient, field: str, value: object) -> None:
    response = client.put(
        "/api/datasets/sample/episodes/0/analysis", json={field: value}, headers={"If-None-Match": "*"}
    )
    accepted = value is None or type(value) is bool
    assert response.status_code == (200 if accepted else 422), response.text


def test_allow_header_combines_separately_included_routers() -> None:
    app = FastAPI()
    app.add_exception_handler(HTTPException, api_http_exception_handler)
    read_router = APIRouter()
    write_router = APIRouter()

    @read_router.get("/{item}")
    def read(item: str) -> dict[str, str]:
        return {"item": item}

    @write_router.put("/{item}")
    def write(item: str) -> dict[str, str]:
        return {"item": item}

    app.include_router(read_router, prefix="/items")
    app.include_router(write_router, prefix="/items")
    with TestClient(app) as client:
        response = client.request("PATCH", "/items/example")
        assert response.status_code == 405
        assert set(response.headers["Allow"].split(", ")) == {"GET", "PUT"}
        assert client.get("/items/example").json() == {"item": "example"}
        assert client.put("/items/example").status_code == 200
        assert client.get("/missing/example").status_code == 404


@pytest.mark.parametrize("index", [False, True, "1", 1.5, 0, 1, 1.0])
def test_joint_index_runtime_matches_published_schema(client: TestClient, index: object) -> None:
    document = client.get("/openapi.json").json()
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/JointConfigUpdate", "components": document["components"]}
    )
    body = {"groups": [{"id": "arm", "label": "Arm", "indices": [index]}]}
    response = client.put("/api/joint-config/defaults", json=body, headers={"If-None-Match": "*"})
    assert (response.status_code == 200) == validator.is_valid(body)


@pytest.mark.parametrize(
    ("path", "method", "statuses"),
    [
        ("/api/datasets/{dataset_id}", "get", {400, 404}),
        ("/api/datasets/{dataset_id}/episodes/{episode_idx}/annotations", "put", {400, 404, 412, 428}),
        ("/api/datasets/{dataset_id}/labels/save", "post", {400, 412, 428}),
        ("/api/auth/media-session", "post", {400, 401, 403}),
    ],
)
def test_errors_have_response_schemas(client: TestClient, path: str, method: str, statuses: set[int]) -> None:
    responses = client.get("/openapi.json").json()["paths"][path][method]["responses"]
    for status in statuses:
        assert responses[str(status)]["content"]["application/json"]["schema"]


def test_missing_detection_dataset_is_not_an_empty_episode(client: TestClient) -> None:
    response = client.post("/api/datasets/missing-dataset/episodes/0/detect", json={})
    assert response.status_code == 404


def test_parameter_constraints_are_published(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    parameters = paths["/api/datasets/{dataset_id}/export/preview"]["get"]["parameters"]
    for name in ("dataset_id", "episode_indices"):
        schema = next(parameter["schema"] for parameter in parameters if parameter["name"] == name)
        assert "pattern" in schema
        assert schema["type"] == "string"


def test_video_contract_documents_supported_containers(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    responses = paths["/api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"]["get"]["responses"]
    for status in ("200", "206"):
        assert set(responses[status]["content"]) == {"video/mp4", "video/webm", "video/x-msvideo", "video/quicktime"}


@pytest.mark.parametrize("value", [True, False, "1"])
def test_cluster_rejects_non_numeric_coordinates(client: TestClient, value: object) -> None:
    response = client.post("/api/ai/cluster", json={"trajectories": [[[value]], [[0.0]]]})
    assert response.status_code == 422


@pytest.mark.parametrize("magnitude", [1e200, 8e307])
@pytest.mark.parametrize("step", [1.0, 1e-300])
def test_trajectory_metrics_remain_finite_for_large_coordinates(
    client: TestClient, magnitude: float, step: float
) -> None:
    response = client.post(
        "/api/ai/trajectory-analysis",
        json={
            "positions": [[magnitude * (index % 3 - 1)] for index in range(12)],
            "timestamps": [step * index for index in range(12)],
        },
    )
    assert response.status_code == 200
    metrics = response.json()
    for name in ("smoothness", "normalized_smoothness", "efficiency", "jitter"):
        assert metrics[name] is not None
        assert 0 <= metrics[name] <= 1
    assert metrics["efficiency"] == pytest.approx(2 / 14)


def test_detection_schema_exposes_item_constraints(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/DetectionRequest", "components": document["components"]}
    )
    assert not validator.is_valid({"frames": [-1]})
    assert not validator.is_valid({"labels": ["x" * 101]})


@pytest.mark.parametrize("dataset_id", ["\ri", "sam\r\nple", "a" * 255 + "\r\n", "\r\n", ".", "../sample"])
def test_dataset_parameter_schema_matches_sanitized_input(client: TestClient, dataset_id: str) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/datasets/{dataset_id}/labels/options"]["get"]
    schema = next(item["schema"] for item in operation["parameters"] if item["name"] == "dataset_id")
    response = client.get(f"/api/datasets/{quote(dataset_id, safe='')}/labels/options")
    assert Draft202012Validator(schema).is_valid(dataset_id) == (response.status_code == 200)


@pytest.mark.parametrize("offset", [0.0, 1e200, 8e307])
@pytest.mark.parametrize(("end", "efficiency"), [(0.0, 0.0), (0.5, 1 / 3), (2.0, 1.0)])
def test_trajectory_efficiency_preserves_motion_with_constant_offsets(
    client: TestClient, offset: float, end: float, efficiency: float
) -> None:
    response = client.post(
        "/api/ai/trajectory-analysis",
        json={"positions": [[offset, 0.0], [offset, 1.0], [offset, end]], "timestamps": [0.0, 1.0, 2.0]},
    )
    assert response.status_code == 200
    assert response.json()["efficiency"] == pytest.approx(efficiency)


@pytest.mark.parametrize("parameter", ["episode_indices", "removed_frames"])
@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("0", True),
        ("0,", True),
        (",0", True),
        ("0,,0", True),
        ("0\r0", True),
        (" \t,+0, , -1,\r\n", True),
        ("", False),
        (", ,", False),
        ("0,x", False),
        ("0 0", False),
        ("0_0", False),
        ("\u0660", False),
    ],
)
def test_csv_contract_matches_existing_resource_requests(
    client: TestClient, tmp_path: Path, parameter: str, value: str, accepted: bool
) -> None:
    write_source(tmp_path / "sample")
    operation = client.get("/openapi.json").json()["paths"]["/api/datasets/{dataset_id}/export/preview"]["get"]
    schema = next(item["schema"] for item in operation["parameters"] if item["name"] == parameter)
    response = client.get("/api/datasets/sample/export/preview", params={"episode_indices": "0", parameter: value})
    assert response.status_code == (200 if accepted else 400)
    assert Draft202012Validator(schema).is_valid(value) == accepted
