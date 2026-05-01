"""Security tests for path traversal remediation (issue #387)."""

import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.models.detection import DetectionRequest
from src.api.routers.export import ExportRequest
from src.api.validation import (
    SAFE_CAMERA_NAME_PATTERN,
    SAFE_DATASET_ID_PATTERN,
    path_string_param,
    query_csv_ints_param,
    range_header_param,
    validate_path_containment,
)


class TestPathStringParam:
    """Tests for the generic validated path-string dependency factory."""

    @pytest.mark.parametrize(
        "dataset_id",
        [
            "valid-dataset",
            "my_dataset.v1",
            "Dataset123",
        ],
    )
    def test_valid_dataset_ids_accepted(self, dataset_id):
        dependency = path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")
        result = dependency(dataset_id)
        assert result == dataset_id

    @pytest.mark.parametrize(
        "dataset_id",
        [
            "../etc/passwd",
            "..\\windows\\system32",
            "/etc/passwd",
            "C:\\Windows\\",
            "dataset\x00id",
            ".",
            "..",
            "valid/../escape",
            "%2e%2e%2f",
        ],
    )
    def test_traversal_dataset_ids_rejected(self, dataset_id):
        dependency = path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")
        with pytest.raises(HTTPException) as exc_info:
            dependency(dataset_id)
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        "camera",
        [
            "camera_01",
            "front.left",
            "RGB-sensor",
        ],
    )
    def test_valid_camera_names_accepted(self, camera):
        dependency = path_string_param("camera", pattern=SAFE_CAMERA_NAME_PATTERN, label="camera name")
        result = dependency(camera)
        assert result == camera

    @pytest.mark.parametrize(
        "camera",
        [
            "../../../etc/passwd",
            "camera/../../secret",
            "cam\x00era",
        ],
    )
    def test_traversal_camera_names_rejected(self, camera):
        dependency = path_string_param("camera", pattern=SAFE_CAMERA_NAME_PATTERN, label="camera name")
        with pytest.raises(HTTPException) as exc_info:
            dependency(camera)
        assert exc_info.value.status_code == 400


class TestValidatePathContainment:
    """Tests for validate_path_containment utility."""

    def test_contained_path_accepted(self, tmp_path):
        child = tmp_path / "datasets" / "sample"
        child.mkdir(parents=True)
        result = validate_path_containment(child, tmp_path)
        assert result == child.resolve()

    def test_traversal_path_rejected(self, tmp_path):
        escape_path = tmp_path / ".." / ".." / "etc" / "passwd"
        with pytest.raises(HTTPException) as exc_info:
            validate_path_containment(escape_path, tmp_path)
        assert exc_info.value.status_code == 400

    def test_prefix_confusion_rejected(self, tmp_path):
        """Base /tmp/data must not match /tmp/data-backup."""
        base = tmp_path / "data"
        base.mkdir()
        imposter = tmp_path / "data-backup" / "secret"
        imposter.mkdir(parents=True)
        with pytest.raises(HTTPException) as exc_info:
            validate_path_containment(imposter, base)
        assert exc_info.value.status_code == 400

    def test_base_directory_accepted(self, tmp_path):
        """Path equal to base directory should be accepted."""
        result = validate_path_containment(tmp_path, tmp_path)
        assert result == tmp_path.resolve()


class TestRequestModelSanitization:
    """Tests for request-body sanitization that should happen before service logic."""

    def test_detection_request_sanitizes_model_and_coerces_primitives(self):
        request = DetectionRequest.model_validate(
            {
                "frames": ["1", "2"],
                "confidence": "0.4",
                "model": "yolo11n\r\n",
            }
        )
        assert request.frames == [1, 2]
        assert request.confidence == 0.4
        assert request.model == "yolo11n"

    def test_export_request_sanitizes_output_path_and_coerces_primitives(self, tmp_path):
        request = ExportRequest.model_validate(
            {
                "episodeIndices": ["1", "2"],
                "outputPath": f"{tmp_path / 'export-output'}\r\n",
                "applyEdits": "true",
            }
        )
        assert request.episodeIndices == [1, 2]
        assert request.applyEdits is True
        assert request.outputPath == str(tmp_path / "export-output")


class TestQueryCsvIntsParam:
    """Tests for the generic comma-separated integer query dependency factory."""

    def test_required_param_returns_list(self):
        dependency = query_csv_ints_param("episode_indices")
        result = dependency("1, 2,3")
        assert result == [1, 2, 3]

    def test_optional_param_returns_empty_collection_when_missing(self):
        dependency = query_csv_ints_param("removed_frames", required=False, as_set=True)
        result = dependency(None)
        assert result == set()

    def test_optional_param_returns_set(self):
        dependency = query_csv_ints_param("removed_frames", required=False, as_set=True)
        result = dependency("2, 5,2\r\n")
        assert result == {2, 5}

    def test_invalid_param_raises_http_400(self):
        dependency = query_csv_ints_param("episode_indices")
        with pytest.raises(HTTPException) as exc_info:
            dependency("1,two,3")
        assert exc_info.value.status_code == 400


class TestRangeHeaderParam:
    """Tests for the generic HTTP Range header dependency factory."""

    def test_missing_header_returns_empty_range(self):
        dependency = range_header_param()
        assert dependency(None) == (None, None)

    def test_closed_range_parses_offset_and_length(self):
        dependency = range_header_param()
        assert dependency("bytes=0-1023") == (0, 1024)

    def test_open_ended_range_parses_offset_only(self):
        dependency = range_header_param()
        assert dependency("bytes=100-\r\n") == (100, None)

    def test_invalid_reversed_range_raises_http_400(self):
        dependency = range_header_param()
        with pytest.raises(HTTPException) as exc_info:
            dependency("bytes=10-5")
        assert exc_info.value.status_code == 400

    def test_invalid_non_numeric_range_raises_http_400(self):
        dependency = range_header_param()
        with pytest.raises(HTTPException) as exc_info:
            dependency("bytes=start-end")
        assert exc_info.value.status_code == 400


class TestEndpointTraversalRejection:
    """Integration tests: endpoints reject traversal inputs with HTTP 400."""

    @pytest.fixture
    def client(self, tmp_path):
        """Lightweight test client that does not require a real dataset directory."""
        os.environ["DATA_DIR"] = str(tmp_path)

        import src.api.services.dataset_service as ds_mod

        ds_mod._dataset_service = None

        from src.api.main import app

        with TestClient(app) as c:
            yield c

        ds_mod._dataset_service = None

    # HTTP clients and ASGI routers normalize "../" in URL paths before routing,
    # so traversal segments may produce 404 (no matching route) rather than 400
    # (validation rejection). Both outcomes block the traversal attempt.

    def test_export_traversal_dataset_id(self, client):
        resp = client.post("/api/datasets/../etc/passwd/export")
        assert resp.status_code in (400, 404)

    def test_export_stream_traversal_dataset_id(self, client):
        resp = client.post("/api/datasets/../etc/passwd/export/stream")
        assert resp.status_code in (400, 404)

    def test_datasets_traversal_dataset_id(self, client):
        resp = client.get("/api/datasets/../etc/passwd")
        assert resp.status_code in (400, 404)

    def test_labels_traversal_dataset_id(self, client):
        resp = client.get("/api/datasets/../etc/passwd/episodes/0/labels")
        assert resp.status_code in (400, 404)

    def test_detection_traversal_dataset_id(self, client):
        resp = client.get("/api/datasets/../etc/passwd/episodes/0/detections")
        assert resp.status_code in (400, 404)

    def test_datasets_traversal_camera_name(self, client):
        resp = client.get(
            "/api/datasets/valid_dataset/episodes/0/frames",
            params={"camera": "../../../etc/passwd"},
        )
        assert resp.status_code in (400, 404)
