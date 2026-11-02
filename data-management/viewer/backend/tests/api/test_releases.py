"""Release API contract tests for the curated dataset release surface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import PrincipalContext, require_principal_context
from src.api.routers.releases import router
from src.api.services.dataset_service import DatasetService
from src.api.services.release_service import ReleaseApiSettings, ReleaseService, get_release_service
from src.api.services.viewer_release_planner import ViewerReleasePlanner
from tests.lerobot_sources import CAMERA, write_source

BASE = "/api/releases/v1"


@pytest.fixture
def release_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    root = write_source(tmp_path / "datasets" / "native")
    metadata = root / "meta/episodes/chunk-000/file-000.parquet"
    rows = pq.read_table(metadata).to_pylist()
    for row in rows:
        for key, value in {"min": 0.1, "max": 0.9, "mean": 0.5, "std": 0.1}.items():
            row[f"stats/{CAMERA}/{key}"] = [[[value]]] * 3
        row[f"stats/{CAMERA}/count"] = [row["length"]]
    pq.write_table(pa.Table.from_pylist(rows), metadata)
    settings = ReleaseApiSettings(tmp_path / "jobs", {"local": tmp_path / "output"})
    monkeypatch.setenv("RELEASE_JOB_ROOT", str(settings.job_root))
    monkeypatch.setenv("RELEASE_DESTINATIONS", json.dumps({"local": str(tmp_path / "output")}))
    monkeypatch.setenv("DATA_DIR", str(root.parent))
    datasets = DatasetService(base_path=str(root.parent))
    import src.api.services.viewer_release_planner as planner_module

    monkeypatch.setattr(planner_module, "get_dataset_service", lambda: datasets)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(
        settings, planner=ViewerReleasePlanner(settings)
    )
    spec = {
        "destination": "local",
        "package_id": "test-package",
        "attempt_id": "test-attempt",
        "dataset_name": "published",
        "dataset_id": "native",
        "episode_indices": [0, 1],
        "finalized": True,
    }
    with TestClient(app) as test_client:
        yield test_client, spec, settings, app, root


def _preview(test_client: TestClient, spec: dict) -> dict:
    response = test_client.get(BASE + "/preview", params={"selection": json.dumps(spec)})
    assert response.status_code == 200, response.json()
    return response.json()


def _submit(test_client: TestClient, spec: dict, preview: dict, *, key: str = "release-key-1"):
    return test_client.post(
        BASE + "/jobs",
        headers={"Idempotency-Key": key},
        json={
            **spec,
            "preview_digest": preview["digest"],
            "processing_digest": preview["summary"]["processing_digest"],
            "local_disk_acknowledged": True,
        },
    )


def test_preview_is_read_only_and_inventory_is_paged(release_api: tuple) -> None:
    test_client, spec, settings, _app, _root = release_api
    preview = _preview(test_client, spec)
    assert preview["summary"]["selected_count"] == 2
    assert not settings.job_root.exists()
    assert not settings.destinations["local"].exists()
    first = test_client.get(BASE + "/inventory/native?limit=1").json()
    assert first["items"] == [{"episode_index": 0}]
    assert first["next_cursor"] == 1
    second = test_client.get(BASE + "/inventory/native?cursor=1&limit=1").json()
    assert second["items"] == [{"episode_index": 1}]


def test_confirmed_acceptance_replays_and_survives_service_restart(release_api: tuple) -> None:
    test_client, spec, settings, app, _root = release_api
    preview = _preview(test_client, spec)
    accepted = _submit(test_client, spec, preview)
    assert accepted.status_code == 202, accepted.json()
    job = accepted.json()["job_id"]
    assert accepted.headers["location"] == BASE + "/jobs/" + job
    assert accepted.headers["retry-after"] == "5"
    replay = _submit(test_client, spec, preview)
    assert replay.json() == {"job_id": job, "replayed": True}
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(
        settings, planner=ViewerReleasePlanner(settings)
    )
    assert test_client.get(BASE + "/jobs/" + job).json()["state"] == "accepted"
    assert test_client.get(BASE + "/jobs").json()["items"][0]["job_id"] == job
    assert test_client.post(BASE + "/jobs/" + job + "/recover").status_code == 202
    assert test_client.get(BASE + "/jobs/" + job + "/result").status_code == 409


@pytest.mark.parametrize("change", ["confirmation", "disk", "source", "name", "processing"])
def test_stale_or_unconfirmed_submission_never_accepts(release_api: tuple, change: str) -> None:
    test_client, spec, settings, _app, root = release_api
    preview = _preview(test_client, spec)
    if change == "confirmation":
        preview["digest"] = "0" * 64
    elif change == "processing":
        preview["summary"]["processing_digest"] = "0" * 64
    elif change == "name":
        spec = {**spec, "dataset_name": "different"}
    elif change == "source":
        info = root / "meta/info.json"
        value = json.loads(info.read_bytes())
        value["robot_type"] = "changed"
        info.write_text(json.dumps(value))
    if change == "disk":
        response = test_client.post(
            BASE + "/jobs",
            headers={"Idempotency-Key": "release-key-1"},
            json={
                **spec,
                "preview_digest": preview["digest"],
                "processing_digest": preview["summary"]["processing_digest"],
            },
        )
    else:
        response = _submit(test_client, spec, preview)
    assert response.status_code in {409, 422}
    assert not settings.destinations["local"].exists()
    assert test_client.get(BASE + "/jobs").json()["items"] == []


def test_cancel_is_owned_and_csrf_protected(release_api: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, spec, _settings, app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(scope_id="other", auth_mode="local")
    assert test_client.get(BASE + "/jobs/" + job).status_code == 404
    assert test_client.post(BASE + "/jobs/" + job + "/cancel").status_code == 404
    app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(
        scope_id="localuser", auth_mode="local"
    )
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
    assert test_client.post(BASE + "/jobs/" + job + "/cancel").status_code == 403
    test_client.cookies.set("csrf_token", "a" * 64)
    cancelled = test_client.post(BASE + "/jobs/" + job + "/cancel", headers={"X-CSRF-Token": "a" * 64})
    assert cancelled.status_code == 202
    assert cancelled.json()["cancel_requested"] is True


def test_fresh_process_worker_publishes_and_artifacts_reverify(release_api: tuple) -> None:
    test_client, spec, _settings, _app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    completed = subprocess.run(
        [sys.executable, "-m", "src.api.release_worker", "--once"],
        env=dict(os.environ),
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    status = test_client.get(BASE + "/jobs/" + job).json()
    assert status["state"] == "committed", status
    result = test_client.get(BASE + "/jobs/" + job + "/result")
    assert result.status_code == 200, result.json()
    assert result.json()["result"]["publication_state"] == "published"
    for artifact in result.json()["artifacts"]:
        assert test_client.get(BASE + "/jobs/" + job + "/artifacts/" + artifact).status_code == 200
    assert test_client.get(BASE + "/jobs/" + job + "/artifacts/not-allowed").status_code == 404


@pytest.fixture
def local_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")


def test_capabilities_reports_schema_without_paths(client: TestClient, local_auth: None) -> None:
    response = client.get("/api/releases/v1/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "curated-release-api/v1"
    assert body["destinations"] == []
    assert "path" not in str(body).lower()
