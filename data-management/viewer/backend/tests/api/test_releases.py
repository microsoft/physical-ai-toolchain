"""Release API contract tests for the curated dataset release surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import runpy
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
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
from src.api.services.release_service import (
    ReleaseApiError,
    ReleaseApiSettings,
    ReleaseService,
    _source_release,
    get_release_service,
    load_release_api_settings,
)
from src.api.services.viewer_release_planner import ViewerReleasePlanner
from src.api.storage import VersionedValue
from src.api.storage.local_revision import write_conditional
from tests.lerobot_sources import CAMERA, write_source

BASE = "/api/releases/v1"


async def test_given_selected_intent_when_previewed_and_admitted_then_no_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.services.release_service import ReleaseSpec

    settings = ReleaseApiSettings(tmp_path / "jobs", destinations={"output": tmp_path / "output"})
    planner = ViewerReleasePlanner(settings)
    forbidden = AsyncMock(side_effect=AssertionError("preview/admission read the source"))
    monkeypatch.setattr(planner.datasets, "get_dataset", forbidden)
    monkeypatch.setattr("src.api.services.viewer_release_planner.freeze_release_inputs", forbidden)
    service = ReleaseService(settings, planner=planner)
    spec = ReleaseSpec("output", "package", "attempt", "release", "dataset", (0,), finalized=True)

    preview = await service.preview("localuser", spec)
    accepted = await service.submit(
        "localuser",
        spec,
        "intent-key",
        preview_digest=preview["digest"],
        processing_digest="",
        local_disk_acknowledged=True,
    )
    reopened = ReleaseJobStore(settings.job_root)
    payload = parse_strict_json(reopened.frozen_request(accepted.job_id))
    assert preview["summary"]["advisory"] is True
    assert preview["reserved_bytes"] == 0
    assert payload["schema_version"] == "curated-release-intent/v1"
    assert "frozen" not in payload
    forbidden.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["api", "worker"])
@pytest.mark.parametrize(
    ("settings", "app_info", "azure_info", "azure_debug", "invalid"),
    [
        ({}, True, False, False, False),
        ({"LOG_LEVEL": "warning", "AZURE_LOG_LEVEL": "info"}, False, True, False, False),
        ({"LOG_LEVEL": "debug", "AZURE_LOG_LEVEL": "debug"}, True, True, True, False),
        ({"LOG_LEVEL": "trace"}, True, False, False, False),
        ({"LOG_LEVEL": "invalid-private-value"}, False, False, False, True),
        ({"AZURE_LOG_LEVEL": "invalid-private-value"}, False, False, False, True),
    ],
)
def test_given_logging_settings_when_process_starts_then_levels_apply_after_dotenv(
    entrypoint: str,
    settings: dict[str, str],
    app_info: bool,
    azure_info: bool,
    azure_debug: bool,
    invalid: bool,
) -> None:
    environment = {
        **os.environ,
        "PYTHON_DOTENV_DISABLED": "1",
        "STORAGE_BACKEND": "local",
        "VLM_JUDGE_ENABLED": "false",
        "RELEASE_JOB_ROOT": "",
        "RELEASE_JOB_STORAGE": "",
        "RELEASE_DESTINATIONS": "",
    }
    environment.pop("LOG_LEVEL", None)
    environment.pop("AZURE_LOG_LEVEL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import logging
import os
import sys
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: os.environ.update(json.loads(sys.argv[2]))
logging.basicConfig(level=logging.WARNING)
original_handlers = tuple(logging.getLogger().handlers)
try:
    if sys.argv[1] == "worker":
        from src.api.release_worker import main
        assert main(["--once"]) == 2
        app_logger = logging.getLogger("release_worker")
    else:
        import src.api.main
        app_logger = logging.getLogger("src.api.main")
except ValueError as error:
    print(str(error), file=sys.stderr)
    sys.exit(2)
assert tuple(logging.getLogger().handlers) == original_handlers
app_logger.info("APP_INFO_MARKER")
azure_logger = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
azure_logger.info("AZURE_INFO_MARKER")
azure_logger.debug("AZURE_DEBUG_MARKER")
azure_logger.warning("AZURE_WARNING_MARKER")
""",
            entrypoint,
            json.dumps(settings),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "invalid-private-value" not in result.stderr
    if invalid:
        assert result.returncode == 2
        assert "AZURE_WARNING_MARKER" not in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert ("APP_INFO_MARKER" in result.stderr) is app_info
        assert ("AZURE_INFO_MARKER" in result.stderr) is azure_info
        assert ("AZURE_DEBUG_MARKER" in result.stderr) is azure_debug
        assert "AZURE_WARNING_MARKER" in result.stderr


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

    async def fail_queue(*args: object, **kwargs: object) -> tuple:
        raise ReleaseApiError(503, "private provider detail")

    monkeypatch.setattr(release_worker, "load_release_api_settings", lambda _environ: ReleaseApiSettings(tmp_path))
    monkeypatch.setattr(release_worker, "run_queue", fail_queue)
    assert release_worker.main(["--once"]) == 2
    assert "release worker pass failed" in caplog.text
    assert "private provider detail" not in caplog.text


@pytest.mark.parametrize("escalate", [False, True])
def test_given_active_worker_when_supervisor_cancelled_then_process_reaped(
    monkeypatch: pytest.MonkeyPatch, escalate: bool
) -> None:
    from src.api import release_worker

    process = MagicMock(returncode=None)
    process.wait = AsyncMock(
        side_effect=[asyncio.CancelledError(), TimeoutError(), 0] if escalate else [asyncio.CancelledError(), 0]
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(release_worker.asyncio, "create_subprocess_exec", spawn)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(release_worker._supervise_worker(3, 10))

    process.terminate.assert_called_once()
    assert process.kill.call_count == int(escalate)
    assert process.wait.await_count == (3 if escalate else 2)


def test_given_worker_spawn_failure_when_supervised_then_retries_after_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import release_worker

    process = MagicMock(returncode=0)
    process.wait = AsyncMock(side_effect=asyncio.CancelledError())
    spawn = AsyncMock(side_effect=[OSError("unavailable"), process])
    delay = AsyncMock()
    monkeypatch.setattr(release_worker.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(release_worker.asyncio, "sleep", delay)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(release_worker._supervise_worker(3, 10))

    assert spawn.await_count == 2
    delay.assert_awaited_once_with(3)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "301", "invalid"])
def test_given_invalid_worker_timing_when_starting_then_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from src.api.release_worker import _worker_seconds

    monkeypatch.setenv("RELEASE_WORKER_POLL_SECONDS", value)
    with pytest.raises(ValueError):
        _worker_seconds("RELEASE_WORKER_POLL_SECONDS", "3")


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
    from src.api.services.release_service import ReleaseSpec

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

    service = ReleaseService(ReleaseApiSettings(tmp_path / "jobs"))
    with pytest.raises(ReleaseApiError) as error:
        getattr(service, operation)("localuser", "0" * 32)
    assert error.value.status_code == 404


@pytest.mark.parametrize("cursor,limit", [(-1, 10), (0, 0), (0, 101)])
def test_given_invalid_inventory_page_when_requested_then_refused(cursor: int, limit: int) -> None:

    service = ReleaseService(ReleaseApiSettings(None))
    with pytest.raises(ReleaseApiError) as error:
        asyncio.run(service.inventory("native", cursor=cursor, limit=limit))
    assert error.value.status_code == 422


def test_given_unconfigured_service_when_operated_then_safe_unavailability(tmp_path: Path) -> None:
    from src.api.services.release_service import ReleaseSpec, open_release_job_store

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

    test_client, spec, settings, _app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    intent = store.frozen_request(job)
    capture = ViewerReleasePlanner(settings).execution(job, intent).capture
    assert capture is not None
    generated, binding_bytes, reserved, _execution = asyncio.run(capture(lambda: None))
    fence = store.claim(job, "binding-test")
    store.confirm_stopped(job, fence)
    store.capture_execution(
        job,
        fence,
        intent_request=intent,
        frozen_request=generated,
        execution_binding=binding_bytes,
        reserved_bytes=reserved,
    )
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


@pytest.mark.parametrize("change", ["scope", "selection", "destination", "relative", "kind", "source", "consent"])
def test_given_tampered_intent_when_restored_then_refused(release_api: tuple, change: str) -> None:
    client, spec, settings, _app, _root = release_api
    job = _submit(client, spec, _preview(client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    intent = parse_strict_json(store.frozen_request(job))
    if change == "scope":
        intent["scope"] = "foreign"
    elif change == "selection":
        intent["spec"]["episode_indices"] = [0]
    elif change in {"destination", "relative"}:
        intent["effective_destination"]["path"] = "relative" if change == "relative" else str(settings.job_root)
    elif change == "kind":
        intent["effective_destination"]["kind"] = "unsupported"
    elif change == "source":
        intent["source_kind"] = "unsupported"
    else:
        intent["processing_confirmed"] = "true"
    with pytest.raises(ReleaseApiError) as error:
        ViewerReleasePlanner(settings).execution(job, canonical_json_bytes(intent))
    assert error.value.status_code == 409


def test_given_required_saved_processing_without_consent_when_captured_then_no_video_effects(
    release_api: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from physical_ai_curation.release.jobs import ReleaseRefusal
    from physical_ai_curation.release.runner import run_queue

    from src.api.models.episode_edits import EpisodeEditOperations

    client, spec, settings, _app, _root = release_api
    planner = ViewerReleasePlanner(settings)
    source_id, revision = asyncio.run(planner.datasets.get_source_revision("native", 0))
    saved = asyncio.run(
        planner._annotations.save_edits(
            "native",
            0,
            EpisodeEditOperations.model_validate(
                {
                    "datasetId": "native",
                    "episodeIndex": 0,
                    "globalTransform": {"colorFilter": "invert"},
                }
            ),
            author_id="localuser",
            source_id=source_id,
            source_revision=revision,
            frame_count=2,
            cameras={"observation.images.front"},
            if_none_match=True,
        )
    )
    selected = {**spec, "resources": [{"kind": "edits", "episode_index": 0, "expected_revision": saved.etag}]}
    preview = _preview(client, selected)
    preview["summary"]["processing_digest"] = ""
    accepted = _submit(client, selected, preview)
    assert accepted.status_code == 202
    job = accepted.json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    forbidden = MagicMock(side_effect=AssertionError("unapproved video processing"))
    monkeypatch.setattr("physical_ai_curation.release.coordinator._encoded_files", forbidden)
    capture = planner.execution(job, store.frozen_request(job)).capture
    assert capture is not None
    with pytest.raises(ReleaseRefusal, match="processing_confirmation_required"):
        asyncio.run(capture(lambda: None))
    forbidden.assert_not_called()
    assert store.storage.snapshot()[job]["effects"] == {}
    assert parse_strict_json(store.frozen_request(job))["schema_version"] == "curated-release-intent/v1"
    asyncio.run(run_queue(store, planner.execution))
    status = client.get(BASE + "/jobs/" + job).json()
    assert status["state"] == "needs_input"
    assert status["refusal_code"] == "processing_confirmation_required"
    assert not status["recovery_required"]
    assert asyncio.run(run_queue(store, planner.execution)) == ()


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
        monkeypatch.setattr(planner, "reader_for_scope", lambda _scope, **_options: reader)
        expected = (409, "saved_inventory_unavailable")
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings, planner=planner)

    url = BASE + ("/inventory/native" if failure == "oversized" else "/inventory/native/details?episodes=0")
    response = test_client.get(url)

    assert response.status_code == expected[0]
    assert response.json()["detail"]["code"] == expected[1]
    assert "private detail" not in response.text


def test_given_population_page_when_read_then_detail_evidence_is_not_loaded(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _spec, settings, app, _root = release_api
    planner = ViewerReleasePlanner(settings)
    reader = planner.reader_for_scope("localuser", inventory=True)

    async def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("detail evidence read on population page")

    monkeypatch.setattr(reader, "annotation", forbidden)
    monkeypatch.setattr(reader, "edits", forbidden)
    monkeypatch.setattr(reader, "source_revision", forbidden)
    monkeypatch.setattr(planner, "reader_for_scope", lambda _scope, **_options: reader)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings, planner=planner)

    page = test_client.get(BASE + "/inventory/native?limit=2").json()

    assert [item["detailed"] for item in page["items"]] == [False, False]
    assert all(item["annotation"] is None and item["judge"] is None for item in page["items"])


@pytest.mark.parametrize("episodes", ["", "x", "0,0", "-1", "999"])
def test_given_invalid_detail_request_when_read_then_refused(release_api: tuple, episodes: str) -> None:
    test_client, _spec, _settings, _app, _root = release_api

    response = test_client.get(BASE + "/inventory/native/details", params={"episodes": episodes})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_inventory_details"


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

    from src.api.services.release_service import _release_files

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


@pytest.mark.parametrize("assurance", ["storage_revision", "uploaded_content"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("replaced", [False, True])
def test_given_retained_blob_when_read_then_uses_admitted_revision(
    tmp_path: Path, assurance: str, stream: bool, replaced: bool
) -> None:
    from physical_ai_curation.release.azure_publication import BlobReleaseStore
    from physical_ai_curation.release.errors import ReleaseVerificationError
    from physical_ai_curation.release.models import INVENTORY_PATH, MANIFEST_PATH, Inventory
    from test_release_publishers import _FakeContainer

    from src.api.services.release_service import _ReleaseFiles

    path = "meta/info.json"
    _inventoried_store(tmp_path, {path: b"{}"})
    container = _FakeContainer("fixture", "releases", {})
    container.put("package/" + path, b"{}")
    store = BlobReleaseStore(container, "package")
    revision = store.list_objects()[0].revision
    entry = {"assurance": assurance, "path": path, "length": 2, "revision": revision}
    if assurance == "storage_revision":
        entry["copy_operation_sha256"] = "a" * 64
    else:
        entry.update(sha256=hashlib.sha256(b"{}").hexdigest(), operation_sha256="a" * 64)
    inventory_bytes = canonical_json_bytes(Inventory.model_validate({"entries": [entry]}).model_dump(mode="json"))
    manifest_bytes = canonical_json_bytes({"inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest()})
    container.put("package/" + INVENTORY_PATH, inventory_bytes)
    container.put("package/" + MANIFEST_PATH, manifest_bytes)
    files = _ReleaseFiles(
        store, name="package", manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(), metadata_max_bytes=100
    )
    if replaced:
        container.put("package/" + path, b"[]")
        container.forbid_downloads = True

    def read() -> bytes:
        return b"".join(files.stream(path)) if stream else files.read_bytes(path)

    if replaced:
        with pytest.raises(ReleaseVerificationError, match="revision changed"):
            read()
    else:
        assert read() == b"{}"


@pytest.mark.parametrize("failure", ["missing", "budget", "long", "short"])
def test_given_invalid_media_response_when_buffered_then_safe_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from src.api.services.release_service import _release_media, _ReleaseFiles

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
    from src.api.services.release_service import _episode_records, _ReleaseFiles

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
    from src.api.services.release_service import ReleaseSpec

    _client, body, settings, _app, _root = release_api
    spec = ReleaseSpec(**{**body, "episode_indices": tuple(body["episode_indices"])})
    planner = ViewerReleasePlanner(settings if missing != "worker" else ReleaseApiSettings(None))
    if missing == "dataset":
        monkeypatch.setattr(planner.datasets, "get_dataset", AsyncMock(return_value=None))
    if missing == "blob_provider":
        with pytest.raises(ReleaseApiError, match="release source is unavailable"):
            planner._source("native", finalized=True, kind="blob")
    elif missing == "dataset":
        assert asyncio.run(planner.prepare("localuser", spec, settings.destinations["local"])).summary["advisory"]
        with pytest.raises(ReleaseApiError) as error:
            asyncio.run(planner._capture("localuser", spec, settings.destinations["local"], lambda: None, "local"))
        assert error.value.status_code == 404
    else:
        with pytest.raises(ReleaseApiError) as error:
            asyncio.run(planner.prepare("localuser", spec, settings.destinations["local"]))
        assert error.value.status_code == 503
    if missing == "worker":
        with pytest.raises(ReleaseApiError, match="release worker is not configured"):
            planner.execution("job", b"{}")


def test_given_blob_source_local_destination_when_prepare_then_refused_before_freeze(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.release.inputs import BlobReleaseSource

    from src.api.services import viewer_release_planner as planner_module
    from src.api.services.release_service import ReleaseSpec

    _client, body, settings, _app, _root = release_api
    planner = ViewerReleasePlanner(settings)
    container = FakeBlobContainer()
    container.url = "https://example.blob.core.windows.net/datasets"
    source = BlobReleaseSource(container, prefix="native")
    monkeypatch.setattr(planner.datasets, "has_blob_provider", lambda: True)
    monkeypatch.setattr(planner, "_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        planner_module, "freeze_release_inputs", AsyncMock(side_effect=AssertionError("freeze reached"))
    )
    spec = ReleaseSpec(**{**body, "episode_indices": tuple(body["episode_indices"])})

    with pytest.raises(ReleaseApiError, match="azure_to_local_unsupported") as error:
        asyncio.run(planner.prepare("localuser", spec, settings.destinations["local"]))
    assert error.value.status_code == 422


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
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda **_kwargs: credential)
    client = MagicMock()
    client.get_container_client.return_value = container
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(azure.storage.blob, "BlobServiceClient", factory)
    destination = BlobLocation("https://example.blob.core.windows.net", "output", "native/releases")
    spec = ReleaseSpec(**{**body, "episode_indices": tuple(body["episode_indices"])})

    prepared, _execution = asyncio.run(planner._capture("localuser", spec, destination, lambda: None, "blob"))

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

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("native execution must not run before admission")

    monkeypatch.setattr("physical_ai_curation.release.native_worker.restore_inputs", forbidden)
    monkeypatch.setattr("physical_ai_curation.release.coordinator.run_release_generation", forbidden)
    cloud_settings = replace(settings, destinations={"cloud": BlobLocation(destination.account_url, "output", "")})
    planner.settings = cloud_settings
    service = ReleaseService(cloud_settings, planner=planner)
    _app.dependency_overrides[get_release_service] = lambda: service
    cloud_body = {**body, "destination": "cloud"}
    preview = _preview(_client, cloud_body)

    accepted = _submit(_client, cloud_body, preview)

    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    reopened = ReleaseService(cloud_settings)
    assert reopened.accepted_request("localuser", "release-key-1")["job_id"] == job_id
    assert reopened.status("localuser", job_id)["state"] == "accepted"
    monkeypatch.setattr(planner, "prepare", AsyncMock(side_effect=AssertionError("replay must not plan")))
    replay = _submit(_client, cloud_body, preview)
    assert replay.status_code == 202
    assert replay.json() == {"job_id": job_id, "replayed": True}


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

    store = MagicMock(spec=ReleaseJobStore)
    store.request_cancel.side_effect = CapacityError("private capacity detail")
    service = ReleaseService(ReleaseApiSettings(tmp_path), store_factory=lambda _root: store)
    with pytest.raises(ReleaseApiError) as error:
        service.cancel("localuser", "job")
    assert error.value.status_code == 507
    assert "private" not in error.value.detail


def test_given_inconsistent_execution_destination_when_result_read_then_refused(tmp_path: Path) -> None:

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


def test_retired_preparation_lifecycle_is_not_published(release_api: tuple) -> None:
    _client, _spec, _settings, app, _root = release_api
    paths = app.openapi()["paths"]
    assert not [path for path in paths if path.startswith(BASE + "/preparations")]
    assert BASE + "/preview" in paths
    assert BASE + "/jobs" in paths


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
    assert preview["reserved_bytes"] == 0
    assert preview["summary"]["advisory"] is True
    assert preview["summary"]["capacity"] is None
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
    assert preview.json()["summary"]["available_bytes"] is None
    labels.episodes["1"] = ["changed-after-preview"]
    (root / "meta/episode_labels.json").write_text(labels.model_dump_json())
    accepted = _submit(
        test_client, {**spec, "description": "Selected saved labels", "resources": row["resources"]}, preview.json()
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    store = ReleaseJobStore(_settings.job_root)
    execution = ViewerReleasePlanner(_settings).execution(job_id, store.frozen_request(job_id))
    result = asyncio.run(execute_job(store, job_id, execution))
    assert result.publication_state == "published"
    handle = test_client.get(SOURCE).json()["items"][0]["handle"]
    assert test_client.get(f"{SOURCE}/{handle}").json()["labels"]["episodes"] == {"1": ["changed-after-preview"]}


def test_given_inventory_page_when_labels_shared_then_next_page_reads_fresh_labels(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _spec, _settings, _app, root = release_api
    labels = DatasetLabelsFile(dataset_id="native", episodes={"0": ["first"], "1": ["first"]})
    path = root / "meta/episode_labels.json"
    path.write_text(labels.model_dump_json())
    original = LocalLabelStorage.load_versioned
    reads = []

    async def counted_load(storage: LocalLabelStorage, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        reads.append(dataset_id)
        return await original(storage, dataset_id)

    monkeypatch.setattr(LocalLabelStorage, "load_versioned", counted_load)
    first = test_client.get(BASE + "/inventory/native?limit=2")
    assert first.status_code == 200
    assert [item["labels"] for item in first.json()["items"]] == [["first"], ["first"]]
    assert reads == ["native"]

    labels.episodes["1"] = ["updated"]
    path.write_text(labels.model_dump_json())
    second = test_client.get(BASE + "/inventory/native?cursor=1&limit=1")
    assert second.status_code == 200
    assert second.json()["items"][0]["labels"] == ["updated"]
    assert reads == ["native", "native"]
    assert first.json()["items"][1]["resources"] != second.json()["items"][0]["resources"]


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "cancelled"])
def test_given_concurrent_inventory_when_page_finishes_then_reads_settle_in_order(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from dataclasses import replace

    from physical_ai_curation.saved_input import SavedInputError, SavedResource

    from src.api.services.release_service import ReleaseOffloadSettings

    _client, _spec, settings, _app, _root = release_api
    settings = replace(
        settings,
        offload=ReleaseOffloadSettings(
            read_concurrency=2, operation_timeout_seconds=0.1 if outcome == "timeout" else 5
        ),
    )
    planner = ViewerReleasePlanner(settings)
    reader = planner.reader_for_scope("localuser", inventory=True)
    original = reader.annotation
    monkeypatch.setattr(planner, "reader_for_scope", lambda _scope, **_options: reader)

    async def exercise() -> None:
        started = asyncio.Event()
        finish = asyncio.Event()
        active = 0
        peak = 0

        async def annotation(dataset_id: str, episode_index: int) -> SavedResource:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
            try:
                await finish.wait()
                if outcome == "failure" and episode_index == 0:
                    raise SavedInputError("private storage detail")
                return await original(dataset_id, episode_index)
            finally:
                active -= 1

        monkeypatch.setattr(reader, "annotation", annotation)
        task = asyncio.create_task(
            ReleaseService(settings, planner=planner).inventory_details("native", episodes=[0, 1], scope="localuser")
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "success":
            finish.set()
            page = await task
            assert [item["episode_index"] for item in page["items"]] == [0, 1]
        else:
            if outcome == "failure":
                finish.set()
            with pytest.raises(ReleaseApiError) as error:
                await task
            assert error.value.status_code == (504 if outcome == "timeout" else 409)
            assert "private" not in error.value.detail
        assert peak == 2
        assert active == 0

    asyncio.run(exercise())


def test_given_inventory_reader_when_source_reused_then_only_current_batch_is_cached(release_api: tuple) -> None:
    _client, _spec, settings, _app, root = release_api
    planner = ViewerReleasePlanner(settings)

    async def exercise() -> None:
        reader = planner.reader_for_scope("localuser", inventory=True)
        original = await reader.source_revision("native", 0)
        info = root / "meta/info.json"
        info.write_text(info.read_text() + "\n")
        assert await reader.source_revision("native", 0) == original
        fresh = planner.reader_for_scope("localuser", inventory=True)
        assert await fresh.source_revision("native", 0) != original
        authoritative = planner.reader_for_scope("localuser")
        assert await authoritative.source_revision("native", 0) != original

    asyncio.run(exercise())


@pytest.mark.parametrize("author", ["private-author", "another-private-author"])
def test_given_other_saved_author_when_previewed_then_automatically_attributes(release_api: tuple, author: str) -> None:
    client, spec, settings, _app, root = release_api
    labels = DatasetLabelsFile(
        dataset_id="native",
        episodes={"1": ["success"]},
        provenance={
            "1": {
                "schema_version": "1.0.0",
                "contributions": [
                    {
                        "id": "saved-contribution",
                        "field": "labels/success",
                        "origin": "human",
                        "author_id": author,
                        "value": True,
                        "sequence": 0,
                        "timestamp": "2026-11-03T00:00:00Z",
                    }
                ],
            }
        },
    )
    (root / "meta/episode_labels.json").write_text(labels.model_dump_json())
    inventory = client.get(BASE + "/inventory/native?limit=1&cursor=1").json()

    response = client.post(BASE + "/preview", json={**spec, "resources": inventory["items"][0]["resources"]})

    assert response.status_code == 200, response.json()
    assert author not in response.text
    assert not settings.job_root.exists()


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
    row = test_client.get(BASE + "/inventory/native/details?episodes=1").json()["items"][0]
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
    completed = subprocess.run(
        [sys.executable, "-m", "src.api.release_worker", "--once"],
        env={**os.environ, "VLM_JUDGE_JOB_DIR": str(store.root)},
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
    row = test_client.get(BASE + "/inventory/native/details?episodes=1").json()["items"][0]
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
    test_client, spec, settings, app, _root = release_api
    accepted = _submit(test_client, spec, _preview(test_client, spec)).json()
    store = ReleaseJobStore(settings.job_root)
    assert store.source_association(accepted["job_id"]) is None
    jobs = test_client.get(BASE + "/jobs?dataset_id=native").json()["items"]
    accepted_event = next(
        event for event in store.storage.snapshot()[accepted["job_id"]]["events"] if event["event"] == "job.accepted"
    )
    assert isinstance(jobs[0]["created_at"], str)
    created_at = datetime.fromisoformat(jobs[0]["created_at"])
    assert created_at.tzinfo == UTC
    assert created_at.timestamp() == pytest.approx(accepted_event["occurred_at"], abs=0.001, rel=0)
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
    assert binding["schema"] == "curated-release-intent/v1"
    payload = parse_strict_json(store.frozen_request(job))
    assert payload["effective_destination"] == {"kind": "local", "path": str(expected.resolve())}
    assert store.source_association(job) is None
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
    assert response.status_code == 202 if change == "source" else response.status_code in {409, 422}
    assert not settings.destinations["local"].exists()
    assert len(test_client.get(BASE + "/jobs").json()["items"]) == int(change == "source")


def test_given_failed_release_when_status_and_history_read_then_recovery_required_is_visible(
    release_api: tuple,
) -> None:
    test_client, spec, settings, _app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    assert test_client.get(BASE + "/jobs/" + job).json()["recovery_required"] is False
    assert store.record_dispatch_failure(job, "resolver_failed", expected_fence=0, worker_id="test-worker")

    assert test_client.get(BASE + "/jobs/" + job).json()["recovery_required"] is True
    history = test_client.get(BASE + "/jobs").json()["items"]
    assert next(item for item in history if item["job_id"] == job)["recovery_required"] is True
    store.claim(job, "retry-worker")
    assert test_client.get(BASE + "/jobs/" + job).json()["recovery_required"] is False


def test_given_cancelled_captured_job_when_source_unavailable_then_cleanup_configuration_resolves(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, spec, settings, _app, _root = release_api
    job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    planner = ViewerReleasePlanner(settings)
    intent = store.frozen_request(job)
    execution = planner.execution(job, intent)
    captured, binding, reservation, _execution = asyncio.run(execution.capture(lambda: None))
    fence = store.claim(job, "test-worker")
    store.capture_execution(
        job,
        fence,
        intent_request=intent,
        frozen_request=captured,
        execution_binding=binding,
        reserved_bytes=reservation,
    )
    assert test_client.post(BASE + "/jobs/" + job + "/cancel").status_code == 202

    def unavailable(*args: object, **kwargs: object) -> None:
        raise AssertionError("cancellation must not reopen the source")

    monkeypatch.setattr(planner, "_source", unavailable)
    cleanup = planner.execution(job, store.frozen_request(job))
    assert cleanup.source is None
    assert cleanup.destination == execution.destination


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
    preview = _preview(test_client, spec)
    job = _submit(test_client, spec, preview).json()["job_id"]
    store = ReleaseJobStore(settings.job_root)
    assert parse_strict_json(store.frozen_request(job))["schema_version"] == "curated-release-intent/v1"
    source_info = json.loads((root / "meta/info.json").read_bytes())
    source_info["robot_type"] = "changed-at-release"
    (root / "meta/info.json").write_text(json.dumps(source_info))
    execution = ViewerReleasePlanner(settings).execution(job, store.frozen_request(job))

    result = asyncio.run(execute_job(store, job, execution))

    assert result is not None
    assert result.publication_state == "published"
    reopened = ReleaseJobStore(settings.job_root)
    assert parse_strict_json(reopened.frozen_request(job))["schema_version"] == "curated-release-generation/v1"
    output_info = json.loads((execution.destination / spec["dataset_name"] / "meta/info.json").read_bytes())
    assert output_info["robot_type"] == "changed-at-release"
    assert _submit(test_client, spec, preview).json() == {"job_id": job, "replayed": True}
    root.rename(root.parent / "source-unavailable")
    from physical_ai_curation.release.record_storage import RecordLimits
    from physical_ai_curation.release.source_associations import LocalSourceAssociations

    intent = store.source_association(job)
    associations = LocalSourceAssociations(execution.destination, limits=RecordLimits())
    assert associations.discover(source_key=intent.source_key, scope_sha256=intent.scope_sha256) == [
        execution.destination / spec["dataset_name"]
    ]


def test_given_backend_lifecycle_when_job_accepted_then_worker_publishes(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api import main as main_mod

    test_client, spec, _settings, _app, _source_root = release_api
    monkeypatch.setattr(main_mod, "_job_router", None)
    monkeypatch.setenv("RELEASE_WORKER_POLL_SECONDS", "0.1")

    async def run_backend() -> None:
        async with main_mod.lifespan(FastAPI()):
            job = _submit(test_client, spec, _preview(test_client, spec)).json()["job_id"]
            async with asyncio.timeout(30):
                while test_client.get(BASE + "/jobs/" + job).json()["state"] != "committed":
                    await asyncio.sleep(0.1)
            assert test_client.get(BASE + "/jobs/" + job + "/result").status_code == 200

    asyncio.run(run_backend())


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
    intent = ReleaseJobStore(settings.job_root).source_association(_job)
    assert intent is not None
    assert items[0]["created_at"] == intent.accepted_at
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
        {
            "assurance": "content_sha256",
            "path": path.relative_to(root).as_posix(),
            "length": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*.json"))
    ]
    (root / "release").mkdir()
    inventory = canonical_json_bytes({"schema_version": "2.0.0", "entries": entries})
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


class _FakeBlobPages(list):
    """Blob listing that also exposes the Azure `by_page()` protocol as one final page."""

    def by_page(self, continuation_token: str | None = None) -> Iterator[Iterator[_FakeBlobItem]]:
        yield iter(list(self))


class FakeBlobContainer:
    """In-memory container exposing only the read operations used by release association and store reads."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_blobs(
        self,
        name_starts_with: str = "",
        include: list[str] | None = None,
        results_per_page: int | None = None,
    ) -> _FakeBlobPages:
        return _FakeBlobPages(
            _FakeBlobItem(name, data)
            for name, data in sorted(self.objects.items())
            if name.startswith(name_starts_with)
        )

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
    preview = _preview(test_client, selected)
    summary = preview["summary"]
    assert summary["advisory"] is True
    assert summary["capacity"] is None
    assert preview["reserved_bytes"] == 0
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


@pytest.mark.parametrize(
    "name,value",
    [
        ("RELEASE_READ_CONCURRENCY", "0"),
        ("RELEASE_READ_CONCURRENCY", "65"),
        ("RELEASE_CONTROL_CONCURRENCY", "-1"),
        ("RELEASE_CONTROL_CONCURRENCY", "invalid"),
        ("RELEASE_PREPARATION_CONCURRENCY", "0"),
        ("RELEASE_PREPARATION_CONCURRENCY", "65"),
        ("RELEASE_READ_INFLIGHT_BYTES", "0"),
        ("RELEASE_OPERATION_TIMEOUT_SECONDS", "0"),
        ("RELEASE_OPERATION_TIMEOUT_SECONDS", "nan"),
        ("RELEASE_OPERATION_TIMEOUT_SECONDS", "601"),
    ],
)
def test_given_invalid_offload_setting_when_loading_settings_then_refused(name: str, value: str) -> None:
    from src.api.services.release_service import load_release_api_settings

    with pytest.raises(ValueError):
        load_release_api_settings({name: value})


def test_given_budget_below_retained_read_bound_when_loading_settings_then_refused() -> None:
    from src.api.services.release_service import load_release_api_settings

    environ = {
        "CURATION_RELEASE_READ_MEDIA_BYTES": "2048",
        "RELEASE_READ_INFLIGHT_BYTES": "1024",
    }
    with pytest.raises(ValueError):
        load_release_api_settings(environ)


def _async_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _stalled_source_releases(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event, dict]:
    entered = threading.Event()
    release = threading.Event()
    observed: dict[str, bool] = {}

    def stalled(self: ReleaseService, scope: str, dataset_id: str) -> dict:
        entered.set()
        observed["released_by_test"] = release.wait(timeout=5)
        return {"schema_version": "curated-release-api/v1", "items": [], "next_cursor": None}

    monkeypatch.setattr(ReleaseService, "source_releases", stalled)
    return entered, release, observed


def test_given_stalled_retained_read_when_status_requested_then_status_completes(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_client, _spec, settings, app, _root = release_api
    entered, release, observed = _stalled_source_releases(monkeypatch)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    async def scenario() -> tuple[int, int]:
        async with _async_client(app) as client:
            read = asyncio.create_task(client.get(BASE + "/sources/native/releases"))
            await asyncio.to_thread(entered.wait, 5)
            status = await asyncio.wait_for(client.get(BASE + "/jobs/" + "0" * 32), timeout=3)
            release.set()
            return status.status_code, (await read).status_code

    status_code, read_status = asyncio.run(scenario())
    assert status_code == 404
    assert observed.get("released_by_test") is True
    assert read_status == 200


def test_given_saturated_read_lane_when_second_read_requested_then_busy_until_thread_settles(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_client, _spec, _settings, app, _root = release_api
    monkeypatch.setenv("RELEASE_READ_CONCURRENCY", "1")
    settings = load_release_api_settings(os.environ)
    entered, release, _observed = _stalled_source_releases(monkeypatch)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    async def scenario() -> tuple[int, int, int]:
        async with _async_client(app) as client:
            first = asyncio.create_task(client.get(BASE + "/sources/native/releases"))
            await asyncio.to_thread(entered.wait, 5)
            second = await client.get(BASE + "/sources/native/releases")
            release.set()
            first_status = (await first).status_code
            third = await client.get(BASE + "/sources/native/releases")
            return second.status_code, first_status, third.status_code

    second_status, first_status, third_status = asyncio.run(scenario())
    assert second_status == 503
    assert first_status == 200
    assert third_status == 200


def test_given_byte_budget_exhausted_when_second_read_requested_then_refused(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_client, _spec, _settings, app, _root = release_api
    monkeypatch.setenv("CURATION_RELEASE_READ_METADATA_BYTES", "1024")
    monkeypatch.setenv("CURATION_RELEASE_READ_MEDIA_BYTES", "1024")
    monkeypatch.setenv("RELEASE_READ_INFLIGHT_BYTES", "1024")
    settings = load_release_api_settings(os.environ)
    entered, release, _observed = _stalled_source_releases(monkeypatch)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    async def scenario() -> int:
        async with _async_client(app) as client:
            first = asyncio.create_task(client.get(BASE + "/sources/native/releases"))
            await asyncio.to_thread(entered.wait, 5)
            second = await client.get(BASE + "/sources/native/releases")
            release.set()
            await first
            return second.status_code

    assert asyncio.run(scenario()) == 503


def test_given_timed_out_read_when_thread_still_running_then_capacity_held_until_settled(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_client, _spec, _settings, app, _root = release_api
    monkeypatch.setenv("RELEASE_READ_CONCURRENCY", "1")
    monkeypatch.setenv("RELEASE_OPERATION_TIMEOUT_SECONDS", "0.2")
    settings = load_release_api_settings(os.environ)
    _entered, release, _observed = _stalled_source_releases(monkeypatch)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    async def scenario() -> tuple[int, int, int]:
        async with _async_client(app) as client:
            first = await client.get(BASE + "/sources/native/releases")
            second = await client.get(BASE + "/sources/native/releases")
            release.set()
            recovered = 503
            for _ in range(100):
                recovered = (await client.get(BASE + "/sources/native/releases")).status_code
                if recovered == 200:
                    break
                await asyncio.sleep(0.05)
            return first.status_code, second.status_code, recovered

    assert asyncio.run(scenario()) == (504, 503, 200)


def test_given_disconnected_read_when_thread_still_running_then_capacity_held_until_settled(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_client, _spec, _settings, app, _root = release_api
    monkeypatch.setenv("RELEASE_READ_CONCURRENCY", "1")
    settings = load_release_api_settings(os.environ)
    entered, release, _observed = _stalled_source_releases(monkeypatch)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    async def scenario() -> tuple[int, int]:
        async with _async_client(app) as client:
            read = asyncio.create_task(client.get(BASE + "/sources/native/releases"))
            await asyncio.to_thread(entered.wait, 5)
            read.cancel()
            with pytest.raises(asyncio.CancelledError):
                await read
            second = await client.get(BASE + "/sources/native/releases")
            release.set()
            recovered = 503
            for _ in range(100):
                recovered = (await client.get(BASE + "/sources/native/releases")).status_code
                if recovered == 200:
                    break
                await asyncio.sleep(0.05)
            return second.status_code, recovered

    assert asyncio.run(scenario()) == (503, 200)


def test_given_shutdown_offload_when_service_operates_then_refused_without_work(tmp_path: Path) -> None:
    from src.api.services.release_service import close_release_offload

    service = ReleaseService(ReleaseApiSettings(tmp_path / "jobs"))
    close_release_offload()
    with pytest.raises(ReleaseApiError) as error:
        asyncio.run(service.control(service.status, "localuser", "0" * 32))
    assert error.value.status_code == 503


def _cooperative_operation(entered: threading.Event, stopped: threading.Event) -> str:
    from src.api.services.release_service import check_cancelled

    entered.set()
    try:
        for _ in range(500):
            check_cancelled()
            time.sleep(0.01)
        return "completed"
    finally:
        stopped.set()


def test_given_stalled_preparation_when_deadline_expires_then_request_returns_and_capacity_is_held(
    tmp_path: Path,
) -> None:
    from src.api.services.release_service import ReleaseOffloadSettings

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def prepare(*args: object) -> object:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return "prepared"

        settings = ReleaseApiSettings(
            tmp_path / "jobs", offload=ReleaseOffloadSettings(operation_timeout_seconds=0.05, preparation_concurrency=1)
        )
        service = ReleaseService(settings, planner=SimpleNamespace(prepare=prepare))
        pending = asyncio.create_task(service._prepare("localuser", None, tmp_path))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            done, _ = await asyncio.wait({pending}, timeout=0.5)
            assert done, "Preparation must return a timeout without waiting for stalled cleanup"
            with pytest.raises(ReleaseApiError) as error:
                pending.result()
            assert error.value.status_code == 504
            with pytest.raises(ReleaseApiError) as busy:
                await service._prepare("localuser", None, tmp_path)
            assert busy.value.detail == "release_busy"
            assert await service.control(lambda: "responsive") == "responsive"
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await asyncio.sleep(0)
        async with asyncio.timeout(1):
            while True:
                try:
                    assert await service._prepare("localuser", None, tmp_path) == "prepared"
                    break
                except ReleaseApiError as busy:
                    assert busy.detail == "release_busy"
                    await asyncio.sleep(0)

    asyncio.run(scenario())


def _stalled_operation(entered: threading.Event, release: threading.Event) -> str:
    entered.set()
    release.wait(timeout=5)
    return "done"


def test_given_replaced_preparation_executor_when_work_is_admitted_then_capacity_and_remaining_steps_survive() -> None:
    from src.api.services.release_service import ReleaseOffload, ReleaseOffloadSettings, current_preparation_work

    async def scenario() -> None:
        settings = ReleaseOffloadSettings(preparation_concurrency=1)
        first = ReleaseOffload(settings)
        second = ReleaseOffload(settings)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def prepare() -> str:
            work = current_preparation_work()
            assert work is not None
            assert await work.run(lambda: "first") == "first"
            entered.set()
            await release.wait()
            return await work.run(lambda: "second")

        pending = asyncio.create_task(first.prepare(prepare))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            first.close()
            with pytest.raises(ReleaseApiError) as busy:
                await second.prepare(prepare)
            assert busy.value.detail == "release_busy"
            release.set()
            assert await asyncio.wait_for(pending, 1) == "second"
            assert await second.prepare(prepare) == "second"
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            first.close()
            second.close()

    asyncio.run(scenario())


def _eventually_admitted(offload: object, timeout: float = 5.0) -> object:
    from src.api.services.release_service import ReleaseOffload

    assert isinstance(offload, ReleaseOffload)
    deadline = time.monotonic() + timeout
    while True:
        try:
            return asyncio.run(offload.run("read", lambda: "free"))
        except ReleaseApiError as error:
            if error.status_code != 503 or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def test_given_timed_out_operation_when_it_checks_cancellation_then_work_stops_and_capacity_frees() -> None:
    from src.api.services.release_service import ReleaseOffload, ReleaseOffloadSettings

    offload = ReleaseOffload(ReleaseOffloadSettings(read_concurrency=1, operation_timeout_seconds=0.2))
    entered = threading.Event()
    stopped = threading.Event()
    try:
        with pytest.raises(ReleaseApiError) as error:
            asyncio.run(offload.run("read", _cooperative_operation, entered, stopped, response_bytes=1))
        assert error.value.status_code == 504
        assert stopped.wait(timeout=5)
        assert _eventually_admitted(offload) == "free"
    finally:
        offload.close()


def test_given_repeatedly_cancelled_awaiter_when_operation_checks_cancellation_then_work_stops_once() -> None:
    from src.api.services.release_service import ReleaseOffload, ReleaseOffloadSettings

    offload = ReleaseOffload(ReleaseOffloadSettings(read_concurrency=1, operation_timeout_seconds=30))
    entered = threading.Event()
    stopped = threading.Event()

    async def scenario() -> None:
        task = asyncio.create_task(offload.run("read", _cooperative_operation, entered, stopped, response_bytes=1))
        await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        assert stopped.wait(timeout=5)
        assert _eventually_admitted(offload) == "free"
    finally:
        offload.close()


def test_given_running_operation_when_shutdown_closes_offload_then_settlement_is_reported() -> None:
    from src.api.services.release_service import ReleaseOffloadSettings, close_release_offload, get_release_offload

    offload = get_release_offload(ReleaseOffloadSettings(read_concurrency=1, operation_timeout_seconds=30))
    entered = threading.Event()
    stopped = threading.Event()

    async def scenario() -> tuple[bool, ReleaseApiError]:
        task = asyncio.create_task(offload.run("read", _cooperative_operation, entered, stopped, response_bytes=1))
        await asyncio.to_thread(entered.wait, 5)
        settled = await asyncio.to_thread(close_release_offload, 5.0)
        with pytest.raises(ReleaseApiError) as error:
            await task
        return settled, error.value

    settled, error = asyncio.run(scenario())
    assert settled is True
    assert stopped.is_set()
    assert error.status_code == 503


def test_given_closed_offload_with_running_work_when_new_generation_admits_then_process_cap_holds() -> None:
    from src.api.services.release_service import ReleaseOffload, ReleaseOffloadSettings

    settings = ReleaseOffloadSettings(read_concurrency=1, operation_timeout_seconds=30)
    first = ReleaseOffload(settings)
    entered = threading.Event()
    release = threading.Event()

    async def scenario() -> int:
        task = asyncio.create_task(first.run("read", _stalled_operation, entered, release, response_bytes=1))
        await asyncio.to_thread(entered.wait, 5)
        first.close()
        second = ReleaseOffload(settings)
        try:
            with pytest.raises(ReleaseApiError) as error:
                await second.run("read", lambda: "admitted")
            return error.value.status_code
        finally:
            second.close()
            release.set()
            assert await task == "done"

    assert asyncio.run(scenario()) == 503


def test_given_lower_read_limit_when_older_work_running_then_new_limit_counts_it() -> None:
    from dataclasses import replace

    from src.api.services.release_service import ReleaseOffloadSettings, close_release_offload, get_release_offload

    base = ReleaseOffloadSettings(read_concurrency=2, operation_timeout_seconds=30)
    first = get_release_offload(base)
    entered = threading.Event()
    release = threading.Event()

    async def scenario() -> int:
        task = asyncio.create_task(first.run("read", _stalled_operation, entered, release, response_bytes=1))
        await asyncio.to_thread(entered.wait, 5)
        second = get_release_offload(replace(base, read_concurrency=1))
        assert second is not first
        try:
            with pytest.raises(ReleaseApiError) as error:
                await second.run("read", lambda: "admitted")
            return error.value.status_code
        finally:
            release.set()
            assert await task == "done"

    try:
        assert asyncio.run(scenario()) == 503
    finally:
        close_release_offload()


def test_given_submit_when_store_opened_then_blocking_setup_runs_off_the_event_loop(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _test_client, spec, settings, _app, _root = release_api
    service = ReleaseService(settings, planner=ViewerReleasePlanner(settings))
    seen: dict[str, int] = {}

    def store(self: ReleaseService) -> None:
        seen["store_thread"] = threading.get_ident()
        raise ReleaseApiError(503, "release_unavailable")

    monkeypatch.setattr(ReleaseService, "_store", store)

    async def scenario() -> None:
        seen["loop_thread"] = threading.get_ident()
        with pytest.raises(ReleaseApiError):
            await service.submit(
                "localuser",
                spec,
                "thread-probe-key",
                preview_digest="",
                processing_digest="",
                local_disk_acknowledged=False,
            )

    asyncio.run(scenario())
    assert seen["store_thread"] != seen["loop_thread"]


def test_real_http_stalled_read_leaves_status_and_admission_responsive(
    release_api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import uvicorn

    _test_client, _spec, _settings, app, _root = release_api
    monkeypatch.setenv("RELEASE_READ_CONCURRENCY", "1")
    settings = load_release_api_settings(os.environ)
    entered, release, _observed = _stalled_source_releases(monkeypatch)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(settings)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    original_log_level = logging.getLogger("uvicorn.error").level
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="off"))
    assert logging.getLogger("uvicorn.error").level == original_log_level
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise AssertionError("loopback server did not start")
            time.sleep(0.05)

        read_status: dict[str, int] = {}

        def read() -> None:
            with httpx.Client(timeout=10) as reader:
                read_status["code"] = reader.get(base + BASE + "/sources/native/releases").status_code

        reader_thread = threading.Thread(target=read)
        reader_thread.start()
        assert entered.wait(timeout=5)

        with httpx.Client(timeout=10) as client:
            started = time.monotonic()
            status = client.get(base + BASE + "/jobs/" + "0" * 32)
            status_elapsed = time.monotonic() - started
            refused = client.get(base + BASE + "/sources/native/releases")

        release.set()
        reader_thread.join(timeout=10)
        assert status.status_code == 404
        assert status_elapsed < 3
        assert refused.status_code == 503
        assert read_status["code"] == 200
    finally:
        release.set()
        server.should_exit = True
        server_thread.join(timeout=10)
        assert not server_thread.is_alive()


def test_given_unavailable_source_io_when_previewed_then_real_http_stays_responsive(
    release_api: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    from dataclasses import replace

    import uvicorn

    from src.api.services.release_service import ReleaseOffloadSettings

    _client, spec, original, app, _root = release_api
    settings = replace(
        original,
        offload=ReleaseOffloadSettings(preparation_concurrency=1, operation_timeout_seconds=1),
    )
    forbidden = MagicMock(side_effect=AssertionError("preview must not access source storage"))
    monkeypatch.setattr(ViewerReleasePlanner, "_source", forbidden)
    app.dependency_overrides[get_release_service] = lambda: ReleaseService(
        settings, planner=ViewerReleasePlanner(settings)
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    original_log_level = logging.getLogger("uvicorn.error").level
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off", timeout_graceful_shutdown=0.1))
    assert logging.getLogger("uvicorn.error").level == original_log_level
    server_thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    server_thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            preview = client.post(BASE + "/preview", json=spec)
            assert preview.status_code == 200
            assert preview.json()["summary"]["advisory"] is True
            assert client.get(BASE + "/jobs/" + "0" * 32).status_code == 404
        forbidden.assert_not_called()
    finally:
        server.should_exit = True
        server_thread.join(5)
        listener.close()
        assert not server_thread.is_alive()
    assert ReleaseService(settings).jobs("localuser", cursor=0, limit=20)["items"] == []


@pytest.mark.parametrize("cooperative", [True, False])
def test_given_server_process_when_shutdown_requested_then_exit_is_bounded(cooperative: bool) -> None:
    import selectors
    import signal

    descendant = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    child = (
        "import signal,time; "
        + (
            "signal.signal(signal.SIGTERM, lambda *args: exit(0)); "
            if cooperative
            else "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        )
        + ("" if cooperative else f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {descendant!r}]); ")
        + "print('ready', flush=True); time.sleep(60)"
    )
    script = (
        "import sys; from src.api.server import supervise_server; "
        "sys.exit(supervise_server([sys.executable, '-c', sys.argv[1]], shutdown_seconds=0.2))"
    )
    process = subprocess.Popen([sys.executable, "-c", script, child], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(5), "Supervisor did not start its child"
            assert process.stdout.readline() == b"ready\n"
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=3)
        assert time.monotonic() - started < 3
        assert process.returncode == (0 if cooperative else 137), stderr.decode()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)


@pytest.mark.parametrize("grace,total", [("0", "25"), ("nan", "25"), ("10", "10"), ("10", "601")])
def test_given_invalid_server_shutdown_limits_when_loaded_then_refused(
    monkeypatch: pytest.MonkeyPatch, grace: str, total: str
) -> None:
    from src.api.server import shutdown_limits

    monkeypatch.setenv("DATAVIEWER_GRACEFUL_SHUTDOWN_SECONDS", grace)
    monkeypatch.setenv("DATAVIEWER_SHUTDOWN_TIMEOUT_SECONDS", total)
    with pytest.raises(ValueError):
        shutdown_limits()


def test_given_server_entrypoint_when_launched_then_shutdown_and_network_settings_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import server

    observed = []

    def supervise(command: list[str], *, shutdown_seconds: float) -> int:
        observed.append((command, shutdown_seconds))
        return 0

    monkeypatch.setenv("DATAVIEWER_GRACEFUL_SHUTDOWN_SECONDS", "3")
    monkeypatch.setenv("DATAVIEWER_SHUTDOWN_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("BACKEND_HOST", "127.0.0.1")
    monkeypatch.setenv("BACKEND_PORT", "18000")
    monkeypatch.setattr(server, "supervise_server", supervise)
    monkeypatch.setattr(sys, "argv", ["server", "--reload"])
    assert server.main() == 0
    command, total = observed[0]
    assert total == 9
    assert command[command.index("--timeout-graceful-shutdown") + 1] == "3"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "18000"
    assert "--reload" in command
