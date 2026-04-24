"""Unit tests for the export router (`src/api/routers/export.py`).

Exercises synchronous export, SSE-streaming export, and the preview
endpoint via the FastAPI test client with the dataset service and
HDF5 exporter mocked out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from src.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def dataset_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a base dir with a dataset folder and an output folder beneath it."""
    base = tmp_path / "datasets"
    base.mkdir()
    dataset_dir = base / "ds-1"
    dataset_dir.mkdir()
    output_dir = base / "out"
    output_dir.mkdir()
    return base, dataset_dir, output_dir


@pytest.fixture
def mock_service(dataset_layout: tuple[Path, Path, Path]):
    """Build a mock DatasetService with sane defaults for the export router."""
    base, dataset_dir, _output = dataset_layout
    svc = MagicMock()
    svc.base_path = str(base)
    svc.get_dataset = AsyncMock(return_value=MagicMock(name="dataset"))
    svc._get_dataset_path = MagicMock(return_value=dataset_dir)
    svc.get_episode = AsyncMock()
    return svc


@pytest.fixture
def override_service(mock_service):
    """Install dependency override for `get_dataset_service` and clean up after."""
    from src.api.main import app
    from src.api.services.dataset_service import get_dataset_service

    app.dependency_overrides[get_dataset_service] = lambda: mock_service
    try:
        yield mock_service
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)


def _make_export_result(success: bool = True, error: str | None = None) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.output_files = ["episode_0.hdf5"]
    result.error = error
    result.stats = {"episodes": 1, "frames_written": 10}
    return result


def _patch_exporter(monkeypatch: pytest.MonkeyPatch, exporter_mock: MagicMock) -> None:
    monkeypatch.setattr("src.api.routers.export.HDF5Exporter", exporter_mock)


# ---------------------------------------------------------------------------
# POST /api/datasets/{dataset_id}/export
# ---------------------------------------------------------------------------


class TestExportEpisodes:
    def test_dataset_not_found_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.post(
            "/api/datasets/missing/export",
            json={"episodeIndices": [0], "outputPath": "/tmp/x", "applyEdits": False},
        )
        assert resp.status_code == 404

    def test_invalid_dataset_path_returns_400(self, client: TestClient, override_service) -> None:
        override_service._get_dataset_path = MagicMock(side_effect=ValueError("no path"))
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": "/tmp/x", "applyEdits": False},
        )
        assert resp.status_code == 400
        assert "valid path" in resp.json()["detail"]

    def test_dataset_path_traversal_returns_400(
        self, client: TestClient, override_service, tmp_path: Path
    ) -> None:
        outside = tmp_path / "escape"
        outside.mkdir()
        override_service._get_dataset_path = MagicMock(return_value=outside)
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(outside), "applyEdits": False},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_dataset_path_missing_returns_400(
        self, client: TestClient, override_service, dataset_layout
    ) -> None:
        base, dataset_dir, _ = dataset_layout
        # Resolves under base but does not exist on disk.
        override_service._get_dataset_path = MagicMock(return_value=base / "nope")
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(dataset_dir), "applyEdits": False},
        )
        assert resp.status_code == 400
        assert "local path" in resp.json()["detail"]

    def test_output_path_traversal_returns_400(
        self, client: TestClient, override_service, dataset_layout, tmp_path: Path
    ) -> None:
        _, _dataset, _ = dataset_layout
        outside = tmp_path / "outside-out"
        outside.mkdir()
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(outside), "applyEdits": False},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_output_mkdir_failure_returns_400(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        base, _dataset, output_dir = dataset_layout

        original_mkdir = Path.mkdir

        def boom(self: Path, *args: Any, **kwargs: Any) -> None:
            if str(self) == str(output_dir):
                raise OSError("permission denied")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", boom)
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        )
        assert resp.status_code == 400
        assert "Invalid output path" in resp.json()["detail"]

    def test_success_with_full_edits(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _dataset, output_dir = dataset_layout
        exporter_instance = MagicMock()
        exporter_instance.export_episodes.return_value = _make_export_result()
        exporter_cls = MagicMock(return_value=exporter_instance)
        _patch_exporter(monkeypatch, exporter_cls)

        body = {
            "episodeIndices": [0],
            "outputPath": str(output_dir),
            "applyEdits": True,
            "edits": {
                "0": {
                    "episodeIndex": 0,
                    "globalTransform": {"crop": {"x": 0, "y": 0, "width": 10, "height": 10}},
                    "cameraTransforms": {
                        "cam0": {"resize": {"width": 64, "height": 64}},
                    },
                    "removedFrames": [3, 4],
                    "insertedFrames": [{"afterFrameIndex": 1, "interpolationFactor": 0.5}],
                    "subtasks": [
                        {
                            "id": "s1",
                            "label": "grasp",
                            "frameRange": [0, 9],
                            "color": "#ff0000",
                            "source": "manual",
                        }
                    ],
                }
            },
        }
        resp = client.post("/api/datasets/ds-1/export", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["outputFiles"] == ["episode_0.hdf5"]
        assert data["stats"]["episodes"] == 1
        # Edits should have been parsed into the exporter call.
        kwargs = exporter_instance.export_episodes.call_args.kwargs
        assert kwargs["episode_indices"] == [0]
        assert 0 in kwargs["edits_map"]

    def test_import_error_returns_501(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _dataset, output_dir = dataset_layout
        exporter_cls = MagicMock(side_effect=ImportError("h5py missing"))
        _patch_exporter(monkeypatch, exporter_cls)
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        )
        assert resp.status_code == 501
        assert "h5py missing" in resp.json()["detail"]

    def test_export_error_returns_500(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.api.services.hdf5_exporter import HDF5ExportError

        _, _dataset, output_dir = dataset_layout
        exporter_instance = MagicMock()
        exporter_instance.export_episodes.side_effect = HDF5ExportError("write failed")
        exporter_cls = MagicMock(return_value=exporter_instance)
        _patch_exporter(monkeypatch, exporter_cls)
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        )
        assert resp.status_code == 500
        assert "write failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/datasets/{dataset_id}/export/stream
# ---------------------------------------------------------------------------


class TestExportEpisodesStream:
    def test_dataset_not_found_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.post(
            "/api/datasets/missing/export/stream",
            json={"episodeIndices": [0], "outputPath": "/tmp/x", "applyEdits": False},
        )
        assert resp.status_code == 404

    def test_invalid_dataset_path_returns_400(self, client: TestClient, override_service) -> None:
        override_service._get_dataset_path = MagicMock(side_effect=ValueError("no path"))
        resp = client.post(
            "/api/datasets/ds-1/export/stream",
            json={"episodeIndices": [0], "outputPath": "/tmp/x", "applyEdits": False},
        )
        assert resp.status_code == 400

    def test_output_path_traversal_returns_400(
        self, client: TestClient, override_service, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside-stream-out"
        outside.mkdir()
        resp = client.post(
            "/api/datasets/ds-1/export/stream",
            json={"episodeIndices": [0], "outputPath": str(outside), "applyEdits": False},
        )
        assert resp.status_code == 400

    def test_stream_success_emits_progress_and_complete(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.api.services.hdf5_exporter import ExportProgress

        _, _dataset, output_dir = dataset_layout

        def fake_export(*, episode_indices, edits_map, progress_callback):
            progress_callback(
                ExportProgress(
                    current_episode=0,
                    total_episodes=1,
                    current_frame=5,
                    total_frames=10,
                    percentage=50.0,
                    status="working",
                )
            )
            return _make_export_result()

        exporter_instance = MagicMock()
        exporter_instance.export_episodes.side_effect = fake_export
        exporter_cls = MagicMock(return_value=exporter_instance)
        _patch_exporter(monkeypatch, exporter_cls)

        with client.stream(
            "POST",
            "/api/datasets/ds-1/export/stream",
            json={
                "episodeIndices": [0],
                "outputPath": str(output_dir),
                "applyEdits": True,
                "edits": {
                    "0": {
                        "episodeIndex": 0,
                        "removedFrames": [1],
                    }
                },
            },
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

        assert "event: progress" in body
        assert "event: complete" in body
        # Complete payload echoes export result.
        complete_blob = body.split("event: complete")[1]
        # Strip the leading "\ndata: " prefix to get the JSON payload.
        json_blob = complete_blob.split("data: ", 1)[1].split("\n\n", 1)[0]
        complete_payload = json.loads(json_blob)
        assert complete_payload["success"] is True
        assert complete_payload["outputFiles"] == ["episode_0.hdf5"]

    def test_stream_import_error_emits_error_event(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _dataset, output_dir = dataset_layout
        exporter_cls = MagicMock(side_effect=ImportError("missing dep"))
        _patch_exporter(monkeypatch, exporter_cls)

        with client.stream(
            "POST",
            "/api/datasets/ds-1/export/stream",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

        assert "event: error" in body
        assert "Export not available" in body

    def test_stream_generic_exception_emits_error_event(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _dataset, output_dir = dataset_layout
        exporter_instance = MagicMock()
        exporter_instance.export_episodes.side_effect = RuntimeError("disk full")
        exporter_cls = MagicMock(return_value=exporter_instance)
        _patch_exporter(monkeypatch, exporter_cls)

        with client.stream(
            "POST",
            "/api/datasets/ds-1/export/stream",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

        assert "event: error" in body
        assert "disk full" in body


# ---------------------------------------------------------------------------
# GET /api/datasets/{dataset_id}/export/preview
# ---------------------------------------------------------------------------


class TestPreviewExport:
    def test_dataset_not_found_returns_404(self, client: TestClient, override_service) -> None:
        override_service.get_dataset = AsyncMock(return_value=None)
        resp = client.get(
            "/api/datasets/missing/export/preview",
            params={"episode_indices": "0"},
        )
        assert resp.status_code == 404

    def test_preview_aggregates_frames_and_removals(
        self, client: TestClient, override_service
    ) -> None:
        ep0 = MagicMock()
        ep0.meta.length = 10
        ep1 = MagicMock()
        ep1.meta.length = 5

        async def get_episode(_dataset_id: str, idx: int):
            return {0: ep0, 1: ep1}.get(idx)

        override_service.get_episode = AsyncMock(side_effect=get_episode)

        resp = client.get(
            "/api/datasets/ds-1/export/preview",
            params={"episode_indices": "0,1", "removed_frames": "1,2,20"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["episodeCount"] == 2
        assert data["originalFrames"] == 15
        # Frames 1 and 2 removed from each episode (frame 20 exceeds both lengths).
        assert data["removedFrames"] == 4
        assert data["outputFrames"] == 11
        assert data["estimatedSizeMb"] == pytest.approx(11 * 0.1)

    def test_preview_skips_missing_episode(
        self, client: TestClient, override_service
    ) -> None:
        override_service.get_episode = AsyncMock(return_value=None)
        resp = client.get(
            "/api/datasets/ds-1/export/preview",
            params={"episode_indices": "7"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["episodeCount"] == 1
        assert data["originalFrames"] == 0
        assert data["outputFrames"] == 0
