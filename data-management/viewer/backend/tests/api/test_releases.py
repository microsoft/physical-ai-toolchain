"""Release API contract tests for the curated dataset release surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from physical_ai_curation.models import DatasetLabelsFile
from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json
from physical_ai_curation.release.jobs import ReleaseJobStore
from physical_ai_curation.release.record_storage import BlobLocation, release_collection_location
from physical_ai_curation.release.runner import execute_job
from physical_ai_curation.release.store import LocalReleaseStore

from src.api.auth import PrincipalContext, require_principal_context
from src.api.models.annotations import EpisodeAnnotationFile
from src.api.routers.releases import router
from src.api.services.annotation_service import AnnotationService
from src.api.services.dataset_service import DatasetService
from src.api.services.label_storage import LocalLabelStorage
from src.api.services.release_service import ReleaseApiSettings, ReleaseService, _source_release, get_release_service
from src.api.services.viewer_release_planner import ViewerReleasePlanner
from src.api.storage.local_revision import write_conditional
from tests.lerobot_sources import CAMERA, write_source

BASE = "/api/releases/v1"


@pytest.mark.parametrize("arguments", [[], ["--once", "--limit", "0"], ["--once", "--limit", "101"]])
def test_given_invalid_worker_arguments_when_run_then_usage_error(arguments: list[str]) -> None:
    from src.api.release_worker import main

    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("root", ["", "relative/jobs"])
def test_given_disabled_or_invalid_worker_when_run_then_configuration_exit(
    monkeypatch: pytest.MonkeyPatch, root: str
) -> None:
    from src.api.release_worker import main

    monkeypatch.setenv("RELEASE_JOB_ROOT", root)
    monkeypatch.delenv("RELEASE_JOB_STORAGE", raising=False)
    monkeypatch.delenv("RELEASE_DESTINATIONS", raising=False)
    assert main(["--once"]) == 2


@pytest.mark.parametrize("injected", [False, True])
def test_given_empty_worker_queue_when_run_then_safe_success_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, injected: bool
) -> None:
    from src.api import release_worker

    settings = ReleaseApiSettings(tmp_path / "jobs")
    monkeypatch.setattr(release_worker, "load_release_api_settings", lambda _environ: settings)
    planner = ViewerReleasePlanner(settings) if injected else None
    monkeypatch.setattr(release_worker, "get_release_planner", lambda: planner)
    with caplog.at_level("INFO", logger="release_worker"):
        assert release_worker.main(["--once", "--limit", "100", "--worker-id", "test-worker"]) == 0
    summary = json.loads(caplog.records[-1].message)
    assert summary == {"schema_version": "curated-release-worker-outcome/v1", "outcomes": []}


def test_given_worker_api_failure_when_run_then_safe_configuration_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from src.api import release_worker
    from src.api.services.release_service import ReleaseApiError

    async def fail_queue(*args: object, **kwargs: object) -> tuple:
        raise ReleaseApiError(503, "private provider detail")

    monkeypatch.setattr(release_worker, "load_release_api_settings", lambda _environ: ReleaseApiSettings(tmp_path))
    monkeypatch.setattr(release_worker, "run_queue", fail_queue)
    assert release_worker.main(["--once"]) == 2
    assert "release worker pass failed" in caplog.text
    assert "private provider detail" not in caplog.text


def test_given_worker_module_when_disabled_then_process_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELEASE_JOB_ROOT", "")
    monkeypatch.delenv("RELEASE_DESTINATIONS", raising=False)
    monkeypatch.delenv("RELEASE_JOB_STORAGE", raising=False)
    monkeypatch.setattr(sys, "argv", ["release_worker", "--once"])
    monkeypatch.delitem(sys.modules, "src.api.release_worker", raising=False)
    with pytest.raises(SystemExit) as error:
        runpy.run_module("src.api.release_worker", run_name="__main__")
    assert error.value.code == 2


@pytest.mark.parametrize(
    "environ",
    [
        {"RELEASE_JOB_ROOT": "relative"},
        {"RELEASE_DESTINATIONS": "{"},
        {"RELEASE_DESTINATIONS": "[]"},
        {"RELEASE_DESTINATIONS": '{"INVALID":"/output"}'},
        {"RELEASE_DESTINATIONS": '{"local":"relative"}'},
        {"RELEASE_JOB_STORAGE": "[]"},
        {"RELEASE_JOB_STORAGE": "{"},
    ],
)
def test_given_invalid_release_configuration_when_loaded_then_refused(environ: dict[str, str]) -> None:
    from src.api.services.release_service import load_release_api_settings

    with pytest.raises(ValueError):
        load_release_api_settings(environ)


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "x" * 2001),
        ("destination", "INVALID"),
        ("package_id", "bad/id"),
        ("attempt_id", "bad/id"),
        ("dataset_name", "bad--name"),
        ("dataset_id", "../private"),
        ("episode_indices", ()),
        ("episode_indices", (-1,)),
        ("episode_indices", (True,)),
        ("episode_indices", (0, 0)),
    ],
)
def test_given_invalid_selection_when_constructed_then_safe_refusal(field: str, value: object) -> None:
    from src.api.services.release_service import ReleaseApiError, ReleaseSpec

    selection = dict(
        destination="local",
        package_id="package",
        attempt_id="attempt",
        dataset_name="output",
        dataset_id="native",
        episode_indices=(0,),
    )
    selection[field] = value
    with pytest.raises(ReleaseApiError) as error:
        ReleaseSpec(**selection)
    assert error.value.status_code == 422


@pytest.mark.parametrize("operation", ["status", "cancel", "recover", "result"])
def test_given_unknown_job_when_operated_then_not_found(tmp_path: Path, operation: str) -> None:
    from src.api.services.release_service import ReleaseApiError

    service = ReleaseService(ReleaseApiSettings(tmp_path / "jobs"))
    with pytest.raises(ReleaseApiError) as error:
        getattr(service, operation)("localuser", "0" * 32)
    assert error.value.status_code == 404


@pytest.mark.parametrize("cursor,limit", [(-1, 10), (0, 0), (0, 101)])
def test_given_invalid_inventory_page_when_requested_then_refused(cursor: int, limit: int) -> None:
    from src.api.services.release_service import ReleaseApiError

    service = ReleaseService(ReleaseApiSettings(None))
    with pytest.raises(ReleaseApiError) as error:
        asyncio.run(service.inventory("native", cursor=cursor, limit=limit))
    assert error.value.status_code == 422


def test_given_unconfigured_service_when_operated_then_safe_unavailability(tmp_path: Path) -> None:
    from src.api.services.release_service import ReleaseApiError, ReleaseSpec, open_release_job_store

    spec = ReleaseSpec("local", "package", "attempt", "output", "native", (0,))
    disabled = ReleaseApiSettings(None)
    with pytest.raises(ReleaseApiError, match="not configured"):
        open_release_job_store(disabled)
    with pytest.raises(ReleaseApiError, match="not configured"):
        ReleaseService(disabled).jobs("localuser", cursor=0, limit=10)
    service = ReleaseService(ReleaseApiSettings(tmp_path / "jobs", {"local": tmp_path / "output"}))
    with pytest.raises(ReleaseApiError, match="planning is not configured"):
        asyncio.run(service.preview("localuser", spec))
    with pytest.raises(ReleaseApiError, match="source is unavailable"):
        asyncio.run(service.inventory("native", cursor=0, limit=10))
    with pytest.raises(ReleaseApiError, match="invalid"):
        service.accepted_request("localuser", "bad/key")
    with pytest.raises(ReleaseApiError, match="not found"):
        service.accepted_request("localuser", "missing-key")


def test_given_blob_destination_when_configured_then_location_and_planner_are_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.api.services import release_service as service_module

    settings = service_module.load_release_api_settings(
        {
            "RELEASE_DESTINATIONS": json.dumps(
                {
                    "remote": {
                        "account_url": "https://example.blob.core.windows.net",
                        "container": "releases",
                        "prefix": "output",
                    }
                }
            )
        }
    )
    assert settings.destinations["remote"] == BlobLocation(
        "https://example.blob.core.windows.net", "releases", "output"
    )
    monkeypatch.setattr(service_module, "_PLANNER", None)
    planner = ViewerReleasePlanner(ReleaseApiSettings(tmp_path))
    service_module.set_release_planner(planner)
    assert service_module.get_release_planner() is planner
    service_module.set_release_planner(None)
    assert service_module.get_release_planner() is None
    monkeypatch.setenv("RELEASE_JOB_ROOT", "relative")
    with pytest.raises(service_module.ReleaseApiError, match="configuration is invalid"):
        service_module.get_release_service()


@pytest.mark.parametrize("error_type,status", [(OSError, 422), (ValueError, 422), (KeyError, 422), (TypeError, 422)])
def test_given_unavailable_source_when_previewed_then_safe_error(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception], status: int
) -> None:
    test_client, spec, _settings, _app, _root = release_api

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise error_type("private source details")

    monkeypatch.setattr(ViewerReleasePlanner, "prepare", unavailable)
    response = test_client.post(BASE + "/preview", json=spec)
    assert response.status_code == status
    assert "private source details" not in response.text


@pytest.mark.parametrize(
    "error_name,status", [("CapacityError", 507), ("JobConflictError", 409), ("ReleaseJobError", 422)]
)
def test_given_admission_failure_when_submitted_then_safe_status(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, error_name: str, status: int
) -> None:
    from physical_ai_curation.release import jobs

    test_client, spec, _settings, _app, _root = release_api
    preview = _preview(test_client, spec)

    def refuse(*args: object, **kwargs: object) -> None:
        raise getattr(jobs, error_name)("private admission detail")

    monkeypatch.setattr(ReleaseJobStore, "accept", refuse)
    response = _submit(test_client, spec, preview)
    assert response.status_code == status
    assert "private admission detail" not in response.text


def test_given_invalid_job_lookup_when_called_then_safe_rejection(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.release.jobs import ReleaseJobError

    test_client, spec, settings, app, _root = release_api
    assert test_client.get(BASE + "/preview", params={"selection": "{"}).status_code == 422
    assert test_client.get(BASE + "/requests", headers={"Idempotency-Key": "bad/key"}).status_code == 422
    assert test_client.post(BASE + "/jobs", headers={"Idempotency-Key": "bad/key"}, json=spec).status_code == 422
    service = ReleaseService(settings)
    from src.api.services.release_service import ReleaseApiError

    with pytest.raises(ReleaseApiError):
        service.jobs("localuser", cursor=-1, limit=1)

    def refuse(*args: object, **kwargs: object) -> None:
        raise ReleaseJobError("private storage detail")

    monkeypatch.setattr(ReleaseJobStore, "page", refuse)
    assert test_client.get(BASE + "/jobs").status_code == 422
    monkeypatch.setattr(ReleaseJobStore, "accepted_request", refuse)
    response = test_client.get(BASE + "/requests", headers={"Idempotency-Key": "valid-key"})
    assert response.status_code == 422
    assert "private storage detail" not in response.text
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(ReleaseApiSettings(None))
    assert test_client.get(BASE + "/jobs").status_code == 503


@pytest.mark.parametrize("change", ["schema", "alias", "relative", "kind", "association", "blob"])
def test_given_tampered_execution_binding_when_restored_then_refused(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from src.api.services import viewer_release_planner as planner_module
    from src.api.services.release_service import ReleaseApiError

    test_client, spec, settings, _app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    binding = parse_strict_json(store.execution_binding(job))
    if change == "schema":
        binding["schema"] = "unsupported"
    elif change == "alias":
        binding["destination"] = "missing"
    elif change == "relative":
        binding["effective_destination"]["path"] = "relative"
    elif change == "kind":
        binding["effective_destination"] = {"kind": "unsupported"}
    elif change == "association":
        binding["effective_destination"]["path"] = str(settings.job_root.parent / "foreign")
    else:
        binding["effective_destination"] = {
            "kind": "blob",
            "account_url": "https://example.blob.core.windows.net",
            "container": "releases",
            "prefix": "",
        }
    monkeypatch.setattr(store, "execution_binding", lambda _job: canonical_json_bytes(binding))
    monkeypatch.setattr(planner_module, "open_release_job_store", lambda _settings: store)
    with pytest.raises(ReleaseApiError) as error:
        ViewerReleasePlanner(settings).execution(job, store.frozen_request(job))
    assert error.value.status_code == 409


@pytest.mark.parametrize("failure", ["oversized", "unreadable"])
def test_given_invalid_source_inventory_when_read_then_safe_refusal(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    test_client, _spec, settings, app, _root = release_api
    planner = ViewerReleasePlanner(settings)
    if failure == "oversized":
        monkeypatch.setattr(planner.datasets, "list_episodes", AsyncMock(return_value=[None] * 10001))
        expected = (422, "source_inventory_limit_exceeded")
    else:
        reader = planner.reader_for_scope("localuser")
        monkeypatch.setattr(reader, "annotation", AsyncMock(side_effect=SavedInputError("private detail")))
        monkeypatch.setattr(planner, "reader_for_scope", lambda _scope: reader)
        expected = (409, "saved_inventory_unavailable")
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings, planner=planner)

    response = test_client.get(BASE + "/inventory/native")

    assert response.status_code == expected[0]
    assert response.json()["detail"]["code"] == expected[1]
    assert "private detail" not in response.text


@pytest.mark.parametrize("failure", ["missing_planner", "invalid_binding"])
def test_given_unavailable_recovery_binding_when_recovered_then_safe_refusal(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    test_client, spec, settings, app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    planner = ViewerReleasePlanner(settings) if failure == "invalid_binding" else None
    if planner is not None:
        monkeypatch.setattr(planner, "execution", MagicMock(side_effect=ValueError("private binding detail")))
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings, planner=planner)

    response = test_client.post(BASE + f"/jobs/{job}/recover")

    assert response.status_code == (409 if planner else 503)
    assert "private binding detail" not in response.text


def test_given_unavailable_destination_when_previewed_then_safe_refusal(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.services import release_service as service_module

    test_client, spec, _settings, _app, _root = release_api
    response = test_client.get(BASE + "/preview", params={"selection": json.dumps({**spec, "destination": "missing"})})
    assert response.status_code == 404
    monkeypatch.setattr(
        service_module, "release_collection_location", MagicMock(side_effect=ValueError("private path"))
    )
    response = test_client.get(BASE + "/preview", params={"selection": json.dumps(spec)})
    assert response.status_code == 422
    assert "private path" not in response.text
    assert test_client.get(BASE + "/sources/native/releases").json()["items"] == []


def test_given_invalid_retained_source_request_when_read_then_refused() -> None:
    from src.api.services.release_service import ReleaseApiError

    service = ReleaseService(ReleaseApiSettings(None))
    with pytest.raises(ReleaseApiError, match="dataset id is invalid"):
        service.source_releases("localuser", "../private")
    with pytest.raises(ReleaseApiError, match="invalid_episode_page"):
        service.source_episodes("localuser", "native", "handle", cursor=-1, limit=1)


def _inventoried_store(root: Path, contents: dict[str, bytes]) -> tuple[LocalReleaseStore, str]:
    """Build minimal inventoried bytes for store-backed post-admission checks."""
    from physical_ai_curation.release.models import INVENTORY_PATH, MANIFEST_PATH, Inventory, InventoryEntry

    inventory = Inventory(
        entries=tuple(
            InventoryEntry(path=path, length=len(content), sha256=hashlib.sha256(content).hexdigest())
            for path, content in sorted(contents.items())
        )
    )
    inventory_bytes = canonical_json_bytes(inventory.model_dump(mode="json"))
    manifest = canonical_json_bytes(
        {
            "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
            "frozen_input_path": "frozen.json",
        }
    )
    for path, content in {**contents, INVENTORY_PATH: inventory_bytes, MANIFEST_PATH: manifest}.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return LocalReleaseStore(root), hashlib.sha256(manifest).hexdigest()


@pytest.mark.parametrize("control", ["manifest", "inventory"])
def test_given_changed_control_after_admission_when_files_opened_then_refused(tmp_path: Path, control: str) -> None:
    from physical_ai_curation.release.models import INVENTORY_PATH, MANIFEST_PATH

    from src.api.services.release_service import ReleaseApiError, _release_files

    store, digest = _inventoried_store(tmp_path, {"meta/info.json": b"{}"})
    item = _source_release(
        "local",
        "native",
        "0" * 64,
        dataset_name="published",
        package_id="package",
        manifest_sha256=digest,
        store=store,
        local_root=tmp_path,
    )
    target = tmp_path / (MANIFEST_PATH if control == "manifest" else INVENTORY_PATH)
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(ReleaseApiError, match="release_verification_failed") as error:
        _release_files(item)
    assert error.value.status_code == 409


@pytest.mark.parametrize("failure", ["missing", "budget", "metadata", "long", "short", "hash"])
def test_given_invalid_inventoried_read_when_consumed_then_refused(tmp_path: Path, failure: str) -> None:
    from physical_ai_curation.release.errors import ReleaseVerificationError

    from src.api.services.release_service import _ReleaseFiles

    store, digest = _inventoried_store(tmp_path, {"meta/info.json": b"{}", "videos/camera.mp4": b"video"})
    files = _ReleaseFiles(
        store, name="published", manifest_sha256=digest, metadata_max_bytes=1 if failure == "budget" else 100
    )
    if failure == "missing":
        with pytest.raises(FileNotFoundError):
            files.read_bytes("meta/missing.json")
    elif failure in {"budget", "metadata"}:
        if failure == "metadata":
            (tmp_path / "meta/info.json").write_bytes(b"[]")
        with pytest.raises(ReleaseVerificationError, match="metadata"):
            files.read_bytes("meta/info.json")
    else:
        (tmp_path / "videos/camera.mp4").write_bytes({"long": b"longer", "short": b"tiny", "hash": b"other"}[failure])
        with pytest.raises(ReleaseVerificationError, match="media"):
            list(files.stream("videos/camera.mp4"))


@pytest.mark.parametrize("failure", ["missing", "budget", "long", "short"])
def test_given_invalid_media_response_when_buffered_then_safe_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from src.api.services.release_service import ReleaseApiError, _release_media, _ReleaseFiles

    store, digest = _inventoried_store(tmp_path, {"videos/camera.mp4": b"video"})
    files = _ReleaseFiles(store, name="published", manifest_sha256=digest, metadata_max_bytes=100)
    if failure in {"long", "short"}:
        monkeypatch.setattr(files, "stream", lambda _path: iter([b"longer" if failure == "long" else b"tiny"]))
    with pytest.raises(ReleaseApiError) as error:
        _release_media(
            files,
            "missing.mp4" if failure == "missing" else "videos/camera.mp4",
            media_max_bytes=1 if failure == "budget" else 100,
        )
    assert error.value.status_code == {"missing": 404, "budget": 422, "long": 409, "short": 409}[failure]


@pytest.mark.parametrize("failure", ["manifest", "base_budget", "projection_budget", "episode_type"])
def test_given_invalid_retained_detail_when_read_then_safe_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from physical_ai_curation.release.models import MANIFEST_PATH

    from src.api.services import release_service as service_module

    contents = {
        "meta/info.json": canonical_json_bytes({"total_episodes": True if failure == "episode_type" else 1}),
        "frozen.json": b"{}",
        "annotations/episodes/episode_000000.json": b'{"description":"' + b"a" * 1000 + b'"}',
    }
    store, digest = _inventoried_store(tmp_path, contents)
    item = _source_release(
        "local",
        "native",
        "0" * 64,
        dataset_name="published",
        package_id="package",
        manifest_sha256=digest,
        store=store,
        local_root=tmp_path,
    )
    service = ReleaseService(ReleaseApiSettings(None))
    monkeypatch.setattr(service, "_find_source", lambda *_args: item)
    original_read = store.read_control_bytes
    reads = 0

    def read_control(relative: str, *, max_bytes: int) -> bytes:
        nonlocal reads
        content = original_read(relative, max_bytes=max_bytes)
        if relative == MANIFEST_PATH:
            reads += 1
            if failure == "manifest" and reads == 2:
                return content + b" "
            if failure in {"base_budget", "projection_budget"} and reads == 2:
                monkeypatch.setattr(service_module, "MAX_CONTROL_BYTES", 1 if failure == "base_budget" else 100)
        return content

    monkeypatch.setattr(store, "read_control_bytes", read_control)
    with pytest.raises(service_module.ReleaseApiError, match="release_verification_failed") as error:
        service.source_release("localuser", "native", item.handle)
    assert error.value.status_code == 409


def test_given_invalid_episode_metadata_when_read_then_safe_refusal(tmp_path: Path) -> None:
    from src.api.services.release_service import ReleaseApiError, _episode_records, _ReleaseFiles

    store, digest = _inventoried_store(tmp_path, {"meta/info.json": b"invalid json"})
    files = _ReleaseFiles(store, name="published", manifest_sha256=digest, metadata_max_bytes=100)
    with pytest.raises(ReleaseApiError, match="release_verification_failed"):
        _episode_records(files)


def test_given_optional_azure_sdk_missing_when_errors_resolved_then_local_errors_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.services.release_service import _release_read_errors

    monkeypatch.setitem(sys.modules, "azure.core.exceptions", None)
    assert OSError in _release_read_errors()


@pytest.mark.parametrize("missing", ["dataset", "worker", "blob_provider"])
def test_given_missing_planner_dependency_when_used_then_refused(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    from src.api.services.release_service import ReleaseApiError, ReleaseSpec

    _client, body, settings, _app, _root = release_api
    spec = ReleaseSpec(**{**body, "episode_indices": tuple(body["episode_indices"])})
    planner = ViewerReleasePlanner(settings if missing != "worker" else ReleaseApiSettings(None))
    if missing == "dataset":
        monkeypatch.setattr(planner.datasets, "get_dataset", AsyncMock(return_value=None))
    if missing == "blob_provider":
        with pytest.raises(ReleaseApiError, match="release source is unavailable"):
            planner._source("native", finalized=True, kind="blob")
    else:
        with pytest.raises(ReleaseApiError) as error:
            asyncio.run(planner.prepare("localuser", spec, settings.destinations["local"]))
        assert error.value.status_code == (404 if missing == "dataset" else 503)
    if missing == "worker":
        with pytest.raises(ReleaseApiError, match="release worker is not configured"):
            planner.execution("job", b"{}")


def test_given_blob_source_and_destination_when_planned_then_configured_binding_is_retained(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    import azure.identity
    import azure.storage.blob

    from src.api.services import viewer_release_planner as planner_module
    from src.api.services.label_storage import BlobLabelStorage
    from src.api.services.release_service import ReleaseSpec
    from src.api.services.saved_input_service import AzureJudgeStateReader, ViewerSavedInputReader

    _client, body, settings, _app, root = release_api
    planner = ViewerReleasePlanner(settings)
    local_reader = ViewerSavedInputReader(planner.datasets, planner._annotations, LocalLabelStorage(str(root.parent)))
    container = FakeBlobContainer()
    monkeypatch.setattr(container, "url", "https://example.blob.core.windows.net/datasets", raising=False)
    container.objects = {
        "native/" + path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    provider = MagicMock(account_name="example", container_name="datasets")
    provider.get_blob_prefix.return_value = "native"
    monkeypatch.setattr(planner.datasets, "_blob_provider", provider)
    monkeypatch.setattr(planner.datasets, "has_blob_provider", lambda: True)
    monkeypatch.setattr(planner.datasets, "get_dataset", AsyncMock(return_value=object()))
    monkeypatch.setattr(planner.datasets, "get_source_revision", AsyncMock(return_value=("source-id", "revision-1")))
    monkeypatch.setattr(planner_module, "get_app_config", lambda: SimpleNamespace(storage_backend="azure"))
    assert isinstance(planner._judge_state(), AzureJudgeStateReader)
    assert isinstance(planner._label_storage(), BlobLabelStorage)
    monkeypatch.setattr(planner, "reader_for_scope", lambda _scope: local_reader)
    credential = object()
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda: credential)
    client = MagicMock()
    client.get_container_client.return_value = container
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(azure.storage.blob, "BlobServiceClient", factory)
    destination = BlobLocation("https://example.blob.core.windows.net", "output", "native/releases")
    spec = ReleaseSpec(**{**body, "episode_indices": tuple(body["episode_indices"])})

    prepared = asyncio.run(planner.prepare("localuser", spec, destination))

    binding = parse_strict_json(prepared.execution_binding)
    assert binding["source_kind"] == "blob"
    assert binding["effective_destination"]["container"] == "output"
    assert binding["association"]["effective_destination"] == destination.as_text()
    assert prepared.summary["destination_kind"] == "blob"
    assert prepared.reserved_bytes > 0
    assert factory.call_args.kwargs["account_url"] == "https://example.blob.core.windows.net"
    assert factory.call_args.kwargs["credential"] is credential
    assert factory.call_args.kwargs["retry_total"] == 0
    client.get_container_client.assert_called_once_with("datasets")


@pytest.mark.parametrize("kind", ["local", "blob"])
@pytest.mark.parametrize("mismatch", ["source", "alias"])
def test_given_foreign_association_when_listed_then_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, mismatch: str
) -> None:
    from src.api.services import release_service as service_module

    verified = SimpleNamespace(
        source_relative_path="other" if mismatch == "source" else "native",
        destination_alias="other" if mismatch == "alias" else "destination",
    )
    container = MagicMock()
    factory = MagicMock(return_value=container)
    monkeypatch.setattr(service_module, "_blob_container_client", factory)
    associations = service_module.LocalSourceAssociations if kind == "local" else service_module.BlobSourceAssociations
    monkeypatch.setattr(associations, "discover_verified", lambda _self, **_kwargs: [verified])
    location = tmp_path if kind == "local" else BlobLocation("https://example.blob.core.windows.net", "releases")
    service = ReleaseService(ReleaseApiSettings(None, {"destination": location}))

    assert service.source_releases("localuser", "native")["items"] == []
    if kind == "blob":
        factory.assert_called_once_with("https://example.blob.core.windows.net", "releases")
        container.close.assert_called_once_with()


def test_given_job_capacity_failure_when_cancelled_then_safe_status(tmp_path: Path) -> None:
    from physical_ai_curation.release.jobs import CapacityError

    from src.api.services.release_service import ReleaseApiError

    store = MagicMock(spec=ReleaseJobStore)
    store.request_cancel.side_effect = CapacityError("private capacity detail")
    service = ReleaseService(ReleaseApiSettings(tmp_path), store_factory=lambda _root: store)
    with pytest.raises(ReleaseApiError) as error:
        service.cancel("localuser", "job")
    assert error.value.status_code == 507
    assert "private" not in error.value.detail


def test_given_inconsistent_execution_destination_when_result_read_then_refused(tmp_path: Path) -> None:
    from src.api.services.release_service import ReleaseApiError

    store = MagicMock(spec=ReleaseJobStore)
    store.get.return_value = SimpleNamespace(state="committed")
    store.storage = MagicMock()
    store.storage.snapshot.return_value = {
        "job": {
            "attempt_key": "package/attempt",
            "attempt_prefix": "published",
            "destination": str(tmp_path / "accepted"),
        }
    }
    planner = MagicMock(spec=ViewerReleasePlanner)
    planner.execution.return_value = SimpleNamespace(destination=tmp_path / "changed")
    service = ReleaseService(ReleaseApiSettings(tmp_path), store_factory=lambda _root: store, planner=planner)

    with pytest.raises(ReleaseApiError, match="configured_destination_changed") as error:
        service.result("localuser", "job")
    assert error.value.status_code == 409
    assert not (tmp_path / "changed").exists()


def test_given_saved_input_verification_failure_when_previewed_then_safe_error(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    client, spec, _settings, _app, _root = release_api
    monkeypatch.setattr(ViewerReleasePlanner, "prepare", AsyncMock(side_effect=SavedInputError("private detail")))
    response = client.get(BASE + "/preview", params={"selection": json.dumps(spec)})
    assert response.status_code == 422
    assert "private detail" not in response.text


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
    annotations = AnnotationService(base_path=str(root.parent))
    monkeypatch.setattr(planner_module, "get_annotation_service", lambda: annotations)
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


@pytest.mark.parametrize("name", ["CURATION_RELEASE_READ_METADATA_BYTES", "CURATION_RELEASE_READ_MEDIA_BYTES"])
@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_given_invalid_retained_read_limit_when_loading_settings_then_refused(name: str, value: str) -> None:
    from src.api.services.release_service import load_release_api_settings

    with pytest.raises(ValueError, match="positive integer"):
        load_release_api_settings({name: value})


def test_given_retained_read_overrides_when_loading_settings_then_applied() -> None:
    from src.api.services.release_service import load_release_api_settings

    settings = load_release_api_settings(
        {"CURATION_RELEASE_READ_METADATA_BYTES": "1024", "CURATION_RELEASE_READ_MEDIA_BYTES": "2048"}
    )
    assert settings.metadata_max_bytes == 1024
    assert settings.media_max_bytes == 2048


def test_given_blob_job_state_when_service_reopens_then_accepted_job_is_retained(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_release_jobs import _RecordContainer

    from src.api.services import release_service as service_module

    test_client, spec, settings, app, _root = release_api
    monkeypatch.setenv(
        "RELEASE_JOB_STORAGE",
        json.dumps(
            {"account_url": "https://example.blob.core.windows.net", "container": "state", "prefix": "releases"}
        ),
    )
    configured = service_module.load_release_api_settings(os.environ)
    assert configured.job_storage is not None
    container = _RecordContainer()
    monkeypatch.setattr(service_module, "_default_blob_container", lambda _location: container)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(
        configured, planner=ViewerReleasePlanner(configured)
    )
    accepted = _submit(test_client, spec, _preview(test_client, spec))
    assert accepted.status_code == 202, accepted.json()
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(configured)
    assert test_client.get(BASE + "/jobs/" + accepted.json()["job_id"]).json()["state"] == "accepted"
    assert not (settings.job_root / "jobs").exists()


def test_given_browser_release_request_when_preflight_then_idempotency_header_allowed(client: TestClient) -> None:
    from src.api.main import _config

    response = client.options(
        BASE + "/jobs",
        headers={
            "Origin": _config.cors_origins[0],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key,X-CSRF-Token,Content-Type",
        },
    )
    assert response.status_code == 200
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()
    response = client.get(BASE + "/capabilities", headers={"Origin": _config.cors_origins[0]})
    assert "location" in response.headers["access-control-expose-headers"].lower()


def test_preview_is_read_only_and_inventory_is_paged(release_api: tuple) -> None:
    test_client, spec, settings, _app, _root = release_api
    preview = _preview(test_client, spec)
    assert preview["summary"]["selected_count"] == 2
    assert preview["reserved_bytes"] >= (
        preview["summary"]["native_scratch_bytes"] + preview["summary"]["destination_budget_bytes"]
    )
    assert not settings.job_root.exists()
    assert not settings.destinations["local"].exists()
    first = test_client.get(BASE + "/inventory/native?limit=1").json()
    assert [item["episode_index"] for item in first["items"]] == [0]
    assert first["next_cursor"] == 1
    second = test_client.get(BASE + "/inventory/native?cursor=1&limit=1").json()
    assert [item["episode_index"] for item in second["items"]] == [1]


def test_given_saved_labels_when_inventory_read_then_exact_revisions_are_selectable(release_api: tuple) -> None:
    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
    from physical_ai_curation.release.inputs import SavedSelection

    from src.api.services.saved_input_service import ViewerSavedInputReader

    test_client, spec, _settings, _app, root = release_api
    labels = DatasetLabelsFile(
        dataset_id="native",
        episodes={"1": ["accepted"]},
        provenance={"1": {"schema_version": "1.0.0", "contributions": []}},
    )
    (root / "meta/episode_labels.json").write_text(labels.model_dump_json())
    standalone = LocalSavedInputReader(root)
    viewer = ViewerSavedInputReader(
        DatasetService(base_path=str(root.parent)),
        AnnotationService(base_path=str(root.parent)),
        LocalLabelStorage(root.parent),
    )
    for kind in ("labels", "ledger"):
        selection = SavedSelection(kind=kind, episode_index=1, expected_revision="pending")
        assert asyncio.run(standalone.release_resource("native", selection)) == asyncio.run(
            viewer.release_resource("native", selection)
        )
    inventory = test_client.get(BASE + "/inventory/native?limit=1&cursor=1").json()
    row = inventory["items"][0]
    assert row["labels"] == ["accepted"]
    assert row["resources"][0]["kind"] == "labels"
    preview = test_client.post(
        BASE + "/preview",
        json={
            **spec,
            "description": "Selected saved labels",
            "resources": row["resources"],
        },
    )
    assert preview.status_code == 200, preview.json()
    assert preview.json()["summary"]["destination_kind"] == "local"
    assert preview.json()["summary"]["available_bytes"] > 0
    labels.episodes["1"] = ["changed-after-preview"]
    (root / "meta/episode_labels.json").write_text(labels.model_dump_json())
    refused = _submit(
        test_client, {**spec, "description": "Selected saved labels", "resources": row["resources"]}, preview.json()
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "stale_preview"
    assert not (_settings.job_root / "jobs").exists()


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


def test_given_bound_judge_evidence_when_inventory_read_then_revision_is_selectable(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.adapters.job_storage import LocalJobStore
    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader, ScopedJudgeEvidenceAuthority
    from physical_ai_curation.evidence import bind_job_evidence
    from physical_ai_curation.release.inputs import SavedSelection
    from physical_ai_curation.saved_input import SavedInputError

    from tests.api.test_saved_judge_evidence_authority import _judge_job

    test_client, spec, settings, _app, root = release_api
    source = asyncio.run(DatasetService(base_path=str(root.parent)).get_source_revision("native", 1))
    job = _judge_job("judge-run", run_order=1, actor="localuser", episode=1, source=source)
    job["dataset_id"] = "native"
    target = job["targets"][0]
    target["result"]["episode_id"] = "native/episode_000001"
    target["evidence"] = bind_job_evidence(job, target, target["result"]).to_dict()
    store = LocalJobStore(settings.job_root.parent / "judge")

    async def persist() -> None:
        async with store.transaction() as state:
            state["jobs"][job["id"]] = job

    asyncio.run(persist())
    monkeypatch.setattr(ViewerReleasePlanner, "_judge_state", lambda _self: store)
    reader = LocalSavedInputReader(root)
    reader.judge_evidence = ScopedJudgeEvidenceAuthority(store, reader.source_revision, principal_scope_id="localuser")
    row = test_client.get(BASE + "/inventory/native?limit=1&cursor=1").json()["items"][0]
    assert row["judge"] == {"status": "succeeded", "freshness": "current", "confidence": 1.0, "outcome": "success"}
    assert any(resource["kind"] == "evidence" for resource in row["resources"])
    selected = SavedSelection(**next(item for item in row["resources"] if item["kind"] == "evidence"))
    standalone = asyncio.run(reader.release_resource("native", selected))
    assert standalone.value == {"job": job, "target": target}
    assert standalone.etag == selected.expected_revision
    selected_spec = {**spec, "resources": row["resources"]}
    preview = test_client.post(BASE + "/preview", json=selected_spec)
    assert preview.status_code == 200
    accepted = _submit(test_client, selected_spec, preview.json())
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    execution = parse_strict_json(ReleaseJobStore(settings.job_root).execution_binding(job_id))
    config = settings.job_root.parent / "worker.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "curated-release-worker/v1",
                "job_root": str(settings.job_root),
                "source_root": str(root),
                "destination_root": execution["effective_destination"]["path"],
                "selection": execution["selection"],
                "context": execution["context"],
                "finalized": True,
                "judge_state_root": str(store.root),
            }
        )
    )
    completed = subprocess.run(
        [sys.executable, "-m", "physical_ai_curation.release.runner", "--config", str(config), "--job", job_id],
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout.decode()
    assert test_client.get(BASE + "/jobs/" + job_id).json()["state"] == "committed"
    assert test_client.get(BASE + "/jobs/" + job_id + "/result").status_code == 200

    target["input"]["source_revision"] = "stale"
    target["evidence"] = bind_job_evidence(job, target, target["result"]).to_dict()
    asyncio.run(persist())
    with pytest.raises(SavedInputError, match="source revision changed"):
        asyncio.run(reader.release_resource("native", selected))
    row = test_client.get(BASE + "/inventory/native?limit=1&cursor=1").json()["items"][0]
    assert row["judge"]["status"] == "unavailable"
    assert not any(resource["kind"] == "evidence" for resource in row["resources"])
    assert test_client.post(BASE + "/preview", json=spec).status_code == 200


def test_given_headless_job_when_listing_then_source_filter_does_not_fail(release_api: tuple) -> None:
    from physical_ai_curation.release.jobs import JobRequest
    from physical_ai_curation.release.publication import PublicationAttempt

    test_client, _spec, settings, _app, _root = release_api
    store = ReleaseJobStore(settings.job_root)
    accepted = store.accept(
        JobRequest(
            scope="localuser",
            principal="localuser",
            idempotency_key="headless-job",
            attempt=PublicationAttempt("headless-package", "headless-attempt", "headless"),
            destination=settings.destinations["local"],
            frozen_request=b"{}",
            reserved_bytes=1,
        )
    )
    response = test_client.get(BASE + "/jobs")
    assert response.status_code == 200
    assert response.json()["items"][0]["job_id"] == accepted.job_id
    assert response.json()["items"][0]["dataset_id"] is None
    assert test_client.get(BASE + "/jobs?dataset_id=native").json()["items"] == []


def test_given_body_preview_when_requested_then_no_state_is_written(release_api: tuple) -> None:
    test_client, spec, settings, _app, _root = release_api
    response = test_client.post(BASE + "/preview", json=spec)
    assert response.status_code == 200, response.json()
    assert response.json()["digest"] == _preview(test_client, spec)["digest"]
    assert not settings.job_root.exists()
    assert not settings.destinations["local"].exists()


def test_given_lost_acceptance_when_source_removed_then_replay_uses_accepted_request(release_api: tuple) -> None:
    test_client, spec, settings, app, root = release_api
    preview = _preview(test_client, spec)
    accepted = _submit(test_client, spec, preview)
    assert accepted.status_code == 202
    root.rename(root.parent / "source-unavailable")
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    replay = _submit(test_client, spec, preview)
    assert replay.status_code == 202, replay.json()
    assert replay.json() == {"job_id": accepted.json()["job_id"], "replayed": True}
    conflict = _submit(test_client, {**spec, "episode_indices": [0]}, preview)
    assert conflict.status_code == 409


def test_given_accepted_request_when_lookup_then_only_owner_can_resolve(release_api: tuple) -> None:
    test_client, spec, _settings, app, _root = release_api
    accepted = _submit(test_client, spec, _preview(test_client, spec)).json()
    response = test_client.get(BASE + "/requests", headers={"Idempotency-Key": "release-key-1"})
    assert response.status_code == 200
    assert response.json()["job_id"] == accepted["job_id"]
    assert test_client.get(BASE + "/jobs?dataset_id=another-source").json()["items"] == []
    app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(scope_id="other", auth_mode="local")
    assert test_client.get(BASE + "/requests", headers={"Idempotency-Key": "release-key-1"}).status_code == 404


def test_nested_source_acceptance_binds_collection_and_survives_alias_change(release_api: tuple) -> None:
    test_client, spec, settings, _app, root = release_api
    group = root.parent / "group-a"
    group.mkdir()
    root.rename(group / root.name)
    spec = {**spec, "dataset_id": "group-a--native"}
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    record = store.storage.snapshot()[job]
    expected = settings.destinations["local"] / "group-a/native/releases"

    assert record["destination"] == str(expected.resolve())
    binding = json.loads(store.execution_binding(job))
    assert binding["schema"] == "curated-release-execution/v3"
    assert binding["effective_destination"] == {"kind": "local", "path": str(expected.resolve())}
    intent = store.source_association(job)
    assert intent is not None
    assert intent.source_relative_path == "group-a/native"
    assert intent.source_identity == binding["context"]["source"][0]
    assert intent.source_revision == binding["context"]["source"][1]
    assert intent.scope_sha256 == record["principal_digest"]
    assert intent.effective_destination == str(expected.resolve())
    assert intent.attempt.package_id == spec["package_id"]
    assert "accepted_at" not in binding["association"]
    changed = ReleaseApiSettings(settings.job_root, {"local": root.parent / "different-output"})
    execution = ViewerReleasePlanner(changed).execution(job, store.frozen_request(job))
    assert execution.destination == expected.resolve()
    assert not expected.exists()


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


def test_given_accepted_viewer_job_when_runner_executes_then_association_is_retained(release_api: tuple) -> None:
    test_client, spec, settings, _app, root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    execution = ViewerReleasePlanner(settings).execution(job, store.frozen_request(job))

    result = asyncio.run(execute_job(store, job, execution))

    assert result is not None
    assert result.publication_state == "published"
    root.rename(root.parent / "source-unavailable")
    from physical_ai_curation.release.record_storage import RecordLimits
    from physical_ai_curation.release.source_associations import LocalSourceAssociations

    intent = store.source_association(job)
    associations = LocalSourceAssociations(execution.destination, limits=RecordLimits())
    assert associations.discover(source_key=intent.source_key, scope_sha256=intent.scope_sha256) == [
        execution.destination / spec["dataset_name"]
    ]


def test_fresh_process_worker_publishes_and_artifacts_reverify(release_api: tuple) -> None:
    test_client, spec, settings, _app, source_root = release_api
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
    published = settings.destinations["local"] / "native/releases" / spec["dataset_name"]
    artifact_contents = {}
    for artifact in result.json()["artifacts"]:
        response = test_client.get(BASE + "/jobs/" + job + "/artifacts/" + artifact)
        assert response.status_code == 200
        content = (published / "release" / (artifact + (".md" if artifact == "report" else ".json"))).read_bytes()
        assert response.json()["artifact"] == artifact
        assert response.json()["content"] == (content.decode() if artifact == "report" else json.loads(content))
        artifact_contents[artifact] = response.json()
    source_root.rename(source_root.with_name("removed-source"))
    retained = test_client.get(BASE + "/jobs/" + job + "/result")
    assert retained.status_code == 200, retained.json()
    assert retained.json() == result.json()
    for artifact, expected in artifact_contents.items():
        response = test_client.get(BASE + "/jobs/" + job + "/artifacts/" + artifact)
        assert response.status_code == 200
        assert response.json() == expected
    assert test_client.get(BASE + "/jobs/" + job + "/artifacts/not-allowed").status_code == 404
    (published / "release/marker.json").unlink()
    assert test_client.get(BASE + "/jobs/" + job + "/result").status_code == 409
    assert test_client.get(BASE + "/jobs/" + job + "/artifacts/manifest").status_code == 409


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


SOURCE = BASE + "/sources/native/releases"


def _publish_local(test_client: TestClient, spec: dict, settings: ReleaseApiSettings) -> tuple[str, Path]:
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    execution = ViewerReleasePlanner(settings).execution(job, store.frozen_request(job))
    result = asyncio.run(execute_job(store, job, execution))
    assert result is not None and result.publication_state == "published"
    return job, settings.destinations["local"] / "native/releases" / spec["dataset_name"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_release_is_listed_and_read_after_job_and_source_removal(release_api: tuple) -> None:
    test_client, spec, settings, _app, root = release_api
    _job, published = _publish_local(test_client, spec, settings)
    expected_episodes = json.loads((published / "meta/info.json").read_bytes())["total_episodes"]
    assert expected_episodes == 2

    listing = test_client.get(SOURCE)
    assert listing.status_code == 200, listing.json()
    items = listing.json()["items"]
    assert [item["dataset_name"] for item in items] == [spec["dataset_name"]]
    assert items[0]["manifest_sha256"] == _sha256(published / "release/manifest.json")
    assert test_client.get(BASE + "/sources/other/releases").json()["items"] == []
    handle = items[0]["handle"]

    shutil.rmtree(settings.job_root)
    root.rename(root.parent / "source-unavailable")

    read = test_client.get(f"{SOURCE}/{handle}")
    assert read.status_code == 200, read.json()
    body = read.json()
    assert body["total_episodes"] == expected_episodes
    assert body["manifest_sha256"] == items[0]["manifest_sha256"]
    frozen_name = json.loads((published / "release/manifest.json").read_bytes())["frozen_input_path"]
    assert body["frozen_evidence"] == json.loads((published / frozen_name).read_bytes())
    assert str(settings.destinations["local"]) not in read.text
    assert test_client.get(SOURCE).json()["items"] == items
    assert test_client.get(f"{SOURCE}/{'0' * 64}").status_code == 404


def test_source_release_is_hidden_from_other_principal(release_api: tuple) -> None:
    test_client, spec, settings, app, _root = release_api
    _publish_local(test_client, spec, settings)
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(scope_id="other", auth_mode="local")

    assert test_client.get(SOURCE).json()["items"] == []
    assert test_client.get(f"{SOURCE}/{handle}").status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_source_release_handle_refuses_mutation(release_api: tuple, method: str) -> None:
    test_client, spec, settings, _app, _root = release_api
    _job, published = _publish_local(test_client, spec, settings)
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    before = _sha256(published / "release/manifest.json")

    response = getattr(test_client, method)(f"{SOURCE}/{handle}")

    assert response.status_code == 405
    assert _sha256(published / "release/manifest.json") == before
    assert test_client.get(f"{SOURCE}/{handle}").status_code == 200


@pytest.mark.parametrize("target", ["marker", "manifest", "frozen"])
def test_tampered_source_release_is_hidden_and_restores_when_bytes_return(release_api: tuple, target: str) -> None:
    test_client, spec, settings, _app, _root = release_api
    _job, published = _publish_local(test_client, spec, settings)
    listing = test_client.get(SOURCE).json()["items"]
    handle = listing[0]["handle"]
    manifest_path = published / "release/manifest.json"
    frozen_path = published / json.loads(manifest_path.read_bytes())["frozen_input_path"]
    path = {
        "marker": published / "release/marker.json",
        "manifest": manifest_path,
        "frozen": frozen_path,
    }[target]
    original = path.read_bytes()

    path.write_bytes(original + b" ")
    assert test_client.get(SOURCE).json()["items"] == []
    assert test_client.get(f"{SOURCE}/{handle}").status_code == 404

    path.write_bytes(original)
    assert test_client.get(SOURCE).json()["items"] == listing
    assert test_client.get(f"{SOURCE}/{handle}").status_code == 200


SOURCE_EPISODES = SOURCE


def test_source_episodes_are_paged_and_media_is_served_without_paths(release_api: tuple) -> None:
    test_client, spec, settings, _app, _root = release_api
    _job, published = _publish_local(test_client, spec, settings)
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]

    page = test_client.get(f"{SOURCE}/{handle}/episodes", params={"limit": 1})
    assert page.status_code == 200, page.json()
    body = page.json()
    assert body["total"] == 2
    assert body["next_cursor"] == 1
    assert [item["episode_index"] for item in body["items"]] == [0]
    assert body["items"][0]["cameras"] == [CAMERA]
    assert str(published) not in page.text
    assert str(settings.destinations["local"]) not in page.text

    second = test_client.get(f"{SOURCE}/{handle}/episodes", params={"cursor": 1, "limit": 1}).json()
    assert [item["episode_index"] for item in second["items"]] == [1]
    assert second["next_cursor"] is None

    media = test_client.get(f"{SOURCE}/{handle}/episodes/0/media/{CAMERA}")
    assert media.status_code == 200
    assert media.headers["content-type"] == "video/mp4"
    assert media.content == (published / f"videos/{CAMERA}/chunk-000/file-000.mp4").read_bytes()


def test_source_episode_reads_refuse_unknown_episode_camera_and_bad_pages(release_api: tuple) -> None:
    test_client, spec, settings, _app, _root = release_api
    _publish_local(test_client, spec, settings)
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]

    assert test_client.get(f"{SOURCE}/{handle}/episodes", params={"cursor": -1}).status_code == 422
    assert test_client.get(f"{SOURCE}/{handle}/episodes", params={"limit": 101}).status_code == 422
    missing_episode = test_client.get(f"{SOURCE}/{handle}/episodes/99/media/{CAMERA}")
    assert missing_episode.status_code == 404
    assert missing_episode.json()["detail"]["code"] == "release_episode_not_found"
    missing_camera = test_client.get(f"{SOURCE}/{handle}/episodes/0/media/observation.images.absent")
    assert missing_camera.status_code == 404
    assert missing_camera.json()["detail"]["code"] == "release_media_not_found"
    assert test_client.get(f"{SOURCE}/{handle}/episodes/0/media/..").status_code == 404
    assert test_client.get(f"{SOURCE}/{'0' * 64}/episodes").status_code == 404


def test_given_verified_projection_when_browsed_then_output_coordinates_not_frozen_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "published"
    (root / "meta").mkdir(parents=True)
    (root / "annotations/episodes").mkdir(parents=True)
    annotation = {
        "schema_version": "1.0.0",
        "dataset_id": "published",
        "output_episode_index": 0,
        "source_episode_id": "native/episode_000017",
        "annotations": [{"object_detections": [{"frame_index": 2}]}],
    }
    labels = {"dataset_id": "published", "episode_labels": {"0": ["accepted"]}}
    frozen = {"dataset_id": "native", "episodes": [{"source_episode_id": "native/episode_000017", "frame_index": 2}]}
    (root / "meta/info.json").write_bytes(canonical_json_bytes({"total_episodes": 1}))
    (root / "meta/episode_labels.json").write_bytes(canonical_json_bytes(labels))
    (root / "annotations/episodes/episode_000000.json").write_bytes(canonical_json_bytes(annotation))
    (root / "evidence").mkdir()
    (root / "evidence/frozen-input.json").write_bytes(canonical_json_bytes(frozen))
    entries = [
        {"path": path.relative_to(root).as_posix(), "length": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(root.rglob("*.json"))
    ]
    (root / "release").mkdir()
    inventory = canonical_json_bytes({"schema_version": "1.0.0", "entries": entries})
    (root / "release/inventory.json").write_bytes(inventory)
    manifest = canonical_json_bytes(
        {"inventory_sha256": hashlib.sha256(inventory).hexdigest(), "frozen_input_path": "evidence/frozen-input.json"}
    )
    (root / "release/manifest.json").write_bytes(manifest)
    item = _source_release(
        "local",
        "native",
        "0" * 64,
        dataset_name="published",
        package_id="package",
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        store=LocalReleaseStore(root),
        local_root=root,
    )
    service = ReleaseService(ReleaseApiSettings(None, {}))
    monkeypatch.setattr(service, "_find_source", lambda *_: item)

    body = service.source_release("scope", "native", item.handle)

    assert body["annotations"] == [annotation]
    assert body["labels"] == labels
    assert body["frozen_evidence"] == frozen
    assert body["frozen_evidence"] not in body["annotations"]


class _FakeBlobItem:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self.size = len(data)
        self.etag = hashlib.sha256(data).hexdigest()
        self.copy = None


class _FakeBlobProperties:
    def __init__(self, item: _FakeBlobItem) -> None:
        self.size = item.size
        self.etag = item.etag
        self.copy = None


class _FakeBlobDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def chunks(self) -> Iterator[bytes]:
        yield self._data


class _FakeBlobClient:
    def __init__(self, container: FakeBlobContainer, name: str) -> None:
        self._container = container
        self._name = name

    def get_blob_properties(self) -> _FakeBlobProperties:
        from azure.core.exceptions import ResourceNotFoundError

        data = self._container.objects.get(self._name)
        if data is None:
            raise ResourceNotFoundError("missing")
        return _FakeBlobProperties(_FakeBlobItem(self._name, data))

    def download_blob(self, etag: str | None = None, match_condition: object = None, **_: object) -> _FakeBlobDownload:
        return _FakeBlobDownload(self._container.objects[self._name])


class FakeBlobContainer:
    """In-memory container exposing only the read operations used by release association and store reads."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_blobs(self, name_starts_with: str = "", include: list[str] | None = None) -> list[_FakeBlobItem]:
        return [
            _FakeBlobItem(name, data)
            for name, data in sorted(self.objects.items())
            if name.startswith(name_starts_with)
        ]

    def get_blob_client(self, name: str, *, version_id: str | None = None) -> _FakeBlobClient:
        assert version_id is None
        return _FakeBlobClient(self, name)


class _UnavailableBlobContainer:
    def list_blobs(self, name_starts_with: str = "", include: list[str] | None = None) -> list[_FakeBlobItem]:
        from azure.core.exceptions import ServiceRequestError

        raise ServiceRequestError("container unavailable")


BLOB_ACCOUNT = "https://releases.blob.core.windows.net"
BLOB_PREFIX = "native/releases"


def _mirror_publication(local_collection: Path, container: FakeBlobContainer, destination: BlobLocation) -> None:
    """Copy one verified local collection into the fake container, rewriting only the destination intent."""
    collection_text = release_collection_location(destination, "native").as_text()
    for path in sorted(local_collection.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(local_collection).as_posix()
        data = path.read_bytes()
        if relative.startswith("_release_associations/"):
            record = parse_strict_json(data)
            record["intent"]["effective_destination"] = collection_text
            record["intent"]["destination_alias"] = "remote"
            data = canonical_json_bytes(record)
        container.objects[f"{BLOB_PREFIX}/{relative}"] = data


@pytest.fixture
def blob_source(release_api: tuple) -> Iterator[tuple]:
    test_client, spec, settings, app, root = release_api
    _job, _published = _publish_local(test_client, spec, settings)
    destination = BlobLocation(BLOB_ACCOUNT, "releases")
    container = FakeBlobContainer()
    _mirror_publication(settings.destinations["local"] / "native/releases", container, destination)
    service = ReleaseService(
        ReleaseApiSettings(settings.job_root, {"remote": destination}),
        blob_container_factory=lambda location: container,
    )
    app.dependency_overrides[get_release_service] = lambda: service
    yield test_client, spec, settings, container, root


def test_blob_release_reads_match_local_contract_and_survive_source_removal(blob_source: tuple) -> None:
    test_client, spec, settings, _container, root = blob_source
    listing = test_client.get(SOURCE)
    assert listing.status_code == 200, listing.json()
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["dataset_name"] == spec["dataset_name"]
    handle = items[0]["handle"]

    read = test_client.get(f"{SOURCE}/{handle}")
    assert read.status_code == 200, read.json()
    assert read.json()["total_episodes"] == 2
    assert read.json()["annotations"] == []
    assert "blob.core.windows.net" not in read.text
    assert "remote" not in read.text

    shutil.rmtree(settings.job_root)
    root.rename(root.parent / "source-removed")
    retained = test_client.get(f"{SOURCE}/{handle}")
    assert retained.status_code == 200
    assert retained.json() == read.json()


def test_blob_episode_reads_match_local_contract_and_survive_source_removal(blob_source: tuple) -> None:
    test_client, _spec, settings, container, root = blob_source
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]

    page = test_client.get(f"{SOURCE}/{handle}/episodes", params={"limit": 1})
    assert page.status_code == 200, page.json()
    body = page.json()
    assert body["total"] == 2
    assert body["next_cursor"] == 1
    assert [item["episode_index"] for item in body["items"]] == [0]
    assert body["items"][0]["cameras"] == [CAMERA]
    assert "blob.core.windows.net" not in page.text

    full = test_client.get(f"{SOURCE}/{handle}/episodes", params={"limit": 100})
    assert full.status_code == 200, full.json()
    assert [item["episode_index"] for item in full.json()["items"]] == [0, 1]

    media = test_client.get(f"{SOURCE}/{handle}/episodes/0/media/{CAMERA}")
    assert media.status_code == 200, media.json()
    assert media.headers["content-type"] == "video/mp4"
    assert media.content == container.objects[f"{BLOB_PREFIX}/published/videos/{CAMERA}/chunk-000/file-000.mp4"]

    shutil.rmtree(settings.job_root)
    root.rename(root.parent / "source-removed")
    retained_full = test_client.get(f"{SOURCE}/{handle}/episodes", params={"limit": 100})
    assert retained_full.status_code == 200
    assert retained_full.json() == full.json()
    retained_media = test_client.get(f"{SOURCE}/{handle}/episodes/0/media/{CAMERA}")
    assert retained_media.status_code == 200
    assert retained_media.content == media.content


@pytest.mark.parametrize("remote", [False, True])
def test_given_selected_curation_when_released_then_retained_output_projections_are_immutable(
    release_api: tuple, remote: bool
) -> None:
    test_client, spec, settings, app, root = release_api
    labels = DatasetLabelsFile(dataset_id="native", episodes={"1": ["accepted"], "99": ["excluded"]})
    labels_etag = asyncio.run(LocalLabelStorage(str(root.parent)).save("native", labels, if_none_match=True))
    annotation_path = root / "annotations/episodes/episode_000001.json"
    annotation = EpisodeAnnotationFile.model_validate(
        {
            "dataset_id": "native",
            "episode_index": 1,
            "annotations": [
                {
                    "annotator_id": "localuser",
                    "timestamp": "2026-11-02T00:00:00Z",
                    "task_completeness": {"rating": "success", "confidence": 5},
                    "trajectory_quality": {
                        "overall_score": 5,
                        "metrics": {"smoothness": 5, "efficiency": 5, "safety": 5, "precision": 5},
                    },
                    "data_quality": {"overall_quality": "good", "issues": []},
                    "anomalies": {"anomalies": []},
                }
            ],
        }
    )
    annotation_etag = write_conditional(annotation_path, annotation.model_dump_json(), if_none_match=True)
    selected = {
        **spec,
        "episode_indices": [1],
        "resources": [
            {"kind": "labels", "episode_index": 1, "expected_revision": labels_etag},
            {"kind": "annotation", "episode_index": 1, "expected_revision": annotation_etag},
        ],
    }
    baseline = _preview(test_client, {**spec, "episode_indices": [1]})
    preview = _preview(test_client, selected)
    summary = preview["summary"]
    assert summary["projection_objects"] == 2
    assert summary["projection_bytes"] > 0
    assert summary["capacity"]["generated_bytes"] == (
        baseline["summary"]["capacity"]["generated_bytes"] + summary["projection_bytes"]
    )
    assert preview["reserved_bytes"] >= summary["destination_budget_bytes"] + summary["native_scratch_bytes"]
    _job, published = _publish_local(test_client, selected, settings)
    frozen_bytes = (published / "evidence/frozen-input.json").read_bytes()
    assert b"labels/episode_000001" in frozen_bytes
    assert b'"output_episode_index":0' in (published / "annotations/episodes/episode_000000.json").read_bytes()
    if remote:
        destination = BlobLocation(BLOB_ACCOUNT, "releases")
        container = FakeBlobContainer()
        _mirror_publication(settings.destinations["local"] / "native/releases", container, destination)
        service = ReleaseService(
            ReleaseApiSettings(settings.job_root, {"remote": destination}),
            blob_container_factory=lambda location: container,
        )
        app.dependency_overrides[get_release_service] = lambda: service
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    before = test_client.get(f"{SOURCE}/{handle}").json()
    assert before["labels"]["dataset_id"] == "published"
    assert before["labels"]["episodes"] == {"0": ["accepted"]}
    assert len(before["annotations"]) == 1
    assert before["annotations"][0]["output_episode_index"] == 0
    assert before["annotations"][0]["source_episode_id"].endswith("episode_000001")
    shutil.rmtree(settings.job_root)
    root.rename(root.parent / "source-removed")
    retained = test_client.get(f"{SOURCE}/{handle}")
    assert retained.status_code == 200, retained.json()
    assert retained.json() == before
    for method in ("post", "put", "patch", "delete"):
        assert test_client.request(method.upper(), f"{SOURCE}/{handle}", json={}).status_code == 405
    assert (published / "evidence/frozen-input.json").read_bytes() == frozen_bytes


def test_given_blob_bytes_change_after_admission_when_read_then_inventoried_hash_refuses_response(
    blob_source: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _spec, _settings, container, _root = blob_source
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    service = test_client.app.dependency_overrides[get_release_service]()
    original = service._find_source

    def admit_then_change(*args: object) -> object:
        item = original(*args)
        name = f"{BLOB_PREFIX}/published/meta/info.json"
        changed = json.loads(container.objects[name])
        changed["total_episodes"] = 3
        container.objects[name] = canonical_json_bytes(changed)
        return item

    monkeypatch.setattr(service, "_find_source", admit_then_change)
    response = test_client.get(f"{SOURCE}/{handle}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "release_verification_failed"


@pytest.mark.parametrize("view", ["metadata", "episodes", "media"])
def test_given_blob_revision_read_failure_after_admission_when_browsing_then_safe_verification_error(
    blob_source: tuple, monkeypatch: pytest.MonkeyPatch, view: str
) -> None:
    from azure.core.exceptions import HttpResponseError

    test_client, _spec, _settings, _container, _root = blob_source
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    service = test_client.app.dependency_overrides[get_release_service]()
    original = service._find_source

    def refuse_read(*_args: object, **_kwargs: object) -> bytes:
        raise HttpResponseError("private-account endpoint and provider failure")

    def admit_then_fail(*args: object) -> object:
        item = original(*args)
        monkeypatch.setattr(item.store, "read_control_bytes", refuse_read)
        return item

    monkeypatch.setattr(service, "_find_source", admit_then_fail)
    suffix = {"metadata": "", "episodes": "/episodes", "media": f"/episodes/0/media/{CAMERA}"}[view]
    response = test_client.get(f"{SOURCE}/{handle}{suffix}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "release_verification_failed"
    assert "private-account" not in response.text


def test_blob_tampered_publication_is_hidden_and_listing_failure_is_explicit(blob_source: tuple) -> None:
    test_client, _spec, settings, container, _root = blob_source
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    frozen_name = f"{BLOB_PREFIX}/published/evidence/frozen-input.json"
    original = container.objects[frozen_name]

    container.objects[frozen_name] = original + b" "
    assert test_client.get(SOURCE).json()["items"] == []
    assert test_client.get(f"{SOURCE}/{handle}").status_code == 404

    container.objects[frozen_name] = original
    assert test_client.get(f"{SOURCE}/{handle}").status_code == 200

    service = ReleaseService(
        ReleaseApiSettings(settings.job_root, {"remote": BlobLocation(BLOB_ACCOUNT, "releases")}),
        blob_container_factory=lambda location: _UnavailableBlobContainer(),
    )
    test_client.app.dependency_overrides[get_release_service] = lambda: service
    unavailable = test_client.get(SOURCE)
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "release_verification_failed"
