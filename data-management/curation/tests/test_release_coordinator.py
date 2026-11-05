"""Release generation coordinator refusal before publication effects."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from physical_ai_curation.models import DatasetLabelsFile
from physical_ai_curation.release import coordinator as coordinator_module
from physical_ai_curation.release.canonical import sha256_hex
from physical_ai_curation.release.composition import prepare_saved_generation
from physical_ai_curation.release.coordinator import GenerationInputs, generation_request_bytes, run_release_generation
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    FrozenReleaseInputs,
    LocalReleaseSource,
    ReleaseInputError,
    ReleaseSelection,
    SavedSelection,
    freeze_release_inputs,
)
from physical_ai_curation.release.jobs import (
    AuthorityError,
    CancellationError,
    JobRequest,
    ReleaseJobStore,
    ReleaseRefusal,
    ReleaseRuntimeSettings,
)
from physical_ai_curation.release.media import (
    MediaWindow,
    ObjectPlanRequest,
    ReleasePlanError,
    StorageEndpoint,
    SubsetEpisodeInput,
)
from physical_ai_curation.release.models import ProducerIdentity, VersionIdentity
from physical_ai_curation.release.native_worker import _source_spec, native_operation, restore_inputs
from physical_ai_curation.release.publication import LocalPublisher, PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.runner import ConfiguredExecution, _LifecycleMonitor, execute_job, run_queue
from physical_ai_curation.release.runner import main as run_worker
from physical_ai_curation.release.source_associations import LocalSourceAssociations, source_association_binding
from physical_ai_curation.release.store import LocalReleaseStore
from physical_ai_curation.release.verification import verify_published
from physical_ai_curation.saved_input import SavedResource
from pydantic import TypeAdapter
from pytest_mock import MockerFixture
from test_release_composition import _generation_case
from test_release_contracts import _producer_dict
from test_release_inputs import SavedReader, _context, _selection
from test_release_jobs import (
    ATTEMPT,
    BLOB,
    OWNER,
    SCOPE,
    FsContainer,
    OffsetClock,
    _ChildBlobPublisher,
    _request,
    _sdk_calls,
    _store,
)
from test_release_media import _encode_source
from test_release_publishers import _association_intent


def _source_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "data").mkdir(parents=True)
    (root / "data/frames.jsonl").write_bytes(b'{"frame_index":0}\n')
    (root / "meta").mkdir()
    (root / "meta/info.json").write_bytes(b'{"total_episodes":1}')
    return root


def _inputs(request: object) -> GenerationInputs:
    return GenerationInputs(
        request=request,  # type: ignore[arg-type]
        sources={},
        source_root=Path("."),
        source_info={},
        source_tables={},
        source_tasks=None,  # type: ignore[arg-type]
        camera_statistics=None,
        preserved_dataset_statistics=None,
        encode_options=None,
        producer=None,  # type: ignore[arg-type]
        confirmed_digest="0" * 64,
        scratch_bytes=0,
        output_bytes=0,
    )


def test_given_source_drift_after_freeze_when_generation_then_refused_before_any_effect(tmp_path: Path) -> None:
    root = _source_root(tmp_path)
    source = LocalReleaseSource(root, finalized=True)
    reader = SavedReader()
    context: FreezeContext = _context()
    selection: ReleaseSelection = _selection()
    expected = asyncio.run(freeze_release_inputs(source, reader, selection, context))

    path = root / "data/frames.jsonl"
    before = path.stat()
    path.write_bytes(b'{"frame_index":1}\n')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    clock = OffsetClock()
    store = _store(tmp_path / "jobs", clock)
    destination = tmp_path / "destination"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    _, _, request = _generation_case()

    with pytest.raises(ReleaseInputError, match="changed"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=expected,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=_inputs(request),
            )
        )

    assert not destination.exists() or not any(destination.rglob("*"))


def test_given_controller_inventory_longer_than_lease_when_executed_then_live_fence_publishes(tmp_path: Path) -> None:
    lease_seconds = 5
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = ReleaseJobStore(
        tmp_path / "jobs",
        settings=ReleaseRuntimeSettings(lease_seconds=lease_seconds, reserve_bytes=0),
        clock=OffsetClock(),
        free_bytes=lambda _path: 10**12,
    )
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    inventory = coordinator_module._inventory

    def delayed_inventory(local_store: LocalReleaseStore, checkpoint: object = None) -> object:
        time.sleep(lease_seconds + 2)
        return inventory(local_store, checkpoint=checkpoint)

    with patch.object(coordinator_module, "_inventory", side_effect=delayed_inventory):
        result = asyncio.run(
            execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination))
        )

    assert result is not None
    assert store.storage.snapshot()[job_id]["state"] == "committed"


def test_given_final_verification_longer_than_lease_when_executed_then_live_fence_publishes_and_monitor_settles(
    tmp_path: Path,
) -> None:
    lease_seconds = 5
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = ReleaseJobStore(
        tmp_path / "jobs",
        settings=ReleaseRuntimeSettings(lease_seconds=lease_seconds, reserve_bytes=0),
        clock=OffsetClock(),
        free_bytes=lambda _path: 10**12,
    )
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    real_verify = coordinator_module.verify_published

    def delayed_verify(
        local_store: LocalReleaseStore, expected: str | None = None, *, checkpoint: object = None
    ) -> object:
        time.sleep(lease_seconds + 2)
        return real_verify(local_store, expected, checkpoint=checkpoint)

    with patch.object(coordinator_module, "verify_published", side_effect=delayed_verify):
        asyncio.run(execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination)))

    assert store.storage.snapshot()[job_id]["state"] == "committed"
    assert not any(thread.name == f"release-lease-{job_id}" for thread in threading.enumerate())


def test_given_final_verification_fails_when_executed_then_error_propagates_and_monitor_settles(
    tmp_path: Path,
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = ReleaseJobStore(
        tmp_path / "jobs",
        settings=ReleaseRuntimeSettings(lease_seconds=5, reserve_bytes=0),
        clock=OffsetClock(),
        free_bytes=lambda _path: 10**12,
    )
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id

    with (
        patch.object(coordinator_module, "verify_published", side_effect=RuntimeError("injected verification failure")),
        pytest.raises(RuntimeError, match="injected verification failure"),
    ):
        asyncio.run(execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination)))

    assert store.storage.snapshot()[job_id]["state"] == "committed"
    assert not any(thread.name == f"release-lease-{job_id}" for thread in threading.enumerate())


def test_given_cancel_recorded_when_real_monitor_renews_then_stored_lease_expiry_advances(tmp_path: Path) -> None:
    store = ReleaseJobStore(
        tmp_path / "jobs",
        settings=ReleaseRuntimeSettings(lease_seconds=5, reserve_bytes=0),
        clock=OffsetClock(),
        free_bytes=lambda _path: 10**12,
    )
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()[job_id]["lease_expires"]
    monitor = _LifecycleMonitor(store, job_id, fence)
    monitor._interval = 0.01
    monitor.start()
    try:
        deadline = time.monotonic() + 5
        while store.storage.snapshot()[job_id]["lease_expires"] <= before and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        monitor.stop()

    assert store.storage.snapshot()[job_id]["lease_expires"] > before


def test_given_cancel_requested_during_blocking_inventory_when_executed_then_no_further_effect(
    tmp_path: Path,
) -> None:
    lease_seconds = 5
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = ReleaseJobStore(
        tmp_path / "jobs",
        settings=ReleaseRuntimeSettings(lease_seconds=lease_seconds, reserve_bytes=0),
        clock=OffsetClock(),
        free_bytes=lambda _path: 10**12,
    )
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    inventory = coordinator_module._inventory

    def cancelling_inventory(local_store: LocalReleaseStore, checkpoint: object = None) -> object:
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
        time.sleep(lease_seconds)
        return inventory(local_store, checkpoint=checkpoint)

    with (
        patch.object(coordinator_module, "_inventory", side_effect=cancelling_inventory),
        pytest.raises(CancellationError),
    ):
        asyncio.run(execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination)))

    record = store.storage.snapshot()[job_id]
    assert record["state"] != "committed"
    assert "release/manifest.json" not in record["effects"]
    assert not any(thread.name == f"release-lease-{job_id}" for thread in threading.enumerate())


def test_given_transient_heartbeat_error_when_lease_valid_then_monitor_retries(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    monitor = _LifecycleMonitor(store, job_id, fence)
    heartbeat = mocker.patch.object(store, "heartbeat", side_effect=[TimeoutError("contention"), False])
    mocker.patch.object(monitor._stop, "wait", side_effect=[False, False, True])

    monitor._run()

    monitor.check()
    assert heartbeat.call_count == 2


def test_given_cancellation_recorded_when_monitor_still_running_then_lease_keeps_renewing() -> None:
    beats: list[str] = []

    def heartbeat(job_id: str, fence: object) -> bool:
        beats.append(job_id)
        return True

    store = SimpleNamespace(settings=SimpleNamespace(lease_seconds=20), heartbeat=heartbeat, now=time.monotonic)
    monitor = _LifecycleMonitor(store, "job-1", 1)
    monitor._interval = 0.01
    monitor.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                monitor.check()
            except CancellationError:
                break
            time.sleep(0.01)
        recorded = len(beats)
        while len(beats) <= recorded and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        monitor.stop()

    assert len(beats) > recorded


def test_given_checkpoint_when_inventory_then_called_per_object_and_chunk(tmp_path: Path) -> None:
    store = LocalReleaseStore(tmp_path / "release")
    for name, content in (("data/a.bin", b"alpha"), ("data/b.bin", b"beta")):
        store.write_verified_object(name, (content,), length=len(content), sha256=sha256_hex(content))
    calls: list[int] = []

    coordinator_module._inventory(store, checkpoint=lambda: calls.append(1))

    assert len(calls) >= 2


def test_given_checkpoint_raises_during_inventory_then_error_propagates(tmp_path: Path) -> None:
    store = LocalReleaseStore(tmp_path / "release")
    content = b"alpha"
    store.write_verified_object("data/a.bin", (content,), length=len(content), sha256=sha256_hex(content))

    def checkpoint() -> None:
        raise CancellationError("cancellation requested")

    with pytest.raises(CancellationError):
        coordinator_module._inventory(store, checkpoint=checkpoint)


def test_given_task_cancelled_during_threaded_source_inventory_when_executed_then_thread_settled(
    tmp_path: Path,
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    started = threading.Event()
    finished = threading.Event()

    def slow_inventory(self: LocalReleaseSource, checkpoint: object = None) -> tuple:
        started.set()
        for _ in range(300):
            time.sleep(0.01)
            if checkpoint is not None:
                checkpoint()
        finished.set()
        return ()

    async def scenario() -> bool:
        task = asyncio.create_task(
            execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination))
        )
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return finished.is_set()

    with patch.object(LocalReleaseSource, "inventory", slow_inventory):
        assert asyncio.run(scenario()) is True


def test_given_fence_lost_during_source_inventory_when_executed_then_dispatch_stopped_without_effect(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = ReleaseJobStore(
        tmp_path / "jobs",
        settings=ReleaseRuntimeSettings(lease_seconds=5, reserve_bytes=0),
        clock=OffsetClock(),
        free_bytes=lambda _path: 10**12,
    )
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    generation = mocker.patch.object(
        coordinator_module, "run_release_generation", wraps=coordinator_module.run_release_generation
    )

    def live_inventory(self: LocalReleaseSource, checkpoint: object = None) -> tuple:
        for _ in range(400):
            time.sleep(0.01)
            if checkpoint is not None:
                checkpoint()
        return ()

    mocker.patch.object(LocalReleaseSource, "inventory", live_inventory)
    mocker.patch.object(ReleaseJobStore, "heartbeat", side_effect=AuthorityError("fence replaced"))

    with pytest.raises(AuthorityError, match="fence replaced"):
        asyncio.run(execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination)))

    generation.assert_awaited_once()
    assert store.storage.snapshot()[job_id]["state"] != "committed"
    assert not destination.exists() or not any(destination.rglob("*"))
    assert not any(thread.name == f"release-lease-{job_id}" for thread in threading.enumerate())


def test_given_fence_lost_after_commit_when_terminal_diagnostic_rejected_then_success_kept(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    recorded = store.record_diagnostic

    def late_success_diagnostic(
        job: str, fence: int, operation: str, category: str, status: str, *, duration_ms: int = 0
    ) -> None:
        if status == "succeeded":
            raise AuthorityError("fence is no longer current")
        recorded(job, fence, operation, category, status, duration_ms=duration_ms)

    mocker.patch.object(store, "record_diagnostic", side_effect=late_success_diagnostic)

    result = asyncio.run(
        execute_job(store, job_id, ConfiguredExecution(source, reader, selection, context, destination))
    )

    assert result is not None
    assert store.storage.snapshot()[job_id]["state"] == "committed"


def test_given_blob_location_when_native_source_spec_then_secret_free_fields(tmp_path: Path) -> None:
    location = BlobLocation(account_url="https://acct.blob.example", container="release", prefix="run-1/")

    assert _source_spec(location) == {
        "kind": "blob",
        "account_url": "https://acct.blob.example",
        "container": "release",
        "prefix": "run-1",
    }
    assert _source_spec(tmp_path)["kind"] == "local"


def test_given_query_bearing_container_url_when_blob_location_then_refused() -> None:
    client = SimpleNamespace(url="https://acct.blob.example/release?sv=signature")

    with pytest.raises(ReleaseInputError, match="query-free https container address"):
        _ = BlobReleaseSource(client).location


def test_given_persisted_request_without_native_buffers_when_restored_then_fresh_guarded_identities(
    tmp_path: Path,
) -> None:
    _source, _reader, _selection, _context, frozen, inputs, _video = _native_case(tmp_path)
    prepared = prepare_saved_generation(
        frozen.content,
        inputs.request,
        inputs.sources,
        encode_options=inputs.encode_options,
        camera_statistics=inputs.camera_statistics,
    )
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    fence = store.claim(job_id, "worker-restore")
    empty = replace(inputs, source_info={}, source_tables={}, source_tasks=pa.table({}))

    restored = restore_inputs(store, job_id, fence, frozen, prepared, empty)

    assert restored.source_info == inputs.source_info
    assert restored.native_table_identities
    assert restored.native_task_identity
    assert restored.source_tables == {}
    assert generation_request_bytes(frozen, restored) == store.frozen_request(job_id)
    with native_operation(store, job_id, fence, frozen, prepared, restored, mode="validate"):
        pass


@pytest.mark.parametrize("processed", [False, True])
@pytest.mark.parametrize("associated", [False, True])
def test_given_accepted_job_when_independent_runner_reopens_then_verified_without_acceptor_buffers(
    tmp_path: Path, processed: bool, associated: bool
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path, processed=processed)
    destination = tmp_path / "output"
    root = tmp_path / "jobs"
    store = _store(root, OffsetClock())
    intent = _association_intent(destination).model_copy(
        update={"attempt": ATTEMPT, "scope_sha256": sha256_hex(OWNER.encode()), "frozen_input_sha256": frozen.sha256}
    )
    job_id = store.accept(
        replace(
            _request(destination),
            frozen_request=generation_request_bytes(frozen, inputs),
            reserved_bytes=32 * 1024**2,
            execution_binding=source_association_binding(intent) if associated else b"",
        )
    ).job_id
    del store, inputs
    reopened = _store(root, OffsetClock())

    result = asyncio.run(
        execute_job(reopened, job_id, ConfiguredExecution(source, reader, selection, context, destination))
    )

    assert result is not None
    assert verify_published(LocalReleaseStore(destination / ATTEMPT.prefix), result.identity.manifest_sha256) == result
    record = reopened.storage.snapshot()[job_id]
    assert record["state"] == "committed"
    assert record["diagnostics"]["validation.succeeded"]["count"] == 1
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())
    if associated:
        from physical_ai_curation.release.record_storage import RecordLimits

        source.root.rename(tmp_path / "source-unavailable")
        associations = LocalSourceAssociations(destination, limits=RecordLimits())
        assert associations.discover(source_key=intent.source_key, scope_sha256=intent.scope_sha256) == [
            destination / ATTEMPT.prefix
        ]


@pytest.mark.parametrize("foreign", [False, True])
def test_given_cancelled_associated_job_when_runner_reopens_then_pending_cleanup_precedes_capacity_release(
    tmp_path: Path, foreign: bool
) -> None:
    from physical_ai_curation.release.record_storage import RecordLimits

    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    intent = _association_intent(destination).model_copy(
        update={"attempt": ATTEMPT, "scope_sha256": sha256_hex(OWNER.encode()), "frozen_input_sha256": frozen.sha256}
    )
    job_id = store.accept(
        replace(
            _request(destination),
            frozen_request=generation_request_bytes(frozen, inputs),
            reserved_bytes=32 * 1024**2,
            execution_binding=source_association_binding(intent),
        )
    ).job_id
    associations = LocalSourceAssociations(destination, limits=RecordLimits())
    associations.ensure_intent(intent.model_copy(update={"source_revision": "foreign"}) if foreign else intent)
    store.request_cancel(job_id, scope=_request(destination).scope, principal=OWNER)
    source.root.rename(tmp_path / "removed-source")
    configured = ConfiguredExecution(None, reader, selection, context, destination)

    if foreign:
        with pytest.raises(ReleaseVerificationError):
            asyncio.run(execute_job(store, job_id, configured))
    else:
        assert asyncio.run(execute_job(store, job_id, configured)) is None

    snapshot = store.storage.snapshot()
    if foreign:
        assert snapshot[job_id]["state"] == "cancelling"
        assert snapshot[job_id]["reservation"]["status"] != "released"
    else:
        assert job_id not in snapshot
    assert (destination / "_release_associations" / f"{ATTEMPT.package_id}.json").exists() is foreign


@pytest.mark.parametrize("missing", [False, True])
def test_given_stale_or_missing_source_when_independent_runner_restores_then_no_destination_effect(
    tmp_path: Path, missing: bool
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(
        replace(
            _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
        )
    ).job_id
    if not missing:
        (source.root / "meta/info.json").write_bytes(b"{}")

    with pytest.raises(ReleaseInputError):
        asyncio.run(
            execute_job(
                store, job_id, ConfiguredExecution(None if missing else source, reader, selection, context, destination)
            )
        )

    assert not destination.exists()
    assert store.storage.snapshot()[job_id]["diagnostics"]["validation.failed"]["count"] == 1


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_given_invalid_queue_limit_when_runner_pass_then_no_job_is_resolved(
    tmp_path: Path, mocker: MockerFixture, limit: object
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    resolve = mocker.Mock()

    with pytest.raises(ValueError, match="between 1 and 100"):
        asyncio.run(run_queue(store, resolve, limit=limit))

    resolve.assert_not_called()


@pytest.mark.parametrize("limit", [1, 2])
def test_given_leased_and_queued_jobs_when_runner_pass_then_bounded_safe_outcomes(
    tmp_path: Path, mocker: MockerFixture, limit: int
) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "jobs", clock)
    destination = tmp_path / "output"
    job_ids = [
        store.accept(
            _request(destination, key=f"queue-{index}", attempt=PublicationAttempt(f"package-{index}", "attempt-1"))
        ).job_id
        for index in range(3)
    ]
    store.claim(job_ids[0], "active-worker")
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)
    resolve = mocker.Mock(return_value=configured)
    execute = mocker.patch(
        "physical_ai_curation.release.runner.execute_job", side_effect=[None, RuntimeError("private worker detail")]
    )

    outcomes = asyncio.run(run_queue(store, resolve, worker_id="queue-worker", limit=limit))

    queued = sorted(job_ids[1:])
    assert outcomes == tuple(
        {"job_id": job_id, "status": "settled" if index == 0 else "recovery_required"}
        for index, job_id in enumerate(queued[:limit])
    )
    assert [call.args for call in resolve.call_args_list] == [(job_id, b'{"request":1}') for job_id in queued[:limit]]
    assert execute.await_count == limit
    assert all(call.kwargs == {"worker_id": "queue-worker"} for call in execute.await_args_list)


def test_given_permanent_resolver_failure_on_lowest_job_when_queue_passes_then_healthy_followers_progress(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "jobs", clock)
    destination = tmp_path / "output"
    job_ids = sorted(
        store.accept(
            _request(destination, key=f"retry-{index}", attempt=PublicationAttempt(f"retry-{index}", "attempt-1"))
        ).job_id
        for index in range(4)
    )
    failing, followers = job_ids[0], job_ids[1:]
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)
    resolver_calls: list[str] = []

    def resolve(job_id: str, _frozen: bytes) -> ConfiguredExecution:
        resolver_calls.append(job_id)
        if job_id == failing:
            raise RuntimeError("private resolver detail")
        return configured

    def settle(active: ReleaseJobStore, job_id: str, _configured: ConfiguredExecution, *, worker_id: str) -> None:
        active.claim(job_id, worker_id)

    execute = mocker.patch("physical_ai_curation.release.runner.execute_job", side_effect=settle)

    first = asyncio.run(run_queue(store, resolve, worker_id="queue-worker", limit=1))
    assert first == (
        {"job_id": failing, "status": "recovery_required"},
        {"job_id": followers[0], "status": "settled"},
    )

    second = asyncio.run(run_queue(store, resolve, worker_id="queue-worker", limit=1))
    assert second == ({"job_id": followers[1], "status": "settled"},)

    restarted = _store(tmp_path / "jobs", clock)
    third = asyncio.run(run_queue(restarted, resolve, worker_id="queue-worker", limit=1))
    assert third == ({"job_id": followers[2], "status": "settled"},)
    assert resolver_calls == [failing, followers[0], followers[1], followers[2]]

    clock.advance(3600)
    fourth = asyncio.run(run_queue(restarted, resolve, worker_id="queue-worker", limit=1))
    assert fourth == ({"job_id": followers[0], "status": "settled"},)
    retried = asyncio.run(run_queue(restarted, resolve, worker_id="queue-worker", limit=100))
    assert retried[-1] == {"job_id": failing, "status": "recovery_required"}
    assert restarted.storage.snapshot()[failing]["failed_attempts"] == 2
    assert execute.await_count == 6


def test_given_many_failing_jobs_when_queue_pass_then_failed_dispatches_are_bounded(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    failed_dispatch_bound = 8
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    job_ids = sorted(
        store.accept(
            _request(destination, key=f"bulk-{index}", attempt=PublicationAttempt(f"bulk-{index}", "attempt-1"))
        ).job_id
        for index in range(failed_dispatch_bound + 2)
    )
    resolve = mocker.Mock(side_effect=RuntimeError("private resolver detail"))
    execute = mocker.patch("physical_ai_curation.release.runner.execute_job")

    outcomes = asyncio.run(run_queue(store, resolve, limit=1))

    assert outcomes == tuple(
        {"job_id": job_id, "status": "recovery_required"} for job_id in job_ids[:failed_dispatch_bound]
    )
    assert resolve.call_count == failed_dispatch_bound
    execute.assert_not_called()


def test_given_claim_during_resolution_when_resolver_fails_then_stale_failure_is_not_recorded(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id

    def resolve(active_job_id: str, _frozen: bytes) -> ConfiguredExecution:
        store.claim(active_job_id, "other-worker")
        raise RuntimeError("private resolver detail")

    execute = mocker.patch("physical_ai_curation.release.runner.execute_job")

    asyncio.run(run_queue(store, resolve, worker_id="queue-worker", limit=1))

    row = store.storage.snapshot()[job_id]
    assert row["lease_owner"] == "other-worker"
    assert row["failed_attempts"] == 0
    assert row["retry_not_before"] is None
    assert row["last_failure"] is None
    execute.assert_not_called()


def test_given_reused_worker_name_when_resolver_loses_race_then_live_claim_is_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id

    def resolve(active_job_id: str, _frozen: bytes) -> ConfiguredExecution:
        store.claim(active_job_id, "release-worker")
        raise RuntimeError("resolver unavailable")

    asyncio.run(run_queue(store, resolve))

    row = store.storage.snapshot()[job_id]
    assert row["fence"] == 1
    assert row["failed_attempts"] == 0
    assert row["retry_not_before"] is None


def test_given_failing_cancellation_backlog_when_restarted_then_healthy_job_progresses(tmp_path: Path) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "jobs", clock)
    destination = tmp_path / "output"
    job_ids = sorted(
        store.accept(
            _request(destination, key=f"cancel-{index}", attempt=PublicationAttempt(f"cancel-{index}", "attempt-1"))
        ).job_id
        for index in range(10)
    )
    for job_id in job_ids:
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    healthy = job_ids[-1]

    def resolve(job_id: str, _frozen: bytes) -> ConfiguredExecution:
        if job_id != healthy:
            raise RuntimeError("resolver unavailable")
        return ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)

    asyncio.run(run_queue(store, resolve))
    restarted = _store(tmp_path / "jobs", clock)
    asyncio.run(run_queue(restarted, resolve))

    assert healthy not in restarted.storage.snapshot()


def test_given_retry_environment_when_settings_loaded_then_validated_bounds_apply(tmp_path: Path) -> None:
    settings = ReleaseRuntimeSettings.from_environment(
        {
            "CURATION_RELEASE_RETRY_BASE_SECONDS": "7",
            "CURATION_RELEASE_RETRY_MAX_SECONDS": "21",
            "CURATION_RELEASE_MAX_FAILED_DISPATCHES": "2",
        }
    )
    assert settings.retry_base_seconds == 7
    assert settings.retry_max_seconds == 21
    assert settings.max_failed_dispatches == 2
    clock = OffsetClock()
    store = ReleaseJobStore(tmp_path / "jobs", settings=settings, clock=clock, free_bytes=lambda _path: 10**12)
    job_id = store.accept(_request(tmp_path / "output")).job_id
    before = clock()
    assert store.record_dispatch_failure(job_id, "resolver_failed", expected_fence=0, worker_id="worker")
    assert before + 7 <= store.storage.snapshot()[job_id]["retry_not_before"] <= clock() + 7


@pytest.mark.parametrize("value", ["0", "-1", "nan", "1.5", "3601"])
def test_given_invalid_retry_environment_when_settings_loaded_then_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_RETRY_BASE_SECONDS": value})


def test_given_own_claim_when_worker_fails_then_failure_is_recorded_with_backoff(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), tmp_path / "output")

    def claim_then_fail(active: ReleaseJobStore, active_job_id: str, _configured: object, *, worker_id: str) -> None:
        active.claim(active_job_id, worker_id)
        raise RuntimeError("private worker detail")

    mocker.patch("physical_ai_curation.release.runner.execute_job", side_effect=claim_then_fail)

    outcomes = asyncio.run(run_queue(store, mocker.Mock(return_value=configured), worker_id="queue-worker", limit=1))

    assert outcomes == ({"job_id": job_id, "status": "recovery_required"},)
    row = store.storage.snapshot()[job_id]
    assert row["lease_owner"] == "queue-worker"
    assert row["failed_attempts"] == 1
    assert row["last_failure"] == "worker_failed"
    assert row["retry_not_before"] is not None


@pytest.mark.parametrize(
    "code",
    ["processing_confirmation_required", "insufficient_capacity", "source_unavailable", "selection_exceeds_bounds"],
)
def test_given_capture_refusal_when_queue_runs_then_needs_input_is_not_recaptured(tmp_path: Path, code: str) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "jobs", clock)
    destination = tmp_path / "output"
    request = replace(_request(destination), frozen_request=b'{"schema_version":"curated-release-intent/v1"}')
    job_id = store.accept(request).job_id
    captures = []

    async def capture(checkpoint: object) -> tuple[bytes, bytes, int, ConfiguredExecution]:
        captures.append(checkpoint)
        raise ReleaseRefusal(code)

    configured = ConfiguredExecution(None, SavedReader(), _selection(), None, destination, capture)
    asyncio.run(run_queue(store, lambda *_args: configured))
    clock.advance(7200)
    assert asyncio.run(run_queue(store, lambda *_args: configured)) == ()

    status = store.get(job_id, scope=SCOPE, principal=OWNER)
    assert len(captures) == 1
    assert status.state == "needs_input"
    assert status.refusal_code == code
    assert not status.recovery_required
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    asyncio.run(run_queue(store, lambda *_args: configured))
    assert job_id not in store.storage.snapshot()


def test_given_job_page_when_projected_then_binding_and_acceptance_use_one_snapshot(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    snapshot = mocker.spy(store.storage, "snapshot")

    page = store.page(scope=SCOPE, principal=OWNER)

    assert page.items[0].job_id == job_id
    assert page.execution_bindings[job_id] == b""
    assert page.accepted_times[job_id]
    assert snapshot.call_count == 1


def test_given_cancel_during_capture_when_queue_runs_then_next_pass_cleans_without_backoff(tmp_path: Path) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    request = replace(_request(destination), frozen_request=b'{"schema_version":"curated-release-intent/v1"}')
    job_id = store.accept(request).job_id

    async def capture(checkpoint: object) -> tuple[bytes, bytes, int, ConfiguredExecution]:
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
        raise CancellationError("cancellation requested")

    configured = ConfiguredExecution(None, SavedReader(), _selection(), None, destination, capture)
    asyncio.run(run_queue(store, lambda *_args: configured))

    row = store.storage.snapshot()[job_id]
    assert row["last_failure"] is None
    assert row["failed_attempts"] == 0
    assert row["lease_expires"] is None
    assert not any(event["event"] == "dispatch.failed" for event in row["events"])
    asyncio.run(run_queue(store, lambda *_args: configured))
    assert job_id not in store.storage.snapshot()


def test_given_large_local_jsonl_when_limited_then_episode_metadata_is_streamed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    from physical_ai_dataset_io.lerobot import LocalLeRobotFiles, iter_episodes

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "total_episodes": 100000,
                "fps": 30,
                "data_path": "data/episode_{episode_index:06d}.parquet",
                "video_path": "videos/{video_key}/episode_{episode_index:06d}.mp4",
                "features": {"front": {"dtype": "video"}},
            }
        )
    )
    (tmp_path / "meta/episodes.jsonl").write_bytes(
        b'{"episode_index":0,"tasks":["pick"],"length":2}\n' + b"invalid-json\n" * 100000
    )
    files = LocalLeRobotFiles(tmp_path)
    read = mocker.spy(files, "read_bytes")

    records = list(iter_episodes(files, limit=1))

    assert [record.episode_index for record in records] == [0]
    assert "meta/episodes.jsonl" not in [call.args[0] for call in read.call_args_list]


@pytest.mark.parametrize(
    "template",
    [
        "data/{episode_index:>9999999999}.parquet",
        "{episode_index!r}",
        "{episode_index.real}",
        "{episode_index:{episode_chunk}}",
    ],
)
def test_given_unsafe_v21_template_when_resolving_then_refused_before_formatting(template: str) -> None:
    from physical_ai_curation.release.source_format import object_path

    with pytest.raises(ReleaseInputError, match="template is invalid"):
        object_path(
            {"codebase_version": "v2.1", "chunks_size": 1000, "data_path": template},
            {"episode_index": 0},
            "data",
        )


def test_given_cancelling_job_under_retry_backoff_without_stop_obligation_when_queue_pass_then_backoff_applies(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    request = _request(destination)
    job_id = store.accept(request).job_id
    assert store.record_dispatch_failure(job_id, "resolver_failed", expected_fence=0, worker_id="queue-worker")
    assert store.storage.snapshot()[job_id]["retry_not_before"] is not None
    store.request_cancel(job_id, scope=request.scope, principal=request.principal)
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)
    execute = mocker.patch("physical_ai_curation.release.runner.execute_job")

    asyncio.run(run_queue(store, mocker.Mock(return_value=configured), worker_id="queue-worker", limit=1))

    execute.assert_not_called()


def test_given_cancelling_job_under_retry_backoff_with_unstopped_runtime_when_queue_pass_then_urgent(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    request = _request(destination)
    job_id = store.accept(request).job_id
    assert store.record_dispatch_failure(job_id, "resolver_failed", expected_fence=0, worker_id="queue-worker")
    store.request_cancel(job_id, scope=request.scope, principal=request.principal)
    with store.storage.transaction() as records:
        records[job_id]["runtimes"]["unproven-runtime"] = {"stopped": False}
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)
    execute = mocker.patch("physical_ai_curation.release.runner.execute_job")

    asyncio.run(run_queue(store, mocker.Mock(return_value=configured), worker_id="queue-worker", limit=1))

    execute.assert_awaited_once()
    assert execute.await_args.args[1] == job_id


def test_given_recovery_request_under_retry_backoff_when_queue_pass_then_backoff_is_reset(tmp_path: Path) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    assert store.record_dispatch_failure(job_id, "resolver_failed", expected_fence=0, worker_id="queue-worker")
    assert store.storage.snapshot()[job_id]["retry_not_before"] is not None
    assert store.get(job_id, scope=SCOPE, principal=OWNER).recovery_required

    store.request_recovery(job_id, scope=SCOPE, principal=OWNER)

    assert store.storage.snapshot()[job_id]["retry_not_before"] is None
    assert store.get(job_id, scope=SCOPE, principal=OWNER).recovery_required
    store.claim(job_id, "retry-worker")
    assert not store.get(job_id, scope=SCOPE, principal=OWNER).recovery_required


def test_given_uncertain_recovery_under_retry_backoff_when_queue_pass_then_recovery_is_eligible(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    job_id = store.accept(_request(destination)).job_id
    assert store.record_dispatch_failure(job_id, "worker_failed", expected_fence=0, worker_id="queue-worker")
    with store.storage.transaction() as records:
        records[job_id]["effects"]["uncertain-effect"] = {"status": "uncertain"}
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)
    execute = mocker.patch("physical_ai_curation.release.runner.execute_job")

    asyncio.run(run_queue(store, mocker.Mock(return_value=configured), worker_id="queue-worker", limit=1))

    execute.assert_awaited_once()
    assert execute.await_args.args[1] == job_id


def test_given_publication_in_progress_when_dispatch_failure_recorded_then_row_is_not_mutated(tmp_path: Path) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    with store.storage.transaction() as records:
        records[job_id]["state"] = "committing"

    assert not store.record_dispatch_failure(job_id, "worker_failed", expected_fence=0, worker_id="queue-worker")

    row = store.storage.snapshot()[job_id]
    assert row["state"] == "committing"
    assert row["failed_attempts"] == 0
    assert row["last_failure"] is None


@pytest.mark.parametrize("state", ["committed", "cancelled"])
def test_given_terminal_job_when_runner_pass_then_not_redispatched(mocker: MockerFixture, state: str) -> None:
    store = mocker.Mock(spec=ReleaseJobStore)
    store.storage = mocker.Mock()
    store.storage.snapshot.return_value = {"terminal-job": {"state": state}}
    resolve = mocker.Mock()

    assert asyncio.run(run_queue(store, resolve)) == ()

    resolve.assert_not_called()
    store.frozen_request.assert_not_called()


def test_given_cancelled_job_when_runner_retries_then_no_source_or_output_required(tmp_path: Path) -> None:
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    request = _request(destination)
    job_id = store.accept(request).job_id
    store.request_cancel(job_id, scope=request.scope, principal=request.principal)
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)

    assert asyncio.run(execute_job(store, job_id, configured)) is None
    assert asyncio.run(execute_job(store, job_id, configured)) is None

    assert job_id not in store.storage.snapshot()
    assert not destination.exists()


def test_given_wrong_destination_when_runner_executes_then_job_remains_unclaimed(tmp_path: Path) -> None:
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(tmp_path / "accepted")).job_id
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), tmp_path / "other")

    with pytest.raises(AuthorityError, match="configured destination differs"):
        asyncio.run(execute_job(store, job_id, configured))

    assert store.storage.snapshot()[job_id]["state"] == "accepted"
    assert not configured.destination.exists()


def test_given_unsupported_durable_request_when_runner_executes_then_failure_is_recorded(tmp_path: Path) -> None:
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(_request(destination)).job_id
    configured = ConfiguredExecution(None, SavedReader(), _selection(), _context(), destination)

    with pytest.raises(ReleaseInputError, match="unsupported durable generation request"):
        asyncio.run(execute_job(store, job_id, configured))

    assert store.storage.snapshot()[job_id]["diagnostics"]["validation.failed"]["count"] == 1
    assert not destination.exists()


def _worker_configuration(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": "curated-release-worker/v1",
        "job_root": str(tmp_path / "jobs"),
        "source_root": str(_source_root(tmp_path)),
        "destination_root": str(tmp_path / "output"),
        "selection": TypeAdapter(ReleaseSelection).dump_python(_selection(), mode="json"),
        "context": TypeAdapter(FreezeContext).dump_python(
            replace(_context(), principal_scope_id="localuser"), mode="json"
        ),
        "finalized": True,
    }


@pytest.mark.parametrize("manifest", [None, "a" * 64])
@pytest.mark.parametrize("judge_authority", [False, True])
def test_given_valid_worker_configuration_when_cli_executes_then_versioned_safe_result(
    tmp_path: Path,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    manifest: str | None,
    judge_authority: bool,
) -> None:
    config = _worker_configuration(tmp_path)
    if judge_authority:
        config["judge_state_root"] = str(tmp_path / "judge")
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(config))
    result = None if manifest is None else SimpleNamespace(identity=SimpleNamespace(manifest_sha256=manifest))
    execute = mocker.patch("physical_ai_curation.release.runner.execute_job", return_value=result)

    assert run_worker(["--config", str(path), "--job", "accepted-job"]) == 0

    expected = {"schema_version": "curated-release-worker-result/v1", "job_id": "accepted-job", "status": "settled"}
    if manifest is not None:
        expected["manifest_sha256"] = manifest
    assert json.loads(capsys.readouterr().out) == expected
    execute.assert_awaited_once()
    store, job_id, configured = execute.await_args.args
    assert isinstance(store, ReleaseJobStore)
    assert job_id == "accepted-job"
    assert configured.context.principal_scope_id == "localuser"
    assert configured.source.root == Path(config["source_root"])
    assert configured.destination == Path(config["destination_root"])
    assert (configured.reader.judge_evidence is not None) is judge_authority


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "future/v2"),
        ("finalized", False),
        ("unexpected", "private"),
        ("judge_state_root", "  "),
        ("judge_state_root", 123),
        ("context", TypeAdapter(FreezeContext).dump_python(_context(), mode="json")),
    ],
)
def test_given_invalid_worker_configuration_when_cli_executes_then_no_dispatch_or_private_detail(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str], field: str, value: object
) -> None:
    config = _worker_configuration(tmp_path)
    config[field] = value
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(config))
    execute = mocker.patch("physical_ai_curation.release.runner.execute_job")

    assert run_worker(["--config", str(path), "--job", "private-job"]) == 1

    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "curated-release-worker-result/v1",
        "status": "recovery_required",
    }
    execute.assert_not_awaited()


@pytest.mark.parametrize("failure", ["missing", "oversized", "execution"])
def test_given_worker_failure_when_cli_executes_then_safe_recovery_response(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    path = tmp_path / "worker.json"
    if failure == "oversized":
        path.write_bytes(b" " * (1024**2 + 1))
    elif failure == "execution":
        path.write_text(json.dumps(_worker_configuration(tmp_path)))
    execute = mocker.patch(
        "physical_ai_curation.release.runner.execute_job", side_effect=RuntimeError("private execution failure")
    )

    assert run_worker(["--config", str(path), "--job", "private-job"]) == 1

    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "curated-release-worker-result/v1",
        "status": "recovery_required",
    }
    assert execute.await_count == (1 if failure == "execution" else 0)


def test_given_container_url_when_blob_location_then_container_and_https_account() -> None:
    client = SimpleNamespace(url="https://acct.blob.example/release")

    location = BlobReleaseSource(client).location

    assert location.container == "release"
    assert location.account_url.startswith("https://")


def _native_case(
    tmp_path: Path,
    *,
    processed: bool = False,
    camera_statistics: bool = True,
    null_camera_statistics: bool = False,
) -> tuple:
    root = tmp_path / "native"
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    camera = "observation.images.front"
    video_path = f"videos/{camera}/chunk-000/file-000.mp4"
    (root / video_path).parent.mkdir(parents=True)
    video = _encode_source([np.full((24, 32, 3), 30, dtype=np.uint8)] * 2)
    (root / video_path).write_bytes(video)
    table = pa.table(
        {
            "episode_index": [0, 0],
            "frame_index": [0, 1],
            "index": [0, 1],
            "timestamp": [0.0, 0.1],
            "task_index": [0, 0],
            "observation.state": [[1.0, 1.0], [2.0, 2.0]],
        }
    )
    pq.write_table(table, root / "data/chunk-000/file-000.parquet")
    tasks = pa.table({"task_index": [0], "task": ["pick"]})
    pq.write_table(tasks, root / "meta/tasks.parquet")
    info = {
        "codebase_version": "v3.0",
        "fps": 10.0,
        "total_episodes": 1,
        "total_frames": 2,
        "features": {
            **{
                name: {"dtype": "int64", "shape": [1], "names": None}
                for name in ("episode_index", "frame_index", "index", "task_index")
            },
            "timestamp": {"dtype": "float64", "shape": [1], "names": None},
            "observation.state": {"dtype": "float64", "shape": [2], "names": ["x", "y"]},
            camera: {
                "dtype": "video",
                "shape": [24, 32, 3],
                "info": {"video.codec": "h264", "video.pix_fmt": "yuv420p"},
            },
        },
    }
    summary = {
        "count": [2],
        **{name: [[[value]]] * 3 for name, value in (("min", 0.1), ("max", 0.2), ("mean", 0.15), ("std", 0.01))},
    }
    row = {
        "episode_index": 0,
        "length": 2,
        "data/chunk_index": 0,
        "data/file_index": 0,
        f"videos/{camera}/chunk_index": 0,
        f"videos/{camera}/file_index": 0,
        f"videos/{camera}/from_timestamp": 0.0,
        f"videos/{camera}/to_timestamp": 0.2,
        **{f"stats/{camera}/{name}": value for name, value in summary.items()},
    }
    cameras = [camera]
    if processed:
        wrist = "observation.images.wrist"
        cameras.append(wrist)
        second = root / video_path.replace(camera, wrist)
        second.parent.mkdir(parents=True)
        second.write_bytes(video)
        info["features"][wrist] = info["features"][camera]
        row.update({name.replace(camera, wrist): value for name, value in list(row.items()) if camera in name})
    if not camera_statistics:
        row = {
            name: None if name.startswith("stats/") else value
            for name, value in row.items()
            if null_camera_statistics or not name.startswith("stats/")
        }
    (root / "meta/info.json").write_text(json.dumps(info))
    pq.write_table(pa.Table.from_pylist([row]), root / "meta/episodes/chunk-000/file-000.parquet")
    source = LocalReleaseSource(root, finalized=True)
    reader, selection, context = SavedReader(), _selection(), _context()
    if processed:
        reader.edit = SavedResource({"operations": {"globalTransform": {"colorFilter": "invert"}}}, "edit-v1")
        selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="edit-v1"))
    frozen = asyncio.run(freeze_release_inputs(source, reader, selection, context))
    endpoint = StorageEndpoint(alias="local", kind="local", volume="same")
    request = ObjectPlanRequest(
        source=endpoint,
        destination=endpoint,
        scratch=endpoint,
        target_format=VersionIdentity(name="lerobot", version="v3.0"),
        frozen_input_sha256=frozen.sha256,
        selected_episode_ids=frozen.content.selected_episode_ids,
        windows=tuple(
            MediaWindow(
                frozen.content.selected_episode_ids[0],
                name,
                next(item for item in frozen.content.source_objects if item.path == video_path.replace(camera, name)),
                0,
                0.2,
                0.2,
            )
            for name in cameras
        ),
        tool=VersionIdentity(name="pyav-libx264", version="test"),
        source_objects=frozen.content.source_objects,
    )
    episode = frozen.content.selected_episode_ids[0]
    inputs = GenerationInputs(
        request,
        {episode: SubsetEpisodeInput(episode, 0, 2, 10.0)},
        root,
        info,
        {episode: table},
        tasks,
        {episode: {name: summary for name in cameras} if camera_statistics else {}},
        None,
        None,
        ProducerIdentity.model_validate(_producer_dict()),
        "",
        8 * 1024**2,
        1024**2,
    )
    prepared = prepare_saved_generation(
        frozen.content, request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    return source, reader, selection, context, frozen, replace(inputs, confirmed_digest=prepared.plan.digest), video


@pytest.mark.parametrize("processed", [False, True])
def test_given_frozen_native_source_when_planning_then_fixture_equivalent_without_acceptor_tables(
    tmp_path: Path, processed: bool
) -> None:
    from physical_ai_curation.release.coordinator import plan_generation_inputs

    source, _reader, _selection, _context_value, frozen, expected, _video = _native_case(tmp_path, processed=processed)
    planned = plan_generation_inputs(
        source,
        frozen,
        source_endpoint=expected.request.source,
        destination_endpoint=expected.request.destination,
        scratch_endpoint=expected.request.scratch,
        producer=expected.producer,
        tool=expected.request.tool,
        scratch_bytes=expected.scratch_bytes,
        output_bytes=expected.output_bytes,
    )

    assert generation_request_bytes(frozen, planned) == generation_request_bytes(frozen, expected)
    assert planned.source_tables == {}
    assert planned.source_tasks.num_rows == 0
    assert not (tmp_path / "output").exists()


NATIVE_BLOB_CODE = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from test_release_jobs import fs_factory
from physical_ai_curation.release.native_worker import _process
_process(Path(sys.argv[1]), container_factory=fs_factory(Path(sys.argv[2])))
"""


def test_given_unrestored_native_inputs_when_generating_then_copy_precedes_one_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.release import native_worker

    source, reader, selection, context, frozen, inputs, video = _native_case(tmp_path)
    inputs = replace(inputs, source_info={}, source_tables={}, source_tasks=pa.table({}))
    destination = tmp_path / "output"
    store = _store(tmp_path / "ledger", OffsetClock())
    job = store.accept(
        replace(
            _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
        )
    )
    fence = store.claim(job.job_id, "worker")
    modes = []
    original = native_worker.native_operation

    def observe(*args: Any, **kwargs: Any) -> Any:
        modes.append(kwargs["mode"])
        copied = list((destination / ATTEMPT.prefix).rglob("*.mp4"))
        assert len(copied) == 1, "unchanged copies must precede native assembly"
        assert copied[0].read_bytes() == video
        return original(*args, **kwargs)

    monkeypatch.setattr(native_worker, "native_operation", observe)
    result = asyncio.run(
        run_release_generation(
            store=store,
            job_id=job.job_id,
            fence=fence,
            destination=destination,
            attempt=ATTEMPT,
            expected=frozen,
            source=source,
            reader=reader,
            selection=selection,
            context=context,
            inputs=inputs,
        )
    )
    assert modes == ["assemble"]
    assert result is not None
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).state == "committed"


@pytest.mark.parametrize("invalid_frames", [False, True])
def test_given_invalid_or_overbudget_projection_when_generating_then_refused_before_runtime_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_frames: bool
) -> None:
    source, reader, _old_selection, context, _old_frozen, inputs, _video = _native_case(tmp_path)
    reader.annotations = SavedResource(
        {"annotations": [{"object_detections": [{"frame_index": 99 if invalid_frames else 0}]}]},
        "annotation-v1",
    )
    selection = _selection(SavedSelection("annotation", 0, "annotation-v1"))
    frozen = asyncio.run(freeze_release_inputs(source, reader, selection, context))
    inputs = replace(inputs, request=replace(inputs.request, frozen_input_sha256=frozen.sha256))
    prepared = prepare_saved_generation(
        frozen.content, inputs.request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    inputs = replace(
        inputs,
        confirmed_digest=prepared.plan.digest,
        output_bytes=inputs.output_bytes if invalid_frames else prepared.plan.capacity.payload_bytes,
    )
    destination = tmp_path / "output"
    store = _store(tmp_path / "ledger", OffsetClock())
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job = store.accept(request)
    fence = store.claim(job.job_id, "worker")

    def forbid_dispatch(*args: object, **kwargs: object) -> None:
        raise AssertionError("projection rejection must precede runtime and destination effects")

    monkeypatch.setattr("physical_ai_curation.release.native_worker.native_operation", forbid_dispatch)
    monkeypatch.setattr("physical_ai_curation.release.coordinator._publication_runtime", forbid_dispatch)
    with pytest.raises((ReleaseInputError, ReleasePlanError), match=r"outside|destination budget"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=inputs,
            )
        )
    assert not destination.exists()
    assert store.get(job.job_id, scope=request.scope, principal=OWNER).scratch_bytes == 0


def _v21_case(
    tmp_path: Path, *, with_stats: bool = True, stats_file: bool = True
) -> tuple[LocalReleaseSource, FrozenReleaseInputs, GenerationInputs]:
    source, reader, selection, context, _, inputs, _ = _native_case(tmp_path)
    root = source.root
    camera = "observation.images.front"
    video = f"videos/chunk-000/{camera}/episode_000000.mp4"
    (root / video).parent.mkdir(parents=True)
    (root / f"videos/{camera}/chunk-000/file-000.mp4").rename(root / video)
    (root / "data/chunk-000/file-000.parquet").rename(root / "data/chunk-000/episode_000000.parquet")
    info = dict(inputs.source_info)
    info.update(
        codebase_version="v2.1",
        chunks_size=1000,
        data_path="data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        video_path="videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
    )
    (root / "meta/info.json").write_text(json.dumps(info))
    (root / "meta/tasks.jsonl").write_text(json.dumps({"task_index": 0, "task": "pick"}) + "\n")
    (root / "meta/episodes.jsonl").write_text(json.dumps({"episode_index": 0, "tasks": ["pick"], "length": 2}) + "\n")
    stats = next(iter(inputs.camera_statistics.values())) if with_stats else {}
    if stats_file:
        (root / "meta/episodes_stats.jsonl").write_text(json.dumps({"episode_index": 0, "stats": stats}) + "\n")
    (root / "meta/tasks.parquet").unlink()
    (root / "meta/episodes/chunk-000/file-000.parquet").unlink()
    frozen = asyncio.run(freeze_release_inputs(source, reader, selection, context))
    planned = coordinator_module.plan_generation_inputs(
        source,
        frozen,
        source_endpoint=inputs.request.source,
        destination_endpoint=inputs.request.destination,
        scratch_endpoint=inputs.request.scratch,
        producer=inputs.producer,
        tool=inputs.request.tool,
        scratch_bytes=inputs.scratch_bytes,
        output_bytes=inputs.output_bytes,
    )
    return source, frozen, planned


def test_given_v21_source_when_planned_then_copies_bind_v21_objects_to_v3_outputs(tmp_path: Path) -> None:
    _, frozen, planned = _v21_case(tmp_path)
    assert planned.producer.source_format.version == "v2.1"
    prepared = prepare_saved_generation(
        frozen.content, planned.request, planned.sources, camera_statistics=planned.camera_statistics
    )
    copies = {obj.output_path: obj.source_path for obj in prepared.plan.objects if obj.action == "copy"}
    original = "videos/chunk-000/observation.images.front/episode_000000.mp4"
    assert copies == {"videos/observation.images.front/chunk-000/file-000.mp4": original}


def test_given_v21_source_without_video_statistics_when_planned_then_copy_warned_not_blocked(tmp_path: Path) -> None:
    _, frozen, planned = _v21_case(tmp_path, with_stats=False)
    prepared = prepare_saved_generation(
        frozen.content, planned.request, planned.sources, camera_statistics=planned.camera_statistics
    )
    assert prepared.plan.blocking == ()
    assert prepared.plan.submittable
    assert any("statistics unavailable for observation.images.front" in item for item in prepared.plan.warnings)
    assert all(obj.action != "transcode" for obj in prepared.plan.objects)
    assert all(not summary for summary in planned.camera_statistics.values())


def test_given_v21_source_without_episode_statistics_file_when_planned_then_not_refused(tmp_path: Path) -> None:
    _, frozen, planned = _v21_case(tmp_path, with_stats=False, stats_file=False)
    prepared = prepare_saved_generation(
        frozen.content, planned.request, planned.sources, camera_statistics=planned.camera_statistics
    )
    assert prepared.plan.submittable
    assert all(not summary for summary in planned.camera_statistics.values())


def test_given_v21_source_when_validated_and_assembled_then_v3_native_metadata(tmp_path: Path) -> None:
    from physical_ai_curation.release.tables import generate_native_selection

    source, frozen, planned = _v21_case(tmp_path)
    bound = coordinator_module._validate_native_inputs(source, frozen, planned, 8 * 1024**2, restore=True)
    prepared = prepare_saved_generation(
        frozen.content, planned.request, planned.sources, camera_statistics=planned.camera_statistics
    )
    result = generate_native_selection(
        prepared,
        bound.source_info,
        bound.source_tables,
        bound.source_tasks,
        {},
        confirmed_digest=planned.confirmed_digest,
    )
    assert result.metadata.info["codebase_version"] == "v3.0"
    assert result.metadata.info["total_frames"] == 2
    (row,) = result.metadata.episodes.to_pylist()
    assert row["videos/observation.images.front/file_index"] == 0
    assert row["videos/observation.images.front/to_timestamp"] == pytest.approx(0.2)


def test_given_v21_metadata_over_row_budget_when_read_then_refused_before_extra_decode() -> None:
    from physical_ai_curation.release.source_format import v21_tasks_table

    with pytest.raises(ReleaseInputError, match="row budget"):
        v21_tasks_table(lambda _path: b'{"task_index":0,"task":"pick"}\nnot-json\n', max_rows=1)


def _blob_native_case(
    tmp_path: Path, *, processed: bool = False, saved: bool = False, version: str = "v3.0", with_stats: bool = True
) -> tuple:
    if version == "v2.1":
        local, local_frozen, inputs = _v21_case(tmp_path, with_stats=with_stats, stats_file=with_stats)
        reader, selection, context = SavedReader(), _selection(), _context()
        inputs = coordinator_module._validate_native_inputs(local, local_frozen, inputs, 8 * 1024**2, restore=True)
    else:
        local, reader, selection, context, _, inputs, _ = _native_case(tmp_path, processed=processed)
    sdk = tmp_path / "sdk"
    remote = FsContainer(sdk, "example", "source")
    for path in local.root.rglob("*"):
        if path.is_file():
            remote.put("native/" + path.relative_to(local.root).as_posix(), path.read_bytes())
    source = BlobReleaseSource(remote, prefix="native")
    if version == "v2.1":
        from physical_ai_curation.release.coordinator import select_blob_dependencies

        source = select_blob_dependencies(source, selection.episode_indices)
    if saved:
        reader.annotations = SavedResource(
            {
                "annotations": [
                    {"annotator_id": "private-human", "detected_objects": [{"label": "cup", "frame_index": 0}]}
                ]
            },
            "annotation-v1",
        )
        labels = DatasetLabelsFile(dataset_id="sample", episodes={"0": ["accepted"], "99": ["excluded"]})

        async def selected_labels(_dataset_id: str, _selection_value: SavedSelection) -> SavedResource:
            return SavedResource(labels.model_dump(mode="json"), "labels-v1")

        reader.release_resource = selected_labels
        selection = replace(
            selection,
            resources=(
                *selection.resources,
                SavedSelection(kind="annotation", episode_index=0, expected_revision="annotation-v1"),
                SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1"),
            ),
        )
    frozen = asyncio.run(freeze_release_inputs(source, reader, selection, context))
    identities = {item.path: item for item in frozen.content.source_objects}
    endpoint = StorageEndpoint(alias="blob", kind="blob", account=source.location.account_url)
    request = replace(
        inputs.request,
        source=endpoint,
        destination=endpoint,
        frozen_input_sha256=frozen.sha256,
        source_objects=frozen.content.source_objects,
        windows=tuple(replace(window, source=identities[window.source.path]) for window in inputs.request.windows),
    )
    inputs = replace(inputs, request=request, source_root=source.location)
    prepared = prepare_saved_generation(
        frozen.content, request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    inputs = replace(inputs, confirmed_digest=prepared.plan.digest)
    local.root.rename(tmp_path / "unavailable-local-source")
    (sdk / "calls.jsonl").unlink(missing_ok=True)
    return source, reader, selection, context, frozen, inputs, sdk, remote


def test_given_blob_selection_when_discovered_then_only_metadata_listed_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.release.coordinator import select_blob_dependencies
    from physical_ai_curation.release.models import SourceObjectRecord

    source, _reader, selection, _context, _frozen, _inputs, sdk, remote = _blob_native_case(tmp_path)
    remote.put("native/videos/unselected.mp4", b"unselected")
    remote.put("native/data/unselected.parquet", b"unselected")
    original_list = remote.list_blobs
    original_read = source.iter_bytes

    def metadata_only_listing(*, name_starts_with: str, **kwargs: object) -> object:
        assert name_starts_with == "native/meta/"
        return original_list(name_starts_with=name_starts_with, **kwargs)

    def metadata_only_read(identity: SourceObjectRecord, **kwargs: object) -> object:
        assert identity.path.startswith("meta/")
        return original_read(identity, **kwargs)

    monkeypatch.setattr(remote, "list_blobs", metadata_only_listing)
    monkeypatch.setattr(source, "iter_bytes", metadata_only_read)

    selected = select_blob_dependencies(source, selection.episode_indices)
    paths = {item.path for item in selected.inventory()}

    assert "data/unselected.parquet" not in paths
    assert "videos/unselected.mp4" not in paths
    assert any(path.startswith("data/") for path in paths)
    assert any(path.startswith("videos/") for path in paths)
    assert all(call["name"].startswith("native/meta/") for call in _sdk_calls(sdk, "download_blob"))


def test_given_oversized_metadata_decode_when_planning_then_refused_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from physical_ai_curation.release.coordinator import select_blob_dependencies

    source, _reader, selection, _context, _frozen, _inputs, _sdk, _remote = _blob_native_case(tmp_path)

    class OversizedMetadata:
        metadata = SimpleNamespace(
            num_rows=1,
            num_row_groups=1,
            row_group=lambda _index: SimpleNamespace(total_byte_size=1024**3),
        )

        def read(self) -> None:
            raise AssertionError("oversized metadata must not be decoded")

    monkeypatch.setattr(pq, "ParquetFile", lambda *_args, **_kwargs: OversizedMetadata())

    with pytest.raises(ReleaseInputError, match=r"metadata.*budget"):
        select_blob_dependencies(source, selection.episode_indices)


@pytest.mark.parametrize("bound", ["bytes", "rows"])
def test_given_oversized_native_metadata_when_restoring_then_refused_before_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    from types import SimpleNamespace

    from physical_ai_curation.release.coordinator import _validate_native_inputs

    source, _reader, _selection, _context, frozen, inputs, _sdk, _remote = _blob_native_case(tmp_path)

    class OversizedMetadata:
        metadata = SimpleNamespace(
            num_rows=source.metadata_max_rows + 1 if bound == "rows" else 1,
            num_row_groups=1,
            row_group=lambda _index: SimpleNamespace(total_byte_size=1024**3 if bound == "bytes" else 1),
        )

        def read(self) -> None:
            raise AssertionError("oversized metadata must not be decoded")

    monkeypatch.setattr(pq, "ParquetFile", lambda *_args, **_kwargs: OversizedMetadata())

    with pytest.raises(ReleaseInputError, match="budget"):
        _validate_native_inputs(source, frozen, inputs, 1024**2, restore=True)


@pytest.mark.parametrize("mode,mutated", [("validate", False), ("assemble", False), ("validate", True)])
def test_given_blob_native_inputs_when_supervised_then_only_conditioned_tables_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, mutated: bool
) -> None:
    _, _, _, _, frozen, inputs, sdk, remote = _blob_native_case(tmp_path)
    prepared = prepare_saved_generation(
        frozen.content, inputs.request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    if mutated:
        remote.put("native/meta/info.json", b"{}")
    monkeypatch.setattr(
        "physical_ai_curation.release.native_worker._command",
        lambda path: [sys.executable, "-B", "-c", NATIVE_BLOB_CODE, str(path), str(sdk), str(Path(__file__).parent)],
    )
    store = _store(tmp_path / "ledger", OffsetClock())
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "blob-native",
            ATTEMPT,
            BLOB,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
            route="blob",
        )
    )
    fence = store.claim(job.job_id, "worker")

    if mutated:
        with (
            pytest.raises(ReleaseInputError),
            native_operation(store, job.job_id, fence, frozen, prepared, inputs, mode=mode),
        ):
            pytest.fail("stale native metadata must not be accepted")
    else:
        with native_operation(store, job.job_id, fence, frozen, prepared, inputs, mode=mode) as outputs:
            assert bool(outputs) == (mode == "assemble")
            if outputs:
                info = next(path for name, path, _, _ in outputs if name == "meta/info.json")
                assert json.loads(info.read_bytes())["total_frames"] == 2

    downloads = _sdk_calls(sdk, "download_blob")
    assert not any("/videos/" in call["name"] for call in downloads)
    assert _sdk_calls(sdk, "upload_blob") == []
    record = store.storage.snapshot()[job.job_id]
    assert record["effects"] == {}
    assert record["runtimes"] and all(runtime["stopped"] for runtime in record["runtimes"].values())
    assert record["scratch_bytes"] == 0


@pytest.mark.parametrize(
    ("processed", "saved", "version", "with_stats"),
    [
        (False, False, "v3.0", True),
        (True, False, "v3.0", True),
        (False, True, "v3.0", True),
        (True, True, "v3.0", True),
        (False, True, "v2.1", True),
        (False, False, "v2.1", False),
    ],
)
def test_given_blob_source_when_coordinated_then_independent_native_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, processed: bool, saved: bool, version: str, with_stats: bool
) -> None:
    source, reader, selection, context, frozen, inputs, sdk, remote = _blob_native_case(
        tmp_path, processed=processed, saved=saved, version=version, with_stats=with_stats
    )
    monkeypatch.setattr(
        "physical_ai_curation.release.native_worker._command",
        lambda path: [sys.executable, "-B", "-c", NATIVE_BLOB_CODE, str(path), str(sdk), str(Path(__file__).parent)],
    )
    store = _store(tmp_path / "ledger", OffsetClock())
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "blob-native",
            ATTEMPT,
            BLOB,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
            route="blob",
        )
    )
    fence = store.claim(job.job_id, "worker")
    publisher = _ChildBlobPublisher(
        FsContainer(sdk, "example", "release"), BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk
    )
    if not processed:
        remote.inject_response_loss("forbid_video_downloads")
        remote.inject_response_loss("forbid_destination_tables")

    result = asyncio.run(
        run_release_generation(
            store=store,
            job_id=job.job_id,
            fence=fence,
            destination=BLOB,
            attempt=ATTEMPT,
            expected=frozen,
            source=source,
            reader=reader,
            selection=selection,
            context=context,
            inputs=inputs,
            publisher=publisher,
        )
    )
    for item in list(remote.list_blobs(name_starts_with="native/")):
        remote.remove(item.name)

    assert verify_published(publisher.store, result.identity.manifest_sha256) == result
    info = json.loads(publisher.store.read_control_bytes("meta/info.json", max_bytes=1024**2))
    assert info["total_frames"] == 2
    if not with_stats:
        preview = json.loads(publisher.store.read_control_bytes("evidence/preview.json", max_bytes=1024**2))
        assert any("statistics unavailable" in warning for warning in preview["warnings"])
        statistics = json.loads(publisher.store.read_control_bytes("meta/stats.json", max_bytes=1024**2))
        assert "observation.images.front" not in statistics
    if saved:
        released_labels = json.loads(publisher.store.read_control_bytes("meta/episode_labels.json", max_bytes=1024**2))
        annotation = json.loads(
            publisher.store.read_control_bytes("annotations/episodes/episode_000000.json", max_bytes=1024**2)
        )
        assert released_labels["episodes"] == {"0": ["accepted"]}
        assert released_labels["dataset_id"] == ATTEMPT.prefix
        assert annotation["output_episode_index"] == 0
        assert annotation["annotations"][0]["detected_objects"][0]["frame_index"] == 0
        inventory = json.loads(publisher.store.read_control_bytes("release/inventory.json", max_bytes=1024**2))
        paths = {entry["path"] for entry in inventory["entries"]}
        assert {"meta/episode_labels.json", "annotations/episodes/episode_000000.json"} <= paths
    record = store.storage.snapshot()[job.job_id]
    assert record["state"] == "committed"
    if processed:
        import av

        assert record["io_counters"].get("storage_copy_bytes", 0) == 0
        for index, window in enumerate(inputs.request.windows):
            output = tmp_path / f"readback-{index}.mp4"
            output.write_bytes(b"".join(publisher.store.iter_bytes(window.source.path, chunk_size=1024**2)))
            with av.open(str(output)) as media:
                frames = [frame.to_ndarray(format="rgb24") for frame in media.decode(video=0)]
            assert len(frames) == 2
            assert np.mean(frames[0]) == pytest.approx(225, abs=6)
        assert record["io_counters"]["affected_download_bytes"] > 0
    else:
        assert record["io_counters"]["storage_copy_bytes"] > 0
        assert not any("/videos/" in call["name"] for call in _sdk_calls(sdk, "download_blob"))
    assert record["io_counters"].get("source_verification_bytes", 0) == 0
    assert record["io_counters"]["destination_verification_bytes"] > 0
    assert record["scratch_bytes"] == 0
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())


def test_given_copy_only_source_when_coordinated_then_independent_marked_release(tmp_path: Path) -> None:
    source, reader, selection, context, frozen, inputs, video = _native_case(tmp_path)
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a", "owner-a", "copy", ATTEMPT, destination, generation_request_bytes(frozen, inputs), 16 * 1024**2
        )
    )
    fence = store.claim(job.job_id, "worker")
    with (
        patch("av.open", side_effect=AssertionError("exact copies must not decode")),
        patch(
            "physical_ai_curation.release.coordinator.pq.read_table",
            side_effect=AssertionError("native reads require supervised child"),
        ),
        patch(
            "physical_ai_curation.release.tables.generate_native_selection",
            side_effect=AssertionError("native assembly requires supervised child"),
        ),
    ):
        result = asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=inputs,
            )
        )
    source.root.rename(tmp_path / "removed-source")
    root = destination / ATTEMPT.prefix
    assert verify_published(LocalReleaseStore(root)).identity == result.identity
    assert sha256_hex((root / inputs.request.windows[0].source.path).read_bytes()) == sha256_hex(video)
    status = store.get(job.job_id, scope="scope-a", principal="owner-a")
    assert status.state == "committed"
    assert status.io_counters["storage_copy_bytes"] == len(video)
    assert status.io_counters["source_verification_bytes"] > len(video)
    assert status.io_counters["destination_verification_bytes"] > sum(
        item.length for item in LocalReleaseStore(root).list_objects()
    )
    assert status.io_counters["generated_output_bytes"] > 0
    assert 0 < status.peak_scratch_bytes < inputs.scratch_bytes
    assert status.observed_scratch_bytes == status.scratch_bytes == 0
    assert LocalPublisher(destination, ATTEMPT, None).discover() == [root]


@pytest.mark.parametrize("null_columns", [False, True])
def test_given_copy_only_source_without_camera_statistics_when_coordinated_then_published_without_camera_summary(
    tmp_path: Path,
    null_columns: bool,
) -> None:
    source, reader, selection, context, frozen, inputs, video = _native_case(
        tmp_path,
        camera_statistics=False,
        null_camera_statistics=null_columns,
    )
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a", "owner-a", "copy", ATTEMPT, destination, generation_request_bytes(frozen, inputs), 16 * 1024**2
        )
    )
    fence = store.claim(job.job_id, "worker")
    with (
        patch("av.open", side_effect=AssertionError("exact copies must not decode")),
        patch(
            "physical_ai_curation.release.coordinator.pq.read_table",
            side_effect=AssertionError("native reads require supervised child"),
        ),
        patch(
            "physical_ai_curation.release.tables.generate_native_selection",
            side_effect=AssertionError("copy-only publication must not assemble native metadata"),
        ),
    ):
        result = asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=inputs,
            )
        )
    root = destination / ATTEMPT.prefix
    assert verify_published(LocalReleaseStore(root)).identity == result.identity
    assert sha256_hex((root / inputs.request.windows[0].source.path).read_bytes()) == sha256_hex(video)
    episode_files = sorted((root / "meta/episodes").rglob("*.parquet"))
    assert episode_files
    for path in episode_files:
        columns = pq.read_schema(path).names
        assert not [name for name in columns if name.startswith("stats/observation.images.front/")]
    preview = json.loads((root / "evidence/preview.json").read_bytes())
    assert any("statistics unavailable for observation.images.front" in warning for warning in preview["warnings"])
    assert "observation.images.front" not in json.loads((root / "meta/stats.json").read_bytes())
    status = store.get(job.job_id, scope="scope-a", principal="owner-a")
    assert status.state == "committed"


def test_given_unbound_native_table_when_generation_then_refused_before_publication(tmp_path: Path) -> None:
    from physical_ai_curation.release.errors import ReleaseVerificationError

    source, reader, selection, context, frozen, inputs, _ = _native_case(tmp_path)
    episode = frozen.content.selected_episode_ids[0]
    altered = inputs.source_tables[episode].set_column(5, "observation.state", pa.array([[999.0], [999.0]]))
    inputs = replace(inputs, source_tables={episode: altered})
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "altered",
            ATTEMPT,
            destination,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
        )
    )
    fence = store.claim(job.job_id, "worker")
    with pytest.raises(ReleaseInputError, match="condition-bound"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=inputs,
            )
        )
    assert store.get(job.job_id, scope="scope-a", principal="owner-a").state != "committed"
    with pytest.raises(ReleaseVerificationError):
        verify_published(LocalReleaseStore(destination / ATTEMPT.prefix))


def test_given_two_processed_cameras_when_coordinated_then_sequential_native_publication(tmp_path: Path) -> None:
    import av

    source, reader, selection, context, frozen, inputs, _ = _native_case(tmp_path, processed=True)
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "processed",
            ATTEMPT,
            destination,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
        )
    )
    fence = store.claim(job.job_id, "worker")
    result = asyncio.run(
        run_release_generation(
            store=store,
            job_id=job.job_id,
            fence=fence,
            destination=destination,
            attempt=ATTEMPT,
            expected=frozen,
            source=source,
            reader=reader,
            selection=selection,
            context=context,
            inputs=inputs,
        )
    )
    source.root.rename(tmp_path / "removed-source")
    root = destination / ATTEMPT.prefix
    assert verify_published(LocalReleaseStore(root)).identity == result.identity
    for window in inputs.request.windows:
        with av.open(str(root / window.source.path)) as video:
            frames = [frame.to_ndarray(format="rgb24") for frame in video.decode(video=0)]
        assert len(frames) == 2
        assert np.mean(frames[0]) == pytest.approx(225, abs=6)
    assert store.get(job.job_id, scope="scope-a", principal="owner-a").effects_stopped
    assert not list((store.root / "scratch").rglob("encoded.mp4"))


def test_given_changed_execution_settings_when_request_accepted_then_refused_before_effects(tmp_path: Path) -> None:
    source, reader, selection, context, frozen, inputs, _ = _native_case(tmp_path)
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "bound",
            ATTEMPT,
            destination,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
        )
    )
    fence = store.claim(job.job_id, "worker")
    changed = replace(inputs, output_bytes=inputs.output_bytes * 2)
    with pytest.raises(ReleaseInputError, match="durably accepted"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=changed,
            )
        )
    assert not destination.exists()
