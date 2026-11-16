from __future__ import annotations

import base64
import json
import math
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import physical_ai_curation
import physical_ai_curation.release.jobs as release_jobs
import physical_ai_curation.release.supervision as supervision
import pytest
from physical_ai_curation.release.blob_source_associations import BlobSourceAssociations
from physical_ai_curation.release.blob_worker import SourceAssociatedBlobPublisher, SupervisedBlobPublisher, _process
from physical_ai_curation.release.canonical import canonical_json_bytes, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.jobs import (
    BLOB_OWNER_CLEANUP_PATH,
    EVENTS_NAME,
    MAX_DIAGNOSTIC_DURATION_MS,
    MAX_DIAGNOSTIC_OPERATIONS,
    AuthorityError,
    CancellationError,
    CapacityError,
    EffectPending,
    JobConflictError,
    JobNotFoundError,
    JobRequest,
    LedgerClosedError,
    ReleaseJobControl,
    ReleaseJobError,
    ReleaseJobStore,
    ReleaseRefusal,
    ReleaseRuntimeSettings,
)
from physical_ai_curation.release.local_worker import SourceAssociatedLocalPublisher, SupervisedLocalPublisher
from physical_ai_curation.release.models import (
    CHECKSUMS_PATH,
    MANIFEST_PATH,
    MARKER_PATH,
    RECEIPT_PATH,
    SourceObject,
    SourceObjectRecord,
)
from physical_ai_curation.release.publication import (
    LocalPublisher,
    PublicationAttempt,
    ownership_bytes,
    ownership_path,
)
from physical_ai_curation.release.record_storage import (
    BlobLocation,
    BlobRecordStorage,
    LocalRecordStorage,
    RecordLimits,
    storage_from_configuration,
)
from physical_ai_curation.release.source_associations import source_association_binding
from physical_ai_curation.release.store import LocalReleaseStore
from physical_ai_curation.release.supervision import (
    MIN_MEMORY_BYTES,
    ProcessIdentity,
    ProcessSupervisor,
    SupervisedRuntime,
    recover_stop_proof,
)
from physical_ai_curation.release.verification import build_marker, verify_candidate
from physical_ai_curation.release.worker import SupervisedOutcome, run_supervised
from pytest_mock import MockerFixture
from test_release_contracts import _manifest_for, _payload_files, _write_package
from test_release_inputs import FakePages
from test_release_publishers import _association_intent, _FakeBlobClient, _FakeContainer

ATTEMPT = PublicationAttempt("pkg-001", "attempt-1", source_dataset_id="sample")
OTHER_ATTEMPT = PublicationAttempt("pkg-002", "attempt-2", source_dataset_id="sample")
DIGEST = "a" * 64
COMMIT_DIGEST = "d" * 64
SCOPE = "release"
OWNER = "user-a"
CONTROLS = {RECEIPT_PATH: (10, "b" * 64), CHECKSUMS_PATH: (20, "c" * 64)}
MEMORY = 512 * 1024**2
SETTINGS = ReleaseRuntimeSettings(lease_seconds=30, reserve_bytes=0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lease_seconds", 0),
        ("lease_seconds", True),
        ("reserve_bytes", -1),
        ("memory_bytes", 1),
        ("grace_seconds", -1),
        ("guardian_seconds", 1),
        ("state_timeout_seconds", 301),
        ("state_max_record_bytes", 0),
        ("state_max_total_bytes", 0),
        ("state_max_jobs", 0),
        ("recovery_seconds", -1),
        ("cleanup_seconds", 0),
        ("blob_reservation_limit_bytes", 0),
        ("retry_base_seconds", 0),
        ("retry_max_seconds", 1),
        ("max_failed_dispatches", 101),
        ("blob_batch_size", 65),
    ],
)
def test_given_invalid_runtime_setting_when_constructed_then_refused(field: str, value: object) -> None:
    with pytest.raises(ReleaseJobError):
        replace(SETTINGS, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scope", "../foreign"),
        ("idempotency_key", ""),
        ("route", "../route"),
        ("route", "blob"),
        ("principal", ""),
        ("principal", False),
        ("principal", "x" * 513),
        ("attempt", "unbound"),
        ("destination", "unbound"),
        ("frozen_request", "not bytes"),
        ("frozen_request", b""),
        ("execution_binding", "not bytes"),
        ("client_request_digest", "invalid"),
        ("reserved_bytes", 0),
        ("reserved_bytes", True),
    ],
)
def test_given_invalid_job_admission_when_constructed_then_refused(tmp_path: Path, field: str, value: object) -> None:
    request = _request(tmp_path / "destination")

    with pytest.raises(ReleaseJobError):
        replace(request, **{field: value})


@pytest.mark.parametrize(
    "body",
    [{"bad-key": 1}, {"ok": True}, {"ok": None}, {"ok": "x" * 65}, {f"key_{index}": index for index in range(9)}],
)
def test_given_unbounded_event_body_when_serialized_then_refused(body: dict[str, object]) -> None:
    from physical_ai_curation.release.jobs import _bounded_body

    with pytest.raises(ReleaseJobError):
        _bounded_body(body)


@pytest.mark.parametrize("failure", [None, "fence", "cancel", "capacity", "effects"])
def test_given_accepted_intent_when_captured_then_binding_is_atomic_and_fenced(
    tmp_path: Path,
    failure: str | None,
) -> None:
    store = ReleaseJobStore(tmp_path / "jobs", settings=SETTINGS, free_bytes=lambda _path: 1000)
    intent = canonical_json_bytes({"schema_version": "curated-release-intent/v1"})
    generated = canonical_json_bytes({"schema_version": "curated-release-generation/v1"})
    request = JobRequest(SCOPE, OWNER, "capture-key", ATTEMPT, tmp_path / "output", intent, 1)
    accepted = store.accept(request)
    timestamp = store.storage.snapshot()[accepted.job_id]["events"][0]["occurred_at"]
    created_at = store.accepted_at(accepted.job_id)
    assert isinstance(created_at, str)
    parsed = datetime.fromisoformat(created_at)
    assert parsed.tzinfo == UTC
    assert parsed.timestamp() == pytest.approx(timestamp, abs=0.001, rel=0)
    fence = store.claim(accepted.job_id, "worker")
    store.confirm_stopped(accepted.job_id, fence)
    if failure == "cancel":
        store.request_cancel(accepted.job_id, scope=SCOPE, principal=OWNER)
    elif failure == "effects":
        store.begin_effect(accepted.job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)
    arguments = dict(
        intent_request=intent,
        frozen_request=generated,
        execution_binding=b"{}",
        reserved_bytes=1001 if failure == "capacity" else 100,
    )
    if failure:
        with pytest.raises((AuthorityError, CancellationError, CapacityError, EffectPending)):
            store.capture_execution(accepted.job_id, fence + int(failure == "fence"), **arguments)
        assert store.frozen_request(accepted.job_id) == intent
        assert store.execution_binding(accepted.job_id) == b""
        assert store.storage.snapshot()[accepted.job_id]["reserved_bytes"] == 1
    else:
        store.capture_execution(accepted.job_id, fence, **arguments)
        reopened = ReleaseJobStore(tmp_path / "jobs", settings=SETTINGS)
        assert reopened.accepted_at(accepted.job_id) == created_at
        assert reopened.frozen_request(accepted.job_id) == generated
        assert reopened.execution_binding(accepted.job_id) == b"{}"
        assert reopened.storage.snapshot()[accepted.job_id]["reserved_bytes"] == 100
        assert store.accept(request).replayed is True
        with pytest.raises(AuthorityError):
            store.capture_execution(accepted.job_id, fence, **arguments)


CHILD_SLEEP = "import sys\nsys.stdin.read()\n"
FILE_LIMIT = 1024 * 1024
FAST_GRACE = 0.2
TOUCH_CODE = "import sys\nopen(sys.argv[1], 'w').close()\n"
CHECK_REGISTERED_CODE = (
    "import os, sys\n"
    "registered = os.path.exists(sys.argv[1])\n"
    "os.write(int(sys.argv[2]), (('registered' if registered else 'missing') + chr(10)).encode())\n"
)
SURVIVOR_CODE = "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nwhile True:\n    signal.pause()\n"
LEADER_CODE = (
    "import subprocess, sys\n"
    "survivor = subprocess.Popen([sys.executable, '-c', sys.argv[1]])\n"
    "print(survivor.pid, flush=True)\n"
)
TREE_CODE = (
    "import os, subprocess, sys\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', sys.argv[2]])\n"
    "os.write(int(sys.argv[1]), (str(grandchild.pid) + chr(10)).encode())\n"
    "sys.stdin.read()\n"
)
PACKAGE_ROOT = str(Path(physical_ai_curation.__file__).resolve().parents[1])
CONTROLLER_CODE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from physical_ai_curation.release.supervision import ProcessSupervisor\n"
    f"TREE, SURVIVOR = {TREE_CODE!r}, {SURVIVOR_CODE!r}\n"
    "ready_fd, registry, proof = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])\n"
    "def register(runtime):\n"
    "    registry.write_text(json.dumps(runtime.to_dict()))\n"
    "supervisor = ProcessSupervisor(\n"
    "    [sys.executable, '-c', TREE, str(ready_fd), SURVIVOR],\n"
    f"    memory_bytes={MEMORY}, inherit_fds=(ready_fd,), proof_path=proof, on_started=register,\n"
    ")\n"
    "supervisor.start()\n"
    "sys.stdin.read()\n"
)


@pytest.mark.parametrize(
    "read", ["get", "page", "execution_binding", "frozen_request", "source_association", "accepted_request"]
)
def test_given_read_only_job_operation_when_polled_then_no_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    accepted = store.accept(_request(tmp_path / "output"))
    before = store.storage.snapshot()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only polling must not acquire write authority")

    monkeypatch.setattr(store.storage, "transaction", forbidden)
    if read == "get":
        assert store.get(accepted.job_id, scope=SCOPE, principal=OWNER).state == "accepted"
    elif read == "page":
        assert store.page(scope=SCOPE, principal=OWNER).items[0].job_id == accepted.job_id
    elif read == "accepted_request":
        assert store.accepted_request(scope=SCOPE, principal=OWNER, idempotency_key="key-1").job_id == accepted.job_id
    else:
        getattr(store, read)(accepted.job_id)
    assert store.storage.snapshot() == before


def _batch_store(root: Path, clock: FakeClock, batch_size: int, *, free: int = 10**12) -> ReleaseJobStore:
    settings = replace(SETTINGS, blob_batch_size=batch_size)
    return ReleaseJobStore(root, settings=settings, clock=clock, free_bytes=lambda _path: free)


def _batch(count: int) -> list[tuple[str, int, str]]:
    return [(f"data/part-{index:02d}.bin", 8, DIGEST) for index in range(count)]


def test_given_blob_batch_size_when_configured_then_only_bounded_values_load() -> None:
    settings = ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_BLOB_BATCH_SIZE": "12"})
    assert settings.blob_batch_size == 12
    for value in ("0", "65", "many"):
        with pytest.raises(ReleaseVerificationError):
            ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_BLOB_BATCH_SIZE": value})


def test_given_twelve_intents_when_begun_then_one_transaction_records_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 12)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    batch = _batch(12)
    calls: list[None] = []
    original = store.storage.transaction

    def counted(*args: object, **kwargs: object) -> object:
        calls.append(None)
        return original(*args, **kwargs)

    monkeypatch.setattr(store.storage, "transaction", counted)
    assert store.begin_effects(job_id, fence, ATTEMPT, batch) == [False] * 12
    assert len(calls) == 1
    assert set(store._snapshot()[job_id]["effects"]) == {path for path, _, _ in batch}


def test_given_batch_over_configured_size_when_begun_then_refused_without_records(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 4)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()

    with pytest.raises(CapacityError):
        store.begin_effects(job_id, fence, ATTEMPT, _batch(5))
    assert store.storage.snapshot() == before


def test_given_batch_exceeding_reserved_estimate_when_begun_then_whole_batch_refused(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest", reserved=16)).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()

    with pytest.raises(CapacityError):
        store.begin_effects(job_id, fence, ATTEMPT, _batch(3))
    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("batch", "error"),
    [
        ([], ReleaseJobError),
        ([("data/a.bin", 8, DIGEST), ("data/a.bin", 8, DIGEST)], ReleaseJobError),
        ([("data/a.bin", 8, DIGEST), (MARKER_PATH, 8, DIGEST)], LedgerClosedError),
    ],
    ids=["empty", "duplicate", "marker"],
)
def test_given_invalid_batch_when_begun_then_refused_without_records(
    tmp_path: Path, batch: list[tuple[str, int, str]], error: type[Exception]
) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()

    with pytest.raises(error):
        store.begin_effects(job_id, fence, ATTEMPT, batch)
    assert store.storage.snapshot() == before


@pytest.mark.parametrize("case", ["stale", "cancelled"])
def test_given_stale_or_cancelled_job_when_batch_begun_then_refused_without_records(tmp_path: Path, case: str) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    if case == "cancelled":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
        error: type[Exception] = CancellationError
        call_fence = fence
    else:
        error = AuthorityError
        call_fence = fence + 1
    before = store.storage.snapshot()

    with pytest.raises(error):
        store.begin_effects(job_id, call_fence, ATTEMPT, _batch(2))
    assert store.storage.snapshot() == before


def test_given_conflicting_identity_in_batch_when_begun_then_no_intent_recorded(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effects(job_id, fence, ATTEMPT, [("data/a.bin", 8, DIGEST)])
    store.finish_effects(job_id, fence, ["data/a.bin"], ok=True)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.begin_effects(job_id, fence, ATTEMPT, [("data/b.bin", 8, DIGEST), ("data/a.bin", 8, "d" * 64)])
    assert store.storage.snapshot() == before


def test_given_acknowledged_batch_when_replayed_then_only_new_items_dispatch(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    batch = _batch(2)
    store.begin_effects(job_id, fence, ATTEMPT, batch)
    store.finish_effects(job_id, fence, [path for path, _, _ in batch], ok=True)

    assert store.begin_effects(job_id, fence, ATTEMPT, [*batch, ("data/part-02.bin", 8, DIGEST)]) == [True, True, False]


def test_given_unknown_path_in_finish_batch_when_finished_then_no_outcome_changes(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effects(job_id, fence, ATTEMPT, _batch(2))
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.finish_effects(job_id, fence, ["data/part-00.bin", "data/unknown.bin"], ok=True)
    assert store.storage.snapshot() == before


def test_given_conflicting_io_in_batch_when_recorded_then_counters_stay_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    store.record_io(job_id, fence, "1" * 32, {"storage_copy_bytes": 5})
    before = store.storage.snapshot()

    with pytest.raises(JobConflictError):
        store.record_ios(
            job_id,
            fence,
            [("2" * 32, {"storage_copy_bytes": 7}, 1), ("1" * 32, {"storage_copy_bytes": 9}, 0)],
        )
    assert store.storage.snapshot() == before


def test_given_identical_io_repeated_in_batch_when_recorded_then_counted_once(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    observation = ("2" * 32, {"storage_copy_bytes": 7}, 1)

    store.record_ios(job_id, fence, [observation, observation])
    assert store._snapshot()[job_id]["io_counters"].get("storage_copy_bytes") == 7


def test_given_effect_set_when_runtime_proof_requested_then_only_exact_set_authorized(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effects(job_id, fence, ATTEMPT, _batch(2))
    paths = ["data/part-00.bin", "data/part-01.bin"]

    with pytest.raises(AuthorityError):
        store.runtime_proof_path(job_id, fence, effect_paths=paths[:1])
    with pytest.raises(AuthorityError):
        store.runtime_proof_path(job_id, fence, effect_paths=[*paths, "data/extra.bin"])
    assert store.runtime_proof_path(job_id, fence, effect_paths=paths).parent.name == job_id


def test_given_execution_binding_when_validated_then_no_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "dest"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("binding validation must not acquire write authority")

    monkeypatch.setattr(store.storage, "transaction", forbidden)
    store.validate_execution_binding(job_id, fence, destination, ATTEMPT, route="local")
    with pytest.raises(AuthorityError):
        store.validate_execution_binding(job_id, fence, destination, ATTEMPT, route="blob")


def test_given_blob_batch_without_registered_runtime_when_authorized_then_refused(tmp_path: Path) -> None:
    store = _batch_store(tmp_path / "ledger", FakeClock(), 16)
    job_id = store.accept(_blob_request(key="batch-blob")).job_id
    fence = store.claim(job_id, "worker-a")
    batch = _batch(2)
    store.begin_effects(job_id, fence, ATTEMPT, batch)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.authorize_blob_writers(job_id, fence, ATTEMPT, batch, destination="unbound")
    assert store.storage.snapshot() == before


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(root: Path, clock: FakeClock, *, free: int = 10**12) -> ReleaseJobStore:
    return ReleaseJobStore(root, settings=SETTINGS, clock=clock, free_bytes=lambda _path: free)


def test_given_fresh_lease_when_heartbeats_repeat_then_only_due_renewal_writes(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job = store.accept(_request(tmp_path / "output"))
    fence = store.claim(job.job_id, "worker")
    with patch.object(store, "_transaction", wraps=store._transaction) as writes:
        for _ in range(5):
            clock.advance(1)
            assert store.heartbeat(job.job_id, fence) is False
        assert writes.call_count == 0
        clock.advance(11)
        assert store.heartbeat(job.job_id, fence) is False
        assert writes.call_count == 1
    store.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    with patch.object(store, "_transaction", wraps=store._transaction) as writes:
        assert store.heartbeat(job.job_id, fence) is True
        assert writes.call_count == 0
    with pytest.raises(AuthorityError):
        store.heartbeat(job.job_id, fence + 1)
    clock.advance(31)
    with pytest.raises(AuthorityError):
        store.heartbeat(job.job_id, fence)


def test_given_settled_cancelled_job_when_lease_expired_then_explicit_purge_is_allowed(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job = store.accept(_request(tmp_path / "output"))
    fence = store.claim(job.job_id, "worker")
    store.confirm_stopped(job.job_id, fence)
    store.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    store.settle_cancellation(job.job_id, fence, output_removed=True)
    clock.advance(31)
    store.purge_cancelled(job.job_id, fence)
    assert store.storage.snapshot() == {}


def _request(
    destination: Path,
    *,
    key: str = "key-1",
    principal: str = OWNER,
    attempt: PublicationAttempt = ATTEMPT,
    reserved: int = 1024,
    frozen: bytes = b'{"request":1}',
) -> JobRequest:
    return JobRequest(
        scope=SCOPE,
        principal=principal,
        idempotency_key=key,
        attempt=attempt,
        destination=destination,
        frozen_request=frozen,
        reserved_bytes=reserved,
    )


@pytest.mark.parametrize("damage", ["empty", "oversized", "duplicate", "marker", "companion", "negative", "boolean"])
def test_given_invalid_effect_batch_when_admitted_then_the_journal_is_unchanged(tmp_path: Path, damage: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    effects = [("data/a.bin", 1, DIGEST)]
    if damage == "empty":
        effects = []
    elif damage == "oversized":
        effects = [(f"data/{index}.bin", 1, DIGEST) for index in range(store.settings.blob_batch_size + 1)]
    elif damage == "duplicate":
        effects *= 2
    elif damage in {"marker", "companion"}:
        effects = [(MARKER_PATH if damage == "marker" else BLOB_OWNER_CLEANUP_PATH, 1, DIGEST)]
    else:
        effects = [("data/a.bin", -1 if damage == "negative" else True, DIGEST)]
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.begin_effects(job_id, fence, ATTEMPT, effects)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("paths", [[], ["data/a.bin", "data/a.bin"]])
def test_given_invalid_finish_batch_when_acknowledged_then_dispatched_effect_is_not_changed(
    tmp_path: Path, paths: list[str]
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    assert not store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.finish_effects(job_id, fence, paths, ok=True)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("damage", ["name", "directory", "nonempty", "unregistered_writer"])
def test_given_unowned_temporary_when_registered_then_no_file_or_ownership_record_changes(
    tmp_path: Path, damage: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    temporary = tmp_path / "temporary"
    if damage == "directory":
        temporary.mkdir()
    else:
        temporary.write_bytes(b"retained bytes" if damage == "nonempty" else b"")
    name = "foreign.partial" if damage == "name" else f".curation-copy-{'a' * 32}.partial"
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.register_local_temporary(job_id, fence, "data/a.bin", name, temporary.stat())

    assert store.storage.snapshot() == before
    if damage == "directory":
        assert temporary.is_dir()
    else:
        assert temporary.read_bytes() == (b"retained bytes" if damage == "nonempty" else b"")


@pytest.mark.parametrize("count", [0, 2])
def test_given_ambiguous_acceptance_history_when_restored_then_no_timestamp_is_invented(
    tmp_path: Path, count: int
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    with store._transaction() as records:
        accepted = records[job_id]["events"][0]
        records[job_id]["events"] = [dict(accepted) for _ in range(count)]
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError, match="acceptance record"):
        store.accepted_at(job_id)

    assert store.storage.snapshot() == before


def test_given_verified_output_when_named_inode_is_replaced_then_cleanup_preserves_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.release.jobs import _remove_verified

    root = tmp_path / "outputs"
    root.mkdir()
    original = root / "owned.bin"
    payload = b"owned output"
    original.write_bytes(payload)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    original_stat = os.stat
    swaps = []

    def replace_before_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == "owned.bin" and kwargs.get("dir_fd") == descriptor:
            original.rename(root / "retained.bin")
            original.write_bytes(b"foreign replacement")
            swaps.append(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", replace_before_stat)
    try:
        assert not _remove_verified(descriptor, "owned.bin", len(payload), sha256(payload).hexdigest())
    finally:
        os.close(descriptor)

    assert swaps == ["owned.bin"]
    assert original.read_bytes() == b"foreign replacement"
    assert (root / "retained.bin").read_bytes() == payload


def test_given_private_scratch_when_child_inode_changes_before_open_then_cleanup_preserves_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.release.jobs import _clear_scratch_directory

    root = tmp_path / "scratch"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "owned.bin").write_bytes(b"owned scratch")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    original_open = os.open
    swaps = []

    def replace_before_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == "child" and kwargs.get("dir_fd") == descriptor:
            child.rename(root / "retained")
            child.mkdir()
            (child / "foreign.bin").write_bytes(b"foreign scratch")
            swaps.append(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    try:
        with pytest.raises(AuthorityError, match="directory changed"):
            _clear_scratch_directory(descriptor, time.monotonic() + 10, [10], 0)
    finally:
        os.close(descriptor)

    assert swaps == ["child"]
    assert (root / "retained/owned.bin").read_bytes() == b"owned scratch"
    assert (child / "foreign.bin").read_bytes() == b"foreign scratch"


@pytest.mark.parametrize("phase", ["open", "remove"])
def test_given_empty_output_directory_when_replaced_during_cleanup_then_foreign_content_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from physical_ai_curation.release.jobs import _remove_empty_output_tree

    root = tmp_path / "outputs"
    child = root / "child"
    child.mkdir(parents=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    original_open = os.open
    original_rmdir = os.rmdir
    swaps = []

    def replace() -> None:
        child.rename(root / "retained")
        if phase == "open":
            child.write_bytes(b"foreign replacement")
        else:
            child.mkdir()
            (child / "foreign.bin").write_bytes(b"foreign replacement")
        swaps.append(phase)

    def open_directory(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if phase == "open" and path == "child" and kwargs.get("dir_fd") == descriptor:
            replace()
        return original_open(path, flags, *args, **kwargs)

    def remove_directory(path: Any, *args: Any, **kwargs: Any) -> None:
        if phase == "remove" and path == "child" and kwargs.get("dir_fd") == descriptor:
            replace()
        original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_directory)
    monkeypatch.setattr(os, "rmdir", remove_directory)
    try:
        assert not _remove_empty_output_tree(descriptor, [10], 0, time.monotonic() + 10)
    finally:
        os.close(descriptor)

    assert swaps == [phase]
    assert (root / "retained").is_dir()
    assert (child if phase == "open" else child / "foreign.bin").read_bytes() == b"foreign replacement"


@pytest.mark.parametrize(
    "damage", ["empty", "identity", "no_counters", "unknown_counter", "batch_conflict", "recorded_conflict", "replay"]
)
def test_given_io_observation_when_identity_or_categories_conflict_then_recorded_totals_are_preserved(
    tmp_path: Path, damage: str
) -> None:
    from physical_ai_curation.release.jobs import IO_COUNTERS

    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    counter = sorted(IO_COUNTERS)[0]
    operation = "a" * 32
    observation = (operation, {counter: 3}, 1)
    if damage in {"recorded_conflict", "replay"}:
        store.record_ios(job_id, fence, [observation])
    observations = [observation]
    if damage == "empty":
        observations = []
    elif damage == "identity":
        observations = [("unbound", {counter: 3}, 1)]
    elif damage == "no_counters":
        observations = [(operation, {}, 1)]
    elif damage == "unknown_counter":
        observations = [(operation, {"unrecorded_counter": 3}, 1)]
    elif damage == "batch_conflict":
        observations.append((operation, {counter: 4}, 1))
    elif damage == "recorded_conflict":
        observations = [(operation, {counter: 4}, 1)]
    before = store.storage.snapshot()

    if damage == "replay":
        store.record_ios(job_id, fence, observations)
    else:
        with pytest.raises(ReleaseJobError):
            store.record_ios(job_id, fence, observations)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "damage", ["planned", "other_fence", "foreign_boot", "live_creator", "inode", "active_runtime"]
)
def test_given_private_workspace_when_cleanup_authority_is_unresolved_then_all_scratch_is_retained(
    tmp_path: Path, damage: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    workspace = store.create_scratch_workspace(job_id, fence, 64)
    (workspace / "owned.bin").write_bytes(b"owned scratch")
    if damage == "inode":
        retained = workspace.with_name("retained")
        workspace.rename(retained)
        workspace.mkdir()
        (workspace / "foreign.bin").write_bytes(b"foreign scratch")
    elif damage != "live_creator":
        with store._transaction() as records:
            row = records[job_id]
            if damage == "planned":
                row["workspace"]["status"] = "planned"
            elif damage == "other_fence":
                row["workspace"]["fence"] = fence + 1
            elif damage == "foreign_boot":
                row["workspace"]["owner"]["boot_id"] = "00000000-0000-0000-0000-000000000000"
            else:
                row["runtimes"]["pending"] = {"fence": fence, "stopped": False}
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.cleanup_scratch_workspace(job_id, fence, recover=damage in {"foreign_boot", "live_creator"})

    assert store.storage.snapshot() == before
    assert (workspace / "owned.bin" if damage != "inode" else retained / "owned.bin").read_bytes() == b"owned scratch"
    if damage == "inode":
        assert (workspace / "foreign.bin").read_bytes() == b"foreign scratch"


@pytest.mark.parametrize("operation", ["release", "authorize"])
def test_given_unstopped_scratch_writer_when_releasing_capacity_then_reservation_is_preserved(
    tmp_path: Path, operation: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    store.reserve_scratch(job_id, fence, 64)
    with store._transaction() as records:
        records[job_id]["runtimes"]["pending"] = {"fence": fence, "stopped": False}
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="runtime stop proof"):
        if operation == "release":
            store.release_scratch(job_id, fence)
        else:
            store.authorize_scratch_cleanup(job_id, fence)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "damage", ["no_path", "foreign_path", "unregistered", "oversized", "invalid_json", "foreign_identity"]
)
def test_given_restored_stop_record_when_proof_is_untrusted_then_cancellation_cannot_release_capacity(
    tmp_path: Path, damage: str
) -> None:
    from physical_ai_curation.release.supervision import MAX_PROOF_BYTES

    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    identity = ProcessIdentity.of(os.getpid())
    proof = store.runtime_proof_path(job_id, fence)
    runtime = SupervisedRuntime(identity, identity, proof)
    store.register_runtime(job_id, fence, runtime)
    with store._transaction() as records:
        recorded = next(iter(records[job_id]["runtimes"].values()))
        recorded["stopped"] = True
        if damage == "no_path":
            recorded["runtime"]["proof_path"] = None
        elif damage == "foreign_path":
            recorded["runtime"]["proof_path"] = str(tmp_path / "foreign.json")
    store.confirm_stopped(job_id, fence)
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    artifact = proof
    if damage == "unregistered":
        artifact = proof.with_name("unregistered.json")
        artifact.write_bytes(b"unregistered proof")
    elif damage == "oversized":
        proof.write_bytes(b"x" * (MAX_PROOF_BYTES + 1))
    elif damage == "invalid_json":
        proof.write_bytes(b"not-json")
    elif damage == "foreign_identity":
        proof.write_bytes(
            canonical_json_bytes(
                {
                    "v": 1,
                    "state": "stopped",
                    "guardian": {**identity.to_dict(), "pid": identity.pid + 1},
                    "leader": identity.to_dict(),
                    "leader_exit_code": 0,
                    "members_reaped": 1,
                }
            )
        )
    before = store.storage.snapshot()
    retained_bytes = artifact.read_bytes() if artifact.exists() else None

    with pytest.raises((AuthorityError, ReleaseVerificationError, json.JSONDecodeError)):
        store.settle_cancellation(job_id, fence, output_removed=True)

    assert store.storage.snapshot() == before
    if retained_bytes is not None:
        assert artifact.read_bytes() == retained_bytes


def _sealed_job(store: ReleaseJobStore, destination: Path) -> tuple[str, int]:
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    store.seal(job_id, fence, CONTROLS)
    for path, (length, digest) in CONTROLS.items():
        store.begin_effect(job_id, fence, ATTEMPT, path, length, digest)
        store.finish_effect(job_id, fence, path, ok=True)
    return job_id, fence


@pytest.mark.parametrize(
    "damage",
    ["missing_control", "extra_control", "negative_length", "boolean_length", "bad_digest", "uppercase_digest"],
)
def test_given_invalid_seal_controls_when_submitted_then_ledger_stays_running(tmp_path: Path, damage: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    controls = dict(CONTROLS)
    if damage == "missing_control":
        del controls[RECEIPT_PATH]
    elif damage == "extra_control":
        controls["foreign.json"] = (1, DIGEST)
    else:
        controls[RECEIPT_PATH] = (
            (-1, DIGEST)
            if damage == "negative_length"
            else (True, DIGEST)
            if damage == "boolean_length"
            else (1, "invalid")
            if damage == "bad_digest"
            else (1, DIGEST.upper())
        )
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.seal(job_id, fence, controls)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "obligation",
    ["runtime", "temporary", "workspace", "scratch_residue", "proof_residue", "scratch_symlink", "proof_symlink"],
)
def test_given_unresolved_cleanup_obligation_when_proofs_purged_then_foreign_bytes_and_ledger_survive(
    tmp_path: Path, obligation: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    row = store.storage.snapshot()[job_id]
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    survivor = foreign / "retain.bin"
    survivor.write_bytes(b"foreign output")
    if obligation == "runtime":
        row["runtimes"] = {"pending": {"stopped": False}}
    elif obligation == "temporary":
        row["temporaries"] = [{"removed": False}]
    elif obligation == "workspace":
        row["workspace"] = {"status": "retained"}
    else:
        root = store.root / ("scratch" if obligation.startswith("scratch") else "runtimes") / job_id
        root.parent.mkdir(parents=True, exist_ok=True)
        if obligation.endswith("symlink"):
            root.symlink_to(foreign, target_is_directory=True)
        else:
            root.mkdir()
            (root / "retain.bin").write_bytes(b"unregistered output")
    before = store.storage.snapshot()

    with pytest.raises((AuthorityError, OSError)):
        store._purge_runtime_proofs(row)

    assert store.storage.snapshot() == before
    assert survivor.read_bytes() == b"foreign output"
    if obligation.endswith("residue"):
        assert (root / "retain.bin").read_bytes() == b"unregistered output"


def test_given_empty_registered_cleanup_directories_when_proofs_purged_then_directories_are_removed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    roots = [store.root / namespace / job_id for namespace in ("scratch", "runtimes")]
    for root in roots:
        root.mkdir(parents=True)
    before = store.storage.snapshot()

    store._purge_runtime_proofs(before[job_id])

    assert all(not root.exists() for root in roots)
    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "damage", ["uncancelled", "stale_fence", "scratch_retained", "reservation_retained", "stop_unconfirmed"]
)
def test_given_unsettled_cancelled_ledger_when_purged_then_durable_record_remains(tmp_path: Path, damage: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    if damage != "uncancelled":
        store.confirm_stopped(job_id, fence)
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
        store.settle_cancellation(job_id, fence, output_removed=True)
        with store._transaction() as records:
            row = records[job_id]
            if damage == "scratch_retained":
                row["scratch_bytes"] = 1
            elif damage == "reservation_retained":
                row["reservation"]["status"] = "retained"
            elif damage == "stop_unconfirmed":
                row["stopped_fence"] = 0
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.purge_cancelled(job_id, fence + 1 if damage == "stale_fence" else fence)

    assert store.storage.snapshot() == before


def _committed_job(store: ReleaseJobStore, destination: Path) -> tuple[str, int]:
    job_id, fence = _sealed_job(store, destination)
    with ReleaseJobControl(store, job_id, fence).publication(ATTEMPT, COMMIT_DIGEST):
        pass
    return job_id, fence


@pytest.mark.parametrize(
    "case",
    [
        "absolute",
        "parent",
        "empty",
        "missing",
        "missing_parent",
        "foreign_symlink",
        "parent_symlink",
        "wrong_length",
        "wrong_digest",
        "wrong_inode",
        "deadline",
        "owned",
    ],
)
def test_given_descriptor_owned_cleanup_when_file_identity_is_checked_then_only_admitted_bytes_are_unlinked(
    tmp_path: Path, case: str
) -> None:
    from physical_ai_curation.release.jobs import _remove_verified

    root = tmp_path / "output"
    root.mkdir()
    target = root / "owned.bin"
    target.write_bytes(b"owned output")
    foreign = tmp_path / "foreign.bin"
    foreign.write_bytes(b"foreign output")
    relative = "owned.bin"
    if case == "absolute":
        relative = str(foreign)
    elif case == "parent":
        relative = "../foreign.bin"
    elif case == "empty":
        relative = ""
    elif case in {"missing", "missing_parent"}:
        relative = "missing.bin" if case == "missing" else "missing/owned.bin"
    elif case == "foreign_symlink":
        (root / "link.bin").symlink_to(foreign)
        relative = "link.bin"
    elif case == "parent_symlink":
        (root / "link").symlink_to(tmp_path, target_is_directory=True)
        relative = "link/foreign.bin"
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        removed = _remove_verified(
            descriptor,
            relative,
            1 if case == "wrong_length" else 12,
            DIGEST if case == "wrong_digest" else sha256_hex(b"owned output"),
            expected_inode=(0, 0) if case == "wrong_inode" else None,
            deadline=-1 if case == "deadline" else None,
        )
    finally:
        os.close(descriptor)

    assert removed == (case in {"missing", "missing_parent", "owned"})
    assert foreign.read_bytes() == b"foreign output"
    if case == "owned":
        assert not target.exists()
    else:
        assert target.read_bytes() == b"owned output"


@pytest.mark.parametrize("budget", ["depth", "deadline", "entries", "file", "symlink", "empty"])
def test_given_output_tree_cleanup_when_budget_or_ownership_is_unproven_then_tree_is_retained(
    tmp_path: Path, budget: str
) -> None:
    from physical_ai_curation.release.jobs import _remove_empty_output_tree

    root = tmp_path / "output"
    root.mkdir()
    child = root / "child"
    if budget == "file":
        child.write_bytes(b"foreign payload")
    elif budget == "symlink":
        child.symlink_to(tmp_path, target_is_directory=True)
    else:
        child.mkdir()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        removed = _remove_empty_output_tree(
            descriptor,
            [0 if budget == "entries" else 16],
            33 if budget == "depth" else 0,
            -1 if budget == "deadline" else time.monotonic() + 10,
        )
    finally:
        os.close(descriptor)

    assert removed == (budget == "empty")
    assert child.exists() == (budget != "empty")
    if budget == "file":
        assert child.read_bytes() == b"foreign payload"


@pytest.mark.parametrize("budget", ["depth", "deadline", "entries", "symlink", "fifo"])
def test_given_private_scratch_cleanup_when_unowned_or_over_budget_then_bytes_remain(
    tmp_path: Path, budget: str
) -> None:
    from physical_ai_curation.release.jobs import _clear_scratch_directory

    root = tmp_path / "scratch"
    root.mkdir()
    child = root / "child"
    if budget == "symlink":
        child.symlink_to(tmp_path, target_is_directory=True)
    elif budget == "fifo":
        os.mkfifo(child)
    else:
        child.write_bytes(b"retained scratch")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(AuthorityError):
            _clear_scratch_directory(
                descriptor,
                -1 if budget == "deadline" else time.monotonic() + 10,
                [0 if budget == "entries" else 16],
                33 if budget == "depth" else 0,
            )
    finally:
        os.close(descriptor)

    assert os.path.lexists(child)
    if budget in {"depth", "deadline", "entries"}:
        assert child.read_bytes() == b"retained scratch"


def test_given_committed_release_when_registration_finished_then_projection_is_sanitized(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")

    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert isinstance(token, str)
    pending = store.get(job_id, scope=SCOPE, principal=OWNER)
    assert pending.registration is not None and pending.registration["status"] == "pending"

    status = store.finish_registration(job_id, token, status="registered")
    assert status.state == "committed"
    assert status.registration is not None
    assert set(status.registration) == {"status", "asset_name", "asset_version", "last_error_category", "attempted_at"}
    assert status.registration["status"] == "registered"
    assert status.registration["last_error_category"] is None
    assert COMMIT_DIGEST not in repr(status.registration)
    assert token not in repr(status.registration)


@pytest.mark.parametrize(
    "changes",
    [
        {"asset_name": ""},
        {"asset_name": "bad/name"},
        {"asset_version": ""},
        {"asset_version": "bad/version"},
        {"target_digest": "invalid"},
        {"target_digest": COMMIT_DIGEST.upper()},
    ],
)
def test_given_invalid_registration_identity_when_started_then_release_is_unchanged(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _fence = _committed_job(store, tmp_path / "output")
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.begin_registration(
            job_id, **({"asset_name": "model", "asset_version": "1", "target_digest": COMMIT_DIGEST} | changes)
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("digest", [None, "invalid"])
def test_given_malformed_retained_commit_when_registration_started_then_provider_authority_is_not_issued(
    tmp_path: Path, digest: str | None
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _fence = _committed_job(store, tmp_path / "output")
    with store._transaction() as records:
        records[job_id]["commit_digest"] = digest
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="valid committed manifest"):
        store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("reason", ["absent", "foreign_token", "expired", "completed", "not_committed"])
def test_given_inactive_registration_authority_when_renewed_then_it_is_not_revived(tmp_path: Path, reason: str) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, _fence = _committed_job(store, tmp_path / "output")
    token = "not-issued"
    if reason != "absent":
        issued = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
        assert issued is not None
        token = "different-token" if reason == "foreign_token" else issued
        if reason == "expired":
            clock.advance(SETTINGS.lease_seconds)
        elif reason == "completed":
            store.finish_registration(job_id, token, status="registered")
        elif reason == "not_committed":
            with store._transaction() as records:
                records[job_id]["state"] = "committing"
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="not live"):
        store.renew_registration(job_id, token)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("status", "category"),
    [("unknown", None), ("failed", None), ("failed", "private-provider-message"), ("registered", "provider_error")],
)
def test_given_invalid_registration_outcome_when_finished_then_pending_attempt_is_preserved(
    tmp_path: Path, status: str, category: str | None
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _fence = _committed_job(store, tmp_path / "output")
    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert token is not None
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.finish_registration(job_id, token, status=status, error_category=category)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("reason", ["invalid_category", "cancelled", "sealed", "retained_scratch", "unconfirmed_stop"])
def test_given_unresolved_release_work_when_input_required_then_job_is_not_paused(tmp_path: Path, reason: str) -> None:
    from physical_ai_curation.release.jobs import INPUT_REFUSALS

    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    if reason != "unconfirmed_stop":
        store.confirm_stopped(job_id, fence)
    if reason == "cancelled":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    elif reason == "sealed":
        store.seal(job_id, fence, CONTROLS)
    elif reason == "retained_scratch":
        with store._transaction() as records:
            records[job_id]["scratch_bytes"] = 1
    before = store.storage.snapshot()

    with pytest.raises(
        ReleaseJobError
        if reason == "invalid_category"
        else CancellationError
        if reason == "cancelled"
        else AuthorityError
    ):
        store.require_input(job_id, fence, "invalid" if reason == "invalid_category" else sorted(INPUT_REFUSALS)[0])

    assert store.storage.snapshot() == before


def test_given_uncancelled_release_when_claim_yielded_then_live_authority_is_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker")
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="not requested"):
        store.yield_cancellation(job_id, fence)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "reason",
    [
        "bad_digest",
        "foreign_attempt",
        "foreign_manifest",
        "cancelled",
        "capacity",
        "active_runtime",
        "identity_conflict",
        "replay",
    ],
)
def test_given_chosen_commit_when_marker_intent_is_admitted_then_only_matching_stopped_authority_can_replay(
    tmp_path: Path, reason: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    marker_digest = sha256_hex(b"marker")
    if reason in {"identity_conflict", "replay"}:
        assert not store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 6, marker_digest)
        store.finish_effect(job_id, fence, MARKER_PATH, ok=True)
    elif reason in {"cancelled", "active_runtime"}:
        with store._transaction() as records:
            if reason == "cancelled":
                records[job_id]["cancel_requested"] = True
            else:
                records[job_id]["runtimes"]["pending"] = {"fence": fence, "stopped": False}
    before = store.storage.snapshot()
    arguments = (
        job_id,
        fence,
        OTHER_ATTEMPT if reason == "foreign_attempt" else ATTEMPT,
        DIGEST if reason == "foreign_manifest" else COMMIT_DIGEST,
        1025 if reason == "capacity" else 7 if reason == "identity_conflict" else 6,
        "invalid" if reason == "bad_digest" else marker_digest,
    )

    if reason == "replay":
        assert store.begin_marker_effect(*arguments)
    else:
        with pytest.raises(ReleaseJobError):
            store.begin_marker_effect(*arguments)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "reason", ["wrong_root", "not_committing", "active_runtime", "foreign_temporary", "unregistered_partial", "empty"]
)
def test_given_marker_residue_when_cleanup_is_requested_then_unresolved_ownership_is_retained(
    tmp_path: Path, reason: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "output"
    job_id, fence = _sealed_job(store, destination)
    if reason != "not_committing":
        store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    root = destination / ATTEMPT.prefix
    root.mkdir(parents=True)
    (root / Path(MARKER_PATH).parent).mkdir(parents=True, exist_ok=True)
    if reason in {"active_runtime", "foreign_temporary"}:
        with store._transaction() as records:
            if reason == "active_runtime":
                records[job_id]["runtimes"]["pending"] = {"fence": fence, "stopped": False}
            else:
                records[job_id]["temporaries"] = [
                    {"name": "foreign.partial", "path": "data/foreign.bin", "removed": False}
                ]
    partial = root / Path(MARKER_PATH).parent / f".curation-copy-{'a' * 32}.partial"
    if reason == "unregistered_partial":
        partial.write_bytes(b"unregistered output")
    before = store.storage.snapshot()

    if reason == "empty":
        store.remove_marker_temporaries(job_id, fence, root)
    else:
        with pytest.raises(AuthorityError):
            store.remove_marker_temporaries(job_id, fence, tmp_path / "foreign" if reason == "wrong_root" else root)

    assert store.storage.snapshot() == before
    if reason == "unregistered_partial":
        assert partial.read_bytes() == b"unregistered output"


def test_given_uncommitted_job_when_registration_begun_then_refused_without_records(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _sealed_job(store, tmp_path / "dest")
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert store.storage.snapshot() == before


def test_given_other_target_digest_when_registration_begun_then_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)

    with pytest.raises(JobConflictError):
        store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest="e" * 64)


@pytest.mark.parametrize(
    ("asset_name", "asset_version", "target"),
    [
        ("", "1", COMMIT_DIGEST),
        ("../model", "1", COMMIT_DIGEST),
        ("model", "1 2", COMMIT_DIGEST),
        ("model", "1", "ABC"),
    ],
    ids=["empty-name", "traversal-name", "spaced-version", "bad-target"],
)
def test_given_invalid_registration_identity_when_begun_then_refused(
    tmp_path: Path, asset_name: str, asset_version: str, target: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.begin_registration(job_id, asset_name=asset_name, asset_version=asset_version, target_digest=target)
    assert store.storage.snapshot() == before


def test_given_failed_registration_when_finished_then_release_effects_and_capacity_preserved(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    before = store.get(job_id, scope=SCOPE, principal=OWNER)

    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert token is not None
    after = store.finish_registration(job_id, token, status="failed", error_category="provider_error")

    assert after.registration is not None
    assert after.registration["status"] == "failed"
    assert after.registration["last_error_category"] == "provider_error"
    assert after.state == before.state == "committed"
    assert after.capacity_state == before.capacity_state
    assert after.cleanup_state == before.cleanup_state
    assert after.effects_stopped == before.effects_stopped
    assert after.destination_current_bytes == before.destination_current_bytes
    assert after.io_counters == before.io_counters


def test_given_failed_registration_when_retried_then_explicit_retry_issues_new_token(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    first = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert first is not None
    store.finish_registration(job_id, first, status="failed", error_category="conflict")

    assert store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST) is None

    second = store.begin_registration(
        job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST, retry=True
    )
    assert second is not None and second != first
    assert store._snapshot()[job_id]["registration"]["attempts"] == 2


def test_given_registered_target_when_begun_again_then_replays_without_token(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert token is not None
    store.finish_registration(job_id, token, status="registered")
    before = store.storage.snapshot()

    assert store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST) is None
    assert store.storage.snapshot() == before


def test_given_held_pending_registration_when_lease_expires_then_reclaim_fences_old_token(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, _ = _committed_job(store, tmp_path / "dest")
    stale = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert stale is not None

    assert store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST) is None

    clock.advance(31)
    current = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert current is not None
    with pytest.raises(AuthorityError, match="stale"):
        store.finish_registration(job_id, stale, status="registered")
    assert store.finish_registration(job_id, current, status="registered").registration is not None


def test_given_stale_registration_token_when_finished_then_refused_without_change(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.finish_registration(job_id, "f" * 32, status="registered")
    assert store.storage.snapshot() == before


def test_given_pending_registration_when_store_reopened_then_token_still_finishes(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, _ = _committed_job(store, tmp_path / "dest")
    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert token is not None

    reopened = _store(tmp_path / "ledger", clock)
    status = reopened.finish_registration(job_id, token, status="registered")
    assert status.registration is not None and status.registration["status"] == "registered"


def test_given_live_registration_when_renewed_then_expired_authority_cannot_revive(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, _ = _committed_job(store, tmp_path / "dest")
    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert token is not None
    clock.advance(20)
    store.renew_registration(job_id, token)
    clock.advance(20)
    store.renew_registration(job_id, token)
    clock.advance(31)
    with pytest.raises(AuthorityError):
        store.renew_registration(job_id, token)


@pytest.mark.parametrize("status", ["conflict", "not_configured"])
def test_given_definitive_registration_outcome_when_finished_then_release_remains_committed(
    tmp_path: Path,
    status: str,
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    token = store.begin_registration(job_id, asset_name="a" * 255, asset_version="r" * 30, target_digest="e" * 64)
    assert token is not None
    result = store.finish_registration(job_id, token, status=status, error_category=status)
    assert result.state == "committed"
    assert result.registration is not None and result.registration["status"] == status


@pytest.mark.parametrize(
    ("status", "category"),
    [("failed", "not-a-category"), ("failed", None), ("registered", "unavailable"), ("pending", None)],
    ids=["unknown-category", "missing-category", "success-with-category", "non-terminal-status"],
)
def test_given_invalid_registration_outcome_when_finished_then_refused_without_change(
    tmp_path: Path, status: str, category: str | None
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, _ = _committed_job(store, tmp_path / "dest")
    token = store.begin_registration(job_id, asset_name="model", asset_version="1", target_digest=COMMIT_DIGEST)
    assert token is not None
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.finish_registration(job_id, token, status=status, error_category=category)
    assert store.storage.snapshot() == before


def _reaped(identity: ProcessIdentity) -> bool:
    """Report whether the identified process has been reaped, so no zombie entry remains."""
    try:
        fd = os.pidfd_open(identity.pid)
    except ProcessLookupError:
        return True
    try:
        return ProcessIdentity.of(identity.pid) != identity
    except FileNotFoundError:
        return True
    finally:
        os.close(fd)


def _await_process_exit(pid: int) -> None:
    fd = os.pidfd_open(pid)
    try:
        if not select.select([fd], [], [], 30)[0]:
            raise AssertionError("process did not exit")
    finally:
        os.close(fd)


def _read_line(fd: int, timeout_seconds: float = 30.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    data = b""
    while not data.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise AssertionError("child did not report before the deadline")
        chunk = os.read(fd, 64)
        if not chunk:
            raise AssertionError("child closed the report pipe early")
        data += chunk
    return data.decode().strip()


def _read_int_line(fd: int) -> int:
    return int(_read_line(fd))


def test_given_accepted_job_when_store_reopened_then_state_and_frozen_request_survive(tmp_path: Path) -> None:
    clock = FakeClock()
    first = _store(tmp_path / "ledger", clock)
    job_id = first.accept(_request(tmp_path / "dest")).job_id

    reopened = _store(tmp_path / "ledger", clock)
    status = reopened.get(job_id, scope=SCOPE, principal=OWNER)

    assert status.state == "accepted"
    assert reopened.frozen_request(job_id) == b'{"request":1}'


@pytest.mark.parametrize(
    "field", ["diagnostic_operations", "diagnostics", "destination_peak_bytes", "execution_binding"]
)
@pytest.mark.parametrize("operation", ["get", "replay", "page"])
def test_given_obsolete_job_record_when_read_then_rejected_without_upgrade(
    tmp_path: Path, field: str, operation: str
) -> None:
    store = _store(tmp_path / "state", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    with store.storage.transaction() as records:
        del records[job_id][field]
    before = store.storage.snapshot()[job_id]

    with pytest.raises(ReleaseJobError, match="unsupported"):
        if operation == "get":
            store.get(job_id, scope=SCOPE, principal=OWNER)
        elif operation == "replay":
            store.accept(_request(tmp_path / "destination"))
        else:
            store.page(scope=SCOPE, principal=OWNER)

    assert store.storage.snapshot()[job_id] == before


def test_given_accepted_job_when_read_independently_then_self_contained_json_is_authority(tmp_path: Path) -> None:
    root = tmp_path / "chosen-folder" / "state"
    store = _store(root, FakeClock())
    accepted = store.accept(_request(tmp_path / "datasets"))

    record = json.loads((root / "jobs" / f"{accepted.job_id}.json").read_bytes())
    assert record["schema_version"] == "curated-release-job/v1"
    assert record["job_id"] == accepted.job_id
    assert record["state"] == "accepted"
    assert record["effects"] == {}
    assert record["reservation"]["status"] == "reserved"
    assert not list(root.rglob("*.sqlite*"))
    assert set(root.iterdir()) == {root / "jobs"}


def test_given_cancelled_named_release_when_retried_then_new_attempt_can_claim_clean_root(tmp_path: Path) -> None:
    store = _store(tmp_path / "state", FakeClock())
    destination = tmp_path / "datasets"
    first = store.accept(_request(destination))
    fence = store.claim(first.job_id, "worker-a")
    publisher = LocalPublisher(destination, ATTEMPT, ReleaseJobControl(store, first.job_id, fence))
    publisher.write("data/item", (b"x",), length=1, sha256=sha256_hex(b"x"))
    store.request_cancel(first.job_id, scope=SCOPE, principal=OWNER)
    store.confirm_stopped(first.job_id, fence)

    assert store.cleanup_owned_outputs(first.job_id, fence, publisher.root) is True
    assert store.cleanup_owned_outputs(first.job_id, fence, publisher.root) is True
    store.settle_cancellation(first.job_id, fence, output_removed=True)
    next_attempt = PublicationAttempt(ATTEMPT.package_id, "attempt-next", ATTEMPT.prefix, ATTEMPT.source_dataset_id)
    second = store.accept(_request(destination, key="next-key", attempt=next_attempt))
    current = store.claim(second.job_id, "worker-b")
    replacement = LocalPublisher(destination, next_attempt, ReleaseJobControl(store, second.job_id, current))
    replacement.write("data/new-item", (b"y",), length=1, sha256=sha256_hex(b"y"))

    assert (replacement.root / "data/new-item").read_bytes() == b"y"
    assert not (replacement.root / "data/item").exists()
    with pytest.raises(AuthorityError):
        store.cleanup_owned_outputs(first.job_id, fence, publisher.root)
    assert (replacement.root / "data/new-item").read_bytes() == b"y"


def test_given_changed_root_owner_when_cleanup_then_payload_and_obligation_retained(tmp_path: Path) -> None:
    store = _store(tmp_path / "state", FakeClock())
    destination = tmp_path / "datasets"
    job = store.accept(_request(destination))
    fence = store.claim(job.job_id, "worker")
    publisher = LocalPublisher(destination, ATTEMPT, ReleaseJobControl(store, job.job_id, fence))
    publisher.write("data/item", (b"x",), length=1, sha256=sha256_hex(b"x"))
    store.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    store.confirm_stopped(job.job_id, fence)
    companion = destination / "_releases" / f"{ATTEMPT.prefix}.json"
    owner = json.loads(companion.read_bytes())
    owner["attempt_id"] = "foreign-attempt"
    companion.write_text(json.dumps(owner))

    assert store.cleanup_owned_outputs(job.job_id, fence, publisher.root) is False
    assert (publisher.root / "data/item").read_bytes() == b"x"
    assert json.loads(companion.read_bytes()) == owner
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).capacity_state == "reserved"


def test_given_scratch_from_unobservable_host_when_recovering_then_retained(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "state", clock)
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker-a")
    workspace = store.create_scratch_workspace(job.job_id, fence, 256)
    with store.storage.transaction() as records:
        records[job.job_id]["workspace"]["owner"]["boot_id"] = "00000000-0000-0000-0000-000000000000"
    clock.advance(31)
    current = store.claim(job.job_id, "replacement")

    with pytest.raises(AuthorityError, match="observable"):
        store.cleanup_scratch_workspace(job.job_id, current, recover=True)
    assert workspace.is_dir()
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).scratch_bytes == 256


@pytest.mark.parametrize("field", ["schema_version", "job_id"])
def test_given_incomplete_record_envelope_when_committed_then_rejected_without_upgrade(
    tmp_path: Path, field: str
) -> None:
    storage = LocalRecordStorage(tmp_path / "state")
    key = "a" * 32
    record = {"schema_version": "curated-release-job/v1", "job_id": key, "state": "accepted"}
    del record[field]

    with pytest.raises(ReleaseVerificationError, match="schema_version or job_id"), storage.transaction() as records:
        records[key] = record

    assert storage.snapshot() == {}


def test_given_local_record_write_failure_when_reopened_then_old_snapshot_is_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalRecordStorage(tmp_path / "state")
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key, "state": "accepted"}
    original = storage.snapshot()

    def fail_replace(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("replacement interrupted")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"), storage.transaction() as records:
        records[key]["state"] = "running"

    reopened = storage_from_configuration(storage.configuration())
    assert reopened.snapshot() == original
    assert [path.name for path in (tmp_path / "state" / "jobs").iterdir()] == [f"{key}.json"]


@pytest.mark.parametrize("frozen_bound", [True, False])
def test_given_accepted_source_association_when_published_then_checkpoint_precedes_supervised_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_bound: bool
) -> None:
    destination = tmp_path / "output/group-a/source-a/releases"
    source = tmp_path / "source"
    files = _payload_files()
    manifest = _manifest_for(files)
    digest = _write_package(source, files, manifest)
    intent = _association_intent(destination).model_copy(
        update={
            "frozen_input_sha256": manifest.frozen_input_sha256 if frozen_bound else "0" * 64,
            "scope_sha256": sha256_hex(OWNER.encode()),
        }
    )
    store = _store(tmp_path / "state", OffsetClock())
    request = replace(
        _request(destination, attempt=intent.attempt, reserved=16 * 1024**2),
        execution_binding=source_association_binding(intent),
    )
    job_id = store.accept(request).job_id
    fence = store.claim(job_id, "worker-a")
    publisher = SourceAssociatedLocalPublisher(destination, intent.attempt, store, job_id, fence, intent)
    for item in LocalReleaseStore(source).list_objects():
        if item.path in {RECEIPT_PATH, CHECKSUMS_PATH}:
            continue
        payload = (source / item.path).read_bytes()
        publisher.write(item.path, (payload,), length=len(payload), sha256=sha256_hex(payload))
    candidate = verify_candidate(publisher.store, digest)
    store.seal(
        job_id,
        fence,
        {
            RECEIPT_PATH: (len(candidate.receipt_bytes), sha256_hex(candidate.receipt_bytes)),
            CHECKSUMS_PATH: (len(candidate.checksums_bytes), sha256_hex(candidate.checksums_bytes)),
        },
    )
    execute = publisher._execute
    observed = []

    def observe_marker(path: str, length: int, content: bytes) -> None:
        if path == MARKER_PATH:
            record = json.loads((destination / "_release_associations/pkg-001.json").read_bytes())
            assert record["checkpoint"] == {"manifest_sha256": digest, "job_id": job_id, "fence": fence}
            assert store.get(job_id, scope=SCOPE, principal=OWNER).state == "committing"
            observed.append(path)
        execute(path, length, content)

    monkeypatch.setattr(publisher, "_execute", observe_marker)
    if not frozen_bound:
        with pytest.raises(ReleaseVerificationError, match="association frozen input"):
            publisher.publish(digest)
        assert not (publisher.root / MARKER_PATH).exists()
        assert store.get(job_id, scope=SCOPE, principal=OWNER).state not in {"committing", "committed"}
        return
    result = publisher.publish(digest)
    source.rename(tmp_path / "source-unavailable")

    assert result.publication_state == "published"
    assert observed == [MARKER_PATH]
    assert publisher.associations.discover(source_key=intent.source_key, scope_sha256=intent.scope_sha256) == [
        publisher.root
    ]
    marker_inode = (publisher.root / MARKER_PATH).stat().st_ino
    sidecar = destination / "_release_associations/pkg-001.json"
    sidecar.unlink()
    with pytest.raises(ReleaseVerificationError, match="checkpoint is missing"):
        publisher.publish(digest)
    assert not sidecar.exists()
    assert (publisher.root / MARKER_PATH).stat().st_ino == marker_inode


@pytest.mark.parametrize("mismatch", ["scope", "binding", "destination", "fence"])
def test_given_unaccepted_source_association_when_publisher_constructed_then_no_destination_write(
    tmp_path: Path, mismatch: str
) -> None:
    destination = tmp_path / "output/group-a/source-a/releases"
    intent = _association_intent(destination).model_copy(
        update={"scope_sha256": sha256_hex(("other" if mismatch == "scope" else OWNER).encode())}
    )
    store = _store(tmp_path / "state", OffsetClock())
    request = replace(
        _request(destination, attempt=intent.attempt),
        execution_binding=b"unaccepted" if mismatch == "binding" else source_association_binding(intent),
    )
    job_id = store.accept(request).job_id
    fence = store.claim(job_id, "worker-a")

    with pytest.raises(AuthorityError, match="accepted binding"):
        SourceAssociatedLocalPublisher(
            tmp_path / "wrong-output" if mismatch == "destination" else destination,
            intent.attempt,
            store,
            job_id,
            fence + 1 if mismatch == "fence" else fence,
            intent,
        )

    assert not destination.exists()
    assert not (tmp_path / "wrong-output").exists()


@pytest.mark.parametrize("residue", ["malformed", "symlink"])
def test_given_invalid_record_when_scanned_then_fail_closed(tmp_path: Path, residue: str) -> None:
    storage = LocalRecordStorage(tmp_path / "state")
    with storage.transaction():
        pass
    target = tmp_path / "state" / "jobs" / f"{'b' * 32}.json"
    if residue == "malformed":
        target.write_bytes(b'{"schema_version":"wrong"}')
    else:
        source = tmp_path / "outside.json"
        source.write_bytes(b"{}")
        target.symlink_to(source)

    with pytest.raises((OSError, ReleaseVerificationError)):
        storage.snapshot()


def test_given_multiple_changed_records_when_transaction_commits_then_no_partial_write(tmp_path: Path) -> None:
    storage = LocalRecordStorage(tmp_path / "state")
    with pytest.raises(ReleaseVerificationError, match="at most one"), storage.transaction() as records:
        records["a" * 32] = {"state": "accepted"}
        records["b" * 32] = {"state": "accepted"}
    assert storage.snapshot() == {}


def test_given_state_limits_in_environment_when_loaded_then_local_adapter_uses_them(tmp_path: Path) -> None:
    settings = ReleaseRuntimeSettings.from_environment(
        {
            "CURATION_RELEASE_STATE_MAX_RECORD_BYTES": "4096",
            "CURATION_RELEASE_STATE_MAX_TOTAL_BYTES": "16384",
            "CURATION_RELEASE_STATE_MAX_JOBS": "3",
        }
    )
    store = ReleaseJobStore(tmp_path, settings=settings)
    config = store.storage.configuration()
    assert config["max_record_bytes"] == 4096
    assert config["max_total_bytes"] == 16384
    assert config["max_jobs"] == 3


@pytest.mark.parametrize(
    "name,value",
    [
        ("CURATION_RELEASE_STATE_MAX_RECORD_BYTES", "0"),
        ("CURATION_RELEASE_STATE_MAX_TOTAL_BYTES", "1"),
        ("CURATION_RELEASE_STATE_MAX_JOBS", "4097"),
    ],
)
def test_given_invalid_state_limit_when_loaded_then_refused(name: str, value: str) -> None:
    with pytest.raises(ReleaseJobError):
        ReleaseRuntimeSettings.from_environment({name: value})


class _RecordLease:
    def __init__(self, container: _RecordContainer) -> None:
        self.container = container
        self.generation = container.generation

    def check(self) -> None:
        if self.generation != self.container.generation or not self.container.leased:
            from azure.core.exceptions import ResourceModifiedError

            raise ResourceModifiedError("lease expired")

    def renew(self, **_kwargs: Any) -> None:
        self.check()

    def release(self, **_kwargs: Any) -> None:
        self.check()
        self.container.leased = False


class _RecordBlob(_FakeBlobClient):
    def acquire_lease(self, **_kwargs: Any) -> _RecordLease:
        if self.container.leased:
            from azure.core.exceptions import HttpResponseError

            error = HttpResponseError("lease held")
            error.status_code = 409
            raise error
        self.container.leased = True
        self.container.generation += 1
        return _RecordLease(self.container)

    def get_blob_properties(self, **_kwargs: Any) -> Any:
        return super().get_blob_properties()

    def upload_blob(self, data: Any, overwrite: bool = False, **kwargs: Any) -> dict:
        lease = kwargs.get("lease")
        if self.name.endswith("/coordinator.lock") and self.container.leased and lease is None:
            from azure.core.exceptions import HttpResponseError

            error = HttpResponseError(
                "lease ID required",
                response=SimpleNamespace(status_code=412, reason="Precondition Failed", headers={}),
            )
            error.error_code = "LeaseIdMissing"
            raise error
        if lease is not None:
            lease.check()
        if kwargs.get("etag") is not None and kwargs["etag"] != self._record().etag:
            from azure.core.exceptions import ResourceModifiedError

            raise ResourceModifiedError("conditional write conflict")
        is_job = "/jobs/" in self.name
        if is_job and self.container.before_job_write is not None:
            callback, self.container.before_job_write = self.container.before_job_write, None
            callback()
        if is_job and self.container.failed_write == "before":
            self.container.failed_write = None
            raise OSError("response lost before record write")
        result = super().upload_blob(data, overwrite=overwrite, **kwargs)
        if is_job and self.container.failed_write == "after":
            self.container.failed_write = None
            raise OSError("response lost after record write")
        return result

    def download_blob(self, **kwargs: Any) -> Any:
        lease = kwargs.get("lease")
        if lease is not None:
            lease.check()
        super().download_blob(**kwargs)
        record = self._record()
        return SimpleNamespace(
            size=len(record.data), properties=SimpleNamespace(etag=record.etag), readall=lambda: record.data
        )


class _RecordContainer(_FakeContainer):
    def __init__(self) -> None:
        super().__init__("example", "release-state", {})
        self.leased = False
        self.generation = 0
        self.failed_write = None
        self.before_job_write = None

    def get_blob_client(self, name: str) -> _RecordBlob:
        return _RecordBlob(self, name)

    def list_blobs(self, *, name_starts_with: str, **_kwargs: Any) -> Iterator[Any]:
        yield from super().list_blobs(name_starts_with=name_starts_with)


def _blob_record_store(container: _RecordContainer) -> BlobRecordStorage:
    return BlobRecordStorage(
        container,
        account_url="https://example.blob.core.windows.net",
        container_name="release-state",
        prefix="selected/subfolder",
    )


@pytest.mark.parametrize("backend", ["local", "blob"])
def test_given_job_record_when_deleted_then_reopen_preserves_other_records(tmp_path: Path, backend: str) -> None:
    container = _RecordContainer()
    storage = LocalRecordStorage(tmp_path) if backend == "local" else _blob_record_store(container)
    keys = ["a" * 32, "b" * 32]
    for key in keys:
        with storage.transaction() as records:
            records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    with storage.transaction() as records:
        del records[keys[0]]
    reopened = LocalRecordStorage(tmp_path) if backend == "local" else _blob_record_store(container)
    assert set(reopened.snapshot()) == {keys[1]}
    if backend == "blob":
        assert container.blobs["selected/subfolder/coordinator.lock"].data == b""


@pytest.mark.parametrize("after", [False, True])
def test_given_lost_record_delete_response_when_reopened_then_pending_delete_finishes(after: bool) -> None:
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    container.faults["delete_blob"] = (after, OSError("response lost"))
    with pytest.raises(OSError, match="response lost"), storage.transaction() as records:
        del records[key]
    with _blob_record_store(container).transaction() as records:
        assert records == {}
    assert container.blobs["selected/subfolder/coordinator.lock"].data == b""


def test_given_changed_record_revision_when_delete_recovered_then_foreign_record_is_preserved() -> None:
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    blob = container.get_blob_client(f"selected/subfolder/jobs/{key}.json")
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    revision = blob.get_blob_properties().etag
    replacement = canonical_json_bytes({"schema_version": "curated-release-job/v1", "job_id": key, "other": True})
    blob.upload_blob(replacement, overwrite=True)
    with pytest.raises(ReleaseVerificationError, match="delete refused"):
        storage._delete(key, revision)
    assert blob.download_blob().readall() == replacement


def test_given_unchanged_blob_records_when_read_again_then_only_listing_is_repeated() -> None:
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    storage.snapshot()
    with patch.object(storage, "_read", wraps=storage._read) as reads:
        first = storage.snapshot()
        first[key]["untrusted"] = True
        assert "untrusted" not in storage.snapshot()[key]
        assert reads.call_count == 0
        container.get_blob_client(f"selected/subfolder/jobs/{key}.json").upload_blob(
            canonical_json_bytes({"schema_version": "curated-release-job/v1", "job_id": key, "fresh": True}),
            overwrite=True,
        )
        assert storage.snapshot()[key]["fresh"] is True
        assert reads.call_count == 1


@pytest.mark.parametrize("phase", ["request", "body"])
def test_given_blob_snapshot_race_when_record_changes_then_returns_fresh_record(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    exceptions = pytest.importorskip("azure.core.exceptions")
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    original = {"schema_version": "curated-release-job/v1", "job_id": key, "state": "accepted"}
    updated = {**original, "state": "running"}
    with storage.transaction() as records:
        records[key] = original
    download = _RecordBlob.download_blob
    reads = []

    def conflict() -> bytes:
        raise exceptions.ResourceModifiedError("job record updated")

    def racing_download(blob: _RecordBlob, **kwargs: Any) -> Any:
        reads.append(kwargs["etag"])
        if len(reads) == 1:
            stream = download(blob, **kwargs)
            blob.upload_blob(canonical_json_bytes(updated), overwrite=True)
            if phase == "request":
                conflict()
            stream.readall = conflict
            return stream
        return download(blob, **kwargs)

    monkeypatch.setattr(_RecordBlob, "download_blob", racing_download)

    assert storage.snapshot() == {key: updated}
    assert len(reads) == 2
    assert reads[0] != reads[1]
    assert not container.leased


def test_given_blob_snapshot_contention_when_timeout_expires_then_stops_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exceptions = pytest.importorskip("azure.core.exceptions")
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    clock = SimpleNamespace(value=0.0)
    reads = []

    def conflicting_download(blob: _RecordBlob, **kwargs: Any) -> Any:
        reads.append(kwargs["etag"])
        raise exceptions.ResourceModifiedError("job record updated")

    def advance(delay: float) -> None:
        clock.value += delay

    monkeypatch.setattr(_RecordBlob, "download_blob", conflicting_download)
    monkeypatch.setattr("physical_ai_curation.release.record_storage.time.monotonic", lambda: clock.value)
    monkeypatch.setattr("physical_ai_curation.release.record_storage.time.sleep", advance)

    with pytest.raises(TimeoutError, match="snapshot contention exceeded timeout"):
        storage.snapshot()

    assert clock.value == storage.configuration()["timeout_seconds"]
    assert len(reads) == 61
    assert not container.leased


@pytest.mark.parametrize("failure", ["permission", "invalid", "oversized"])
def test_given_blob_snapshot_failure_when_not_revision_conflict_then_does_not_retry(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    exceptions = pytest.importorskip("azure.core.exceptions")
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    reads = []
    denied = exceptions.HttpResponseError("permission denied")

    def failed_download(blob: _RecordBlob, **kwargs: Any) -> Any:
        reads.append(kwargs["etag"])
        if failure == "permission":
            raise denied
        return SimpleNamespace(
            size=storage.configuration()["max_record_bytes"] + 1 if failure == "oversized" else 2,
            properties=SimpleNamespace(etag=kwargs["etag"]),
            readall=lambda: b"{}",
        )

    monkeypatch.setattr(_RecordBlob, "download_blob", failed_download)
    expected_error = exceptions.HttpResponseError if failure == "permission" else ReleaseVerificationError
    with pytest.raises(expected_error):
        storage.snapshot()

    assert len(reads) == 1
    assert not container.leased


def test_given_blob_transaction_read_conflict_when_loading_then_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exceptions = pytest.importorskip("azure.core.exceptions")
    container = _RecordContainer()
    storage = _blob_record_store(container)
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"schema_version": "curated-release-job/v1", "job_id": key}
    download = _RecordBlob.download_blob
    reads = []

    def conflicting_download(blob: _RecordBlob, **kwargs: Any) -> Any:
        if "/jobs/" in blob.name:
            reads.append(kwargs["etag"])
            raise exceptions.ResourceModifiedError("unexpected concurrent writer")
        return download(blob, **kwargs)

    monkeypatch.setattr(_RecordBlob, "download_blob", conflicting_download)
    with pytest.raises(ReleaseVerificationError, match="job record changed during read"), storage.transaction():
        pytest.fail("a conflicting transaction must not proceed")

    assert len(reads) == 1
    assert not container.leased


def test_given_leased_coordinator_when_second_owner_reads_then_waits_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("azure.core.exceptions")
    container = _RecordContainer()
    storage = _blob_record_store(container)
    store = ReleaseJobStore(tmp_path / "runtime", settings=SETTINGS, storage=storage)
    accepted = store.accept(_request(tmp_path / "datasets"))
    expected = storage.snapshot()
    contended = threading.Event()
    acquire = _RecordBlob.acquire_lease

    def observe_acquisition(blob: _RecordBlob, **kwargs: Any) -> _RecordLease:
        if blob.container.leased:
            contended.set()
        return acquire(blob, **kwargs)

    def read_from_other_owner() -> dict[str, Any]:
        with _blob_record_store(container).transaction() as records:
            return records

    monkeypatch.setattr(_RecordBlob, "acquire_lease", observe_acquisition)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with storage.transaction():
            pending = executor.submit(read_from_other_owner)
            assert contended.wait(2), "second owner must reach bounded lease acquisition"
            assert not pending.done()
            assert container.leased
        actual = pending.result(timeout=3)

    assert actual == expected
    assert accepted.job_id in actual
    assert container.blobs["selected/subfolder/coordinator.lock"].data == b""
    assert not container.leased


@pytest.mark.parametrize(
    "status,code",
    [(403, "AuthorizationFailure"), (412, "ConditionNotMet"), (409, "ContainerBeingDeleted"), (500, "LeaseIdMissing")],
)
def test_given_coordinator_creation_error_when_not_lease_contention_then_propagates(
    status: int, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    exceptions = pytest.importorskip("azure.core.exceptions")
    error = exceptions.HttpResponseError(
        "coordinator unavailable", response=SimpleNamespace(status_code=status, reason="Failed", headers={})
    )
    error.error_code = code
    container = _RecordContainer()

    def reject_creation(blob: _RecordBlob, *args: Any, **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(_RecordBlob, "upload_blob", reject_creation)
    with pytest.raises(exceptions.HttpResponseError) as raised, _blob_record_store(container).transaction():
        pytest.fail("failed creation must not admit a transaction")

    assert raised.value is error
    assert not container.leased


@pytest.mark.parametrize("failure", ["before", "after"])
def test_given_uncertain_blob_record_write_when_service_reopens_then_pending_intent_recovered(
    tmp_path: Path, failure: str
) -> None:
    pytest.importorskip("azure.core.exceptions")
    container = _RecordContainer()
    storage = _blob_record_store(container)
    store = ReleaseJobStore(tmp_path / "runtime", settings=SETTINGS, storage=storage)
    container.failed_write = failure
    request = _request(tmp_path / "datasets")

    with pytest.raises(OSError, match="response lost"):
        store.accept(request)
    reopened = ReleaseJobStore(tmp_path / "other-runtime", settings=SETTINGS, storage=_blob_record_store(container))
    accepted = reopened.accept(request)

    assert accepted.replayed is True
    assert reopened.frozen_request(accepted.job_id) == request.frozen_request
    assert len(storage.snapshot()) == 1
    assert not list(tmp_path.rglob("*.sqlite*"))
    assert not (tmp_path / "runtime" / "jobs").exists()
    assert container.blobs["selected/subfolder/coordinator.lock"].data == b""


def test_given_expired_blob_lease_when_new_owner_admits_then_prior_intent_prevents_duplicate(
    tmp_path: Path,
) -> None:
    pytest.importorskip("azure.core.exceptions")
    from azure.core.exceptions import ResourceModifiedError

    container = _RecordContainer()
    storage = _blob_record_store(container)
    first = ReleaseJobStore(tmp_path / "runtime", settings=SETTINGS, storage=storage)
    request = _request(tmp_path / "datasets")

    def replace_owner() -> None:
        container.leased = False
        container.generation += 1
        replacement = ReleaseJobStore(
            tmp_path / "replacement", settings=SETTINGS, storage=_blob_record_store(container)
        )
        with pytest.raises(JobConflictError, match="already claimed"):
            replacement.accept(replace(request, principal="different-owner"))

    container.before_job_write = replace_owner
    with pytest.raises(ResourceModifiedError, match="lease expired"):
        first.accept(request)

    assert len(storage.snapshot()) == 1
    restored = first.accept(request)
    assert restored.replayed is True


def test_given_same_key_and_request_when_accepted_concurrently_then_single_job(tmp_path: Path) -> None:
    clock = FakeClock()
    root = tmp_path / "ledger"
    _store(root, clock)
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def accept() -> None:
        try:
            store = _store(root, clock)
            barrier.wait()
            accepted = store.accept(_request(tmp_path / "dest"))
            with lock:
                results.append((accepted.job_id, accepted.replayed))
        except BaseException as error:
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=accept) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len({job_id for job_id, _ in results}) == 1
    assert sum(1 for _, replayed in results if not replayed) == 1


def test_given_same_key_different_request_when_accepted_then_conflict_hides_job(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "dest"))

    with pytest.raises(JobConflictError, match="different request"):
        store.accept(_request(tmp_path / "dest", frozen=b'{"request":2}'))


def test_given_same_principal_and_request_under_new_key_when_accepted_then_live_job_replayed(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    first = store.accept(_request(tmp_path / "dest", key="key-1"))

    second = store.accept(_request(tmp_path / "dest", key="key-2"))

    assert second.job_id == first.job_id
    assert second.replayed is True


def test_given_live_identity_claimed_by_other_principal_when_accepted_then_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "dest", key="key-1"))

    with pytest.raises(JobConflictError, match="already claimed"):
        store.accept(_request(tmp_path / "dest", key="key-2", principal="user-b"))


def test_given_other_principal_when_get_then_job_is_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id

    with pytest.raises(JobNotFoundError):
        store.get(job_id, scope=SCOPE, principal="user-b")


def test_given_insufficient_capacity_when_accepted_then_refused_until_cancellation_releases(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock, free=2048)
    first = store.accept(_request(tmp_path / "dest", key="key-1", reserved=1500))

    with pytest.raises(CapacityError):
        store.accept(_request(tmp_path / "dest", key="key-2", attempt=OTHER_ATTEMPT, reserved=1500))

    store.request_cancel(first.job_id, scope=SCOPE, principal=OWNER)
    store.settle_cancellation(first.job_id, 0, output_removed=True)
    second = store.accept(_request(tmp_path / "dest", key="key-2", attempt=OTHER_ATTEMPT, reserved=1500))

    assert second.replayed is False


def test_given_retained_reservation_when_lease_expires_then_stale_fence_refused_and_capacity_held(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock, free=2048)
    first = store.accept(_request(tmp_path / "dest", key="key-1", reserved=1500)).job_id
    stale = store.claim(first, "worker-a")
    clock.advance(31)
    current = store.claim(first, "worker-b")

    with pytest.raises(AuthorityError):
        store.heartbeat(first, stale)

    store.request_cancel(first, scope=SCOPE, principal=OWNER)
    store.confirm_stopped(first, stale)
    store.confirm_stopped(first, current)
    retained = store.settle_cancellation(first, current, output_removed=False)

    assert retained.cleanup_state == "retained"
    with pytest.raises(CapacityError):
        store.accept(_request(tmp_path / "dest", key="key-2", attempt=OTHER_ATTEMPT, reserved=1500))

    store.settle_cancellation(first, current, output_removed=True)
    store.accept(_request(tmp_path / "dest", key="key-2", attempt=OTHER_ATTEMPT, reserved=1500))


def test_given_unconfirmed_predecessor_when_successor_writes_then_refused_until_stopped(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    stale = store.claim(job_id, "worker-a")
    clock.advance(31)
    current = store.claim(job_id, "worker-b")

    with pytest.raises(AuthorityError, match="predecessor"):
        store.begin_effect(job_id, current, ATTEMPT, "data/a.bin", 1, DIGEST)

    with pytest.raises(AuthorityError, match="stale"):
        store.begin_effect(job_id, stale, ATTEMPT, "data/a.bin", 1, DIGEST)

    store.confirm_stopped(job_id, stale)

    assert store.begin_effect(job_id, current, ATTEMPT, "data/a.bin", 1, DIGEST) is False


def test_given_underestimated_reservation_when_effect_exceeds_then_refused_before_dispatch(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest", reserved=10)).job_id
    fence = store.claim(job_id, "worker-a")

    assert store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 8, DIGEST) is False
    store.finish_effect(job_id, fence, "data/a.bin", ok=True)
    with pytest.raises(CapacityError):
        store.begin_effect(job_id, fence, ATTEMPT, "data/b.bin", 3, DIGEST)

    assert store.begin_effect(job_id, fence, ATTEMPT, "data/b.bin", 2, DIGEST) is False


def test_given_uncertain_effect_when_sealed_then_refused_and_identity_unresolved(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    control = ReleaseJobControl(store, job_id, fence)

    with pytest.raises(OSError), control.effect(ATTEMPT, "data/a.bin", 1, DIGEST):
        raise OSError("disk went away")

    with pytest.raises(AuthorityError, match="unresolved"):
        store.seal(job_id, fence, CONTROLS)
    with pytest.raises(AuthorityError, match="unresolved"):
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)


def test_given_sealed_ledger_when_payload_written_then_refused_while_control_replays(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "dest")

    with pytest.raises(LedgerClosedError):
        store.begin_effect(job_id, fence, ATTEMPT, "media/late.mp4", 1, DIGEST)

    length, digest = CONTROLS[RECEIPT_PATH]
    assert store.begin_effect(job_id, fence, ATTEMPT, RECEIPT_PATH, length, digest) is True

    with pytest.raises(AuthorityError, match="control identity"):
        store.begin_effect(job_id, fence, ATTEMPT, RECEIPT_PATH, length + 1, digest)


def test_given_cancel_before_commit_when_publication_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "dest")
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)

    with pytest.raises(CancellationError), ReleaseJobControl(store, job_id, fence).publication(ATTEMPT, COMMIT_DIGEST):
        pass


def test_given_commit_chosen_when_cancelled_then_too_late(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "dest")
    with ReleaseJobControl(store, job_id, fence).publication(ATTEMPT, COMMIT_DIGEST):
        pass

    with pytest.raises(CancellationError):
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)


def test_given_commit_interrupted_when_recovered_then_only_same_attempt_and_digest_completes(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, stale = _sealed_job(store, tmp_path / "dest")

    with pytest.raises(RuntimeError), ReleaseJobControl(store, job_id, stale).publication(ATTEMPT, COMMIT_DIGEST):
        raise RuntimeError("process died mid-publication")

    with (
        pytest.raises(JobConflictError, match="another manifest"),
        ReleaseJobControl(store, job_id, stale).publication(ATTEMPT, "e" * 64),
    ):
        pass

    clock.advance(31)
    current = store.claim(job_id, "worker-b")
    store.confirm_stopped(job_id, stale)
    with ReleaseJobControl(store, job_id, current).publication(ATTEMPT, COMMIT_DIGEST):
        pass

    assert store.get(job_id, scope=SCOPE, principal=OWNER).state == "committed"


def test_given_event_log_partial_tail_when_flushed_then_repaired_without_duplicates(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    store.accept(_request(tmp_path / "dest", key="key-1"))
    assert store.flush_events() == 1

    with (tmp_path / "ledger" / EVENTS_NAME).open("ab") as handle:
        handle.write(b'{"seq":')
    store.accept(_request(tmp_path / "dest", key="key-2", attempt=OTHER_ATTEMPT, principal="user-b"))
    assert store.flush_events() == 1
    assert store.flush_events() == 0

    records = [json.loads(line) for line in (tmp_path / "ledger" / EVENTS_NAME).read_text().splitlines()]
    assert [record["seq"] for record in records] == [1, 2]
    assert {record["event"] for record in records} == {"job.accepted"}


def test_given_candidate_when_published_end_to_end_then_sealed_committed_and_replayable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    files = _payload_files()
    manifest_digest = _write_package(source, files, _manifest_for(files))
    objects = list(LocalReleaseStore(source).list_objects())
    total = sum(len((source / item.path).read_bytes()) for item in objects)
    destination = tmp_path / "destination"
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(destination, reserved=total + 4096, attempt=ATTEMPT)).job_id
    fence = store.claim(job_id, "worker-a")
    control = ReleaseJobControl(store, job_id, fence)
    publisher = LocalPublisher(destination, ATTEMPT, control)

    for item in objects:
        content = (source / item.path).read_bytes()
        publisher.write(item.path, (content,), length=len(content), sha256=sha256_hex(content))
    candidate = verify_candidate(LocalReleaseStore(publisher.root), manifest_digest)
    control.seal(
        {
            RECEIPT_PATH: (len(candidate.receipt_bytes), sha256_hex(candidate.receipt_bytes)),
            CHECKSUMS_PATH: (len(candidate.checksums_bytes), sha256_hex(candidate.checksums_bytes)),
        }
    )

    result = publisher.publish(manifest_digest)
    reopened = _store(tmp_path / "ledger", clock)
    status = reopened.get(job_id, scope=SCOPE, principal=OWNER)

    assert result.publication_state == "published"
    assert status.state == "committed"
    assert publisher.discover() == [publisher.root]
    assert publisher.publish(manifest_digest) == result
    assert MANIFEST_PATH in {item.path for item in objects}
    assert reopened.flush_events() >= 1
    assert "job.committed" in (tmp_path / "ledger" / EVENTS_NAME).read_text()


def test_given_invalid_settings_when_constructed_then_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseJobError):
        ReleaseRuntimeSettings(lease_seconds=4)
    with pytest.raises(ReleaseJobError):
        ReleaseRuntimeSettings(memory_bytes=MIN_MEMORY_BYTES - 1)
    with pytest.raises(ReleaseJobError):
        ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_LEASE_SECONDS": "many"})

    settings = ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_LEASE_SECONDS": "90"})
    assert settings.lease_seconds == 90


def test_given_invalid_request_when_constructed_then_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseJobError):
        _request(tmp_path / "dest", key="bad key!")
    with pytest.raises(ReleaseJobError):
        _request(tmp_path / "dest", frozen=b"")


def test_given_supervised_predecessor_when_stopped_then_successor_may_write(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    stale = store.claim(job_id, "worker-a")

    with ProcessSupervisor([sys.executable, "-c", CHILD_SLEEP], memory_bytes=MEMORY) as supervisor:
        clock.advance(31)
        current = store.claim(job_id, "worker-b")
        with pytest.raises(AuthorityError, match="predecessor"):
            store.begin_effect(job_id, current, ATTEMPT, "data/a.bin", 1, DIGEST)
        returncode = supervisor.stop()

    store.confirm_stopped(job_id, stale)
    assert returncode is not None
    assert store.begin_effect(job_id, current, ATTEMPT, "data/a.bin", 1, DIGEST) is False


def test_given_supervisor_when_stopped_then_grandchild_is_terminated(tmp_path: Path) -> None:
    child = (
        "import subprocess, sys\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', \"import sys; sys.stdin.read()\"],"
        " stdin=subprocess.PIPE)\n"
        "print(grandchild.pid, flush=True)\n"
        "sys.stdin.read()\n"
    )
    with ProcessSupervisor([sys.executable, "-c", child], memory_bytes=MEMORY, grace_seconds=FAST_GRACE) as supervisor:
        grandchild = ProcessIdentity.of(int(supervisor.output.readline()))
        proof = supervisor.stop()
        # EOF on the inherited stdout proves every process in the group has closed it.
        assert supervisor.output.read() == b""

    assert proof.members_reaped >= 1
    assert _reaped(grandchild)


def test_given_child_over_memory_limit_when_run_then_allocation_fails(tmp_path: Path) -> None:
    child = "x = bytearray(2 * 1024**3)\nprint('allocated', flush=True)\n"
    with ProcessSupervisor([sys.executable, "-c", child], memory_bytes=MEMORY) as supervisor:
        output = supervisor.output.read()
        assert supervisor.wait_for_exit(60)
        proof = supervisor.stop()

    assert proof.leader_exit_code != 0
    assert output == b""


def test_given_unsafe_supervisor_arguments_when_constructed_then_refused() -> None:
    with pytest.raises(ReleaseVerificationError):
        ProcessSupervisor([], memory_bytes=MEMORY)
    with pytest.raises(ReleaseVerificationError):
        ProcessSupervisor([sys.executable], memory_bytes=1)
    python = sys.executable
    for kwargs in (
        {"grace_seconds": math.nan},
        {"grace_seconds": -1.0},
        {"liveness_seconds": 0.0},
        {"liveness_seconds": math.inf},
        {"inherit_fds": (-1,)},
        {"inherit_fds": (True,)},
    ):
        with pytest.raises(ReleaseVerificationError):
            ProcessSupervisor([python], memory_bytes=MEMORY, **kwargs)


def test_given_registration_fails_when_started_then_effect_never_runs(tmp_path: Path) -> None:
    marker = tmp_path / "ran.marker"

    def refuse(runtime: SupervisedRuntime) -> None:
        raise OSError("registry unavailable")

    supervisor = ProcessSupervisor(
        [sys.executable, "-c", TOUCH_CODE, str(marker)],
        memory_bytes=MEMORY,
        grace_seconds=FAST_GRACE,
        on_started=refuse,
    )
    with pytest.raises(ReleaseVerificationError, match="registration failed"):
        supervisor.start()

    assert not marker.exists()


def test_given_registration_acknowledged_when_started_then_effect_runs_after_registration(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    read_fd, write_fd = os.pipe()

    def register(runtime: SupervisedRuntime) -> None:
        registry.write_text(json.dumps(runtime.to_dict()))

    supervisor = ProcessSupervisor(
        [sys.executable, "-c", CHECK_REGISTERED_CODE, str(registry), str(write_fd)],
        memory_bytes=MEMORY,
        grace_seconds=FAST_GRACE,
        inherit_fds=(write_fd,),
        on_started=register,
    )
    try:
        with supervisor:
            os.close(write_fd)
            assert _read_line(read_fd) == "registered"
            assert SupervisedRuntime.from_dict(json.loads(registry.read_text())) == supervisor.runtime
    finally:
        os.close(read_fd)


def test_given_controller_sigkill_when_guardian_sees_eof_then_tree_reaped_and_proof_recovered(tmp_path: Path) -> None:
    registry = tmp_path / "runtime.json"
    proof_path = tmp_path / "proof.json"
    read_fd, write_fd = os.pipe()
    controller = subprocess.Popen(
        [sys.executable, "-c", CONTROLLER_CODE, str(write_fd), str(registry), str(proof_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(write_fd,),
        env={**os.environ, "PYTHONPATH": os.pathsep.join([PACKAGE_ROOT, os.environ.get("PYTHONPATH", "")])},
    )
    os.close(write_fd)
    try:
        grandchild = ProcessIdentity.of(_read_int_line(read_fd))
        controller.kill()
    finally:
        controller.wait()
        os.close(read_fd)

    runtime = SupervisedRuntime.from_dict(json.loads(registry.read_text()))
    proof = recover_stop_proof(runtime, timeout_seconds=60)

    assert proof.members_reaped >= 1
    assert _reaped(grandchild)
    assert _reaped(runtime.leader)


def test_given_tampered_stop_proof_when_recovered_then_refused(tmp_path: Path) -> None:
    proof_path = tmp_path / "proof.json"
    with ProcessSupervisor(
        [sys.executable, "-c", CHILD_SLEEP],
        memory_bytes=MEMORY,
        grace_seconds=FAST_GRACE,
        proof_path=proof_path,
    ) as supervisor:
        runtime = supervisor.runtime
        proof = supervisor.stop()
    assert recover_stop_proof(runtime, timeout_seconds=5) == proof

    payload = json.loads(proof_path.read_text())
    payload["leader"]["start"] += 1
    proof_path.write_text(json.dumps(payload))
    with pytest.raises(ReleaseVerificationError, match="unresolved"):
        recover_stop_proof(runtime, timeout_seconds=5)


def test_given_guardian_killed_when_stop_requested_then_stop_is_unresolved(tmp_path: Path) -> None:
    proof_path = tmp_path / "proof.json"
    supervisor = ProcessSupervisor(
        [sys.executable, "-c", CHILD_SLEEP],
        memory_bytes=MEMORY,
        grace_seconds=FAST_GRACE,
        proof_path=proof_path,
    )
    supervisor.start()
    guardian = supervisor.runtime.guardian.pid
    os.kill(guardian, signal.SIGKILL)
    _await_process_exit(guardian)

    with pytest.raises(ReleaseVerificationError, match="unresolved"):
        supervisor.stop()
    assert not proof_path.exists()


def test_given_silent_controller_when_liveness_expires_then_guardian_stops_tree() -> None:
    with ProcessSupervisor(
        [sys.executable, "-c", CHILD_SLEEP],
        memory_bytes=MEMORY,
        grace_seconds=FAST_GRACE,
        liveness_seconds=0.05,
    ) as supervisor:
        with pytest.raises(ReleaseVerificationError):
            supervisor.wait_for_exit(60)
        proof = supervisor.stop()

    assert proof.leader_exit_code != 0
    assert _reaped(proof.leader)


@pytest.mark.parametrize("body", [b"{", b"null", b"[]", b'{"op":1}', b"{}"])
def test_given_invalid_guardian_frame_when_channel_reads_then_protocol_refused(body: bytes) -> None:
    reader, writer = socket.socketpair()
    with reader, writer:
        channel = supervision._Channel(reader)
        writer.sendall(struct.pack(">I", len(body)) + body)

        with pytest.raises(ReleaseVerificationError, match="protocol"):
            channel.fill(0)


def test_given_fragmented_guardian_frames_when_channel_reads_then_order_and_eof_preserved() -> None:
    reader, writer = socket.socketpair()
    with reader, writer:
        channel = supervision._Channel(reader)
        body = b'{"op":"ping"}'
        frame = struct.pack(">I", len(body)) + body
        writer.sendall(frame[:2])
        channel.fill(0)
        assert not channel.pending()
        writer.sendall(frame[2:6])
        channel.fill(0)
        assert not channel.pending()

        writer.sendall(frame[6:] + frame)
        channel.fill(0)

        assert channel.pop() == {"op": "ping"}
        assert channel.next_message(0) == {"op": "ping"}
        assert channel.next_message(0) is None
        writer.close()
        assert channel.next_message(0.1) is None
        assert channel.closed
        channel.fill(0)


@pytest.mark.parametrize("direction", ["send", "receive"])
def test_given_oversized_guardian_frame_when_channel_used_then_refused(direction: str) -> None:
    reader, writer = socket.socketpair()
    with reader, writer:
        channel = supervision._Channel(reader)

        with pytest.raises(ReleaseVerificationError, match="protocol"):
            if direction == "send":
                channel.send({"op": "x" * (supervision.MAX_FRAME_BYTES + 1)})
            else:
                writer.sendall(struct.pack(">I", supervision.MAX_FRAME_BYTES + 1))
                channel.fill(0)


def test_given_closed_guardian_peer_when_channel_sends_then_control_loss_is_recorded() -> None:
    reader, writer = socket.socketpair()
    with reader, writer:
        channel = supervision._Channel(reader)
        writer.close()

        with pytest.raises(ReleaseVerificationError, match="control channel was lost"):
            channel.send({"op": "ping"})

        assert channel.closed


@pytest.mark.parametrize(
    ("field", "value"),
    [("pid", True), ("start", -1), ("boot_id", ""), ("boot_id", None), ("extra", 1)],
)
def test_given_invalid_process_identity_when_decoded_then_protocol_refused(field: str, value: object) -> None:
    payload = {"pid": 10, "start": 20, "boot_id": "test-boot", field: value}

    with pytest.raises(ReleaseVerificationError, match="protocol"):
        ProcessIdentity.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("v", 2),
        ("state", "running"),
        ("leader_exit_code", True),
        ("members_reaped", -1),
        ("guardian", {"pid": 10, "start": 20, "boot_id": "other-boot"}),
        ("leader", {"pid": 99, "start": 21, "boot_id": "test-boot"}),
        ("extra", 1),
    ],
)
def test_given_invalid_stop_proof_when_decoded_then_never_confirms_stop(field: str, value: object) -> None:
    runtime = SupervisedRuntime(ProcessIdentity(10, 20, "test-boot"), ProcessIdentity(11, 21, "test-boot"), None)
    payload = {
        "v": 1,
        "state": "stopped",
        "guardian": runtime.guardian.to_dict(),
        "leader": runtime.leader.to_dict(),
        "leader_exit_code": 0,
        "members_reaped": 0,
        field: value,
    }

    with pytest.raises(ReleaseVerificationError, match="unresolved"):
        supervision.StopProof.from_payload(payload, runtime)


@pytest.mark.parametrize("kind", ["none", "missing", "symlink", "fifo", "oversized", "invalid-json"])
def test_given_unsafe_or_absent_stop_proof_when_recovered_then_stop_remains_unresolved(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "proof.json"
    if kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "oversized":
        path.write_bytes(b" " * (supervision.MAX_PROOF_BYTES + 1))
    elif kind == "invalid-json":
        path.write_bytes(b"{")
    identity = ProcessIdentity.of(os.getpid())
    runtime = SupervisedRuntime(identity, identity, None if kind == "none" else path)

    with pytest.raises(ReleaseVerificationError, match="unresolved"):
        recover_stop_proof(runtime, timeout_seconds=0)


def test_given_reused_pid_identity_when_checked_then_original_process_counts_as_exited() -> None:
    current = ProcessIdentity.of(os.getpid())

    assert not current.has_exited()
    assert replace(current, start=current.start + 1).has_exited()
    assert replace(current, boot_id="previous-boot").has_exited()


def test_given_invalid_runtime_proof_path_when_decoded_then_protocol_refused() -> None:
    identity = ProcessIdentity.of(os.getpid())
    payload = SupervisedRuntime(identity, identity, None).to_dict()
    payload["proof_path"] = False

    with pytest.raises(ReleaseVerificationError, match="protocol"):
        SupervisedRuntime.from_dict(payload)


@pytest.mark.parametrize("operation", ["runtime", "output", "stop", "ping", "wait_for_exit"])
def test_given_unstarted_supervisor_when_used_then_refused(operation: str) -> None:
    supervisor = ProcessSupervisor([sys.executable, "-c", CHILD_SLEEP], memory_bytes=MEMORY)

    with pytest.raises(ReleaseVerificationError, match="not started"):
        if operation == "wait_for_exit":
            supervisor.wait_for_exit(0)
        else:
            value = getattr(supervisor, operation)
            if callable(value):
                value()


@pytest.mark.parametrize(
    "options",
    [
        {"argv": ["x" * (supervision.MAX_ARGV_BYTES + 1)]},
        {"file_bytes": 0},
        {"inherit_fds": (1, 1)},
        {"inherit_fds": (-1,)},
        {"inherit_fds": (True,)},
        {"inherit_fds": tuple(range(supervision.MAX_INHERITED_FDS + 1))},
    ],
)
def test_given_unsafe_supervisor_bounds_when_constructed_then_refused(options: dict[str, Any]) -> None:
    arguments = {"argv": [sys.executable, "-c", CHILD_SLEEP], "memory_bytes": MEMORY, **options}

    with pytest.raises(ReleaseVerificationError):
        ProcessSupervisor(**arguments)


def test_given_unavailable_process_launch_when_supervisor_starts_then_descriptors_are_closed(
    mocker: MockerFixture,
) -> None:
    supervisor = ProcessSupervisor([sys.executable, "-c", CHILD_SLEEP], memory_bytes=MEMORY)
    mocker.patch("physical_ai_curation.release.supervision.subprocess.Popen", side_effect=OSError("unavailable"))
    descriptors = set(os.listdir("/proc/self/fd"))

    with pytest.raises(ReleaseVerificationError, match="guardian could not start"):
        supervisor.start()

    assert set(os.listdir("/proc/self/fd")) == descriptors


def test_given_missing_worker_directory_when_supervisor_starts_then_guardian_failure_is_reported(
    tmp_path: Path,
) -> None:
    supervisor = ProcessSupervisor([sys.executable, "-c", CHILD_SLEEP], memory_bytes=MEMORY, cwd=tmp_path / "missing")

    with pytest.raises(ReleaseVerificationError, match="supervised start failed"):
        supervisor.start()


@pytest.mark.parametrize("condition", ["not-requested", "stale-fence", "not-stopped", "scratch", "effect"])
def test_given_unsettled_job_when_cancellation_settles_then_capacity_is_retained(
    tmp_path: Path, condition: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    if condition == "scratch":
        store.reserve_scratch(job_id, fence, 100)
    if condition == "effect":
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    if condition != "not-requested":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    if condition != "not-stopped":
        store.confirm_stopped(job_id, fence)
    before = store.storage.snapshot()[job_id]

    with pytest.raises((AuthorityError, CancellationError)):
        store.settle_cancellation(job_id, fence + 1 if condition == "stale-fence" else fence, output_removed=True)

    assert store.storage.snapshot()[job_id] == before
    assert store.get(job_id, scope=SCOPE, principal=OWNER).capacity_state == "reserved"


@pytest.mark.parametrize("observation", ["complete", "conflict", "oversized", "absent"])
def test_given_uncertain_local_output_when_reconciled_then_content_identity_controls_outcome(
    tmp_path: Path, observation: str
) -> None:
    destination = tmp_path / "destination"
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    digest = sha256_hex(b"abc")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, digest)
    store.finish_effect(job_id, fence, "data/a.bin", ok=False)
    path = destination / ATTEMPT.prefix / "data/a.bin"
    if observation != "absent":
        path.parent.mkdir(parents=True)
        path.write_bytes({"complete": b"abc", "conflict": b"xyz", "oversized": b"abcd"}[observation])
    before = store.storage.snapshot()[job_id]

    if observation in ("conflict", "oversized"):
        with pytest.raises(JobConflictError, match="conflicts"):
            store.reconcile_local_outputs(job_id, fence)
        assert store.storage.snapshot()[job_id] == before
    else:
        assert store.reconcile_local_outputs(job_id, fence) == 0
        effects = store.storage.snapshot()[job_id]["effects"]
        if observation == "absent":
            assert effects == {}
        else:
            assert effects["data/a.bin"]["status"] == "acknowledged"
            assert path.read_bytes() == b"abc"


@pytest.mark.parametrize("condition", ["invalid-observation", "unknown-effect", "identity", "pending", "conflict"])
def test_given_invalid_effect_observation_when_reconciled_then_journal_is_unchanged(
    tmp_path: Path, condition: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    store.finish_effect(job_id, fence, "data/a.bin", ok=False)
    before = store.storage.snapshot()[job_id]
    observed = condition if condition in ("pending", "conflict", "invalid-observation") else "complete"

    with pytest.raises((ReleaseJobError, AuthorityError, JobConflictError)):
        store.reconcile_effect(
            job_id,
            fence,
            "data/missing.bin" if condition == "unknown-effect" else "data/a.bin",
            observed=observed,
            length=4 if condition == "identity" else 3,
            digest=DIGEST,
        )

    assert store.storage.snapshot()[job_id] == before


@pytest.mark.parametrize("operation", ["scratch-authorize", "scratch-cleanup", "confirm", "seal", "register"])
def test_given_unproven_runtime_when_cleanup_or_seal_attempted_then_workspace_is_retained(
    tmp_path: Path, operation: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    workspace = store.create_scratch_workspace(job_id, fence, 100)
    payload = workspace / "retained.bin"
    payload.write_bytes(b"owned")
    identity = ProcessIdentity.of(os.getpid())
    runtime = SupervisedRuntime(identity, identity, store.runtime_proof_path(job_id, fence))
    store.register_runtime(job_id, fence, runtime)
    before = store.storage.snapshot()[job_id]

    with pytest.raises(AuthorityError, match="runtime"):
        if operation == "scratch-authorize":
            store.authorize_scratch_cleanup(job_id, fence)
        elif operation == "scratch-cleanup":
            store.cleanup_scratch_workspace(job_id, fence)
        elif operation == "confirm":
            store.confirm_stopped(job_id, fence)
        elif operation == "seal":
            store.seal(job_id, fence, CONTROLS)
        else:
            store.register_runtime(job_id, fence, runtime)

    assert store.storage.snapshot()[job_id] == before
    assert payload.read_bytes() == b"owned"


@pytest.mark.parametrize("condition", ["none", "outside", "invalid-name"])
def test_given_unconfined_runtime_proof_when_registered_then_ledger_is_unchanged(
    tmp_path: Path, condition: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    path = store.runtime_proof_path(job_id, fence)
    if condition == "outside":
        path = tmp_path / path.name
    elif condition == "invalid-name":
        path = path.with_name("invalid.json")
    identity = ProcessIdentity.of(os.getpid())
    runtime = SupervisedRuntime(identity, identity, None if condition == "none" else path)
    before = store.storage.snapshot()[job_id]

    with pytest.raises(AuthorityError, match="runtime proof"):
        store.register_runtime(job_id, fence, runtime)

    assert store.storage.snapshot()[job_id] == before


@pytest.mark.parametrize("operation", ["confirm", "recover"])
def test_given_unknown_fence_when_runtime_stop_recorded_then_refused(tmp_path: Path, operation: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()[job_id]

    with pytest.raises(AuthorityError, match="unknown fence"):
        if operation == "confirm":
            store.confirm_stopped(job_id, fence + 1)
        else:
            store.recover_runtimes(job_id, fence + 1, timeout_seconds=0)

    assert store.storage.snapshot()[job_id] == before


@pytest.mark.parametrize("condition", ["live-creator", "foreign-fence", "planned", "changed-inode"])
def test_given_unresolved_workspace_ownership_when_cleaned_then_files_are_retained(
    tmp_path: Path, condition: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    workspace = store.create_scratch_workspace(job_id, fence, 100)
    payload = workspace / "retained.bin"
    payload.write_bytes(b"owned")
    with store.storage.transaction() as records:
        ownership = records[job_id]["workspace"]
        if condition == "foreign-fence":
            ownership["fence"] += 1
        elif condition == "planned":
            ownership["status"] = "planned"
        elif condition == "changed-inode":
            ownership["inode"] += 1
    before = store.storage.snapshot()[job_id]

    with pytest.raises(AuthorityError):
        store.cleanup_scratch_workspace(job_id, fence, recover=condition == "live-creator")

    assert store.storage.snapshot()[job_id] == before
    assert payload.read_bytes() == b"owned"


def test_given_owned_nested_scratch_files_when_cleaned_then_reservation_can_be_released(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    workspace = store.create_scratch_workspace(job_id, fence, 100)
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "data.bin").write_bytes(b"owned")

    store.cleanup_scratch_workspace(job_id, fence)
    store.release_scratch(job_id, fence)

    assert not workspace.exists()
    record = store.storage.snapshot()[job_id]
    assert record["workspace"]["status"] == "removed"
    assert record["reservation"]["status"] == "reserved"
    assert record["scratch_bytes"] == 0


def _owned_effect_job(
    tmp_path: Path, clock: FakeClock, path: str, payload: bytes
) -> tuple[ReleaseJobStore, str, int, Path]:
    root = tmp_path / "dest" / ATTEMPT.prefix
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, path, len(payload), sha256_hex(payload))
    store.finish_effect(job_id, fence, path, ok=True)
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    store.confirm_stopped(job_id, fence)
    return store, job_id, fence, root


def test_given_same_key_different_attempt_when_accepted_then_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "dest"))

    with pytest.raises(JobConflictError, match="different request"):
        store.accept(_request(tmp_path / "dest", attempt=OTHER_ATTEMPT))


def test_given_same_key_different_destination_or_reservation_when_accepted_then_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "dest"))

    with pytest.raises(JobConflictError, match="different request"):
        store.accept(_request(tmp_path / "other"))
    with pytest.raises(JobConflictError, match="different request"):
        store.accept(_request(tmp_path / "dest", reserved=2048))


def test_given_same_key_different_route_when_accepted_then_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "dest"))

    with pytest.raises(JobConflictError, match="different request"):
        store.accept(_blob_request(key="key-1"))


BLOB = BlobLocation("https://Example.blob.core.windows.net:443", "release", "datasets")
ACCOUNT_BLOB = BlobLocation("https://example.blob.core.windows.net", "release", "other")


def _blob_request(
    *,
    key: str = "blob-key",
    reserved: int = 1024,
    destination: BlobLocation = BLOB,
    attempt: PublicationAttempt = ATTEMPT,
) -> JobRequest:
    return JobRequest(
        scope=SCOPE,
        principal=OWNER,
        idempotency_key=key,
        attempt=attempt,
        destination=destination,
        frozen_request=b'{"request":1}',
        reserved_bytes=reserved,
        route="blob",
    )


def _no_local_disk(_path: Path) -> int:
    raise AssertionError("remote payload admission must not stat local disk")


def _blob_publisher_job(store: ReleaseJobStore, *, dispatched: bool) -> tuple[str, int]:
    job_id = store.accept(_blob_request(key="blob-supervised")).job_id
    fence = store.claim(job_id, "worker-a")
    if dispatched:
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    return job_id, fence


def _copy_spec(publisher: SupervisedBlobPublisher) -> dict[str, Any]:
    source = SourceObject(path="data/a.bin", revision=DIGEST, length=3, sha256=DIGEST)
    content = publisher._copy_protocol(source, "https://src.example/a", {}, '"etag"')
    return json.loads(content)


def _spec_fd(spec: dict[str, Any]) -> int:
    fd = os.memfd_create("test-blob-effect", os.MFD_CLOEXEC)
    os.write(fd, json.dumps(spec).encode())
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def test_given_blob_publisher_when_generic_marker_write_requested_then_refused(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, fence = _blob_publisher_job(store, dispatched=False)
    publisher = SupervisedBlobPublisher(_FakeContainer("Example", "release", {}), BLOB, ATTEMPT, store, job_id, fence)

    with pytest.raises(ReleaseVerificationError, match="marker"):
        publisher.write(MARKER_PATH, [b"abc"], length=3, sha256=DIGEST)


def test_given_copy_spec_when_destination_escapes_root_then_child_refuses_before_azure(tmp_path: Path) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, fence = _blob_publisher_job(store, dispatched=True)
    publisher = SupervisedBlobPublisher(_FakeContainer("Example", "release", {}), BLOB, ATTEMPT, store, job_id, fence)
    spec = _copy_spec(publisher)
    spec["blob_name"] = "elsewhere/a.bin"

    with pytest.raises(ReleaseVerificationError, match="outside the authorized root"):
        _process(_spec_fd(spec))


def test_given_copy_spec_when_fence_is_stale_then_child_refuses_before_azure(tmp_path: Path) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, fence = _blob_publisher_job(store, dispatched=True)
    publisher = SupervisedBlobPublisher(_FakeContainer("Example", "release", {}), BLOB, ATTEMPT, store, job_id, fence)
    spec = _copy_spec(publisher)
    spec["fence"] = fence + 1

    with pytest.raises(AuthorityError):
        _process(_spec_fd(spec))


@pytest.mark.parametrize("mode", ["blob_copy", "blob_cleanup", "blob_remove", "blob_owner_cleanup"])
def test_given_copy_spec_when_caller_is_not_registered_runtime_then_child_refuses(tmp_path: Path, mode: str) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, fence = _blob_publisher_job(store, dispatched=True)
    publisher = SupervisedBlobPublisher(_FakeContainer("Example", "release", {}), BLOB, ATTEMPT, store, job_id, fence)
    spec = _copy_spec(publisher)
    if mode != "blob_copy":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
        store.begin_reconciliation(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST, copy_id="copy-a", cancel=True)
        spec.update(mode=mode, copy_id="copy-a")
        if mode == "blob_owner_cleanup":
            store.reconcile_effect(job_id, fence, "data/a.bin", observed="absent", length=3, digest=DIGEST)
            length, digest = store.begin_blob_owner_cleanup(job_id, fence, ATTEMPT)
            spec = json.loads(
                publisher._protocol(BLOB_OWNER_CLEANUP_PATH, length, digest, {"mode": mode, "copy_id": None})
            )

    with pytest.raises(AuthorityError):
        _process(_spec_fd(spec))


def test_given_copy_spec_when_no_copy_dispatched_then_child_refuses_before_azure(tmp_path: Path) -> None:
    clock = OffsetClock()
    store = _store(tmp_path / "ledger", clock)
    job_id, fence = _blob_publisher_job(store, dispatched=True)
    publisher = SupervisedBlobPublisher(_FakeContainer("Example", "release", {}), BLOB, ATTEMPT, store, job_id, fence)
    spec = _copy_spec(publisher)
    spec["path"] = "data/missing.bin"
    spec["blob_name"] = spec["root"] + "data/missing.bin"

    with pytest.raises(AuthorityError):
        _process(_spec_fd(spec))


SDK_HOST = ".blob.core.windows.net"
SDK_TOKEN = "Bearer synthetic-test-token"
TEST_DIR = str(Path(__file__).resolve().parent)
DEST_NAME = f"datasets/{ATTEMPT.prefix}/data/a.bin"
COMPANION = f"datasets/{ownership_path(ATTEMPT.prefix)}"
BLOB_CHILD_CODE = (
    "import sys\n"
    f"sys.path[:0] = [{PACKAGE_ROOT!r}, {TEST_DIR!r}]\n"
    "from pathlib import Path\n"
    "from physical_ai_curation.release.blob_worker import _process\n"
    "from test_release_jobs import fs_factory\n"
    "_process(int(sys.argv[1]), container_factory=fs_factory(Path(sys.argv[2])), "
    "source_authorization='Bearer synthetic-test-token')\n"
)


def _label(value: Any) -> Any:
    """Record enum members by name so the call log stays JSON-serializable."""
    return getattr(value, "name", value)


def _payload(record: dict[str, Any]) -> bytes:
    return base64.b64decode(record["data"])


def _check_etag(etag: str | None, record: dict[str, Any]) -> None:
    if etag is not None and etag != record["etag"]:
        from azure.core.exceptions import ResourceModifiedError

        raise ResourceModifiedError("changed")


def _fs_properties(record: dict[str, Any]) -> SimpleNamespace:
    copy = record["copy"]
    return SimpleNamespace(
        name=record["name"],
        size=record["size"],
        etag=record["etag"],
        metadata=dict(record["metadata"]),
        copy=SimpleNamespace(**copy) if copy else None,
    )


class FsContainer:
    """Blob container persisted under a shared directory so the guardian child sees the same state.

    Each object is one JSON file (payload, metadata, ETag, copy state). Operations are appended to
    calls.jsonl, and response-loss faults are read from faults.json on every call.
    """

    def __init__(self, sdk_root: Path, account: str, name: str) -> None:
        self.sdk_root = sdk_root
        self.account = account
        self.name = name
        self.url = f"https://{account}{SDK_HOST}/{name}"
        self.objects = sdk_root / account / name
        self.objects.mkdir(parents=True, exist_ok=True)

    def get_blob_client(self, name: str, *, version_id: str | None = None) -> FsBlob:
        if version_id is not None:
            raise ValueError("filesystem fixture supports only current unversioned blobs")
        return FsBlob(self, name)

    def delete_blobs(self, *blobs: dict[str, Any], raise_on_any_failure: bool) -> list[SimpleNamespace]:
        results = []
        for blob in blobs:
            self.get_blob_client(blob["name"]).delete_blob(etag=blob["etag"], match_condition=blob["match_condition"])
            results.append(SimpleNamespace(status_code=202))
        return results

    def list_blobs(
        self,
        name_starts_with: str | None = None,
        include: Any = None,
        results_per_page: int | None = None,
    ) -> FakePages:
        items = []
        for path in sorted(self.objects.glob("*.json")):
            record = json.loads(path.read_bytes())
            if name_starts_with is None or record["name"].startswith(name_starts_with):
                items.append(_fs_properties(record))
        return FakePages([items])

    def put(self, name: str, data: bytes, metadata: dict[str, str] | None = None) -> None:
        """Seed an object without logging a call."""
        self.save(name, data, dict(metadata or {}), None)

    def read(self, name: str) -> bytes:
        return _payload(self.load(name))

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def load(self, name: str) -> dict[str, Any]:
        from azure.core.exceptions import ResourceNotFoundError

        path = self._path(name)
        if not path.exists():
            raise ResourceNotFoundError("missing")
        return json.loads(path.read_bytes())

    def save(self, name: str, data: bytes, metadata: dict[str, str], copy: dict[str, str] | None) -> None:
        record = {
            "name": name,
            "data": base64.b64encode(data).decode(),
            "size": len(data),
            "etag": f'"{os.urandom(8).hex()}"',
            "metadata": metadata,
            "copy": copy,
        }
        path = self._path(name)
        staging = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        staging.write_bytes(json.dumps(record).encode())
        os.replace(staging, path)

    def remove(self, name: str) -> None:
        self._path(name).unlink()

    def log(self, operation: str, name: str, **details: Any) -> None:
        entry = {"op": operation, "account": self.account, "container": self.name, "name": name, **details}
        with (self.sdk_root / "calls.jsonl").open("a") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")

    def inject_response_loss(self, operation: str) -> None:
        self._write_faults({**self._read_faults(), operation: True})

    def lose_response(self, operation: str) -> None:
        """Raise after the service applied the operation, as when its response is lost."""
        faults = self._read_faults()
        if faults.pop(operation, False):
            self._write_faults(faults)
            raise OSError("response lost after the service applied the operation")

    def _read_faults(self) -> dict[str, bool]:
        path = self.sdk_root / "faults.json"
        return json.loads(path.read_bytes()) if path.exists() else {}

    def _write_faults(self, faults: dict[str, bool]) -> None:
        (self.sdk_root / "faults.json").write_text(json.dumps(faults))

    def _path(self, name: str) -> Path:
        return self.objects / f"{sha256_hex(name.encode())}.json"


class FsBlob:
    """Blob client over FsContainer covering only the operations the copy path uses."""

    def __init__(self, container: FsContainer, name: str) -> None:
        self.container = container
        self.name = name

    @property
    def url(self) -> str:
        return f"{self.container.url}/{self.name}"

    def get_blob_properties(self) -> SimpleNamespace:
        return _fs_properties(self.container.load(self.name))

    def download_blob(
        self,
        etag: str | None = None,
        match_condition: Any = None,
        *,
        offset: int | None = None,
        length: int | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.container.log("download_blob", self.name)
        faults = self.container._read_faults()
        if faults.get("forbid_video_downloads") and "/videos/" in self.name:
            raise AssertionError("copy-only release must not download video payloads")
        if faults.get("forbid_destination_tables") and self.container.name == "release" and "/data/" in self.name:
            raise AssertionError("release verification must not download destination tables")
        record = self.container.load(self.name)
        _check_etag(etag, record)
        data = _payload(record)
        if offset is not None:
            data = data[offset:] if length is None else data[offset : offset + length]
        return SimpleNamespace(chunks=lambda: iter([data]))

    def upload_blob(
        self, data: Any, overwrite: bool = False, metadata: dict[str, str] | None = None, **kwargs: Any
    ) -> dict:
        from azure.core.exceptions import ResourceExistsError

        self.container.log("upload_blob", self.name, overwrite=overwrite, metadata=metadata)
        if not overwrite and self.container.exists(self.name):
            raise ResourceExistsError("exists")
        payload = data if isinstance(data, bytes) else b"".join(data)
        self.container.save(self.name, payload, dict(metadata or {}), None)
        return {}

    def delete_blob(self, etag: str | None = None, match_condition: Any = None, **kwargs: Any) -> None:
        self.container.log("delete_blob", self.name)
        _check_etag(etag, self.container.load(self.name))
        self.container.remove(self.name)
        self.container.lose_response("delete_blob")
        self.container.lose_response(f"delete_blob:{self.name}")

    def abort_copy(self, copy_id: str) -> None:
        from azure.core.exceptions import HttpResponseError

        self.container.log("abort_copy", self.name, copy_id=copy_id)
        record = self.container.load(self.name)
        assert record["copy"]["id"] == copy_id
        if self.container._read_faults().get("complete_before_abort"):
            self.container.save(self.name, _payload(record), record["metadata"], {"id": copy_id, "status": "success"})
            raise HttpResponseError(
                "completed before abort", response=SimpleNamespace(status_code=409, reason="Conflict", headers={})
            )
        self.container.save(self.name, b"", record["metadata"], {"id": copy_id, "status": "aborted"})

    def start_copy_from_url(self, source_url: str, metadata: dict[str, str] | None = None, **kwargs: Any) -> dict:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError

        self.container.log(
            "start_copy_from_url",
            self.name,
            source_url=source_url,
            metadata=metadata,
            **{key: _label(value) for key, value in kwargs.items()},
        )
        if kwargs.get("match_condition") == MatchConditions.IfMissing and self.container.exists(self.name):
            raise ResourceExistsError("destination exists")
        source, source_name = self._source(source_url)
        record = source.load(source_name)
        if "source_etag" in kwargs:
            _check_etag(kwargs["source_etag"], record)
        copy_id = f"copy-{os.urandom(4).hex()}"
        copied_metadata = dict(metadata if metadata is not None else record["metadata"])
        self.container.save(self.name, _payload(record), copied_metadata, {"status": "success", "id": copy_id})
        self.container.lose_response("start_copy_from_url")
        return {"copy_id": copy_id, "copy_status": "success"}

    def _source(self, source_url: str) -> tuple[FsContainer, str]:
        parts = urlsplit(source_url)
        container_name, _, blob_name = parts.path.lstrip("/").partition("/")
        return FsContainer(self.container.sdk_root, parts.netloc.split(".")[0], container_name), blob_name


def fs_factory(sdk_root: Path) -> Callable[[str, str], FsContainer]:
    """Container factory for the child; resolves the account from the canonical account URL."""

    def build(account_url: str, container: str) -> FsContainer:
        return FsContainer(sdk_root, urlsplit(account_url).netloc.split(".")[0], container)

    return build


def _sdk_calls(sdk_root: Path, operation: str) -> list[dict[str, Any]]:
    log = sdk_root / "calls.jsonl"
    if not log.exists():
        return []
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    return [entry for entry in entries if entry["op"] == operation]


class _ChildBlobPublisher(SupervisedBlobPublisher):
    """Runs the real guardian child against the filesystem SDK fake instead of the module entry point."""

    def __init__(self, *args: Any, sdk_root: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sdk_root = sdk_root

    def _command(self, descriptor: int) -> list[str]:
        return [sys.executable, "-B", "-c", BLOB_CHILD_CODE, str(descriptor), str(self.sdk_root)]


def _source_object(source: FsContainer, path: str, data: bytes) -> tuple[SourceObject, str]:
    etag = source.get_blob_client(path).get_blob_properties().etag
    revision = sha256_hex(canonical_json_bytes({"etag": etag, "version_id": None}))
    return SourceObject(path=path, revision=revision, length=len(data), sha256=sha256_hex(data)), etag


def _child_copy_fixture(tmp_path: Path, *, revision_only: bool = False) -> SimpleNamespace:
    from physical_ai_curation.release.models import RevisionSourceObject
    from physical_ai_curation.release.store import blob_source_identity

    sdk = tmp_path / "sdk"
    source = FsContainer(sdk, "example", "source")
    source.put("data/a.bin", b"abc", {"kind": "frame"})
    source_object, source_etag = _source_object(source, "data/a.bin", b"abc")
    if revision_only:
        source_object = RevisionSourceObject(
            path=source_object.path,
            revision=source_object.revision,
            length=source_object.length,
            storage_identity=blob_source_identity(source.url, source_object.path),
        )
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS, clock=OffsetClock(), free_bytes=_no_local_disk)
    job = store.accept(_blob_request(key="blob-copy", reserved=1024))
    fence = store.claim(job.job_id, "worker-a")
    destination = FsContainer(sdk, "example", "release")
    publisher = _ChildBlobPublisher(destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk)
    return SimpleNamespace(
        sdk=sdk,
        source=source,
        source_object=source_object,
        source_etag=source_etag,
        store=store,
        job_id=job.job_id,
        fence=fence,
        destination=destination,
        publisher=publisher,
    )


def _multi_copy_fixture(tmp_path: Path, count: int, *, batch_size: int = 16) -> SimpleNamespace:
    sdk = tmp_path / "sdk"
    source = FsContainer(sdk, "example", "source")
    items: list[tuple[Any, str, str]] = []
    for index in range(count):
        path = f"data/part-{index:02d}.bin"
        source.put(path, b"abc", {"kind": "frame"})
        source_object, etag = _source_object(source, path, b"abc")
        items.append((source_object, etag, path))
    store = _batch_store(tmp_path / "ledger", OffsetClock(), batch_size)
    job = store.accept(_blob_request(key="blob-batch", reserved=1024))
    fence = store.claim(job.job_id, "worker-a")
    destination = FsContainer(sdk, "example", "release")
    publisher = _ChildBlobPublisher(destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk)
    return SimpleNamespace(
        sdk=sdk, source=source, items=items, store=store, job_id=job.job_id, fence=fence, publisher=publisher
    )


def test_given_twelve_copies_when_batched_then_one_child_issues_every_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _multi_copy_fixture(tmp_path, 12)
    dispatched: list[list[str]] = []
    original = fixture.publisher._execute_batch

    def counted(paths: list[str], length: int, content: bytes) -> None:
        dispatched.append(list(paths))
        original(paths, length, content)

    monkeypatch.setattr(fixture.publisher, "_execute_batch", counted)
    progress = fixture.publisher.copy_batch(fixture.source, fixture.items)

    assert [item.state for item in progress] == ["complete"] * 12
    assert [len(paths) for paths in dispatched] == [12]
    assert len(_sdk_calls(fixture.sdk, "start_copy_from_url")) == 12
    assert len([call for call in _sdk_calls(fixture.sdk, "download_blob") if call["name"] == COMPANION]) < 12
    effects = fixture.store._snapshot()[fixture.job_id]["effects"]
    assert {effect["status"] for effect in effects.values()} == {"acknowledged"}


def test_given_nine_copies_and_batch_size_four_when_copied_then_bounded_groups_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _multi_copy_fixture(tmp_path, 9, batch_size=4)
    dispatched: list[list[str]] = []
    original = fixture.publisher._execute_batch

    def counted(paths: list[str], length: int, content: bytes) -> None:
        dispatched.append(list(paths))
        original(paths, length, content)

    monkeypatch.setattr(fixture.publisher, "_execute_batch", counted)
    progress = fixture.publisher.copy_batch(fixture.source, fixture.items)

    assert [item.state for item in progress] == ["complete"] * 9
    assert [len(paths) for paths in dispatched] == [4, 4, 1]


def test_given_acknowledged_copy_in_batch_when_replayed_then_not_recopied_and_counted_once(tmp_path: Path) -> None:
    fixture = _multi_copy_fixture(tmp_path, 3)
    first, first_etag, first_path = fixture.items[0]
    assert fixture.publisher.copy(fixture.source, first, first_etag, source_path=first_path).state == "complete"

    progress = fixture.publisher.copy_batch(fixture.source, fixture.items)

    assert [item.state for item in progress] == ["complete"] * 3
    assert len(_sdk_calls(fixture.sdk, "start_copy_from_url")) == 3
    assert fixture.store._snapshot()[fixture.job_id]["io_counters"]["storage_copy_bytes"] == 9


def test_given_child_fails_after_one_copy_when_reconciled_and_replayed_then_no_recopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _multi_copy_fixture(tmp_path, 3)
    publisher = fixture.publisher

    build_batch_protocol = publisher._batch_protocol

    def corrupt_second_item(entries: list[tuple[SourceObjectRecord, str, str]]) -> bytes:
        # The real child copies item one, then refuses item two before any Azure call.
        spec = json.loads(build_batch_protocol(entries))
        spec["items"][1]["source_url"] = "https://other.blob.core.windows.net/source/data/b.bin"
        return json.dumps(spec, separators=(",", ":")).encode()

    monkeypatch.setattr(publisher, "_batch_protocol", corrupt_second_item)
    with pytest.raises(ReleaseVerificationError, match="supervised blob copy did not complete"):
        publisher.copy_batch(fixture.source, fixture.items)
    monkeypatch.undo()

    progress = publisher.copy_batch(fixture.source, fixture.items)

    assert [item.state for item in progress] == ["complete"] * 3
    assert len(_sdk_calls(fixture.sdk, "start_copy_from_url")) == 3
    assert fixture.store._snapshot()[fixture.job_id]["io_counters"]["storage_copy_bytes"] == 9


def _generated_fixture(tmp_path: Path, *, batch_size: int = 16) -> SimpleNamespace:
    sdk = tmp_path / "sdk"
    destination = FsContainer(sdk, "example", "release")
    store = _batch_store(tmp_path / "ledger", OffsetClock(), batch_size)
    job = store.accept(_blob_request(key="blob-generated", reserved=1024))
    fence = store.claim(job.job_id, "worker-a")
    publisher = _ChildBlobPublisher(destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk)
    out = tmp_path / "out"
    out.mkdir()
    return SimpleNamespace(
        sdk=sdk, destination=destination, store=store, job_id=job.job_id, fence=fence, publisher=publisher, out=out
    )


def _write_mixed_generated(fixture: SimpleNamespace) -> None:
    publisher = fixture.publisher
    payload = fixture.out / "a.bin"
    payload.write_bytes(b"abc")
    digest = sha256(b"abc").hexdigest()
    publisher.write_from_path("data/a.bin", payload, length=3, sha256=digest)
    publisher.write("data/b.json", [b"abc"], length=3, sha256=digest)
    publisher.write_from_path("data/c.bin", payload, length=3, sha256=digest)


def test_given_mixed_generated_writes_when_batched_then_bounded_groups_dispatch_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _generated_fixture(tmp_path, batch_size=2)
    dispatched: list[list[str]] = []
    original = fixture.publisher._execute_batch

    def counted(paths: list[str], length: int, content: bytes) -> None:
        dispatched.append(list(paths))
        original(paths, length, content)

    monkeypatch.setattr(fixture.publisher, "_execute_batch", counted)
    with fixture.publisher.write_batch():
        _write_mixed_generated(fixture)
        assert [len(paths) for paths in dispatched] == [2]

    assert [len(paths) for paths in dispatched] == [2, 1]
    root = fixture.publisher.root
    assert fixture.destination.read(root + "data/a.bin") == b"abc"
    assert fixture.destination.read(root + "data/b.json") == b"abc"
    assert fixture.destination.read(root + "data/c.bin") == b"abc"
    effects = fixture.store._snapshot()[fixture.job_id]["effects"]
    assert {effect["status"] for effect in effects.values()} == {"acknowledged"}


def test_given_generated_group_when_dispatch_fails_then_effects_are_not_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _generated_fixture(tmp_path, batch_size=4)

    def refused(paths: list[str], length: int, content: bytes) -> None:
        raise ReleaseVerificationError("supervised blob upload did not complete")

    monkeypatch.setattr(fixture.publisher, "_execute_batch", refused)
    with pytest.raises(ReleaseVerificationError, match="did not complete"), fixture.publisher.write_batch():
        _write_mixed_generated(fixture)

    effects = fixture.store._snapshot()[fixture.job_id]["effects"]
    assert effects
    assert "acknowledged" not in {effect["status"] for effect in effects.values()}


def test_given_batch_item_without_begun_effect_when_child_authorizes_then_refused_before_azure(tmp_path: Path) -> None:
    fixture = _multi_copy_fixture(tmp_path, 2)
    second, second_etag, second_path = fixture.items[1]
    content = fixture.publisher._batch_protocol(
        [(second, second_etag, fixture.source.get_blob_client(second_path).url)]
    )

    with pytest.raises(AuthorityError):
        _process(_spec_fd(json.loads(content)))
    assert _sdk_calls(fixture.sdk, "start_copy_from_url") == []


def test_given_supervised_copy_when_child_issues_server_side_copy_then_destination_verified_and_acknowledged(
    tmp_path: Path,
) -> None:
    fixture = _child_copy_fixture(tmp_path)

    progress = fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)

    assert progress.state == "complete"
    assert fixture.destination.read(DEST_NAME) == b"abc"
    assert fixture.destination.get_blob_client(DEST_NAME).get_blob_properties().metadata["kind"] == "frame"
    assert fixture.destination.read(COMPANION) == ownership_bytes(ATTEMPT)
    assert fixture.source.read("data/a.bin") == b"abc"
    assert fixture.source.get_blob_client("data/a.bin").get_blob_properties().etag == fixture.source_etag
    assert [call for call in _sdk_calls(fixture.sdk, "download_blob") if call["container"] == "source"] == []

    (copy,) = _sdk_calls(fixture.sdk, "start_copy_from_url")
    assert copy["container"] == "release"
    assert copy["name"] == DEST_NAME
    assert copy["source_url"] == "https://example.blob.core.windows.net/source/data/a.bin"
    assert copy["source_etag"] == fixture.source_etag
    assert copy["source_match_condition"] == "IfNotModified"
    assert copy["match_condition"] == "IfMissing"
    assert copy["requires_sync"] is True
    assert copy["source_authorization"] == SDK_TOKEN

    record = fixture.store.storage.snapshot()[fixture.job_id]
    assert record["effects"]["data/a.bin"]["status"] == "acknowledged"
    assert record["runtimes"] and all(runtime["stopped"] for runtime in record["runtimes"].values())
    fixture.store.seal(fixture.job_id, fixture.fence, CONTROLS)


@pytest.mark.parametrize("fault", ["none", "complete_before_abort", "delete_blob"])
def test_given_pending_copy_when_cancelled_then_only_registered_child_reconciles_owned_residue(
    tmp_path: Path, fault: str
) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.destination.inject_response_loss("start_copy_from_url")
    with pytest.raises((ReleaseVerificationError, AuthorityError)):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    record = fixture.destination.load(DEST_NAME)
    fixture.destination.save(DEST_NAME, _payload(record), record["metadata"], {**record["copy"], "status": "pending"})
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    if fault != "none":
        fixture.destination.inject_response_loss(fault)
    if fault == "delete_blob":
        with pytest.raises(ReleaseVerificationError):
            fixture.publisher.reconcile_copy(fixture.source_object, fixture.source_etag, cancel=True)
        pending = fixture.store.storage.snapshot()[fixture.job_id]
        assert pending["effects"]["data/a.bin"]["status"] == "uncertain"
        assert pending["reservation"]["status"] == "reserved"
    fixture.publisher.reconcile_copy(fixture.source_object, fixture.source_etag, cancel=True)

    assert not fixture.destination.exists(DEST_NAME)
    assert fixture.source.read("data/a.bin") == b"abc"
    assert len(_sdk_calls(fixture.sdk, "start_copy_from_url")) == 1
    assert len(_sdk_calls(fixture.sdk, "abort_copy")) == 1
    assert len(_sdk_calls(fixture.sdk, "delete_blob")) == 1
    state = fixture.store.storage.snapshot()[fixture.job_id]
    assert state["effects"] == {}
    assert all(runtime["stopped"] for runtime in state["runtimes"].values())
    with pytest.raises(CancellationError):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)


@pytest.mark.parametrize("foreign", ["companion", "payload"])
def test_given_cancelled_copy_with_foreign_ownership_when_cleanup_then_foreign_bytes_untouched(
    tmp_path: Path, foreign: str
) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    path = COMPANION if foreign == "companion" else DEST_NAME
    fixture.destination.put(path, b"foreign")

    with pytest.raises(ReleaseVerificationError):
        fixture.publisher.reconcile_copy(fixture.source_object, fixture.source_etag, cancel=True)

    assert fixture.destination.read(path) == b"foreign"
    assert fixture.source.read("data/a.bin") == b"abc"
    assert _sdk_calls(fixture.sdk, "delete_blob") == []
    assert _sdk_calls(fixture.sdk, "abort_copy") == []


def test_given_twelve_owned_blobs_when_cancelled_then_cleanup_batches_payload_children(tmp_path: Path) -> None:
    fixture = _multi_copy_fixture(tmp_path, 12)
    fixture.publisher.copy_batch(fixture.source, fixture.items)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    with patch.object(fixture.publisher, "_run_child", wraps=fixture.publisher._run_child) as children:
        fixture.publisher.cleanup_cancelled()
    assert children.call_count == 2
    assert list(fixture.publisher.container.list_blobs()) == []
    assert _sdk_calls(fixture.sdk, "delete_blob")[-1]["name"] == COMPANION
    fixture.store.purge_cancelled(fixture.job_id, fixture.fence)
    assert fixture.job_id not in fixture.store.storage.snapshot()
    assert not (fixture.store.root / "runtimes" / fixture.job_id).exists()


@pytest.mark.parametrize("parent", ["runtimes", "scratch"])
def test_given_unowned_runtime_file_when_cancelled_job_purged_then_record_and_file_retained(
    tmp_path: Path, parent: str
) -> None:
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS)
    job = store.accept(_request(tmp_path / "datasets"))
    store.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    store.settle_cancellation(job.job_id, 0, output_removed=True)
    directory = store.root / parent / job.job_id
    directory.mkdir(parents=True)
    foreign = directory / "unowned.json"
    foreign.write_text("unrelated")
    with pytest.raises(AuthorityError):
        store.purge_cancelled(job.job_id, 0)
    assert job.job_id in store.storage.snapshot()
    assert foreign.read_text() == "unrelated"


@pytest.mark.parametrize("loss", [None, "payload", "companion"])
@pytest.mark.parametrize("revision_only", [False, True])
def test_given_cancelled_blob_root_when_cleaned_then_companion_last_and_capacity_released(
    tmp_path: Path, loss: str | None, revision_only: bool
) -> None:
    fixture = _child_copy_fixture(tmp_path, revision_only=revision_only)
    publisher = fixture.publisher
    publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    publisher.write("meta/info.json", [b"{}"], length=2, sha256=sha256_hex(b"{}"))
    controls = {RECEIPT_PATH: b"receipt", CHECKSUMS_PATH: b"checksums"}
    fixture.store.seal(
        fixture.job_id, fixture.fence, {path: (len(content), sha256_hex(content)) for path, content in controls.items()}
    )
    for path, content in controls.items():
        publisher.write(path, [content], length=len(content), sha256=sha256_hex(content))
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    if loss is not None:
        execute = publisher._execute

        def lose_response(path: str, length: int, content: bytes) -> None:
            if (path == BLOB_OWNER_CLEANUP_PATH) == (loss == "companion"):
                fixture.destination.inject_response_loss("delete_blob")
            execute(path, length, content)

        with patch.object(publisher, "_execute", side_effect=lose_response), pytest.raises(ReleaseVerificationError):
            publisher.cleanup_cancelled()
        state = fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER)
        assert state.state == "cancelling"
        assert state.capacity_state == "retained"
        assert state.cleanup_state == "failed"
        assert fixture.destination.exists(COMPANION) == (loss == "payload")
        reopened = ReleaseJobStore(
            fixture.store.root, settings=SETTINGS, clock=OffsetClock(), free_bytes=_no_local_disk
        )
        publisher = _ChildBlobPublisher(
            fixture.destination,
            BLOB,
            ATTEMPT,
            reopened,
            fixture.job_id,
            fixture.fence,
            sdk_root=fixture.sdk,
        )

    publisher.cleanup_cancelled()

    assert list(fixture.destination.list_blobs()) == []
    assert fixture.source.read("data/a.bin") == b"abc"
    state = fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER)
    assert state.state == "cancelled"
    assert state.effects_stopped
    assert state.cleanup_state == "completed"
    assert state.capacity_state == "released"
    assert _sdk_calls(fixture.sdk, "delete_blob")[-1]["name"] == COMPANION
    assert fixture.store.storage.snapshot()[fixture.job_id]["effects"] == {}
    assert not [call for call in _sdk_calls(fixture.sdk, "download_blob") if call["name"] == DEST_NAME]


def test_given_companion_replaced_after_preflight_when_child_cleans_then_no_delete(tmp_path: Path) -> None:
    fixture = _child_copy_fixture(tmp_path)
    publisher = fixture.publisher
    publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    execute = publisher._execute

    def replace_before_dispatch(path: str, length: int, content: bytes) -> None:
        fixture.destination.put(COMPANION, b"foreign")
        execute(path, length, content)

    with (
        patch.object(publisher, "_execute", side_effect=replace_before_dispatch),
        pytest.raises(ReleaseVerificationError),
    ):
        publisher.cleanup_cancelled()

    assert fixture.destination.read(DEST_NAME) == b"abc"
    assert fixture.destination.read(COMPANION) == b"foreign"
    assert _sdk_calls(fixture.sdk, "delete_blob") == []


@pytest.mark.parametrize("complete_before_abort", [False, True])
def test_given_pending_copy_when_whole_root_cleaned_then_abort_settles_before_capacity_release(
    tmp_path: Path, complete_before_abort: bool
) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.destination.inject_response_loss("start_copy_from_url")
    with pytest.raises((ReleaseVerificationError, AuthorityError)):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    record = fixture.destination.load(DEST_NAME)
    fixture.destination.save(DEST_NAME, _payload(record), record["metadata"], {**record["copy"], "status": "pending"})
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    if complete_before_abort:
        fixture.destination.inject_response_loss("complete_before_abort")

    fixture.publisher.cleanup_cancelled()

    assert list(fixture.destination.list_blobs()) == []
    assert fixture.source.read("data/a.bin") == b"abc"
    assert len(_sdk_calls(fixture.sdk, "abort_copy")) == 1
    assert fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER).capacity_state == "released"


def test_given_blocked_cleanup_child_when_budget_expires_then_stopped_and_capacity_retained(tmp_path: Path) -> None:
    fixture = _child_copy_fixture(tmp_path)
    publisher = fixture.publisher
    publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    fixture.store.settings = replace(SETTINGS, cleanup_seconds=1, grace_seconds=0)

    with (
        patch.object(publisher, "_command", return_value=[sys.executable, "-c", CHILD_SLEEP]),
        pytest.raises(ReleaseVerificationError),
    ):
        publisher.cleanup_cancelled()

    record = fixture.store.storage.snapshot()[fixture.job_id]
    assert record["effects"]["data/a.bin"]["status"] == "uncertain"
    assert len(record["runtimes"]) == 2
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())
    state = fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER)
    assert state.state == "cancelling"
    assert state.cleanup_state == "failed"
    assert state.capacity_state == "retained"
    assert fixture.destination.read(DEST_NAME) == b"abc"
    assert fixture.destination.exists(COMPANION)


@pytest.mark.parametrize("foreign", ["unknown", "marker", "companion"])
def test_given_foreign_or_marked_blob_root_when_cleanup_then_refused_before_any_delete(
    tmp_path: Path, foreign: str
) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    path = (
        COMPANION
        if foreign == "companion"
        else fixture.publisher.root + (MARKER_PATH if foreign == "marker" else "unknown.bin")
    )
    fixture.destination.put(path, b"foreign")

    with pytest.raises(ReleaseVerificationError):
        fixture.publisher.cleanup_cancelled()

    assert fixture.destination.read(path) == b"foreign"
    assert fixture.destination.read(DEST_NAME) == b"abc"
    assert _sdk_calls(fixture.sdk, "delete_blob") == []
    assert fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER).capacity_state != "released"


def test_given_response_lost_after_server_copy_when_reconciled_then_settles_without_second_copy(tmp_path: Path) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.destination.inject_response_loss("start_copy_from_url")

    with pytest.raises((ReleaseVerificationError, AuthorityError)):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)

    assert fixture.destination.read(DEST_NAME) == b"abc"
    with pytest.raises(AuthorityError, match="unresolved"):
        fixture.store.seal(fixture.job_id, fixture.fence, CONTROLS)

    with pytest.raises(AuthorityError, match="unresolved"):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    progress = fixture.publisher.reconcile_copy(fixture.source_object, fixture.source_etag)

    assert progress.state == "complete"
    assert len(_sdk_calls(fixture.sdk, "start_copy_from_url")) == 1
    assert fixture.destination.read(DEST_NAME) == b"abc"
    fixture.store.seal(fixture.job_id, fixture.fence, CONTROLS)


def test_given_foreign_destination_object_when_copy_requested_then_refused_before_child(tmp_path: Path) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.destination.put(COMPANION, ownership_bytes(ATTEMPT))
    fixture.destination.put(DEST_NAME, b"foreign")

    with pytest.raises(ReleaseVerificationError, match="collision"):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)

    assert _sdk_calls(fixture.sdk, "start_copy_from_url") == []
    assert fixture.destination.read(DEST_NAME) == b"foreign"


def test_given_tampered_ownership_companion_when_copy_requested_then_no_child_runs(tmp_path: Path) -> None:
    fixture = _child_copy_fixture(tmp_path)
    fixture.destination.put(COMPANION, ownership_bytes(OTHER_ATTEMPT))

    with pytest.raises(ReleaseVerificationError, match="ownership"):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)

    assert _sdk_calls(fixture.sdk, "start_copy_from_url") == []
    assert fixture.destination.read(COMPANION) == ownership_bytes(OTHER_ATTEMPT)


@pytest.mark.parametrize("lose_marker_stop_ack", [False, True])
@pytest.mark.parametrize("associated", [False, True])
def test_given_blob_candidate_when_supervised_upload_and_publish_then_independently_marked(
    tmp_path: Path, lose_marker_stop_ack: bool, associated: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AssociatedChildPublisher(_ChildBlobPublisher, SourceAssociatedBlobPublisher):
        """Reuse the registered filesystem-SDK child with the associated publication policy."""

    root = tmp_path / "candidate"
    files = _payload_files()
    digest = _write_package(root, files, _manifest_for(files))
    sdk = tmp_path / "sdk"
    destination = FsContainer(sdk, "example", "release")
    clock = OffsetClock()
    store = _store(tmp_path / "ledger", clock)
    intent = _association_intent(tmp_path).model_copy(
        update={
            "attempt": ATTEMPT,
            "source_dataset_id": ATTEMPT.source_dataset_id,
            "effective_destination": BLOB.as_text(),
            "scope_sha256": sha256_hex(OWNER.encode()),
            "frozen_input_sha256": json.loads((root / MANIFEST_PATH).read_bytes())["frozen_input_sha256"],
        }
    )
    monkeypatch.setattr(
        FsBlob,
        "acquire_lease",
        lambda _blob, **_kwargs: SimpleNamespace(renew=lambda **_kwargs: None, release=lambda **_kwargs: None),
        raising=False,
    )
    job = store.accept(
        replace(
            _blob_request(key="blob-publish", reserved=8 * 1024**2),
            execution_binding=source_association_binding(intent) if associated else b"",
        )
    )
    fence = store.claim(job.job_id, "worker")
    publisher_type = AssociatedChildPublisher if associated else _ChildBlobPublisher
    association_arguments = {"intent": intent} if associated else {}
    publisher = publisher_type(
        destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk, **association_arguments
    )
    for item in LocalReleaseStore(root).list_objects():
        publisher.write_from_path(
            item.path, root / item.path, length=item.length, sha256=sha256_hex((root / item.path).read_bytes())
        )
    candidate = verify_candidate(publisher.store, digest)
    store.seal(
        job.job_id,
        fence,
        {
            RECEIPT_PATH: (len(candidate.receipt_bytes), sha256_hex(candidate.receipt_bytes)),
            CHECKSUMS_PATH: (len(candidate.checksums_bytes), sha256_hex(candidate.checksums_bytes)),
        },
    )

    if associated:
        bind_manifest = publisher.associations.bind_manifest

        def bind_under_commit(*args: Any, **kwargs: Any) -> None:
            assert store.storage.snapshot()[job.job_id]["state"] == "committing"
            bind_manifest(*args, **kwargs)

        monkeypatch.setattr(publisher.associations, "bind_manifest", bind_under_commit)

    if lose_marker_stop_ack:
        execute = publisher._execute

        def lose_marker_ack(path: str, length: int, content: bytes) -> None:
            if path == MARKER_PATH:
                with patch.object(
                    store, "confirm_runtime_stopped", side_effect=RuntimeError("stop acknowledgment lost")
                ):
                    execute(path, length, content)
            else:
                execute(path, length, content)

        with (
            patch.object(publisher, "_execute", side_effect=lose_marker_ack),
            pytest.raises(RuntimeError, match="acknowledgment lost"),
        ):
            publisher.publish(digest)
        clock.advance(31)
        fence = store.claim(job.job_id, "successor")
        publisher = publisher_type(
            destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk, **association_arguments
        )
    result = publisher.publish(digest)
    root.rename(tmp_path / "source-unavailable")

    from physical_ai_curation.release.verification import verify_published

    assert verify_published(publisher.store, digest) == result
    assert publisher.publish(digest) == result
    record = store.storage.snapshot()[job.job_id]
    assert record["state"] == "committed"
    assert all(effect["status"] == "acknowledged" for effect in record["effects"].values())
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())
    if associated:
        assert publisher.associations.discover(source_key=intent.source_key, scope_sha256=intent.scope_sha256) == [
            publisher.root
        ]
    with pytest.raises((AuthorityError, LedgerClosedError)):
        publisher.write("data/late.bin", [b"abc"], length=3, sha256=sha256_hex(b"abc"))


ASSOCIATION_RECORD = f"datasets/_release_associations/{ATTEMPT.package_id}.json"


class _AssociatedChildBlobPublisher(_ChildBlobPublisher, SourceAssociatedBlobPublisher):
    """Registered filesystem-SDK child with the associated publication and cancellation policy."""


def _associated_cancel_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(
        FsBlob,
        "acquire_lease",
        lambda _blob, **_kwargs: SimpleNamespace(renew=lambda **_kwargs: None, release=lambda **_kwargs: None),
        raising=False,
    )
    sdk = tmp_path / "sdk"
    source = FsContainer(sdk, "example", "source")
    source.put("data/a.bin", b"abc", {"kind": "frame"})
    source_object, source_etag = _source_object(source, "data/a.bin", b"abc")
    intent = _association_intent(tmp_path).model_copy(
        update={
            "attempt": ATTEMPT,
            "source_dataset_id": ATTEMPT.source_dataset_id,
            "effective_destination": BLOB.as_text(),
            "scope_sha256": sha256_hex(OWNER.encode()),
        }
    )
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS, clock=OffsetClock(), free_bytes=_no_local_disk)
    job = store.accept(
        replace(
            _blob_request(key="blob-associated-cancel", reserved=1024),
            execution_binding=source_association_binding(intent),
        )
    )
    fence = store.claim(job.job_id, "worker-a")
    destination = FsContainer(sdk, "example", "release")
    publisher = _AssociatedChildBlobPublisher(
        destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk, intent=intent
    )
    return SimpleNamespace(
        sdk=sdk,
        source=source,
        source_object=source_object,
        source_etag=source_etag,
        intent=intent,
        store=store,
        job_id=job.job_id,
        fence=fence,
        destination=destination,
        publisher=publisher,
    )


def _pending_associated_copy(fixture: SimpleNamespace) -> None:
    fixture.destination.inject_response_loss("start_copy_from_url")
    with pytest.raises((ReleaseVerificationError, AuthorityError)):
        fixture.publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    record = fixture.destination.load(DEST_NAME)
    fixture.destination.save(DEST_NAME, _payload(record), record["metadata"], {**record["copy"], "status": "pending"})
    assert fixture.destination.exists(ASSOCIATION_RECORD)


def test_given_associated_pending_copy_when_cancelled_then_record_removed_after_output_and_companion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _associated_cancel_fixture(tmp_path, monkeypatch)
    _pending_associated_copy(fixture)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)

    fixture.publisher.cleanup_cancelled()

    assert not fixture.destination.exists(DEST_NAME)
    assert not fixture.destination.exists(COMPANION)
    assert not fixture.destination.exists(ASSOCIATION_RECORD)
    assert [call["name"] for call in _sdk_calls(fixture.sdk, "delete_blob")][-2:] == [COMPANION, ASSOCIATION_RECORD]
    assert fixture.source.read("data/a.bin") == b"abc"
    state = fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER)
    assert state.state == "cancelled"
    assert state.capacity_state == "released"


@pytest.mark.parametrize(
    ("conflict", "refusal"),
    [("finalized", "association cleanup conflict"), ("foreign", "association conflict")],
)
def test_given_associated_record_conflicts_when_cancelled_then_record_retained_and_capacity_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str, refusal: str
) -> None:
    fixture = _associated_cancel_fixture(tmp_path, monkeypatch)
    _pending_associated_copy(fixture)
    if conflict == "finalized":
        fixture.publisher.associations.bind_manifest(
            fixture.intent, "a" * 64, job_id=fixture.job_id, fence=fixture.fence
        )
    else:
        fixture.destination.remove(ASSOCIATION_RECORD)
        BlobSourceAssociations(fixture.destination, BLOB, limits=RecordLimits()).ensure_intent(
            fixture.intent.model_copy(update={"source_revision": "changed"})
        )
    retained = fixture.destination.read(ASSOCIATION_RECORD)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)

    with pytest.raises(ReleaseVerificationError, match=refusal):
        fixture.publisher.cleanup_cancelled()

    assert fixture.destination.read(ASSOCIATION_RECORD) == retained
    assert all(call["name"] != ASSOCIATION_RECORD for call in _sdk_calls(fixture.sdk, "delete_blob"))
    state = fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER)
    assert state.state == "cancelling"
    assert state.cleanup_state == "failed"
    assert state.capacity_state == "retained"


def test_given_associated_record_delete_lost_when_restarted_then_cleanup_settles_without_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _associated_cancel_fixture(tmp_path, monkeypatch)
    _pending_associated_copy(fixture)
    fixture.store.request_cancel(fixture.job_id, scope=SCOPE, principal=OWNER)
    fixture.destination.inject_response_loss(f"delete_blob:{ASSOCIATION_RECORD}")

    with pytest.raises(OSError, match="response lost"):
        fixture.publisher.cleanup_cancelled()

    assert not fixture.destination.exists(ASSOCIATION_RECORD)
    failed = fixture.store.get(fixture.job_id, scope=SCOPE, principal=OWNER)
    assert failed.state == "cancelling"
    assert failed.cleanup_state == "failed"
    assert failed.capacity_state == "retained"

    reopened = ReleaseJobStore(fixture.store.root, settings=SETTINGS, clock=OffsetClock(), free_bytes=_no_local_disk)
    _AssociatedChildBlobPublisher(
        fixture.destination,
        BLOB,
        ATTEMPT,
        reopened,
        fixture.job_id,
        fixture.fence,
        sdk_root=fixture.sdk,
        intent=fixture.intent,
    ).cleanup_cancelled()

    state = reopened.get(fixture.job_id, scope=SCOPE, principal=OWNER)
    assert state.state == "cancelled"
    assert state.capacity_state == "released"
    assert not fixture.destination.exists(DEST_NAME)
    assert not fixture.destination.exists(COMPANION)
    assert len(_sdk_calls(fixture.sdk, "start_copy_from_url")) == 1


def test_given_blob_destination_with_local_route_when_constructed_then_refused() -> None:
    with pytest.raises(ReleaseJobError, match="route"):
        JobRequest(SCOPE, OWNER, "key-b", ATTEMPT, BLOB, b"frozen", 1, route="local")


def test_given_local_destination_with_blob_route_when_constructed_then_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseJobError, match="route"):
        JobRequest(SCOPE, OWNER, "key-l", ATTEMPT, tmp_path, b"frozen", 1, route="blob")


def test_given_blob_destination_when_accepted_then_local_disk_is_never_measured(tmp_path: Path) -> None:
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS, clock=FakeClock(), free_bytes=_no_local_disk)

    accepted = store.accept(_blob_request())

    assert store.get(accepted.job_id, scope=SCOPE, principal=OWNER).state == "accepted"


def test_given_blob_job_when_reopened_from_another_runtime_then_canonical_binding_survives(tmp_path: Path) -> None:
    storage = LocalRecordStorage(tmp_path / "shared-state")
    first = ReleaseJobStore(tmp_path / "runtime-a", settings=SETTINGS, storage=storage, free_bytes=_no_local_disk)
    job = first.accept(_blob_request())
    second = ReleaseJobStore(tmp_path / "runtime-b", settings=SETTINGS, storage=storage, free_bytes=_no_local_disk)
    replay = second.accept(
        _blob_request(destination=BlobLocation("https://example.blob.core.windows.net", "release", "datasets/"))
    )

    assert replay.job_id == job.job_id and replay.replayed
    record = json.loads((tmp_path / "shared-state/jobs" / f"{job.job_id}.json").read_bytes())
    assert record["destination"] == BLOB.as_text()
    assert record["volume_key"] == f"blob:{BLOB.account_url}"
    assert second.frozen_request(job.job_id) == b'{"request":1}'


def test_given_retained_blob_job_when_other_runtime_submits_then_account_capacity_stays_charged(tmp_path: Path) -> None:
    storage = LocalRecordStorage(tmp_path / "shared-state")
    settings = replace(SETTINGS, blob_reservation_limit_bytes=1000)
    clock = FakeClock()
    first = ReleaseJobStore(
        tmp_path / "runtime-a", settings=settings, clock=clock, storage=storage, free_bytes=_no_local_disk
    )
    job = first.accept(_blob_request(reserved=600))
    fence = first.claim(job.job_id, "worker")
    first.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    first.confirm_stopped(job.job_id, fence)
    first.settle_cancellation(job.job_id, fence, output_removed=False)
    clock.advance(31)
    second = ReleaseJobStore(
        tmp_path / "runtime-b", settings=settings, clock=clock, storage=storage, free_bytes=_no_local_disk
    )
    elsewhere = BlobLocation(BLOB.account_url, "another-container", "different-prefix")

    with pytest.raises(CapacityError):
        second.accept(_blob_request(key="second", reserved=401, destination=elsewhere, attempt=OTHER_ATTEMPT))
    accepted = second.accept(_blob_request(key="second", reserved=400, destination=elsewhere, attempt=OTHER_ATTEMPT))
    assert not accepted.replayed
    assert second.get(job.job_id, scope=SCOPE, principal=OWNER).capacity_state == "retained"


def test_given_blob_payload_admitted_when_local_scratch_is_full_then_scratch_refused_separately(tmp_path: Path) -> None:
    observed = []

    def full_disk(path: Path) -> int:
        observed.append(path)
        return 0

    store = ReleaseJobStore(tmp_path / "runtime", settings=SETTINGS, clock=FakeClock(), free_bytes=full_disk)
    job = store.accept(_blob_request())
    assert observed == []
    fence = store.claim(job.job_id, "worker")

    with pytest.raises(CapacityError, match="scratch"):
        store.reserve_scratch(job.job_id, fence, 1)
    assert len(observed) == 1
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).scratch_bytes == 0


def test_given_blob_reservation_over_account_limit_when_accepted_then_capacity_refused(tmp_path: Path) -> None:
    settings = replace(SETTINGS, blob_reservation_limit_bytes=1000)
    store = ReleaseJobStore(tmp_path / "ledger", settings=settings, clock=FakeClock(), free_bytes=_no_local_disk)

    with pytest.raises(CapacityError):
        store.accept(_blob_request(reserved=1001))


def test_given_blob_reservations_across_prefixes_when_accepted_then_account_limit_is_shared(tmp_path: Path) -> None:
    settings = replace(SETTINGS, blob_reservation_limit_bytes=1000)
    store = ReleaseJobStore(tmp_path / "ledger", settings=settings, clock=FakeClock(), free_bytes=_no_local_disk)
    store.accept(_blob_request(key="first", reserved=600))

    with pytest.raises(CapacityError):
        store.accept(
            _blob_request(
                key="second",
                reserved=500,
                destination=ACCOUNT_BLOB,
                attempt=PublicationAttempt("pkg-003", "attempt-3", source_dataset_id="sample"),
            )
        )
    accepted = store.accept(
        _blob_request(
            key="third",
            reserved=400,
            destination=ACCOUNT_BLOB,
            attempt=PublicationAttempt("pkg-004", "attempt-4", source_dataset_id="sample"),
        )
    )
    assert accepted.replayed is False


def test_given_blob_destination_when_binding_checked_with_local_route_then_refused(tmp_path: Path) -> None:
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS, clock=FakeClock(), free_bytes=_no_local_disk)
    job = store.accept(_blob_request())
    fence = store.claim(job.job_id, "worker")

    with pytest.raises(AuthorityError, match="binding"):
        store.validate_execution_binding(job.job_id, fence, BLOB, ATTEMPT, route="local")
    store.validate_execution_binding(job.job_id, fence, BLOB, ATTEMPT, route="blob")


def test_given_blob_job_when_local_reconciliation_requested_then_refused(tmp_path: Path) -> None:
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS, clock=FakeClock(), free_bytes=_no_local_disk)
    job = store.accept(_blob_request())
    fence = store.claim(job.job_id, "worker")

    with pytest.raises(AuthorityError):
        store.reconcile_local_outputs(job.job_id, fence)


def test_given_blob_prefix_changed_under_same_key_when_accepted_then_conflict(tmp_path: Path) -> None:
    store = ReleaseJobStore(tmp_path / "ledger", settings=SETTINGS, clock=FakeClock(), free_bytes=_no_local_disk)
    store.accept(_blob_request())

    with pytest.raises(JobConflictError, match="different request"):
        store.accept(_blob_request(destination=ACCOUNT_BLOB))


def test_given_equivalent_blob_spellings_when_constructed_then_identity_is_canonical() -> None:
    assert BlobLocation("https://example.blob.core.windows.net", "release", "datasets/") == BLOB
    assert BLOB.as_text() == "https://example.blob.core.windows.net/release/datasets"


@pytest.mark.parametrize(
    "account",
    [
        "http://example.blob.core.windows.net",
        "https://user@example.blob.core.windows.net",
        "https://example.blob.core.windows.net/?sig=1",
        "https://example.blob.core.windows.net\n",
    ],
)
def test_given_unsafe_blob_account_when_constructed_then_refused(account: str) -> None:
    with pytest.raises(ValueError):
        BlobLocation(account, "release", "datasets")


def test_given_blob_reservation_limit_in_environment_when_loaded_then_validated() -> None:
    settings = ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_BLOB_RESERVATION_LIMIT_BYTES": "4096"})
    assert settings.blob_reservation_limit_bytes == 4096
    assert ReleaseRuntimeSettings().blob_reservation_limit_bytes == 1024**4
    for value in ("0", str((1 << 42) + 1)):
        with pytest.raises(ReleaseJobError):
            ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_BLOB_RESERVATION_LIMIT_BYTES": value})


def test_given_pending_copy_when_effect_reports_pending_then_not_acknowledged(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    control = ReleaseJobControl(store, job_id, fence)

    with pytest.raises(AuthorityError, match="pending"), control.effect(ATTEMPT, "data/a.bin", 1, DIGEST):
        raise EffectPending("copy accepted with unknown outcome")

    with pytest.raises(AuthorityError, match="unresolved"):
        store.seal(job_id, fence, CONTROLS)
    with pytest.raises(AuthorityError, match="unresolved"):
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)


def test_given_stale_fence_when_finish_acknowledges_then_refused_and_unresolved(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    stale = store.claim(job_id, "worker-a")
    assert store.begin_effect(job_id, stale, ATTEMPT, "data/a.bin", 1, DIGEST) is False
    clock.advance(31)
    current = store.claim(job_id, "worker-b")

    with pytest.raises(AuthorityError, match="stale"):
        store.finish_effect(job_id, stale, "data/a.bin", ok=True)
    store.confirm_stopped(job_id, stale)
    with pytest.raises(AuthorityError, match="unresolved"):
        store.seal(job_id, current, CONTROLS)


def _uncertain_effect(store: ReleaseJobStore, job_id: str, fence: int) -> None:
    control = ReleaseJobControl(store, job_id, fence)
    with pytest.raises(OSError), control.effect(ATTEMPT, "data/a.bin", 1, DIGEST):
        raise OSError("write interrupted")


def test_given_uncertain_effect_when_reconciled_absent_then_redispatchable(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    _uncertain_effect(store, job_id, fence)

    outcome = store.reconcile_effect(job_id, fence, "data/a.bin", observed="absent", length=1, digest=DIGEST)

    assert outcome == "abandoned"
    assert store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST) is False


def test_given_uncertain_effect_when_reconciled_complete_then_acknowledged(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    _uncertain_effect(store, job_id, fence)

    outcome = store.reconcile_effect(job_id, fence, "data/a.bin", observed="complete", length=1, digest=DIGEST)

    assert outcome == "acknowledged"
    assert store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST) is True


def test_given_uncertain_effect_when_reconciled_pending_then_still_unresolved(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    _uncertain_effect(store, job_id, fence)

    with pytest.raises(AuthorityError, match="pending"):
        store.reconcile_effect(job_id, fence, "data/a.bin", observed="pending", length=1, digest=DIGEST)
    with pytest.raises(AuthorityError, match="unresolved"):
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)


def test_given_uncertain_effect_when_reconciled_conflict_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    _uncertain_effect(store, job_id, fence)

    with pytest.raises(JobConflictError, match="conflict"):
        store.reconcile_effect(job_id, fence, "data/a.bin", observed="conflict", length=1, digest=DIGEST)
    with pytest.raises(AuthorityError, match="unresolved"):
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)


def test_given_reconciliation_with_other_identity_or_observation_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    _uncertain_effect(store, job_id, fence)

    with pytest.raises(AuthorityError, match="identity"):
        store.reconcile_effect(job_id, fence, "data/a.bin", observed="complete", length=2, digest=DIGEST)
    with pytest.raises(ReleaseJobError, match="observation"):
        store.reconcile_effect(job_id, fence, "data/a.bin", observed="maybe", length=1, digest=DIGEST)


def test_given_stale_writer_when_seal_or_publish_then_refused(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    stale = store.claim(job_id, "worker-a")
    clock.advance(31)
    current = store.claim(job_id, "worker-b")

    with pytest.raises(AuthorityError, match="stale"):
        store.seal(job_id, stale, CONTROLS)
    with (
        pytest.raises(AuthorityError, match="stale"),
        ReleaseJobControl(store, job_id, stale).publication(ATTEMPT, COMMIT_DIGEST),
    ):
        pass
    with pytest.raises(AuthorityError, match="predecessor"):
        store.seal(job_id, current, CONTROLS)


class OffsetClock:
    """Wall clock with a test-controlled offset, so supervised children observe the same live lease."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


MARKER_CRASH_CODE = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "from physical_ai_curation.release.jobs import ReleaseJobStore, ReleaseRuntimeSettings\n"
    "from physical_ai_curation.release.models import MARKER_PATH\n"
    "ledger, job_id, fence, release_dir = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])\n"
    "jobs = ReleaseJobStore(ledger, settings=ReleaseRuntimeSettings(lease_seconds=30, reserve_bytes=0))\n"
    "name = '.curation-copy-' + os.urandom(16).hex() + '.partial'\n"
    "fd = os.open(release_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n"
    "jobs.register_local_temporary(job_id, fence, MARKER_PATH, name, os.fstat(fd))\n"
    "os.write(fd, b'{\"partial')\n"
    "os._exit(3)\n"
)


def _committed_package(tmp_path: Path, clock: OffsetClock) -> tuple[ReleaseJobStore, str, int, Path, Path, str]:
    """Stage every payload and receipt, then commit the publication without writing the marker."""
    source = tmp_path / "source"
    files = _payload_files()
    manifest_digest = _write_package(source, files, _manifest_for(files))
    objects = list(LocalReleaseStore(source).list_objects())
    total = sum(len((source / item.path).read_bytes()) for item in objects)
    destination = tmp_path / "destination"
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(destination, reserved=total + 1024 * 1024)).job_id
    fence = store.claim(job_id, "worker-a")
    control = ReleaseJobControl(store, job_id, fence)
    publisher = LocalPublisher(destination, ATTEMPT, control)
    for item in objects:
        content = (source / item.path).read_bytes()
        publisher.write(item.path, (content,), length=len(content), sha256=sha256_hex(content))
    candidate = verify_candidate(LocalReleaseStore(publisher.root), manifest_digest)
    control.seal(
        {
            RECEIPT_PATH: (len(candidate.receipt_bytes), sha256_hex(candidate.receipt_bytes)),
            CHECKSUMS_PATH: (len(candidate.checksums_bytes), sha256_hex(candidate.checksums_bytes)),
        }
    )
    for path, body in ((RECEIPT_PATH, candidate.receipt_bytes), (CHECKSUMS_PATH, candidate.checksums_bytes)):
        publisher.write(path, (body,), length=len(body), sha256=sha256_hex(body))
    commit = control.publication(ATTEMPT, manifest_digest)
    commit.__enter__()  # Leave the commit open, as if the writer crashed before the marker.
    return store, job_id, fence, destination, publisher.root, manifest_digest


def _marker_identity(root: Path, manifest_digest: str) -> tuple[bytes, int, str]:
    marker = build_marker(
        verify_candidate(LocalReleaseStore(root), manifest_digest),
        release_name=ATTEMPT.prefix,
        source_dataset_id=ATTEMPT.source_dataset_id,
    )
    return marker, len(marker), sha256_hex(marker)


def test_given_sealed_ledger_when_marker_requested_before_commit_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", OffsetClock())
    job_id, fence = _sealed_job(store, tmp_path / "dest")

    with pytest.raises(AuthorityError, match="committing"):
        store.begin_marker_effect(job_id, fence, ATTEMPT, DIGEST, 1, DIGEST)


def test_given_running_job_when_generic_effect_claims_marker_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", OffsetClock())
    job = store.accept(_request(tmp_path / "destination"))
    fence = store.claim(job.job_id, "worker")

    with pytest.raises(LedgerClosedError, match="marker"):
        store.begin_effect(job.job_id, fence, ATTEMPT, MARKER_PATH, 1, DIGEST)


def test_given_unacknowledged_marker_when_publication_returns_then_commit_stays_unresolved(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", OffsetClock())
    job_id, fence = _sealed_job(store, tmp_path / "destination")

    with pytest.raises(AuthorityError, match="unresolved"), store.publication(job_id, fence, ATTEMPT, COMMIT_DIGEST):
        store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 1, DIGEST)

    assert store.get(job_id, scope=SCOPE, principal=OWNER).state == "committing"


@pytest.mark.parametrize("lose_stop_ack", [False, True])
def test_given_marker_writer_interrupted_after_registered_temp_when_successor_publishes_then_residue_removed(
    tmp_path: Path,
    lose_stop_ack: bool,
) -> None:
    clock = OffsetClock()
    store, job_id, stale, destination, root, digest = _committed_package(tmp_path, clock)
    _, length, marker_digest = _marker_identity(root, digest)
    assert store.begin_marker_effect(job_id, stale, ATTEMPT, digest, length, marker_digest) is False

    with (
        patch.object(store, "confirm_runtime_stopped", side_effect=RuntimeError("controller stop acknowledgment lost"))
        if lose_stop_ack
        else nullcontext(),
        pytest.raises(RuntimeError, match="acknowledgment lost") if lose_stop_ack else nullcontext(),
    ):
        run_supervised(
            store,
            job_id,
            stale,
            [
                sys.executable,
                "-c",
                MARKER_CRASH_CODE,
                str(tmp_path / "ledger"),
                job_id,
                str(stale),
                str(root),
            ],
            memory_bytes=MEMORY,
            heartbeat_seconds=0.01,
            grace_seconds=FAST_GRACE,
            effect_path=MARKER_PATH,
        )
    if not lose_stop_ack:
        store.finish_effect(job_id, stale, MARKER_PATH, ok=False)
    assert len(list(root.glob(".curation-copy-*.partial"))) == 1
    assert not (root / MARKER_PATH).exists()

    clock.advance(31)
    successor = store.claim(job_id, "worker-b")
    result = SupervisedLocalPublisher(destination, ATTEMPT, store, job_id, successor).publish(digest)

    assert result.publication_state == "published"
    assert (root / MARKER_PATH).is_file()
    assert list(root.glob(".curation-copy-*.partial")) == []
    assert store.get(job_id, scope=SCOPE, principal=OWNER).state == "committed"


def test_given_marker_written_with_lost_response_when_successor_publishes_then_acknowledged_without_rewrite(
    tmp_path: Path,
) -> None:
    clock = OffsetClock()
    store, job_id, stale, destination, root, digest = _committed_package(tmp_path, clock)
    marker, length, marker_digest = _marker_identity(root, digest)
    assert store.begin_marker_effect(job_id, stale, ATTEMPT, digest, length, marker_digest) is False
    LocalReleaseStore(root).write_verified_object(MARKER_PATH, (marker,), length=length, sha256=marker_digest)
    inode = (root / MARKER_PATH).stat().st_ino

    clock.advance(31)
    successor = store.claim(job_id, "worker-b")
    store.confirm_stopped(job_id, stale)
    result = SupervisedLocalPublisher(destination, ATTEMPT, store, job_id, successor).publish(digest)

    assert result.publication_state == "published"
    assert (root / MARKER_PATH).stat().st_ino == inode
    assert store.get(job_id, scope=SCOPE, principal=OWNER).state == "committed"


def test_given_unregistered_residue_when_successor_publishes_then_refused_and_retained(tmp_path: Path) -> None:
    clock = OffsetClock()
    store, job_id, stale, destination, root, digest = _committed_package(tmp_path, clock)
    _, length, marker_digest = _marker_identity(root, digest)
    assert store.begin_marker_effect(job_id, stale, ATTEMPT, digest, length, marker_digest) is False
    residue = root / f".curation-copy-{'e' * 32}.partial"
    residue.write_bytes(b"foreign")

    clock.advance(31)
    successor = store.claim(job_id, "worker-b")
    store.confirm_stopped(job_id, stale)
    with pytest.raises(AuthorityError, match="unregistered"):
        SupervisedLocalPublisher(destination, ATTEMPT, store, job_id, successor).publish(digest)

    assert residue.read_bytes() == b"foreign"
    assert not (root / MARKER_PATH).exists()


def test_given_superseded_marker_writer_when_marker_or_recovery_then_refused(tmp_path: Path) -> None:
    clock = OffsetClock()
    store, job_id, stale, destination, root, digest = _committed_package(tmp_path, clock)
    _, length, marker_digest = _marker_identity(root, digest)

    clock.advance(31)
    store.claim(job_id, "worker-b")
    with pytest.raises(AuthorityError, match="stale"):
        store.begin_marker_effect(job_id, stale, ATTEMPT, digest, length, marker_digest)
    with pytest.raises(AuthorityError, match="stale"):
        SupervisedLocalPublisher(destination, ATTEMPT, store, job_id, stale).publish(digest)
    assert not (root / MARKER_PATH).exists()


def test_given_supervisor_when_started_then_limits_applied_before_exec() -> None:
    child = (
        "import resource\n"
        "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_FSIZE)[0], flush=True)\n"
    )
    with ProcessSupervisor([sys.executable, "-c", child], memory_bytes=MEMORY, file_bytes=FILE_LIMIT) as supervisor:
        reported = supervisor.output.readline().split()

    assert [int(value) for value in reported] == [MEMORY, FILE_LIMIT]


def test_given_stopped_supervisor_when_stopped_again_then_no_signal_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor_args = [sys.executable, "-c", CHILD_SLEEP]
    with ProcessSupervisor(supervisor_args, memory_bytes=MEMORY, grace_seconds=FAST_GRACE) as supervisor:
        first = supervisor.stop()

        def forbid_signal(*_args: object) -> None:
            pytest.fail("reaped supervision must not signal again")

        monkeypatch.setattr(os, "killpg", forbid_signal)
        monkeypatch.setattr(os, "kill", forbid_signal)
        monkeypatch.setattr(signal, "pidfd_send_signal", forbid_signal)
        assert supervisor.stop() == first


def test_given_file_bound_when_child_writes_past_it_then_child_fails(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    child = "import sys\nwith open(sys.argv[1], 'wb') as handle:\n    handle.write(b'x' * (4 * 1024 * 1024))\n"
    with ProcessSupervisor(
        [sys.executable, "-c", child, str(target)], memory_bytes=MEMORY, file_bytes=FILE_LIMIT
    ) as supervisor:
        assert supervisor.wait_for_exit(60)
        proof = supervisor.stop()

    assert proof.leader_exit_code != 0
    assert target.stat().st_size <= FILE_LIMIT


def test_given_exited_leader_with_survivor_when_stopped_then_survivor_reaped(tmp_path: Path) -> None:
    with ProcessSupervisor(
        [sys.executable, "-c", LEADER_CODE, SURVIVOR_CODE], memory_bytes=MEMORY, grace_seconds=FAST_GRACE
    ) as supervisor:
        survivor = ProcessIdentity.of(int(supervisor.output.readline()))
        assert supervisor.wait_for_exit(60) is True
        proof = supervisor.stop()

    assert proof.leader_exit_code == 0
    assert proof.members_reaped >= 1
    assert _reaped(survivor)


def test_given_cancel_while_running_when_supervised_then_tree_stopped_and_recorded(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    read_fd, write_fd = os.pipe()
    outcomes: list[SupervisedOutcome] = []
    argv = [sys.executable, "-c", TREE_CODE, str(write_fd), SURVIVOR_CODE]
    runner = threading.Thread(
        target=lambda: outcomes.append(
            run_supervised(
                store,
                job_id,
                fence,
                argv,
                memory_bytes=MEMORY,
                heartbeat_seconds=0.01,
                grace_seconds=FAST_GRACE,
                inherit_fds=(write_fd,),
            )
        )
    )
    runner.start()
    try:
        grandchild = ProcessIdentity.of(_read_int_line(read_fd))
    finally:
        os.close(write_fd)

    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    runner.join(timeout=60)
    os.close(read_fd)

    assert not runner.is_alive()
    assert outcomes[0].reason == "cancelled"
    assert _reaped(grandchild)


def test_given_cancel_requested_before_dispatch_when_supervised_then_child_never_started(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    marker = tmp_path / "started"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"

    with pytest.raises(CancellationError):
        run_supervised(
            store,
            job_id,
            fence,
            [sys.executable, "-c", code],
            memory_bytes=MEMORY,
            heartbeat_seconds=5,
            grace_seconds=FAST_GRACE,
        )

    assert not marker.exists()
    assert store.get(job_id, scope=SCOPE, principal=OWNER).effects_stopped is True


def test_given_unresolved_runtime_when_cancel_precedes_dispatch_then_stop_not_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    identity = ProcessIdentity.of(os.getpid())
    proof = store.runtime_proof_path(job_id, fence)
    store.register_runtime(job_id, fence, SupervisedRuntime(identity, identity, proof))
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    marker = tmp_path / "started"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"

    with pytest.raises(AuthorityError, match="runtime"):
        run_supervised(
            store,
            job_id,
            fence,
            [sys.executable, "-c", code],
            memory_bytes=MEMORY,
            heartbeat_seconds=5,
            grace_seconds=FAST_GRACE,
        )

    assert not marker.exists()
    assert store.get(job_id, scope=SCOPE, principal=OWNER).effects_stopped is not True


def test_given_lease_lost_while_running_when_supervised_then_tree_stopped_and_successor_proceeds(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker-a")
    read_fd, write_fd = os.pipe()
    outcomes: list[SupervisedOutcome] = []
    argv = [sys.executable, "-c", TREE_CODE, str(write_fd), SURVIVOR_CODE]
    runner = threading.Thread(
        target=lambda: outcomes.append(
            run_supervised(
                store,
                job_id,
                fence,
                argv,
                memory_bytes=MEMORY,
                heartbeat_seconds=0.01,
                grace_seconds=FAST_GRACE,
                inherit_fds=(write_fd,),
            )
        )
    )
    runner.start()
    try:
        grandchild = ProcessIdentity.of(_read_int_line(read_fd))
    finally:
        os.close(write_fd)

    clock.advance(31)
    runner.join(timeout=60)
    os.close(read_fd)

    assert not runner.is_alive()
    assert outcomes[0].reason == "lease_lost"
    assert _reaped(grandchild)
    with pytest.raises(AuthorityError):
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)
    successor = store.claim(job_id, "worker-b")
    assert store.begin_effect(job_id, successor, ATTEMPT, "data/a.bin", 1, DIGEST) is False


def test_given_cancelled_job_when_cleanup_then_only_recorded_outputs_removed(tmp_path: Path) -> None:
    payload = b"owned-bytes"
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", payload)
    (root / "data").mkdir(parents=True)
    (root / "data" / "a.bin").write_bytes(payload)
    foreign = root / "data" / "foreign.bin"
    foreign.write_bytes(b"other")

    removed = store.cleanup_owned_outputs(job_id, fence, root)
    status = store.settle_cancellation(job_id, fence, output_removed=removed)

    assert removed is False
    assert not (root / "data" / "a.bin").exists()
    assert foreign.read_bytes() == b"other"
    assert status.cleanup_state == "retained"
    assert status.capacity_state == "retained"


@pytest.mark.parametrize("damage", ["foreign_companion", "companion_directory", "companion_symlink", "root_symlink"])
def test_given_cancelled_release_when_cleanup_identity_is_untrusted_then_owned_and_foreign_bytes_survive(
    tmp_path: Path, damage: str
) -> None:
    payload = b"owned-bytes"
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", payload)
    (root / "data").mkdir(parents=True)
    (root / "data/a.bin").write_bytes(payload)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"foreign-bytes")
    companion = root.parent / ownership_path(ATTEMPT.prefix)
    companion.parent.mkdir(parents=True, exist_ok=True)
    if damage == "foreign_companion":
        companion.write_bytes(b"foreign ownership")
    elif damage == "companion_directory":
        companion.mkdir()
    elif damage == "companion_symlink":
        companion.symlink_to(outside)
    else:
        retained_root = root.with_name("retained-root")
        root.rename(retained_root)
        root.symlink_to(retained_root, target_is_directory=True)
    before = store.storage.snapshot()

    assert not store.cleanup_owned_outputs(job_id, fence, root)

    assert store.storage.snapshot() == before
    assert (root / "data/a.bin").read_bytes() == payload
    assert outside.read_bytes() == b"foreign-bytes"
    if damage == "foreign_companion":
        assert companion.read_bytes() == b"foreign ownership"
    elif damage == "companion_directory":
        assert companion.is_dir()
    else:
        assert (companion if damage == "companion_symlink" else root).is_symlink()


@pytest.mark.parametrize(
    "damage", ["uncancelled", "terminal", "foreign_route", "stale_fence", "unstopped", "open_effect"]
)
def test_given_cancelled_release_when_cleanup_authority_is_lost_then_no_files_or_journal_rows_change(
    tmp_path: Path, damage: str
) -> None:
    payload = b"owned-bytes"
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", payload)
    (root / "data").mkdir(parents=True)
    (root / "data/a.bin").write_bytes(payload)
    with store._transaction() as records:
        row = records[job_id]
        if damage == "uncancelled":
            row["cancel_requested"] = False
        elif damage == "terminal":
            row["state"] = "cancelled"
        elif damage == "foreign_route":
            row["route"] = "blob"
        elif damage == "unstopped":
            row["stopped_fence"] = fence - 1
        elif damage == "open_effect":
            row["effects"]["data/a.bin"]["status"] = "uncertain"
    before = store.storage.snapshot()

    with pytest.raises(CancellationError if damage == "uncancelled" else AuthorityError):
        store.cleanup_owned_outputs(job_id, fence + 1 if damage == "stale_fence" else fence, root)

    assert store.storage.snapshot() == before
    assert (root / "data/a.bin").read_bytes() == payload


@pytest.mark.parametrize("damage", ["digest", "foreign_attempt", "running", "controls", "chosen_digest"])
def test_given_release_publication_when_commit_admission_is_invalid_then_decision_is_not_changed(
    tmp_path: Path, damage: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    if damage == "chosen_digest":
        store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    elif damage in {"running", "controls"}:
        with store._transaction() as records:
            if damage == "running":
                records[job_id]["state"] = "running"
            else:
                del records[job_id]["effects"][RECEIPT_PATH]
    before = store.storage.snapshot()

    with (
        pytest.raises(ReleaseJobError),
        store.publication(
            job_id,
            fence,
            OTHER_ATTEMPT if damage == "foreign_attempt" else ATTEMPT,
            "invalid" if damage == "digest" else DIGEST if damage == "chosen_digest" else COMMIT_DIGEST,
        ),
    ):
        pytest.fail("Invalid publication must not receive commit authority")

    assert store.storage.snapshot() == before


def test_given_changed_recorded_output_when_cleanup_then_retained(tmp_path: Path) -> None:
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", b"expected")
    (root / "data").mkdir(parents=True)
    (root / "data" / "a.bin").write_bytes(b"changed!")

    removed = store.cleanup_owned_outputs(job_id, fence, root)

    assert removed is False
    assert (root / "data" / "a.bin").read_bytes() == b"changed!"


def test_given_other_root_when_cleanup_then_identical_foreign_bytes_untouched(tmp_path: Path) -> None:
    payload = b"same bytes do not establish ownership"
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", payload)
    foreign = tmp_path / "foreign"
    (foreign / "data").mkdir(parents=True)
    (foreign / "data/a.bin").write_bytes(payload)

    with pytest.raises(AuthorityError, match="destination"):
        store.cleanup_owned_outputs(job_id, fence, foreign)
    assert (foreign / "data/a.bin").read_bytes() == payload
    assert not root.exists()


def test_given_published_root_when_cleanup_then_nothing_removed(tmp_path: Path) -> None:
    payload = b"owned-bytes"
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", payload)
    (root / "data").mkdir(parents=True)
    (root / "data" / "a.bin").write_bytes(payload)
    (root / MARKER_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MARKER_PATH).write_bytes(b"{}")

    removed = store.cleanup_owned_outputs(job_id, fence, root)

    assert removed is False
    assert (root / "data" / "a.bin").exists()


def test_given_symlinked_owned_path_when_cleanup_then_target_outside_root_untouched(tmp_path: Path) -> None:
    payload = b"outside-bytes"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(payload)
    store, job_id, fence, root = _owned_effect_job(tmp_path, FakeClock(), "data/a.bin", payload)
    (root / "data").mkdir(parents=True)
    (root / "data" / "a.bin").symlink_to(outside)

    removed = store.cleanup_owned_outputs(job_id, fence, root)

    assert removed is False
    assert outside.read_bytes() == payload


def test_given_many_events_when_exported_in_bounded_pages_then_ordered_and_complete(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    for index in range(5):
        store.accept(
            _request(
                tmp_path / f"dest-{index}",
                key=f"key-{index}",
                principal=f"user-{index}",
                attempt=PublicationAttempt(f"pkg-{index:03d}", f"attempt-{index}", source_dataset_id="sample"),
            )
        )

    assert store.flush_events(max_events=2) == 2
    assert store.flush_events(max_events=2) == 2
    assert store.flush_events(max_events=2) == 1
    assert store.flush_events(max_events=2) == 0
    records = [json.loads(line) for line in (tmp_path / "ledger" / EVENTS_NAME).read_text().splitlines()]
    assert [record["seq"] for record in records] == [1, 2, 3, 4, 5]
    with pytest.raises(ReleaseJobError):
        store.flush_events(max_events=0)


def test_given_forged_event_cursor_when_export_recovered_then_authoritative_events_not_skipped(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "dest"))
    store.events_path.write_bytes(b'{"seq":999999,"event_id":"untrusted"}\n')

    with pytest.raises(ReleaseJobError, match="corrupt"):
        store.flush_events()


def test_given_registered_runtime_when_reopened_then_stop_requires_guardian_proof(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "dest")).job_id
    fence = store.claim(job_id, "worker")
    with ProcessSupervisor(
        [sys.executable, "-c", CHILD_SLEEP],
        memory_bytes=MEMORY,
        grace_seconds=FAST_GRACE,
        proof_path=store.runtime_proof_path(job_id, fence),
        on_started=lambda runtime: store.register_runtime(job_id, fence, runtime),
    ) as supervisor:
        reopened = _store(tmp_path / "ledger", clock)
        with pytest.raises(AuthorityError, match="runtime"):
            reopened.confirm_stopped(job_id, fence)
        with pytest.raises(AuthorityError, match="runtime"):
            reopened.seal(job_id, fence, CONTROLS)
        supervisor.stop()

    reopened.recover_runtimes(job_id, fence, timeout_seconds=0)
    reopened.confirm_stopped(job_id, fence)
    assert reopened.get(job_id, scope=SCOPE, principal=OWNER).effects_stopped is True


@pytest.mark.parametrize("processing_memory", [512 * 1024**2, 3 * 1024**3])
def test_given_planned_encoding_when_executed_then_native_result_has_stop_proof(
    tmp_path: Path, processing_memory: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np
    from physical_ai_curation.release.composition import NativeGenerationPlan, PreparedMediaWindow
    from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow
    from physical_ai_curation.release.execution import encode_planned_object
    from physical_ai_curation.release.media import ReleaseMediaLimits, SubsetEpisodeInput, plan_release_objects
    from physical_ai_curation.release.models import SourceObject
    from test_release_media import LOCAL_SCRATCH_SAME, VIDEO, _encode_source, _request, _window

    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "1")
    content = _encode_source([np.full((24, 32, 3), value, dtype=np.uint8) for value in (30, 80)])
    source = SourceObject(path=VIDEO, revision="frozen", length=len(content), sha256=sha256_hex(content))
    limits = ReleaseMediaLimits(processing_memory_bytes=processing_memory, reserve_bytes=0)
    plan = plan_release_objects(
        _request(
            selected_episode_ids=("ep-0",),
            windows=(_window(source=source, end=0.2, duration=0.2, reasons=("invert",)),),
            scratch=LOCAL_SCRATCH_SAME,
            limits=limits,
        )
    )
    window = EncodingWindow("ep-0", 0, 2, (0, 1), "invert")
    prepared = NativeGenerationPlan(
        plan,
        (SubsetEpisodeInput("ep-0", 0, 2, 10),),
        (PreparedMediaWindow(VIDEO, "front", window),),
        EncodeOptions(),
    )
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request_for_encoding(tmp_path)).job_id
    fence = store.claim(job_id, "worker")
    with encode_planned_object(
        store,
        job_id,
        fence,
        prepared,
        plan.objects[0],
        (content,),
        confirmed_digest=plan.digest,
        scratch_bytes=8 * 1024**2,
        output_bytes=1024**2,
        limits=limits,
    ) as encoded:
        assert encoded.media.windows[0].output_to_frame == 2
        assert encoded.length == encoded.path.stat().st_size
        assert sha256_hex(encoded.path.read_bytes()) == encoded.sha256
        assert store.get(job_id, scope=SCOPE, principal=OWNER).effects_stopped
    assert not encoded.path.exists()


def test_given_checkpoint_raises_during_source_spool_when_encoding_then_no_native_child_started(
    tmp_path: Path,
) -> None:
    import numpy as np
    from physical_ai_curation.release.composition import NativeGenerationPlan, PreparedMediaWindow
    from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow
    from physical_ai_curation.release.execution import encode_planned_object
    from physical_ai_curation.release.media import SubsetEpisodeInput, plan_release_objects
    from physical_ai_curation.release.models import SourceObject
    from test_release_media import LOCAL_SCRATCH_SAME, VIDEO, _encode_source, _request, _window

    content = _encode_source([np.full((24, 32, 3), value, dtype=np.uint8) for value in (30, 80)])
    source = SourceObject(path=VIDEO, revision="frozen", length=len(content), sha256=sha256_hex(content))
    plan = plan_release_objects(
        _request(
            selected_episode_ids=("ep-0",),
            windows=(_window(source=source, end=0.2, duration=0.2, reasons=("invert",)),),
            scratch=LOCAL_SCRATCH_SAME,
        )
    )
    window = EncodingWindow("ep-0", 0, 2, (0, 1), "invert")
    prepared = NativeGenerationPlan(
        plan,
        (SubsetEpisodeInput("ep-0", 0, 2, 10),),
        (PreparedMediaWindow(VIDEO, "front", window),),
        EncodeOptions(),
    )
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request_for_encoding(tmp_path)).job_id
    fence = store.claim(job_id, "worker")
    chunks = (content[index : index + 4096] for index in range(0, len(content), 4096))

    def checkpoint() -> None:
        raise CancellationError("cancellation requested")

    with (
        patch("physical_ai_curation.release.execution.run_supervised") as supervised,
        pytest.raises(CancellationError),
        encode_planned_object(
            store,
            job_id,
            fence,
            prepared,
            plan.objects[0],
            chunks,
            confirmed_digest=plan.digest,
            scratch_bytes=8 * 1024**2,
            output_bytes=1024**2,
            checkpoint=checkpoint,
        ),
    ):
        pass
    supervised.assert_not_called()


def _request_for_encoding(tmp_path: Path) -> JobRequest:
    return JobRequest(SCOPE, OWNER, "encode", ATTEMPT, tmp_path / "dest", b"frozen", 16 * 1024**2)


def test_given_bounded_native_sink_when_growth_exceeds_limit_then_no_extra_bytes(tmp_path: Path) -> None:
    from physical_ai_curation.release.execution import BoundedSink

    path = tmp_path / "output"
    with path.open("w+b") as stream:
        sink = BoundedSink(stream, 3)
        assert sink.write(b"abc") == 3
        with pytest.raises(CapacityError):
            sink.write(b"d")
        assert path.stat().st_size <= 3


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_given_invalid_native_sink_bound_when_created_then_refused(tmp_path: Path, limit: Any) -> None:
    from physical_ai_curation.release.execution import BoundedSink

    with (tmp_path / "output").open("w+b") as stream, pytest.raises(CapacityError):
        BoundedSink(stream, limit)


@pytest.mark.parametrize("damage", ["version", "mode", "bound"])
def test_given_invalid_local_child_protocol_when_read_then_descriptor_closes_before_ledger_access(
    mocker: MockerFixture, damage: str
) -> None:
    from physical_ai_curation.release import local_worker

    spec = {"version": 2 if damage == "version" else 1, "mode": "unsupported"}
    if damage == "bound":
        mocker.patch.object(local_worker, "MAX_PROTOCOL_BYTES", 1)
    store = mocker.patch.object(local_worker, "ReleaseJobStore")
    content = canonical_json_bytes(spec)
    descriptor = os.memfd_create("test-local-child", os.MFD_CLOEXEC)
    try:
        os.write(descriptor, content)
        os.lseek(descriptor, 0, os.SEEK_SET)
        child_descriptor = os.dup(descriptor)
        with pytest.raises(ReleaseVerificationError, match=r"protocol|mode"):
            local_worker._process(child_descriptor)
        with pytest.raises(OSError):
            os.fstat(child_descriptor)
        assert os.fstat(descriptor).st_size == len(content)
    finally:
        os.close(descriptor)

    store.assert_not_called()


@pytest.mark.parametrize("worker", ["local_worker", "execution"])
@pytest.mark.parametrize("outcome", ["invalid", "failure", "success"])
def test_given_private_worker_entry_when_invoked_then_fixed_exit_code_is_returned(
    mocker: MockerFixture, worker: str, outcome: str
) -> None:
    from physical_ai_curation.release import execution, local_worker

    module = local_worker if worker == "local_worker" else execution
    operation = mocker.patch.object(module, "_process" if worker == "local_worker" else "_encode")
    if outcome == "failure":
        operation.side_effect = OSError("fixed test failure")
    arguments = [] if outcome == "invalid" else ["12"] if worker == "local_worker" else ["encode", "request.json"]
    mocker.patch.object(sys, "argv", ["worker", *arguments])

    assert module.main() == {"invalid": 2, "failure": 1, "success": 0}[outcome]
    assert operation.call_count == (0 if outcome == "invalid" else 1)


def test_given_native_sink_when_seek_exceeds_bound_then_position_and_bytes_are_preserved(tmp_path: Path) -> None:
    from physical_ai_curation.release.execution import BoundedSink

    with (tmp_path / "output").open("w+b") as stream:
        sink = BoundedSink(stream, 3)
        sink.write(b"abc")
        sink.seek(1)
        with pytest.raises(CapacityError, match="seek"):
            sink.seek(4)
        assert sink.tell() == 1
        sink.flush()
        assert sink.writable() and sink.seekable() and not sink.closed
        assert (tmp_path / "output").read_bytes() == b"abc"
    assert sink.closed


@pytest.mark.parametrize(
    "damage",
    [
        "source_short",
        "source_long",
        "source_digest",
        "source_chunk",
        "request_bound",
        "child_failure",
        "result_bound",
        "output_bound",
        "scratch_bound",
        "success",
    ],
)
def test_given_native_encoding_boundary_when_child_or_content_invalid_then_scratch_is_released(
    tmp_path: Path, mocker: MockerFixture, damage: str
) -> None:
    from physical_ai_curation.release import execution
    from physical_ai_curation.release.composition import NativeGenerationPlan, PreparedMediaWindow
    from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow
    from physical_ai_curation.release.media import ReleaseExecutionError, SubsetEpisodeInput, plan_release_objects
    from test_release_media import LOCAL_SCRATCH_SAME, VIDEO, _window
    from test_release_media import _request as media_request

    content = b"frozen native source"
    source = SourceObject(path=VIDEO, revision="frozen", length=len(content), sha256=sha256_hex(content))
    plan = plan_release_objects(
        media_request(
            selected_episode_ids=("ep-0",),
            windows=(_window(source=source, end=0.2, duration=0.2, reasons=("invert",)),),
            scratch=LOCAL_SCRATCH_SAME,
        )
    )
    prepared = NativeGenerationPlan(
        plan,
        (SubsetEpisodeInput("ep-0", 0, 2, 10),),
        (PreparedMediaWindow(VIDEO, "front", EncodingWindow("ep-0", 0, 2, (0, 1), "invert")),),
        EncodeOptions(),
    )
    directory = tmp_path / "scratch"
    directory.mkdir()
    store = mocker.Mock(settings=SETTINGS)
    store.create_scratch_workspace.return_value = directory
    output_bound = 1024**2
    payload = b"encoded bytes"

    def child(*args: Any, **kwargs: Any) -> SupervisedOutcome:
        output = payload if damage != "output_bound" else b"x" * (output_bound + 1)
        (directory / "encoded.mp4").write_bytes(output)
        result = {
            "media": {
                "windows": [],
                "codec": "libx264",
                "pix_fmt": "yuv420p",
                "fps": 10,
                "width": 32,
                "height": 24,
                "statistic_algorithm": "test",
                "sampling_recipe": "test",
            },
            "peak_output_bytes": 16 * 1024**2 if damage == "scratch_bound" else len(output),
        }
        (directory / "result.json").write_bytes(canonical_json_bytes(result))
        return SimpleNamespace(reason="exited", returncode=1 if damage == "child_failure" else 0)

    native = mocker.patch.object(execution, "run_supervised", side_effect=child)
    if damage in {"request_bound", "result_bound", "source_chunk"}:
        mocker.patch.object(
            execution,
            {"request_bound": "MAX_SPEC_BYTES", "result_bound": "MAX_RESULT_BYTES", "source_chunk": "CHUNK_BYTES"}[
                damage
            ],
            1,
        )
    chunks = {"source_short": content[:-1], "source_long": content + b"x", "source_digest": b"x" * len(content)}.get(
        damage, content
    )
    operation = execution.encode_planned_object(
        store,
        "a" * 32,
        1,
        prepared,
        plan.objects[0],
        (chunks,),
        confirmed_digest=plan.digest,
        scratch_bytes=8 * 1024**2,
        output_bytes=output_bound,
    )

    if damage == "success":
        with operation as encoded:
            assert encoded.path.read_bytes() == payload
            assert encoded.sha256 == sha256_hex(payload)
    else:
        with pytest.raises((CapacityError, ReleaseExecutionError)), operation:
            pass
    store.cleanup_scratch_workspace.assert_called_once_with("a" * 32, 1)
    store.release_scratch.assert_called_once_with("a" * 32, 1)
    if damage.startswith("source_") or damage == "request_bound":
        native.assert_not_called()


def test_given_retained_native_scratch_when_seal_or_cancel_cleanup_then_capacity_stays_charged(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job = store.accept(_request(tmp_path / "destination", reserved=8192))
    fence = store.claim(job.job_id, "worker")
    store.reserve_scratch(job.job_id, fence, 4096)
    with pytest.raises(AuthorityError, match="scratch"):
        store.seal(job.job_id, fence, CONTROLS)
    with pytest.raises(CapacityError):
        store.begin_effect(job.job_id, fence, ATTEMPT, "large", 8192, DIGEST)
    store.confirm_stopped(job.job_id, fence)
    store.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    with pytest.raises(AuthorityError, match="scratch"):
        store.settle_cancellation(job.job_id, fence, output_removed=True)
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).capacity_state == "reserved"


def test_given_foreign_destination_when_execution_binding_checked_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job = store.accept(_request(tmp_path / "destination"))
    fence = store.claim(job.job_id, "worker")
    with pytest.raises(AuthorityError, match="binding"):
        store.validate_execution_binding(job.job_id, fence, tmp_path / "foreign", ATTEMPT, route="local")


def test_given_reopened_jobs_when_paged_then_order_and_principal_confinement_persist(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    first = store.accept(_request(tmp_path / "destination"))
    second = store.accept(_request(tmp_path / "destination", key="second", attempt=OTHER_ATTEMPT, frozen=b"second"))
    store.accept(
        _request(
            tmp_path / "destination",
            key="foreign",
            principal="other",
            attempt=PublicationAttempt("third", "one", source_dataset_id="sample"),
            frozen=b"third",
        )
    )
    reopened = _store(store.root, clock)
    page = reopened.page(scope=SCOPE, principal=OWNER, limit=1)
    assert [item.job_id for item in page.items] == [first.job_id]
    assert page.next_cursor is not None
    last = reopened.page(scope=SCOPE, principal=OWNER, cursor=page.next_cursor, limit=1)
    assert [item.job_id for item in last.items] == [second.job_id]
    assert last.next_cursor is None
    assert reopened.page(scope="other-scope", principal=OWNER).items == ()
    with pytest.raises(ReleaseJobError):
        reopened.page(scope=SCOPE, principal=OWNER, limit=101)


def test_given_distinct_real_volumes_when_scratch_reserved_then_other_jobs_cannot_spend_it(tmp_path: Path) -> None:
    if not Path("/dev/shm").is_dir() or Path("/dev/shm").stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("independent test filesystem unavailable")
    with TemporaryDirectory(prefix="curated-release-", dir="/dev/shm") as state:
        clock = FakeClock()
        store = _store(Path(state), clock, free=8192)
        first = store.accept(_request(tmp_path / "destination", reserved=1024))
        fence = store.claim(first.job_id, "worker")
        store.reserve_scratch(first.job_id, fence, 7000)
        store.begin_effect(first.job_id, fence, ATTEMPT, "payload", 1024, DIGEST)
        store.finish_effect(first.job_id, fence, "payload", ok=True)
        reopened = _store(Path(state), clock, free=8192)
        with pytest.raises(CapacityError):
            reopened.accept(
                _request(Path(state) / "other-destination", key="other", attempt=OTHER_ATTEMPT, reserved=2048)
            )
        clock.advance(31)
        with pytest.raises(CapacityError):
            reopened.accept(
                _request(Path(state) / "other-destination", key="other", attempt=OTHER_ATTEMPT, reserved=2048)
            )
        assert reopened.get(first.job_id, scope=SCOPE, principal=OWNER).scratch_bytes == 7000


def test_given_unresolved_predecessor_when_replacement_dispatches_other_path_then_refused(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job = store.accept(_request(tmp_path / "destination"))
    previous = store.claim(job.job_id, "old")
    store.begin_effect(job.job_id, previous, ATTEMPT, "first", 10, DIGEST)
    store.confirm_stopped(job.job_id, previous)
    clock.advance(31)
    current = store.claim(job.job_id, "replacement")
    with pytest.raises(AuthorityError, match="unresolved"):
        store.begin_effect(job.job_id, current, ATTEMPT, "different", 10, DIGEST)
    store.reconcile_effect(job.job_id, current, "first", observed="complete", length=10, digest=DIGEST)
    assert store.begin_effect(job.job_id, current, ATTEMPT, "different", 10, DIGEST) is False


def test_given_runtime_environment_when_loaded_then_timeouts_are_validated() -> None:
    settings = ReleaseRuntimeSettings.from_environment(
        {
            "CURATION_RELEASE_GRACE_SECONDS": "2",
            "CURATION_RELEASE_GUARDIAN_SECONDS": "40",
            "CURATION_RELEASE_STATE_TIMEOUT_SECONDS": "10",
            "CURATION_RELEASE_RECOVERY_SECONDS": "8",
        }
    )
    assert (
        settings.grace_seconds,
        settings.guardian_seconds,
        settings.state_timeout_seconds,
        settings.recovery_seconds,
    ) == (2, 40, 10, 8)
    with pytest.raises(ReleaseJobError):
        ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_GUARDIAN_SECONDS": "1"})
    with pytest.raises(ReleaseJobError):
        ReleaseRuntimeSettings.from_environment({"CURATION_RELEASE_STATE_TIMEOUT_SECONDS": "0"})


def test_given_effect_intent_when_runtime_borrows_another_path_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    store.begin_effect(job.job_id, fence, ATTEMPT, "owned", 10, DIGEST)
    with pytest.raises(AuthorityError):
        store.runtime_proof_path(job.job_id, fence, effect_path="different")
    assert store.runtime_proof_path(job.job_id, fence, effect_path="owned").parent.is_dir()


def test_given_local_payload_when_written_then_registered_reaped_and_replay_stable(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    request = _request(tmp_path)
    job = store.accept(request)
    fence = store.claim(job.job_id, "worker")
    publisher = SupervisedLocalPublisher(request.destination, ATTEMPT, store, job.job_id, fence)
    payload = b"independent payload"
    digest = sha256_hex(payload)
    publisher.write("payload.bin", (payload,), length=len(payload), sha256=digest)
    output = publisher.root / "payload.bin"
    inode = output.stat().st_ino
    assert output.read_bytes() == payload
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).effects_stopped
    publisher.write("payload.bin", (payload,), length=len(payload), sha256=digest)
    assert output.stat().st_ino == inode


def test_given_local_payload_path_when_control_artifact_then_refused_before_dispatch(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    request = _request(tmp_path)
    job = store.accept(request)
    fence = store.claim(job.job_id, "worker")
    publisher = SupervisedLocalPublisher(request.destination, ATTEMPT, store, job.job_id, fence)
    with pytest.raises(ReleaseVerificationError):
        publisher.write_from_path(MARKER_PATH, tmp_path / "absent", length=1, sha256=DIGEST)
    assert not publisher.root.exists()


def test_given_live_scratch_creator_when_lease_replaced_then_recovery_retains_charge(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job = store.accept(_request(tmp_path))
    stale = store.claim(job.job_id, "old")
    workspace = store.create_scratch_workspace(job.job_id, stale, 256)
    (workspace / "source.bin").write_bytes(b"owned")
    clock.advance(31)
    current = store.claim(job.job_id, "replacement")
    with pytest.raises(AuthorityError):
        store.cleanup_scratch_workspace(job.job_id, current, recover=True)
    with pytest.raises(AuthorityError):
        store.release_scratch(job.job_id, current)
    assert (workspace / "source.bin").read_bytes() == b"owned"
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).scratch_bytes == 256


def test_given_unjournaled_output_residue_when_cancelled_then_capacity_remains_retained(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    request = _request(tmp_path)
    job = store.accept(request)
    fence = store.claim(job.job_id, "worker")
    publisher = LocalPublisher(request.destination, ATTEMPT, ReleaseJobControl(store, job.job_id, fence))
    publisher.write("owned", (b"owned",), length=5, sha256=sha256_hex(b"owned"))
    residue = publisher.root / ".release-partial-unknown"
    residue.write_bytes(b"residue")
    store.request_cancel(job.job_id, scope=SCOPE, principal=OWNER)
    store.confirm_stopped(job.job_id, fence)
    assert not store.cleanup_owned_outputs(job.job_id, fence, publisher.root)
    status = store.settle_cancellation(job.job_id, fence, output_removed=False)
    assert status.capacity_state == "retained"
    assert residue.read_bytes() == b"residue"


def test_given_completed_io_observation_when_replayed_then_counters_are_once_and_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    operation = "a" * 32
    store.record_io(job.job_id, fence, operation, {"storage_copy_bytes": 64}, duration_ms=2)
    store.record_io(job.job_id, fence, operation, {"storage_copy_bytes": 64}, duration_ms=2)
    reopened = _store(tmp_path, FakeClock())
    assert reopened.get(job.job_id, scope=SCOPE, principal=OWNER).io_counters == {"storage_copy_bytes": 64}
    with pytest.raises(JobConflictError):
        store.record_io(job.job_id, fence, operation, {"storage_copy_bytes": 65}, duration_ms=2)
    with pytest.raises(ReleaseJobError):
        store.record_io(job.job_id, fence, "b" * 32, {"private_path": 64})
    with pytest.raises(JobNotFoundError):
        reopened.get(job.job_id, scope=SCOPE, principal="different")


def test_given_scratch_observation_when_status_read_then_actual_peak_not_reserved_estimate(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    store.reserve_scratch(job.job_id, fence, 256)
    store.record_scratch_usage(job.job_id, fence, 64)
    store.release_scratch(job.job_id, fence)
    status = store.get(job.job_id, scope=SCOPE, principal=OWNER)
    assert status.scratch_bytes == status.observed_scratch_bytes == 0
    assert status.peak_scratch_bytes == 64


def test_given_diagnostic_when_replayed_then_counted_once_and_survives_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    operation = "c" * 32
    store.record_diagnostic(job.job_id, fence, operation, "encode", "succeeded", duration_ms=7)
    store.record_diagnostic(job.job_id, fence, operation, "encode", "succeeded", duration_ms=7)
    reopened = _store(tmp_path, FakeClock())
    assert reopened.get(job.job_id, scope=SCOPE, principal=OWNER).diagnostics == {
        "encode.succeeded": {"count": 1, "duration_ms": 7}
    }
    with pytest.raises(JobConflictError):
        store.record_diagnostic(job.job_id, fence, operation, "encode", "failed", duration_ms=7)


def test_given_untrusted_diagnostic_fields_when_recorded_then_refused_without_free_text(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    with pytest.raises(ReleaseJobError):
        store.record_diagnostic(job.job_id, fence, "d" * 32, "/private/path", "failed")
    with pytest.raises(ReleaseJobError):
        store.record_diagnostic(job.job_id, fence, "d" * 32, "copy", "https://user:secret@host")
    with pytest.raises(ReleaseJobError):
        store.record_diagnostic(
            job.job_id, fence, "d" * 32, "copy", "failed", duration_ms=MAX_DIAGNOSTIC_DURATION_MS + 1
        )
    with pytest.raises(ReleaseJobError):
        store.record_diagnostic(job.job_id, fence, "not-hex", "copy", "failed")
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).diagnostics == {}


def test_given_destination_effects_when_reconciled_then_current_drops_and_peak_is_monotonic(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "dest", reserved=1024)).job_id
    fence = store.claim(job_id, "worker-a")
    control = ReleaseJobControl(store, job_id, fence)
    with control.effect(ATTEMPT, "data/a.bin", 100, DIGEST):
        pass
    with pytest.raises(OSError), control.effect(ATTEMPT, "data/b.bin", 200, DIGEST):
        raise OSError("write interrupted")
    status = store.get(job_id, scope=SCOPE, principal=OWNER)
    assert (status.destination_current_bytes, status.destination_peak_bytes) == (300, 300)

    with pytest.raises(AuthorityError, match="pending"):
        store.reconcile_effect(job_id, fence, "data/b.bin", observed="pending", length=200, digest=DIGEST)
    assert store.get(job_id, scope=SCOPE, principal=OWNER).destination_current_bytes == 300

    store.reconcile_effect(job_id, fence, "data/b.bin", observed="absent", length=200, digest=DIGEST)
    reopened = _store(tmp_path / "ledger", FakeClock()).get(job_id, scope=SCOPE, principal=OWNER)
    assert (reopened.destination_current_bytes, reopened.destination_peak_bytes) == (100, 300)


@pytest.mark.parametrize("expired", [False, True])
def test_given_quick_supervised_child_when_dispatched_then_only_live_lease_is_renewed(
    tmp_path: Path, expired: bool
) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job = store.accept(_request(tmp_path / "destination"))
    fence = store.claim(job.job_id, "worker")
    clock.advance(31 if expired else 29)

    def dispatch() -> None:
        outcome = run_supervised(
            store,
            job.job_id,
            fence,
            [sys.executable, "-B", "-c", "pass"],
            memory_bytes=MEMORY,
            file_bytes=FILE_LIMIT,
            heartbeat_seconds=5,
            grace_seconds=FAST_GRACE,
        )
        assert (outcome.reason, outcome.returncode) == ("exited", 0)

    if expired:
        with pytest.raises(AuthorityError, match="lease expired"):
            dispatch()
    else:
        dispatch()
        clock.advance(2)
        assert store.heartbeat(job.job_id, fence) is False


def _interrupted_local_write(tmp_path: Path) -> tuple[ReleaseJobStore, str, int, Path, Path]:
    store = _store(tmp_path / "ledger", FakeClock())
    request = _request(tmp_path / "destination", reserved=1024**2)
    job = store.accept(request)
    fence = store.claim(job.job_id, "worker")
    root = request.destination / ATTEMPT.prefix
    payload = b"prefix" * 2048
    digest = sha256_hex(payload * 2)
    store.begin_effect(job.job_id, fence, ATTEMPT, "data/payload.bin", len(payload) * 2, digest)
    code = (
        "import os,sys\nfrom pathlib import Path\n"
        "from physical_ai_curation.release.jobs import ReleaseJobStore\n"
        "from physical_ai_curation.release.store import LocalReleaseStore\n"
        "store = ReleaseJobStore(Path(sys.argv[1]))\n"
        "def created(name, identity):\n"
        "    store.register_local_temporary(sys.argv[2], int(sys.argv[3]), 'data/payload.bin', name, identity)\n"
        "def chunks():\n"
        "    yield b'prefix' * 2048\n"
        "    os._exit(17)\n"
        "LocalReleaseStore(Path(sys.argv[4])).write_verified_object("
        "'data/payload.bin', chunks(), length=24576, sha256=sys.argv[5], on_created=created)\n"
    )
    outcome = run_supervised(
        store,
        job.job_id,
        fence,
        [sys.executable, "-B", "-c", code, str(store.root), job.job_id, str(fence), str(root), digest],
        memory_bytes=MEMORY,
        file_bytes=FILE_LIMIT,
        heartbeat_seconds=0.05,
        grace_seconds=FAST_GRACE,
        effect_path="data/payload.bin",
    )
    assert outcome.returncode == 17
    store.finish_effect(job.job_id, fence, "data/payload.bin", ok=False)
    (partial,) = (root / "data").glob(".curation-copy-*.partial")
    assert partial.stat().st_size == len(payload)
    return store, job.job_id, fence, root, partial


def test_given_interrupted_registered_writer_when_reopened_then_only_owned_partial_reconciled(tmp_path: Path) -> None:
    store, job_id, fence, root, partial = _interrupted_local_write(tmp_path)
    reopened = _store(store.root, FakeClock())
    assert reopened.reconcile_local_outputs(job_id, fence) == 0
    assert not partial.exists()
    assert not (root / "data/payload.bin").exists()
    reopened.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    assert reopened.cleanup_owned_outputs(job_id, fence, root)
    assert reopened.settle_cancellation(job_id, fence, output_removed=True).capacity_state == "released"


def test_given_replaced_partial_inode_when_reconciled_then_foreign_bytes_retained(tmp_path: Path) -> None:
    store, job_id, fence, _, partial = _interrupted_local_write(tmp_path)
    partial.rename(partial.with_suffix(".held"))
    partial.write_bytes(b"foreign")
    with pytest.raises(AuthorityError):
        store.reconcile_local_outputs(job_id, fence)
    assert partial.read_bytes() == b"foreign"
    assert store.get(job_id, scope=SCOPE, principal=OWNER).capacity_state == "reserved"


def test_given_unregistered_creator_when_temporary_registered_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    store.begin_effect(job.job_id, fence, ATTEMPT, "payload", 1, DIGEST)
    empty = tmp_path / "empty"
    empty.touch()
    with pytest.raises(AuthorityError):
        store.register_local_temporary(
            job.job_id, fence, "payload", ".curation-copy-" + "a" * 32 + ".partial", empty.stat()
        )


def test_given_sealed_job_when_controls_written_then_supervised_and_payload_remains_closed(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    request = _request(tmp_path)
    job = store.accept(request)
    fence = store.claim(job.job_id, "worker")
    controls = {RECEIPT_PATH: b"receipt", CHECKSUMS_PATH: b"checksums"}
    store.seal(job.job_id, fence, {path: (len(content), sha256_hex(content)) for path, content in controls.items()})
    publisher = SupervisedLocalPublisher(request.destination, ATTEMPT, store, job.job_id, fence)
    for path, content in controls.items():
        publisher.write(path, (content,), length=len(content), sha256=sha256_hex(content))
        assert (publisher.root / path).read_bytes() == content
    assert len(list((store.root / "runtimes" / job.job_id).glob("*.json"))) == 2
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).effects_stopped
    with pytest.raises(LedgerClosedError):
        publisher.write("new-payload", (b"x",), length=1, sha256=sha256_hex(b"x"))
    with pytest.raises(AuthorityError):
        publisher.write(RECEIPT_PATH, (b"changed",), length=7, sha256=sha256_hex(b"changed"))


def test_given_exited_scratch_creator_when_store_reopened_then_owned_cleanup_releases_charge(tmp_path: Path) -> None:
    clock = FakeClock()
    root = tmp_path / "ledger"
    store = _store(root, clock)
    job = store.accept(_request(tmp_path))
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from physical_ai_curation.release.jobs import ReleaseJobStore, ReleaseRuntimeSettings\n"
        "store = ReleaseJobStore(Path(sys.argv[1]), "
        "settings=ReleaseRuntimeSettings(lease_seconds=30, reserve_bytes=0), "
        "clock=lambda:1000.0, free_bytes=lambda path:10**12)\n"
        "fence = store.claim(sys.argv[2], 'old')\n"
        "workspace = store.create_scratch_workspace(sys.argv[2], fence, 256)\n"
        "(workspace / 'source.bin').write_bytes(b'owned')\n"
        "print(workspace)\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(root), job.job_id],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([PACKAGE_ROOT, os.environ.get("PYTHONPATH", "")])},
    )
    workspace = Path(result.stdout.strip())
    assert workspace.is_dir()
    clock.advance(31)
    reopened = _store(root, clock)
    current = reopened.claim(job.job_id, "replacement")
    reopened.cleanup_scratch_workspace(job.job_id, current, recover=True)
    reopened.release_scratch(job.job_id, current)
    assert not workspace.exists()
    assert reopened.get(job.job_id, scope=SCOPE, principal=OWNER).scratch_bytes == 0


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_given_foreign_scratch_entry_when_cleanup_requested_then_not_deleted(tmp_path: Path, replacement: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job = store.accept(_request(tmp_path))
    fence = store.claim(job.job_id, "worker")
    workspace = store.create_scratch_workspace(job.job_id, fence, 256)
    foreign = tmp_path / "foreign.bin"
    foreign.write_bytes(b"foreign")
    if replacement == "directory":
        workspace.rename(workspace.with_name(workspace.name + "-held"))
        workspace.mkdir()
        (workspace / "foreign.bin").write_bytes(b"foreign")
    else:
        (workspace / "foreign.bin").symlink_to(foreign)
    with pytest.raises(AuthorityError):
        store.cleanup_scratch_workspace(job.job_id, fence)
    with pytest.raises(AuthorityError):
        store.release_scratch(job.job_id, fence)
    assert (workspace / "foreign.bin").read_bytes() == b"foreign"
    assert foreign.read_bytes() == b"foreign"
    assert store.get(job.job_id, scope=SCOPE, principal=OWNER).scratch_bytes == 256


@pytest.mark.parametrize(
    ("case", "error", "match"),
    [
        ("different_frozen", JobConflictError, "idempotency key is bound to a different request"),
        ("different_client_digest", JobConflictError, "idempotency key is bound to a different request"),
        ("same_identity_other_key", JobConflictError, "release identity is already claimed"),
        ("attempt_bound_elsewhere", JobConflictError, "publication attempt is already bound"),
        ("reserve_exceeds_free", CapacityError, "insufficient capacity"),
    ],
)
def test_given_conflicting_admission_when_accepted_then_refused_without_new_job(
    tmp_path: Path, case: str, error: type[Exception], match: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock(), free=1000 if case == "reserve_exceeds_free" else 10**12)
    base = _request(tmp_path / "output")
    if case == "reserve_exceeds_free":
        request = _request(tmp_path / "output", reserved=1001)
    else:
        store.accept(base)
        if case == "different_frozen":
            request = _request(tmp_path / "output", frozen=b'{"request":2}')
        elif case == "different_client_digest":
            request = replace(base, client_request_digest="e" * 64)
        elif case == "same_identity_other_key":
            request = _request(
                tmp_path / "output",
                key="key-2",
                attempt=PublicationAttempt("pkg-001", "attempt-9", source_dataset_id="sample"),
                frozen=b'{"request":3}',
            )
        else:
            request = _request(tmp_path / "other", key="key-2")
    before = store.storage.snapshot()

    with pytest.raises(error, match=match):
        store.accept(request)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("elapsed", [0, 29])
def test_given_live_lease_when_second_worker_claims_then_refused_until_expiry(tmp_path: Path, elapsed: int) -> None:
    clock = FakeClock()
    store = _store(tmp_path / "ledger", clock)
    job_id = store.accept(_request(tmp_path / "output")).job_id
    store.claim(job_id, "worker-a")
    clock.advance(elapsed)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="lease is held"):
        store.claim(job_id, "worker-b")

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("paused", ["cancelled", "needs_input"])
def test_given_terminal_or_paused_job_when_claimed_then_refused(tmp_path: Path, paused: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    store.confirm_stopped(job_id, fence)
    if paused == "cancelled":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
        store.settle_cancellation(job_id, fence, output_removed=True)
    else:
        store.require_input(job_id, fence, "source_unavailable")

    with pytest.raises(AuthorityError, match="job cannot be claimed"):
        store.claim(job_id, "worker-b")


@pytest.mark.parametrize(
    ("code", "error", "match"),
    [
        ("unknown_refusal", ReleaseJobError, "invalid refusal category"),
        ("source_unavailable", CancellationError, "cancellation requested"),
        ("source_unavailable", AuthorityError, "input refusal has unresolved work"),
    ],
)
def test_given_invalid_or_unresolved_input_refusal_when_recorded_then_job_keeps_running(
    tmp_path: Path, code: str, error: type[Exception], match: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    if error is CancellationError:
        store.confirm_stopped(job_id, fence)
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(error, match=match):
        store.require_input(job_id, fence, code)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("category", "expected_fence", "worker_id"),
    [
        ("unknown_failure", 0, "worker-a"),
        ("worker_failed", 0, ""),
    ],
)
def test_given_invalid_dispatch_failure_when_recorded_then_refused(
    tmp_path: Path, category: str, expected_fence: int, worker_id: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.record_dispatch_failure(job_id, category, expected_fence=expected_fence, worker_id=worker_id)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("case", "error", "match"),
    [
        ("not_requested", CancellationError, "cancellation was not requested"),
        ("stale_fence", AuthorityError, "stale worker fence"),
        ("not_stopped", AuthorityError, "effects are not confirmed stopped"),
        ("open_effect", AuthorityError, "effects are still dispatched or unresolved"),
    ],
)
def test_given_unsafe_cancellation_settlement_when_settled_then_capacity_stays_reserved(
    tmp_path: Path, case: str, error: type[Exception], match: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    if case == "open_effect":
        store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 1, DIGEST)
    if case != "not_requested":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    if case != "not_stopped":
        store.confirm_stopped(job_id, fence)
    settle_fence = fence + 1 if case == "stale_fence" else fence
    before = store.storage.snapshot()

    with pytest.raises(error, match=match):
        store.settle_cancellation(job_id, settle_fence, output_removed=True)

    assert store.storage.snapshot() == before


def test_given_unsettled_cancellation_when_purged_then_record_is_retained(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    store.confirm_stopped(job_id, fence)
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="cancellation is not settled"):
        store.purge_cancelled(job_id, fence)

    assert store.storage.snapshot() == before


def test_given_cancellation_before_seal_when_sealed_then_refused_and_ledger_open(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(CancellationError, match="cancellation was requested before seal"):
        store.seal(job_id, fence, CONTROLS)

    assert store.storage.snapshot() == before


def test_given_sealed_ledger_when_resealed_with_different_controls_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    altered = {**CONTROLS, RECEIPT_PATH: (11, "b" * 64)}
    before = store.storage.snapshot()

    with pytest.raises(JobConflictError, match="ledger is sealed with different controls"):
        store.seal(job_id, fence, altered)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("case", "attempt", "digest", "length", "error", "match"),
    [
        ("uppercase_digest", ATTEMPT, "D" * 64, 1, ReleaseJobError, "marker digest must be lowercase SHA-256"),
        ("not_committing", ATTEMPT, COMMIT_DIGEST, 1, AuthorityError, "marker requires a committing publication"),
        ("foreign_attempt", OTHER_ATTEMPT, COMMIT_DIGEST, 1, AuthorityError, "attempt is not owned by this job"),
    ],
)
def test_given_marker_intent_outside_commit_when_begun_then_refused(
    tmp_path: Path,
    case: str,
    attempt: PublicationAttempt,
    digest: str,
    length: int,
    error: type[Exception],
    match: str,
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    before = store.storage.snapshot()

    with pytest.raises(error, match=match):
        store.begin_marker_effect(job_id, fence, attempt, COMMIT_DIGEST, length, digest)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("case", "error", "match"),
    [
        ("foreign_principal", JobNotFoundError, "job not found"),
        ("committed", CancellationError, "commit was already chosen"),
    ],
)
def test_given_cancellation_request_when_job_is_foreign_or_committed_then_refused(
    tmp_path: Path, case: str, error: type[Exception], match: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    if case == "committed":
        job_id, _fence = _committed_job(store, tmp_path / "output")
        principal = OWNER
    else:
        job_id = store.accept(_request(tmp_path / "output")).job_id
        principal = "user-b"
    before = store.storage.snapshot()

    with pytest.raises(error, match=match):
        store.request_cancel(job_id, scope=SCOPE, principal=principal)

    assert store.storage.snapshot() == before


_WRITER_TEMP = ".curation-copy-" + "a" * 32 + ".partial"


class _SequencedMonotonic:
    """Deterministic monotonic source injected into the release jobs module; the last value repeats."""

    def __init__(self, values: list[float]) -> None:
        self._values = values

    def monotonic(self) -> float:
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]

    def time(self) -> float:
        return time.time()


def _registered_writer(store: ReleaseJobStore, job_id: str, fence: int, effect_path: str | None) -> None:
    identity = ProcessIdentity.of(os.getpid())
    proof = store.runtime_proof_path(job_id, fence, effect_path=effect_path)
    store.register_runtime(job_id, fence, SupervisedRuntime(identity, identity, proof), effect_path=effect_path)


def test_given_unsupported_refusal_code_when_constructed_then_refused() -> None:
    with pytest.raises(ValueError, match="unsupported release refusal"):
        ReleaseRefusal("not-a-code")


@pytest.mark.parametrize("worker", ["", "bad worker"])
def test_given_malformed_worker_identity_when_claimed_then_lease_is_not_granted(tmp_path: Path, worker: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError, match="invalid worker identity"):
        store.claim(job_id, worker)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    ("effect_path", "effect_paths", "match"),
    [
        ("data/a.bin", ["data/a.bin"], "invalid effect scope"),
        (None, [], "effect set is empty"),
    ],
)
def test_given_ambiguous_runtime_scope_when_proof_requested_then_no_proof_directory_is_created(
    tmp_path: Path, effect_path: str | None, effect_paths: list[str], match: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")

    with pytest.raises(AuthorityError, match=match):
        store.runtime_proof_path(job_id, fence, effect_path=effect_path, effect_paths=effect_paths)

    assert not (store.root / "runtimes" / job_id).exists()


def test_given_duplicate_runtime_effect_scope_when_proof_requested_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)

    with pytest.raises(AuthorityError, match="effect set is invalid"):
        store.runtime_proof_path(job_id, fence, effect_paths=["data/a.bin", "data/a.bin"])


@pytest.mark.parametrize(
    ("paths", "match"),
    [
        (["data/a.bin"], "lacks a journaled cancellation reconciliation"),
        (["data/a.bin", "data/a.bin"], "reconciliation set is invalid"),
        ([f"data/{index}.bin" for index in range(17)], "reconciliation set is invalid"),
    ],
)
def test_given_unjournaled_or_oversized_reconciliation_scope_when_proof_requested_then_refused(
    tmp_path: Path, paths: list[str], match: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")

    with pytest.raises(AuthorityError, match=match):
        store.runtime_proof_path(job_id, fence, effect_paths=paths, reconciliation=True)

    assert not (store.root / "runtimes" / job_id).exists()


def test_given_cancellation_requested_when_runtime_registered_then_start_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    identity = ProcessIdentity.of(os.getpid())
    runtime = SupervisedRuntime(identity, identity, store.runtime_proof_path(job_id, fence))
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="not authorized"):
        store.register_runtime(job_id, fence, runtime)

    assert store.storage.snapshot() == before


def test_given_stopped_runtime_identity_when_registered_again_then_single_record_is_kept(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    identity = ProcessIdentity.of(os.getpid())
    runtime = SupervisedRuntime(identity, identity, store.runtime_proof_path(job_id, fence))
    store.register_runtime(job_id, fence, runtime)
    with store._transaction() as records:
        (record,) = records[job_id]["runtimes"].values()
        record["stopped"] = True
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="already registered"):
        store.register_runtime(job_id, fence, runtime)

    assert store.storage.snapshot() == before


def test_given_no_registered_runtime_when_stop_confirmed_then_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    identity = ProcessIdentity.of(os.getpid())

    with pytest.raises(AuthorityError, match="not registered"):
        store.confirm_runtime_stopped(job_id, fence, supervision.StopProof(identity, identity, 0, 0))


@pytest.mark.parametrize("mismatch", ["fence", "unassociated"])
def test_given_source_association_identity_when_validated_then_mismatch_is_refused(
    tmp_path: Path, mismatch: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "output"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    scope = store.storage.snapshot()[job_id]["principal_digest"]
    checked_fence = fence + 1 if mismatch == "fence" else fence

    with pytest.raises(AuthorityError, match="source association does not match"):
        store.validate_source_association(job_id, checked_fence, destination, ATTEMPT, b"binding", scope)


def test_given_foreign_attempt_when_effect_begins_then_no_effect_is_journaled(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="not owned"):
        store.begin_effect(job_id, fence, OTHER_ATTEMPT, "data/a.bin", 3, DIGEST)

    assert store.storage.snapshot() == before


def test_given_sealed_ledger_when_sealed_again_with_identical_controls_then_state_is_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    before = store.storage.snapshot()

    store.seal(job_id, fence, CONTROLS)

    assert store.storage.snapshot() == before


def test_given_sealed_ledger_when_sealed_with_other_controls_then_conflict_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    before = store.storage.snapshot()
    conflicting = {RECEIPT_PATH: (11, "b" * 64), CHECKSUMS_PATH: (20, "c" * 64)}

    with pytest.raises(JobConflictError, match="different controls"):
        store.seal(job_id, fence, conflicting)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("state", ["committing", "committed"])
def test_given_publication_started_when_seal_requested_then_sealing_is_refused(tmp_path: Path, state: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    if state == "committed":
        job_id, fence = _committed_job(store, tmp_path / "output")
    else:
        job_id, fence = _sealed_job(store, tmp_path / "output")
        store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="not sealable"):
        store.seal(job_id, fence, CONTROLS)

    assert store.storage.snapshot() == before


def test_given_recorded_temporary_when_same_name_registered_again_then_ownership_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "output"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 0, DIGEST)
    _registered_writer(store, job_id, fence, "data/a.bin")
    partial = destination / ATTEMPT.prefix / "data" / _WRITER_TEMP
    partial.parent.mkdir(parents=True)
    partial.touch()
    store.register_local_temporary(job_id, fence, "data/a.bin", _WRITER_TEMP, partial.stat())
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="already recorded"):
        store.register_local_temporary(job_id, fence, "data/a.bin", _WRITER_TEMP, partial.stat())

    assert store.storage.snapshot() == before


def test_given_published_marker_when_uncertain_output_reconciled_then_immutable_output_is_refused(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "destination"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    store.finish_effect(job_id, fence, "data/a.bin", ok=False)
    marker = destination / ATTEMPT.prefix / MARKER_PATH
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"{}")
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="immutable"):
        store.reconcile_local_outputs(job_id, fence)

    assert store.storage.snapshot() == before
    assert marker.read_bytes() == b"{}"


def test_given_zero_recovery_budget_when_local_outputs_reconciled_then_no_observation_is_taken(tmp_path: Path) -> None:
    store = ReleaseJobStore(
        tmp_path / "ledger",
        settings=replace(SETTINGS, recovery_seconds=0),
        clock=FakeClock(),
        free_bytes=lambda _path: 10**12,
    )
    destination = tmp_path / "destination"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    store.finish_effect(job_id, fence, "data/a.bin", ok=False)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="budget"):
        store.reconcile_local_outputs(job_id, fence)

    assert store.storage.snapshot() == before


def test_given_deadline_passes_during_read_when_local_output_reconciled_then_effect_stays_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "destination"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    store.finish_effect(job_id, fence, "data/a.bin", ok=False)
    output = destination / ATTEMPT.prefix / "data" / "a.bin"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"abc")
    before = store.storage.snapshot()
    # Deadline is read first, then one effect check (still in budget), then the first chunk check (expired).
    monkeypatch.setattr(release_jobs, "time", _SequencedMonotonic([0.0, 0.0, 1000.0]))

    with pytest.raises(AuthorityError, match="budget"):
        store.reconcile_local_outputs(job_id, fence)

    assert store.storage.snapshot() == before
    assert output.read_bytes() == b"abc"


@pytest.mark.parametrize("damage", ["entry_present", "pending_temporary"])
def test_given_marker_cleanup_deadline_passes_when_residue_scanned_then_cleanup_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    store = ReleaseJobStore(
        tmp_path / "ledger",
        settings=replace(SETTINGS, cleanup_seconds=1),
        clock=FakeClock(),
        free_bytes=lambda _path: 10**12,
    )
    destination = tmp_path / "destination"
    job_id, fence = _sealed_job(store, destination)
    store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    root = destination / ATTEMPT.prefix
    root.mkdir(parents=True)
    if damage == "entry_present":
        (root / "keep.bin").write_bytes(b"entry")
    else:
        with store._transaction() as records:
            records[job_id]["temporaries"].append(
                {"path": MARKER_PATH, "name": _WRITER_TEMP, "fence": fence, "device": 1, "inode": 1, "removed": False}
            )
    before = store.storage.snapshot()
    monkeypatch.setattr(release_jobs, "time", _SequencedMonotonic([0.0, 1000.0]))

    with pytest.raises(AuthorityError, match="marker cleanup budget exhausted"):
        store.remove_marker_temporaries(job_id, fence, root)

    assert store.storage.snapshot() == before


def test_given_replaced_marker_partial_when_cleanup_runs_then_foreign_inode_is_retained(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "destination"
    job_id, fence = _sealed_job(store, destination)
    store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 3, DIGEST)
    _registered_writer(store, job_id, fence, MARKER_PATH)
    root = destination / ATTEMPT.prefix
    root.mkdir(parents=True)
    partial = root / _WRITER_TEMP
    partial.touch()
    store.register_local_temporary(job_id, fence, MARKER_PATH, _WRITER_TEMP, partial.stat())
    with store._transaction() as records:
        for runtime in records[job_id]["runtimes"].values():
            runtime["stopped"] = True
    # Keep the original inode alive so the replacement cannot reuse its number.
    partial.rename(root / "held.partial")
    partial.write_bytes(b"foreign")

    with pytest.raises(AuthorityError, match="ownership is unresolved"):
        store.remove_marker_temporaries(job_id, fence, root)

    assert partial.read_bytes() == b"foreign"


def test_given_marker_runtime_unresolved_when_marker_resolved_then_intent_is_not_settled(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "destination")
    store._begin_publication(job_id, fence, ATTEMPT, COMMIT_DIGEST)
    store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 3, DIGEST)
    _registered_writer(store, job_id, fence, MARKER_PATH)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="runtime stop proof is unresolved"):
        store.resolve_marker_effect(job_id, fence, observed="absent", length=3, digest=DIGEST)

    assert store.storage.snapshot() == before


def test_given_unresolved_runtime_when_binding_recovered_then_execution_is_not_authorized(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    destination = tmp_path / "destination"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    _registered_writer(store, job_id, fence, None)

    with pytest.raises(AuthorityError, match="runtime stop proof is unresolved"):
        store.validate_execution_binding(job_id, fence, destination, ATTEMPT, route="local", recovery=True)


@pytest.mark.parametrize("damage", ["oversized_line", "unknown_identity"])
def test_given_corrupt_event_journal_when_flushed_then_refused_without_truncation(tmp_path: Path, damage: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    store.accept(_request(tmp_path / "output"))
    payloads = {
        "oversized_line": b"x" * 5000 + b"\n",
        "unknown_identity": json.dumps({"seq": 1, "event_id": "untrusted"}).encode() + b"\n",
    }
    payload = payloads[damage]
    store.events_path.parent.mkdir(parents=True, exist_ok=True)
    store.events_path.write_bytes(payload)

    with pytest.raises(ReleaseJobError, match="corrupt"):
        store.flush_events()

    assert store.events_path.read_bytes() == payload


def test_given_diagnostic_budget_exhausted_when_new_operation_recorded_then_only_replay_is_accepted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    operations = [f"{index:032x}" for index in range(MAX_DIAGNOSTIC_OPERATIONS)]
    for operation in operations:
        store.record_diagnostic(job_id, fence, operation, "encode", "succeeded", duration_ms=1)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError, match="budget"):
        store.record_diagnostic(job_id, fence, "f" * 32, "encode", "succeeded", duration_ms=1)
    assert store.storage.snapshot() == before

    store.record_diagnostic(job_id, fence, operations[0], "encode", "succeeded", duration_ms=1)
    assert store.storage.snapshot() == before


@pytest.mark.parametrize("operation", ["A" * 32, "a" * 31])
def test_given_malformed_diagnostic_operation_identity_when_recorded_then_refused(
    tmp_path: Path, operation: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError, match="identity is invalid"):
        store.record_diagnostic(job_id, fence, operation, "copy", "failed")

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(("category", "duration"), [("copy", 7), ("encode", 8)])
def test_given_recorded_diagnostic_identity_when_observation_changes_then_conflict_is_refused(
    tmp_path: Path, category: str, duration: int
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    operation = "e" * 32
    store.record_diagnostic(job_id, fence, operation, "encode", "succeeded", duration_ms=7)
    before = store.storage.snapshot()

    with pytest.raises(JobConflictError):
        store.record_diagnostic(job_id, fence, operation, category, "succeeded", duration_ms=duration)

    assert store.storage.snapshot() == before


def test_given_no_scratch_workspace_when_cleanup_requested_then_ledger_is_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    before = store.storage.snapshot()

    store.cleanup_scratch_workspace(job_id, fence)

    assert store.storage.snapshot() == before


def test_given_reserved_workspace_name_already_exists_when_created_then_foreign_directory_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_jobs, "uuid4", lambda: SimpleNamespace(hex="e" * 32))
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    foreign = store.root.resolve() / "scratch" / job_id / ("e" * 32)
    foreign.mkdir(parents=True)
    (foreign / "foreign.bin").write_bytes(b"foreign")

    with pytest.raises(AuthorityError, match="collided"):
        store.create_scratch_workspace(job_id, fence, 100)

    assert (foreign / "foreign.bin").read_bytes() == b"foreign"


def test_given_workspace_directory_already_removed_when_cleaned_then_removal_is_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    workspace = store.create_scratch_workspace(job_id, fence, 100)
    workspace.rmdir()

    store.cleanup_scratch_workspace(job_id, fence)

    assert store.storage.snapshot()[job_id]["workspace"]["status"] == "removed"


def test_given_workspace_directory_replaced_when_cleaned_then_replacement_is_retained(tmp_path: Path) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "output")).job_id
    fence = store.claim(job_id, "worker-a")
    workspace = store.create_scratch_workspace(job_id, fence, 100)
    # Keep the original inode alive so the replacement cannot reuse its number.
    workspace.rename(workspace.parent / "held-original")
    workspace.mkdir()
    (workspace / "replacement.bin").write_bytes(b"replacement")

    with pytest.raises(AuthorityError, match="identity"):
        store.cleanup_scratch_workspace(job_id, fence)

    assert (workspace / "replacement.bin").read_bytes() == b"replacement"


@pytest.mark.parametrize(("observation", "expected"), [("conflict", "invalid"), ("unknown", "invalid")])
def test_given_invalid_reconciliation_observation_when_reported_then_effect_stays_unresolved(
    tmp_path: Path, observation: str, expected: str
) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id = store.accept(_request(tmp_path / "destination")).job_id
    fence = store.claim(job_id, "worker-a")
    store.begin_effect(job_id, fence, ATTEMPT, "data/a.bin", 3, DIGEST)
    store.finish_effect(job_id, fence, "data/a.bin", ok=False)

    with (
        pytest.raises(ReleaseJobError, match=f"observation is {expected}"),
        ReleaseJobControl(store, job_id, fence).reconciliation(
            ATTEMPT, "data/a.bin", 3, DIGEST, copy_id=None, cancel=False
        ) as observed,
    ):
        observed(observation)

    assert store.storage.snapshot()[job_id]["effects"]["data/a.bin"]["status"] == "uncertain"
