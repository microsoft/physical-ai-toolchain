"""Unit tests for the export router (`src/api/routers/export.py`).

Exercises synchronous export, SSE-streaming export, and the preview
endpoint via the FastAPI test client with the dataset service and
HDF5 exporter mocked out.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.services.lerobot_exporter import LOCK_FILE, PROVENANCE_FILE
from src.api.services.lerobot_language import LanguageInstruction

from .lerobot_sources import hold_lock, stop_export, write_source


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
    svc.dataset_is_lerobot = MagicMock(return_value=False)
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


@pytest.fixture
def saved_annotations():
    """Install an annotation service holding two saved instructions for episode 0, the later one listed first."""
    from src.api.main import app
    from src.api.models.annotations import EpisodeAnnotationFile, LanguageInstructionAnnotation
    from src.api.services.annotation_service import get_annotation_service

    from .test_annotation_service import _build_annotation

    older = _build_annotation("annotator-a").model_copy(
        update={
            "timestamp": datetime(2026, 9, 30, tzinfo=UTC),
            "language_instruction": LanguageInstructionAnnotation(instruction="Older", source="human"),
        }
    )
    newer = _build_annotation("annotator-b").model_copy(
        update={
            "timestamp": datetime(2026, 10, 1, 12, tzinfo=UTC),
            "language_instruction": LanguageInstructionAnnotation(
                instruction="Pick up the gear",
                source="human",
                paraphrases=["Grab the gear"],
                subtask_instructions=["Approach"],
            ),
        }
    )
    files = {0: EpisodeAnnotationFile(episode_index=0, dataset_id="ds-1", annotations=[newer, older])}
    service = MagicMock()
    service.get_annotation = AsyncMock(side_effect=lambda _dataset_id, index: files.get(index))
    app.dependency_overrides[get_annotation_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_annotation_service, None)


def _post_export(client: TestClient, endpoint: str, body: dict[str, Any]) -> None:
    """Run an export through the synchronous or the streaming endpoint and require it to complete."""
    if endpoint == "export":
        resp = client.post("/api/datasets/ds-1/export", json=body)
        assert resp.status_code == 200, resp.text
        return
    with client.stream("POST", "/api/datasets/ds-1/export/stream", json=body) as resp:
        assert resp.status_code == 200
        assert "event: complete" in "".join(resp.iter_text())


def _export_through(client: TestClient, endpoint: str, output: Path) -> dict[str, Any]:
    """Export source episode 1 through an endpoint with the real exporter and return the public result."""
    body = {"episodeIndices": [1], "outputPath": str(output), "includeLanguageInstructions": False}
    if endpoint == "export":
        resp = client.post("/api/datasets/ds-1/export", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()
    with client.stream("POST", "/api/datasets/ds-1/export/stream", json=body) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    return json.loads(text.split("event: complete\ndata: ", 1)[1].split("\n\n", 1)[0])


def _contents(directory: Path) -> dict[str, str]:
    """Map every file under ``directory``, hidden ones included, to a digest of its bytes."""
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _make_export_result(success: bool = True, error: str | None = None) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.output_files = ["episode_0.hdf5"]
    result.error = error
    result.stats = {"total_episodes": 1, "total_frames": 10, "removed_frames": 0, "duration_ms": 25}
    return result


def _patch_exporter(monkeypatch: pytest.MonkeyPatch, exporter_mock: MagicMock) -> None:
    monkeypatch.setattr("src.api.routers.export.HDF5Exporter", exporter_mock)


def _patch_lerobot_exporter(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    instance = MagicMock()
    instance.export_episodes.return_value = _make_export_result()
    cls = MagicMock(return_value=instance)
    monkeypatch.setattr("src.api.routers.export.LeRobotExporter", cls)
    hdf5 = MagicMock(side_effect=AssertionError("HDF5Exporter must not handle LeRobot sources"))
    monkeypatch.setattr("src.api.routers.export.HDF5Exporter", hdf5)
    return cls, instance


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

    def test_dataset_path_traversal_returns_400(self, client: TestClient, override_service, tmp_path: Path) -> None:
        outside = tmp_path / "escape"
        outside.mkdir()
        override_service._get_dataset_path = MagicMock(return_value=outside)
        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(outside), "applyEdits": False},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_dataset_path_missing_returns_400(self, client: TestClient, override_service, dataset_layout) -> None:
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
        _base, _dataset, output_dir = dataset_layout

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
        assert data["stats"]["total_episodes"] == 1
        assert data["error"] is None
        # Edits should have been parsed into the exporter call.
        kwargs = exporter_instance.export_episodes.call_args.kwargs
        assert kwargs["episode_indices"] == [0]
        assert 0 in kwargs["edits_map"]

    def test_an_empty_subtask_list_reaches_the_exporter_as_empty(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _dataset, output_dir = dataset_layout
        exporter_instance = MagicMock()
        exporter_instance.export_episodes.return_value = _make_export_result()
        _patch_exporter(monkeypatch, MagicMock(return_value=exporter_instance))
        edits = {"0": {"episodeIndex": 0, "subtasks": []}, "1": {"episodeIndex": 1, "removedFrames": [2]}}
        body = {"episodeIndices": [0, 1], "outputPath": str(output_dir), "applyEdits": True, "edits": edits}

        assert client.post("/api/datasets/ds-1/export", json=body).status_code == 200

        edits_map = exporter_instance.export_episodes.call_args.kwargs["edits_map"]
        assert (edits_map[0].subtasks, edits_map[1].subtasks) == ([], None)

    def test_trajectory_adjustments_reach_the_exporter(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.api.services.episode_edits import TrajectoryAdjustment

        _, _dataset, output_dir = dataset_layout
        exporter_instance = MagicMock()
        exporter_instance.export_episodes.return_value = _make_export_result()
        _patch_exporter(monkeypatch, MagicMock(return_value=exporter_instance))
        adjustment = {"frameIndex": 2, "channelDeltas": {"0": 0.5}, "channelValues": {"6": 0.7}}
        body = {
            "episodeIndices": [0],
            "outputPath": str(output_dir),
            "applyEdits": True,
            "edits": {"0": {"episodeIndex": 0, "trajectoryAdjustments": [adjustment]}},
        }

        resp = client.post("/api/datasets/ds-1/export", json=body)

        assert resp.status_code == 200, resp.text
        edits = exporter_instance.export_episodes.call_args.kwargs["edits_map"][0]
        assert edits.trajectory_adjustments == [
            TrajectoryAdjustment(frame_index=2, channel_deltas={0: 0.5}, channel_values={6: 0.7})
        ]

    @pytest.mark.parametrize(
        "adjustment",
        [
            '{"frameIndex": -1, "channelDeltas": {"0": 0.5}}',
            '{"frameIndex": 2, "channelDeltas": {"-1": 0.5}}',
            '{"frameIndex": 2, "channelValues": {"0": NaN}}',
            '{"frameIndex": 2, "channelDeltas": {"0": Infinity}}',
        ],
    )
    def test_invalid_trajectory_adjustments_are_rejected_before_export(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
        adjustment: str,
    ) -> None:
        _, _dataset, output_dir = dataset_layout
        exporter_cls = MagicMock()
        _patch_exporter(monkeypatch, exporter_cls)
        body = (
            f'{{"episodeIndices": [0], "outputPath": {json.dumps(str(output_dir))}, "applyEdits": true, '
            f'"edits": {{"0": {{"episodeIndex": 0, "trajectoryAdjustments": [{adjustment}]}}}}}}'
        )

        resp = client.post("/api/datasets/ds-1/export", content=body, headers={"content-type": "application/json"})

        assert resp.status_code == 422, resp.text
        exporter_cls.assert_not_called()

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

    def test_the_exporter_runs_off_the_event_loop(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.api.routers import export as export_router

        _, _dataset, output_dir = dataset_layout
        threads: dict[str, int] = {}
        export_options = export_router._export_options

        async def recording_options(*args: Any, **kwargs: Any) -> dict[str, Any]:
            threads["handler"] = threading.get_ident()
            return await export_options(*args, **kwargs)

        def recording_export(**_options: Any) -> MagicMock:
            threads["exporter"] = threading.get_ident()
            return _make_export_result()

        monkeypatch.setattr(export_router, "_export_options", recording_options)
        instance = MagicMock()
        instance.export_episodes.side_effect = recording_export
        _patch_exporter(monkeypatch, MagicMock(return_value=instance))

        resp = client.post(
            "/api/datasets/ds-1/export",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        )

        assert resp.status_code == 200, resp.text
        assert threads["exporter"] != threads["handler"]


# ---------------------------------------------------------------------------
# POST /api/datasets/{dataset_id}/export/stream
# ---------------------------------------------------------------------------


class TestLeRobotExports:
    def test_lerobot_sources_use_the_lerobot_exporter_without_creating_the_output(
        self, client: TestClient, override_service, dataset_layout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base, dataset_dir, _output = dataset_layout
        override_service.dataset_is_lerobot.return_value = True
        cls, instance = _patch_lerobot_exporter(monkeypatch)
        output = base / "ds-1-edited"

        resp = client.post("/api/datasets/ds-1/export", json={"episodeIndices": [0], "outputPath": str(output)})

        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        cls.assert_called_once_with(dataset_dir, output, dataset_id="ds-1")
        assert instance.export_episodes.call_args.kwargs["episode_indices"] == [0]
        assert not output.exists()

    def test_lerobot_export_refuses_a_non_empty_output(
        self, client: TestClient, override_service, dataset_layout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _base, _dataset, output_dir = dataset_layout
        override_service.dataset_is_lerobot.return_value = True
        cls, _instance = _patch_lerobot_exporter(monkeypatch)
        (output_dir / "existing.txt").write_text("keep")

        resp = client.post("/api/datasets/ds-1/export", json={"episodeIndices": [0], "outputPath": str(output_dir)})

        assert resp.status_code == 400
        assert "new or empty" in resp.json()["detail"]
        cls.assert_not_called()

    @pytest.mark.parametrize("relation", ["inside", "containing"])
    def test_lerobot_export_refuses_paths_nested_with_the_source(
        self, client: TestClient, override_service, dataset_layout, monkeypatch: pytest.MonkeyPatch, relation: str
    ) -> None:
        base, _dataset, _output = dataset_layout
        nested = base / "group" / "ds-1"
        nested.mkdir(parents=True)
        override_service._get_dataset_path.return_value = nested
        override_service.dataset_is_lerobot.return_value = True
        cls, _instance = _patch_lerobot_exporter(monkeypatch)
        output = nested / "derived" if relation == "inside" else base / "group"
        before = sorted(path.relative_to(base).as_posix() for path in base.rglob("*"))

        resp = client.post("/api/datasets/ds-1/export", json={"episodeIndices": [0], "outputPath": str(output)})

        assert resp.status_code == 400
        assert "outside the source dataset" in resp.json()["detail"]
        assert sorted(path.relative_to(base).as_posix() for path in base.rglob("*")) == before
        cls.assert_not_called()

    def test_stream_guards_then_dispatches_lerobot_sources(
        self, client: TestClient, override_service, dataset_layout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base, dataset_dir, _output = dataset_layout
        override_service.dataset_is_lerobot.return_value = True
        cls, _instance = _patch_lerobot_exporter(monkeypatch)

        rejected = client.post(
            "/api/datasets/ds-1/export/stream", json={"episodeIndices": [0], "outputPath": str(dataset_dir / "x")}
        )
        assert rejected.status_code == 400
        assert not (dataset_dir / "x").exists()
        cls.assert_not_called()

        with client.stream(
            "POST",
            "/api/datasets/ds-1/export/stream",
            json={"episodeIndices": [0], "outputPath": str(base / "ds-1-edited")},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

        assert "event: complete" in body
        cls.assert_called_once_with(dataset_dir, base / "ds-1-edited", dataset_id="ds-1")

    @pytest.mark.parametrize("endpoint", ["export", "export/stream"])
    @pytest.mark.parametrize("include", [True, False, None], ids=["included", "left-out", "by-default"])
    def test_lerobot_exports_get_the_latest_saved_language_instruction_unless_left_out(
        self,
        client: TestClient,
        override_service,
        saved_annotations,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str,
        include: bool | None,
    ) -> None:
        base, _dataset, _output = dataset_layout
        override_service.dataset_is_lerobot.return_value = True
        _cls, instance = _patch_lerobot_exporter(monkeypatch)
        body: dict[str, Any] = {"episodeIndices": [0, 1], "outputPath": str(base / "edited")}
        if include is not None:
            body["includeLanguageInstructions"] = include

        _post_export(client, endpoint, body)

        latest = LanguageInstruction(
            "Pick up the gear", ("Grab the gear",), ("Approach",), "annotator-b", "2026-10-01T12:00:00+00:00"
        )
        assert instance.export_episodes.call_args.kwargs["language"] == ({} if include is False else {0: latest})

    @pytest.mark.parametrize("endpoint", ["export", "export/stream"])
    def test_hdf5_exports_ignore_the_language_option(
        self,
        client: TestClient,
        override_service,
        saved_annotations,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str,
    ) -> None:
        _base, _dataset, output_dir = dataset_layout
        instance = MagicMock()
        instance.export_episodes.return_value = _make_export_result()
        _patch_exporter(monkeypatch, MagicMock(return_value=instance))
        body = {"episodeIndices": [0], "outputPath": str(output_dir), "includeLanguageInstructions": True}

        _post_export(client, endpoint, body)

        assert "language" not in instance.export_episodes.call_args.kwargs
        saved_annotations.get_annotation.assert_not_called()

    @pytest.mark.parametrize(
        "state",
        ["after-the-first-move", "foreign-root-file", "after-meta", "live-holder", "free-lock-file", "held-lock-file"],
    )
    @pytest.mark.parametrize("endpoint", ["export", "export/stream"])
    def test_either_endpoint_recovers_or_refuses_what_a_stopped_export_left(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        held_locks: list[int],
        caplog: pytest.LogCaptureFixture,
        endpoint: str,
        state: str,
    ) -> None:
        base, dataset_dir, _output = dataset_layout
        write_source(dataset_dir)
        override_service.dataset_is_lerobot.return_value = True
        output = base / "edited"
        if state in ("free-lock-file", "held-lock-file"):
            output.mkdir()
            (output / LOCK_FILE).touch()
        else:
            stop_export(dataset_dir, output, "after meta moved" if state == "after-meta" else "after the first move")
        if state == "foreign-root-file":
            (output / "notes.txt").write_text("mine")
        if state in ("live-holder", "held-lock-file"):
            held_locks.append(hold_lock(output))
        before, identity = _contents(output), output.stat()

        result = _export_through(client, endpoint, output)

        names = sorted(path.name for path in output.iterdir())
        if state in ("after-the-first-move", "free-lock-file"):
            assert result["success"] is True, result
            assert names == ["data", PROVENANCE_FILE, "meta", "videos"]
            assert json.loads((output / PROVENANCE_FILE).read_text())["source"]["dataset_id"] == "ds-1"
            return
        assert result["success"] is False
        assert result["error"] == "Export failed"
        if state == "foreign-root-file":
            assert names == ["notes.txt"]
            assert (output / "notes.txt").read_text() == "mine"
        elif state == "after-meta":
            assert names == ["data", PROVENANCE_FILE, "meta", "videos"]
            assert _contents(output) == {name: digest for name, digest in before.items() if not name.startswith(".")}
            assert json.loads((output / PROVENANCE_FILE).read_text())["source"]["dataset_id"] == "stopped"
        else:
            assert _contents(output) == before
            assert os.path.samestat(output.stat(), identity)
            assert os.path.samestat(os.stat(output / LOCK_FILE), os.fstat(held_locks[0]))
            assert "another export is writing to this directory" in caplog.text
            if state == "held-lock-file":
                os.close(held_locks.pop())
                assert _export_through(client, endpoint, output)["success"] is True


class TestExportEpisodesStream:
    @pytest.mark.parametrize("suffix", ["", "/stream"])
    def test_failed_results_hide_diagnostics(
        self,
        client: TestClient,
        override_service,
        dataset_layout,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        suffix: str,
    ) -> None:
        _, _, output_dir = dataset_layout
        exporter = MagicMock()
        exporter.export_episodes.return_value = _make_export_result(
            success=False, error="/srv/data/private: permission denied"
        )
        _patch_exporter(monkeypatch, MagicMock(return_value=exporter))
        response = client.post(
            f"/api/datasets/ds-1/export{suffix}",
            json={"episodeIndices": [0], "outputPath": str(output_dir), "applyEdits": False},
        )
        assert response.status_code == 200
        payload = (
            json.loads(response.text.split("event: complete\ndata: ", 1)[1].split("\n\n", 1)[0])
            if suffix
            else response.json()
        )
        assert payload["success"] is False
        assert payload["error"] == "Export failed"
        assert payload["outputFiles"] == ["episode_0.hdf5"]
        assert payload["stats"]["total_episodes"] == 1
        assert "/srv/data/private" not in response.text
        assert "/srv/data/private" in caplog.text

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

    def test_output_path_traversal_returns_400(self, client: TestClient, override_service, tmp_path: Path) -> None:
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
        from src.api.services.episode_edits import ExportProgress

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
        assert '"code": "EXPORT_UNAVAILABLE"' in body
        assert '"message": "Export is unavailable"' in body
        assert "missing dep" not in body

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
        assert '"code": "EXPORT_FAILED"' in body
        assert '"message": "Export failed"' in body
        assert "disk full" not in body


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

    def test_preview_aggregates_frames_and_removals(self, client: TestClient, override_service) -> None:
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

    def test_preview_skips_missing_episode(self, client: TestClient, override_service) -> None:
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
