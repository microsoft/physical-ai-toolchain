from __future__ import annotations

import base64
import json
import math
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import physical_ai_curation
import pytest
from physical_ai_curation.release.blob_worker import SupervisedBlobPublisher, _process
from physical_ai_curation.release.canonical import canonical_json_bytes, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.jobs import (
    BLOB_OWNER_CLEANUP_PATH,
    EVENTS_NAME,
    MAX_DIAGNOSTIC_DURATION_MS,
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
    ReleaseRuntimeSettings,
)
from physical_ai_curation.release.local_worker import SupervisedLocalPublisher
from physical_ai_curation.release.models import MANIFEST_PATH, MARKER_PATH, RECEIPT_PATH, REPORT_PATH, SourceObject
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
    storage_from_configuration,
)
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
from test_release_contracts import _manifest_for, _payload_files, _write_package
from test_release_publishers import _FakeBlobClient, _FakeContainer

ATTEMPT = PublicationAttempt("pkg-001", "attempt-1")
OTHER_ATTEMPT = PublicationAttempt("pkg-002", "attempt-2")
DIGEST = "a" * 64
COMMIT_DIGEST = "d" * 64
SCOPE = "release"
OWNER = "user-a"
CONTROLS = {RECEIPT_PATH: (10, "b" * 64), REPORT_PATH: (20, "c" * 64)}
MEMORY = 512 * 1024**2
SETTINGS = ReleaseRuntimeSettings(lease_seconds=30, reserve_bytes=0)
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


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(root: Path, clock: FakeClock, *, free: int = 10**12) -> ReleaseJobStore:
    return ReleaseJobStore(root, settings=SETTINGS, clock=clock, free_bytes=lambda _path: free)


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


def _sealed_job(store: ReleaseJobStore, destination: Path) -> tuple[str, int]:
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    store.seal(job_id, fence, CONTROLS)
    for path, (length, digest) in CONTROLS.items():
        store.begin_effect(job_id, fence, ATTEMPT, path, length, digest)
        store.finish_effect(job_id, fence, path, ok=True)
    return job_id, fence


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
    next_attempt = PublicationAttempt(ATTEMPT.package_id, "attempt-next", ATTEMPT.prefix)
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


def test_given_local_record_write_failure_when_reopened_then_old_snapshot_is_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalRecordStorage(tmp_path / "state")
    key = "a" * 32
    with storage.transaction() as records:
        records[key] = {"state": "accepted"}
    original = storage.snapshot()

    def fail_replace(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("replacement interrupted")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"), storage.transaction() as records:
        records[key]["state"] = "running"

    reopened = storage_from_configuration(storage.configuration())
    assert reopened.snapshot() == original
    assert [path.name for path in (tmp_path / "state" / "jobs").iterdir()] == [f"{key}.json"]


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
            REPORT_PATH: (len(candidate.report_bytes), sha256_hex(candidate.report_bytes)),
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

    def list_blobs(self, name_starts_with: str | None = None, include: Any = None) -> Iterator[SimpleNamespace]:
        for path in sorted(self.objects.glob("*.json")):
            record = json.loads(path.read_bytes())
            if name_starts_with is None or record["name"].startswith(name_starts_with):
                yield _fs_properties(record)

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

    def download_blob(self, etag: str | None = None, match_condition: Any = None, **kwargs: Any) -> SimpleNamespace:
        self.container.log("download_blob", self.name)
        record = self.container.load(self.name)
        _check_etag(etag, record)
        data = _payload(record)
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


def _child_copy_fixture(tmp_path: Path) -> SimpleNamespace:
    sdk = tmp_path / "sdk"
    source = FsContainer(sdk, "example", "source")
    source.put("data/a.bin", b"abc", {"kind": "frame"})
    source_object, source_etag = _source_object(source, "data/a.bin", b"abc")
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


@pytest.mark.parametrize("loss", [None, "payload", "companion"])
def test_given_cancelled_blob_root_when_cleaned_then_companion_last_and_capacity_released(
    tmp_path: Path, loss: str | None
) -> None:
    fixture = _child_copy_fixture(tmp_path)
    publisher = fixture.publisher
    publisher.copy(fixture.source, fixture.source_object, fixture.source_etag)
    publisher.write("meta/info.json", [b"{}"], length=2, sha256=sha256_hex(b"{}"))
    controls = {RECEIPT_PATH: b"receipt", REPORT_PATH: b"report"}
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
def test_given_blob_candidate_when_supervised_upload_and_publish_then_independently_marked(
    tmp_path: Path, lose_marker_stop_ack: bool
) -> None:
    root = tmp_path / "candidate"
    files = _payload_files()
    digest = _write_package(root, files, _manifest_for(files))
    sdk = tmp_path / "sdk"
    destination = FsContainer(sdk, "example", "release")
    clock = OffsetClock()
    store = _store(tmp_path / "ledger", clock)
    job = store.accept(_blob_request(key="blob-publish", reserved=8 * 1024**2))
    fence = store.claim(job.job_id, "worker")
    publisher = _ChildBlobPublisher(destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk)
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
            REPORT_PATH: (len(candidate.report_bytes), sha256_hex(candidate.report_bytes)),
        },
    )

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
        publisher = _ChildBlobPublisher(destination, BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk)
    result = publisher.publish(digest)
    root.rename(tmp_path / "source-unavailable")

    from physical_ai_curation.release.verification import verify_published

    assert verify_published(publisher.store, digest) == result
    assert publisher.publish(digest) == result
    record = store.storage.snapshot()[job.job_id]
    assert record["state"] == "committed"
    assert all(effect["status"] == "acknowledged" for effect in record["effects"].values())
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())
    with pytest.raises((AuthorityError, LedgerClosedError)):
        publisher.write("data/late.bin", [b"abc"], length=3, sha256=sha256_hex(b"abc"))


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
                attempt=PublicationAttempt("pkg-003", "attempt-3"),
            )
        )
    accepted = store.accept(
        _blob_request(
            key="third",
            reserved=400,
            destination=ACCOUNT_BLOB,
            attempt=PublicationAttempt("pkg-004", "attempt-4"),
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
            REPORT_PATH: (len(candidate.report_bytes), sha256_hex(candidate.report_bytes)),
        }
    )
    for path, body in ((RECEIPT_PATH, candidate.receipt_bytes), (REPORT_PATH, candidate.report_bytes)):
        publisher.write(path, (body,), length=len(body), sha256=sha256_hex(body))
    commit = control.publication(ATTEMPT, manifest_digest)
    commit.__enter__()  # Leave the commit open, as if the writer crashed before the marker.
    return store, job_id, fence, destination, publisher.root, manifest_digest


def _marker_identity(root: Path, manifest_digest: str) -> tuple[bytes, int, str]:
    marker = build_marker(verify_candidate(LocalReleaseStore(root), manifest_digest))
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
                str(root / "release"),
            ],
            memory_bytes=MEMORY,
            heartbeat_seconds=0.01,
            grace_seconds=FAST_GRACE,
            effect_path=MARKER_PATH,
        )
    if not lose_stop_ack:
        store.finish_effect(job_id, stale, MARKER_PATH, ok=False)
    assert len(list((root / "release").glob(".curation-copy-*.partial"))) == 1
    assert not (root / MARKER_PATH).exists()

    clock.advance(31)
    successor = store.claim(job_id, "worker-b")
    result = SupervisedLocalPublisher(destination, ATTEMPT, store, job_id, successor).publish(digest)

    assert result.publication_state == "published"
    assert (root / MARKER_PATH).is_file()
    assert list((root / "release").glob(".curation-copy-*.partial")) == []
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
    residue = root / "release" / f".curation-copy-{'e' * 32}.partial"
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
    assert store.get(job_id, scope=SCOPE, principal=OWNER).effects_stopped is True


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
                attempt=PublicationAttempt(f"pkg-{index:03d}", f"attempt-{index}"),
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


def test_given_planned_encoding_when_executed_then_native_result_has_stop_proof(tmp_path: Path) -> None:
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
    ) as encoded:
        assert encoded.media.windows[0].output_to_frame == 2
        assert encoded.length == encoded.path.stat().st_size
        assert sha256_hex(encoded.path.read_bytes()) == encoded.sha256
        assert store.get(job_id, scope=SCOPE, principal=OWNER).effects_stopped
    assert not encoded.path.exists()


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
            attempt=PublicationAttempt("third", "one"),
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
    controls = {RECEIPT_PATH: b"receipt", REPORT_PATH: b"report"}
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
