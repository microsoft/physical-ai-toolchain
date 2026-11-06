"""Record limits and recovery fail closed without losing the previous snapshot."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from physical_ai_curation.release import record_storage
from physical_ai_curation.release.canonical import canonical_json_bytes
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.record_storage import (
    SCHEMA_VERSION,
    BlobLocation,
    BlobRecordStorage,
    LocalRecordStorage,
    RecordLimits,
    RecordStorage,
    storage_from_configuration,
)
from test_release_jobs import _blob_record_store, _RecordContainer

KEY = "a" * 32
OTHER = "b" * 32


def _record(key: str = KEY) -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "job_id": key}


def _storage(tmp_path: Path, backend: str, **limits: int) -> RecordStorage:
    if backend == "local":
        return LocalRecordStorage(tmp_path / "records", **limits)
    return BlobRecordStorage(
        _RecordContainer(),
        account_url="https://example.blob.core.windows.net",
        container_name="release-state",
        prefix="selected/subfolder",
        **limits,
    )


@pytest.mark.parametrize("backend", ["local", "blob"])
@pytest.mark.parametrize("bound", ["count", "record_bytes", "total_bytes"])
def test_transaction_limits_preserve_previous_snapshot(tmp_path: Path, backend: str, bound: str) -> None:
    size = len(canonical_json_bytes(_record()))
    limits = {"max_record_bytes": size, "max_total_bytes": size * 2, "max_jobs": 2}
    if bound == "count":
        limits["max_jobs"] = 1
    elif bound == "total_bytes":
        limits["max_total_bytes"] = size
    storage = _storage(tmp_path, backend, **limits)
    with storage.transaction() as working:
        working[KEY] = _record()
    before = storage.snapshot()

    with pytest.raises(ReleaseVerificationError, match=r"exceeds|max_total_bytes"), storage.transaction() as working:
        if bound == "record_bytes":
            working[KEY]["extra"] = "oversized"
        else:
            working[OTHER] = _record(OTHER)

    assert storage.snapshot() == before


@pytest.mark.parametrize("backend", ["local", "blob"])
@pytest.mark.parametrize("damage", ["key", "schema", "identity", "not_object"])
def test_transaction_rejects_invalid_record_without_persisting(tmp_path: Path, backend: str, damage: str) -> None:
    storage = _storage(tmp_path, backend)
    key = "invalid" if damage == "key" else KEY
    record = _record()
    if damage == "schema":
        record["schema_version"] = "obsolete"
    elif damage == "identity":
        record["job_id"] = OTHER
    elif damage == "not_object":
        record = []

    with (
        pytest.raises(ReleaseVerificationError, match=r"job ID|invalid schema_version"),
        storage.transaction() as working,
    ):
        working[key] = record

    assert storage.snapshot() == {}


@pytest.mark.parametrize("backend", ["local", "blob"])
def test_transaction_exception_rolls_back_nested_mutation(tmp_path: Path, backend: str) -> None:
    storage = _storage(tmp_path, backend)
    with storage.transaction() as working:
        working[KEY] = {**_record(), "nested": {"values": [1]}}

    with pytest.raises(RuntimeError, match="caller failed"), storage.transaction() as working:
        working[KEY]["nested"]["values"].append(2)
        raise RuntimeError("caller failed")

    assert storage.snapshot()[KEY]["nested"] == {"values": [1]}


@pytest.mark.parametrize("backend", ["local", "blob"])
@pytest.mark.parametrize("bound", ["max_jobs", "max_record_bytes", "max_total_bytes"])
def test_snapshot_enforces_limits_on_existing_records(tmp_path: Path, backend: str, bound: str) -> None:
    storage = _storage(tmp_path, backend)
    with storage.transaction() as working:
        working[KEY] = _record()
    with storage.transaction() as working:
        working[OTHER] = _record(OTHER)
    size = len(canonical_json_bytes(_record()))
    limits = {"max_record_bytes": size, "max_total_bytes": size * 2, "max_jobs": 2}
    limits[bound] = 1 if bound == "max_jobs" else size - 1 if bound == "max_record_bytes" else size
    if backend == "local":
        limited = LocalRecordStorage(tmp_path / "records", **limits)
    else:
        limited = BlobRecordStorage(
            storage._container,
            account_url="https://example.blob.core.windows.net",
            container_name="release-state",
            prefix="selected/subfolder",
            **limits,
        )

    with pytest.raises(ReleaseVerificationError, match=bound):
        limited.snapshot()

    assert len(storage.snapshot()) == 2


@pytest.mark.parametrize("timeout", [True, float("nan"), float("inf"), 0, -1, 301])
def test_record_limits_reject_invalid_contention_timeout(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        RecordLimits(timeout_seconds=timeout)


def test_record_limit_must_fit_total_budget() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        RecordLimits(max_record_bytes=2, max_total_bytes=1)


@pytest.mark.parametrize(
    "config",
    [
        {"backend": "sqlite"},
        {"backend": "local", "root": ".", "unknown": True},
        {"backend": "blob", "unknown": True},
    ],
)
def test_storage_configuration_refuses_unknown_contract(config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        storage_from_configuration(config)


@pytest.mark.parametrize("container", [None, "ab", "Uppercase"])
def test_blob_location_rejects_invalid_container_name(container: object) -> None:
    with pytest.raises(ValueError, match="container name"):
        BlobLocation("https://example.blob.core.windows.net", container)


@pytest.mark.parametrize("prefix", [42, "../escape"])
def test_blob_location_rejects_unsafe_prefix(prefix: object) -> None:
    with pytest.raises((ValueError, ReleaseVerificationError)):
        BlobLocation("https://example.blob.core.windows.net", "release-state", prefix)


@pytest.mark.parametrize("damage", ["not_object", "extra", "schema", "key", "revision", "record", "size"])
def test_pending_transaction_recovery_refuses_malformed_checkpoint(damage: str) -> None:
    container = _RecordContainer()
    storage = _blob_record_store(container)
    storage._limits = RecordLimits(max_record_bytes=128)
    pending = {
        "schema_version": "curated-release-transaction/v1",
        "job_id": KEY,
        "record": _record(),
        "revision": None,
    }
    if damage == "not_object":
        pending = []
    elif damage == "extra":
        pending["unexpected"] = True
    elif damage == "schema":
        pending["schema_version"] = "obsolete"
    elif damage == "key":
        pending["job_id"] = "invalid"
    elif damage == "revision":
        pending["revision"] = 42
    elif damage == "record":
        pending["record"]["job_id"] = OTHER
    else:
        pending["record"]["payload"] = "x" * (storage._limits.max_record_bytes + 2048)
    body = canonical_json_bytes(pending)
    container.put("selected/subfolder/coordinator.lock", body)

    with pytest.raises(ReleaseVerificationError), storage.transaction():
        pytest.fail("malformed checkpoint must not admit a transaction")

    assert storage.snapshot() == {}
    assert container.blobs["selected/subfolder/coordinator.lock"].data == body
    assert not container.leased


def test_pending_record_limit_is_distinct_from_checkpoint_envelope_limit() -> None:
    container = _RecordContainer()
    storage = _blob_record_store(container)
    storage._limits = RecordLimits(max_record_bytes=128)
    container.put(
        "selected/subfolder/coordinator.lock",
        canonical_json_bytes(
            {
                "schema_version": "curated-release-transaction/v1",
                "job_id": KEY,
                "record": {**_record(), "payload": "x" * 128},
                "revision": None,
            }
        ),
    )

    with pytest.raises(ReleaseVerificationError, match="pending job record exceeds"), storage.transaction():
        pytest.fail("oversized pending record must not be replayed")

    assert storage.snapshot() == {}


def test_lease_monitor_records_renewal_failure_before_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = Mock()
    lease.renew.side_effect = RuntimeError("lease lost")
    monitor = record_storage._LeaseMonitor(lease, started=100, timeout=1)
    monkeypatch.setattr(monitor._stop, "wait", Mock(return_value=False))
    monkeypatch.setattr(record_storage.time, "monotonic", lambda: 101)

    monitor._renew_until_stopped()

    lease.renew.assert_called_once_with(timeout=1)
    with pytest.raises(RuntimeError, match="lease lost"):
        monitor.check()


def test_lease_monitor_extends_deadline_only_after_successful_renewal(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = Mock()
    monitor = record_storage._LeaseMonitor(lease, started=0, timeout=1)
    monkeypatch.setattr(monitor._stop, "wait", Mock(side_effect=[False, True]))
    monkeypatch.setattr(record_storage.time, "monotonic", lambda: 100)

    monitor._renew_until_stopped()
    monitor.check()

    monkeypatch.setattr(record_storage.time, "monotonic", lambda: 160)
    with pytest.raises(RuntimeError, match="lease lost"):
        monitor.check()
