"""Storage-native per-job JSON records for curated release state.

Each job is stored as jobs/<job_id>.json beneath an explicit local root or Azure
prefix. The collection is rebuilt by scanning records; no index or database exists.
"""

from __future__ import annotations

import copy
import math
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from physical_ai_curation.release.canonical import canonical_json_bytes, normalize_release_path, parse_strict_json
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.store import (
    CHUNK_BYTES,
    LocalReleaseStore,
    _open_child_directory,
    _open_child_file,
)

SCHEMA_VERSION = "curated-release-job/v1"
_JOBS_DIR = "jobs"
_COORDINATOR = "coordinator.lock"
_RECORD_NAME = re.compile(r"([0-9a-f]{32})\.json")
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_CONTAINER_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])")
_LIMIT_KEYS = ("timeout_seconds", "max_record_bytes", "max_total_bytes", "max_jobs")
_MAX_TIMEOUT_SECONDS = 300
_MAX_RECORD_BYTES = 16 * 1024**2
_MAX_TOTAL_BYTES = 64 * 1024**2
_MAX_JOBS = 4096
_LEASE_SECONDS = 60
_RENEW_SECONDS = 20
_LOCAL_POLL_SECONDS = 0.025
_BLOB_POLL_SECONDS = 0.5


def _check_int(name: str, value: object, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [1, {maximum}]")


@dataclass(frozen=True)
class RecordLimits:
    """Validated contention timeout and size bounds shared by both backends."""

    timeout_seconds: float = 30.0
    max_record_bytes: int = _MAX_RECORD_BYTES
    max_total_bytes: int = _MAX_TOTAL_BYTES
    max_jobs: int = _MAX_JOBS

    def __post_init__(self) -> None:
        timeout = self.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not math.isfinite(timeout):
            raise ValueError("timeout_seconds must be a finite number")
        if not 0 < timeout <= _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be within (0, {_MAX_TIMEOUT_SECONDS}]")
        _check_int("max_record_bytes", self.max_record_bytes, _MAX_RECORD_BYTES)
        _check_int("max_total_bytes", self.max_total_bytes, _MAX_TOTAL_BYTES)
        _check_int("max_jobs", self.max_jobs, _MAX_JOBS)
        if self.max_record_bytes > self.max_total_bytes:
            raise ValueError("max_record_bytes must not exceed max_total_bytes")


class RecordStorage(Protocol):
    """Transactional collection of per-job JSON records keyed by job ID."""

    def transaction(self) -> AbstractContextManager[dict[str, dict[str, Any]]]: ...

    def snapshot(self) -> dict[str, dict[str, Any]]: ...

    def configuration(self) -> dict[str, Any]: ...


def _decode_record(raw: bytes, key: str) -> dict[str, Any]:
    document = parse_strict_json(raw)
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != SCHEMA_VERSION
        or document.get("job_id") != key
    ):
        raise ReleaseVerificationError(f"invalid job record: {key}")
    return document


def _admit(limits: RecordLimits, count: int, total: int, size: int) -> int:
    """Return the running byte total after admitting one record."""
    if count >= limits.max_jobs:
        raise ReleaseVerificationError("job record count exceeds max_jobs")
    if size > limits.max_record_bytes:
        raise ReleaseVerificationError("job record exceeds max_record_bytes")
    if total + size > limits.max_total_bytes:
        raise ReleaseVerificationError("job records exceed max_total_bytes")
    return total + size


def _plan_commit(
    limits: RecordLimits,
    records: Mapping[str, dict[str, Any]],
    sizes: Mapping[str, int],
    working: Mapping[str, Any],
) -> tuple[str, bytes] | None:
    """Return the single changed record as (job_id, canonical bytes), or None when unchanged."""
    if not records.keys() <= working.keys():
        raise ReleaseVerificationError("job record deletion is not supported")
    changed = [
        key
        for key, document in working.items()
        if key not in records or canonical_json_bytes(document) != canonical_json_bytes(records[key])
    ]
    if len(changed) > 1:
        raise ReleaseVerificationError("a transaction may change at most one job record")
    if not changed:
        return None
    key = changed[0]
    if not isinstance(key, str) or not _JOB_ID.fullmatch(key):
        raise ReleaseVerificationError(f"job ID must be 32 lowercase hex characters: {key!r}")
    document = working[key]
    if (
        not isinstance(document, dict)
        or document.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION
        or document.get("job_id", key) != key
    ):
        raise ReleaseVerificationError(f"job record has invalid schema_version or job_id: {key}")
    body = canonical_json_bytes({**document, "schema_version": SCHEMA_VERSION, "job_id": key})
    if len(body) > limits.max_record_bytes:
        raise ReleaseVerificationError("job record exceeds max_record_bytes")
    if key not in records and len(records) >= limits.max_jobs:
        raise ReleaseVerificationError("job record count exceeds max_jobs")
    if sum(sizes.values()) - sizes.get(key, 0) + len(body) > limits.max_total_bytes:
        raise ReleaseVerificationError("job records exceed max_total_bytes")
    return key, body


def _flock(descriptor: int, *, exclusive: bool, timeout: float) -> None:
    import fcntl

    operation = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(descriptor, operation)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("job record lock contention exceeded timeout") from None
            time.sleep(_LOCAL_POLL_SECONDS)


def _read_bounded(descriptor: int, limit: int, name: str) -> bytes:
    chunks = []
    total = 0
    while chunk := os.read(descriptor, min(CHUNK_BYTES, limit + 1 - total)):
        total += len(chunk)
        if total > limit:
            raise ReleaseVerificationError(f"job record exceeds max_record_bytes: {name}")
        chunks.append(chunk)
    return b"".join(chunks)


def _write_local(directory: int, key: str, body: bytes) -> None:
    name = f"{key}.json"
    temporary = f".{key}.{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)
        raise
    os.fsync(directory)


class LocalRecordStorage:
    """Records under root/jobs guarded by a shared OS lock on the jobs directory."""

    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float = 30.0,
        max_record_bytes: int = _MAX_RECORD_BYTES,
        max_total_bytes: int = _MAX_TOTAL_BYTES,
        max_jobs: int = _MAX_JOBS,
    ) -> None:
        self._root = Path(root)
        self._limits = RecordLimits(timeout_seconds, max_record_bytes, max_total_bytes, max_jobs)

    def configuration(self) -> dict[str, Any]:
        return {"backend": "local", "root": str(self._root), **asdict(self._limits)}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        directory = self._existing_jobs()
        if directory is None:
            return {}
        try:
            _flock(directory, exclusive=False, timeout=self._limits.timeout_seconds)
            records, _ = self._load(directory)
            return records
        finally:
            os.close(directory)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, dict[str, Any]]]:
        directory = self._create_jobs()
        try:
            _flock(directory, exclusive=True, timeout=self._limits.timeout_seconds)
            records, sizes = self._load(directory)
            working = copy.deepcopy(records)
            yield working
            planned = _plan_commit(self._limits, records, sizes, working)
            if planned is not None:
                _write_local(directory, *planned)
        finally:
            os.close(directory)

    def _create_jobs(self) -> int:
        root = LocalReleaseStore(self._root)._open_root(create=True)
        try:
            with suppress(FileExistsError):
                os.mkdir(_JOBS_DIR, dir_fd=root)
                os.fsync(root)
            return _open_child_directory(root, _JOBS_DIR)
        finally:
            os.close(root)

    def _existing_jobs(self) -> int | None:
        if not self._root.exists():
            return None
        root = LocalReleaseStore(self._root)._open_root()
        try:
            try:
                os.stat(_JOBS_DIR, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                return None
            return _open_child_directory(root, _JOBS_DIR)
        finally:
            os.close(root)

    def _load(self, directory: int) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        records = {}
        sizes = {}
        total = 0
        for name in sorted(os.listdir(directory)):
            match = _RECORD_NAME.fullmatch(name)
            if match is None:
                continue
            descriptor = _open_child_file(directory, name)
            try:
                raw = _read_bounded(descriptor, self._limits.max_record_bytes, name)
            finally:
                os.close(descriptor)
            key = match.group(1)
            total = _admit(self._limits, len(records), total, len(raw))
            records[key] = _decode_record(raw, key)
            sizes[key] = len(raw)
        return records, sizes


def _validate_account_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or "?" in value
        or "#" in value
    ):
        raise ValueError("account_url must be an https origin without credentials, query, or fragment")
    return f"https://{parts.netloc}"


def _validate_container_name(value: str) -> str:
    if not isinstance(value, str) or not _CONTAINER_NAME.fullmatch(value):
        raise ValueError("container name must be 3-63 lowercase letters, digits, or hyphens")
    return value


def _validate_prefix(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("prefix must be a string")
    prefix = value.rstrip("/")
    return normalize_release_path(prefix) if prefix else ""


def _child(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


@dataclass(frozen=True)
class BlobLocation:
    """Canonical remote Blob destination identity; constructing it never imports the Azure SDK."""

    account_url: str
    container: str
    prefix: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.account_url, str) or any(
            ord(char) <= 32 or ord(char) == 127 for char in self.account_url
        ):
            raise ValueError("account_url contains whitespace or control characters")
        origin = _validate_account_url(self.account_url)
        netloc = origin.removeprefix("https://").lower()
        if netloc.endswith(":443"):
            netloc = netloc[: -len(":443")]
        object.__setattr__(self, "account_url", f"https://{netloc}")
        object.__setattr__(self, "container", _validate_container_name(self.container))
        object.__setattr__(self, "prefix", _validate_prefix(self.prefix))

    def as_text(self) -> str:
        """Return the canonical text used for persistence, fingerprints, and binding comparisons."""
        base = f"{self.account_url}/{self.container}"
        return f"{base}/{self.prefix}" if self.prefix else base


def _blob_container_client(account_url: str, container_name: str) -> Any:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
    return service.get_container_client(container_name)


class _LeaseMonitor:
    """Renews the coordinator lease in the background and reports loss before commit."""

    def __init__(self, lease: Any, *, started: float, timeout: float) -> None:
        self._lease = lease
        self._timeout = timeout
        self._valid_until = started + _LEASE_SECONDS
        self._failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._renew_until_stopped, daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def identity(self) -> Any:
        return self._lease

    def check(self) -> None:
        if self._failed or time.monotonic() >= self._valid_until:
            raise RuntimeError("coordinator lease lost; transaction aborted")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(self._timeout)

    def release(self) -> None:
        from azure.core.exceptions import HttpResponseError

        with suppress(HttpResponseError):
            self._lease.release(timeout=self._timeout)

    def _renew_until_stopped(self) -> None:
        while not self._stop.wait(_RENEW_SECONDS):
            started = time.monotonic()
            try:
                self._lease.renew(timeout=self._timeout)
            except Exception:
                self._failed = True
                return
            self._valid_until = started + _LEASE_SECONDS


class BlobRecordStorage:
    """Records under an Azure prefix; only the coordinator blob is leased."""

    def __init__(
        self,
        container_client: Any,
        *,
        account_url: str,
        container_name: str,
        prefix: str = "",
        timeout_seconds: float = 30.0,
        max_record_bytes: int = _MAX_RECORD_BYTES,
        max_total_bytes: int = _MAX_TOTAL_BYTES,
        max_jobs: int = _MAX_JOBS,
    ) -> None:
        self._container = container_client
        self._account_url = _validate_account_url(account_url)
        self._container_name = _validate_container_name(container_name)
        self._prefix = _validate_prefix(prefix)
        self._coordinator = _child(self._prefix, _COORDINATOR)
        self._jobs_prefix = _child(self._prefix, f"{_JOBS_DIR}/")
        self._limits = RecordLimits(timeout_seconds, max_record_bytes, max_total_bytes, max_jobs)

    def configuration(self) -> dict[str, Any]:
        return {
            "backend": "blob",
            "account_url": self._account_url,
            "container": self._container_name,
            "prefix": self._prefix,
            **asdict(self._limits),
        }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        records, _, _ = self._load()
        return records

    @contextmanager
    def transaction(self) -> Iterator[dict[str, dict[str, Any]]]:
        with self._coordinator_lease() as lease:
            self._recover_pending(lease)
            records, sizes, revisions = self._load()
            working = copy.deepcopy(records)
            yield working
            lease.check()
            planned = _plan_commit(self._limits, records, sizes, working)
            if planned is not None:
                key, body = planned
                self._checkpoint(
                    lease,
                    {
                        "schema_version": "curated-release-transaction/v1",
                        "job_id": key,
                        "record": _decode_record(body, key),
                        "revision": revisions.get(key),
                    },
                )
                self._write(key, body, revisions.get(key))
                self._checkpoint(lease, None)

    def _checkpoint(self, lease: _LeaseMonitor, pending: dict[str, Any] | None) -> None:
        lease.check()
        self._container.get_blob_client(self._coordinator).upload_blob(
            canonical_json_bytes(pending) if pending is not None else b"",
            overwrite=True,
            lease=lease.identity,
            timeout=self._limits.timeout_seconds,
        )

    def _recover_pending(self, lease: _LeaseMonitor) -> None:
        stream = self._container.get_blob_client(self._coordinator).download_blob(
            lease=lease.identity,
            timeout=self._limits.timeout_seconds,
        )
        if stream.size > self._limits.max_record_bytes + 2048:
            raise ReleaseVerificationError("pending job transaction exceeds its bound")
        raw = stream.readall()
        if not raw:
            return
        pending = parse_strict_json(raw)
        if (
            not isinstance(pending, dict)
            or set(pending) != {"schema_version", "job_id", "record", "revision"}
            or pending["schema_version"] != "curated-release-transaction/v1"
            or not isinstance(pending["job_id"], str)
            or not _JOB_ID.fullmatch(pending["job_id"])
            or (pending["revision"] is not None and not isinstance(pending["revision"], str))
        ):
            raise ReleaseVerificationError("invalid pending job transaction")
        body = canonical_json_bytes(pending["record"])
        if len(body) > self._limits.max_record_bytes:
            raise ReleaseVerificationError("pending job record exceeds its bound")
        _decode_record(body, pending["job_id"])
        lease.check()
        self._write(pending["job_id"], body, pending["revision"])
        self._checkpoint(lease, None)

    @contextmanager
    def _coordinator_lease(self) -> Iterator[_LeaseMonitor]:
        from azure.core.exceptions import ResourceExistsError

        timeout = self._limits.timeout_seconds
        blob = self._container.get_blob_client(self._coordinator)
        with suppress(ResourceExistsError):
            blob.upload_blob(b"", overwrite=False, timeout=timeout)
        monitor = self._acquire(blob)
        monitor.start()
        try:
            yield monitor
        finally:
            monitor.stop()
            monitor.release()

    def _acquire(self, blob: Any) -> _LeaseMonitor:
        from azure.core.exceptions import HttpResponseError

        timeout = self._limits.timeout_seconds
        deadline = time.monotonic() + timeout
        while True:
            started = time.monotonic()
            try:
                lease = blob.acquire_lease(lease_duration=_LEASE_SECONDS, timeout=timeout)
            except HttpResponseError as error:
                if error.status_code != 409:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("coordinator lease contention exceeded timeout") from error
                time.sleep(_BLOB_POLL_SECONDS)
            else:
                return _LeaseMonitor(lease, started=started, timeout=timeout)

    def _load(self) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, str]]:
        records = {}
        sizes = {}
        revisions = {}
        total = 0
        listing = self._container.list_blobs(name_starts_with=self._jobs_prefix, timeout=self._limits.timeout_seconds)
        for item in listing:
            match = _RECORD_NAME.fullmatch(item.name.removeprefix(self._jobs_prefix))
            if match is None:
                continue
            key = match.group(1)
            total = _admit(self._limits, len(records), total, item.size)
            raw, revision = self._read(item.name, item.etag)
            records[key] = _decode_record(raw, key)
            sizes[key] = len(raw)
            revisions[key] = revision
        return records, sizes, revisions

    def _read(self, name: str, etag: str) -> tuple[bytes, str]:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError

        try:
            stream = self._container.get_blob_client(name).download_blob(
                etag=etag, match_condition=MatchConditions.IfNotModified, timeout=self._limits.timeout_seconds
            )
        except ResourceModifiedError as error:
            raise ReleaseVerificationError(f"job record changed during read: {name}") from error
        if stream.size > self._limits.max_record_bytes:
            raise ReleaseVerificationError(f"job record exceeds max_record_bytes: {name}")
        return stream.readall(), stream.properties.etag

    def _write(self, key: str, body: bytes, revision: str | None) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

        blob = self._container.get_blob_client(f"{self._jobs_prefix}{key}.json")
        timeout = self._limits.timeout_seconds
        try:
            if revision is None:
                blob.upload_blob(body, overwrite=False, timeout=timeout)
            else:
                blob.upload_blob(
                    body,
                    overwrite=True,
                    etag=revision,
                    match_condition=MatchConditions.IfNotModified,
                    timeout=timeout,
                )
        except (ResourceExistsError, ResourceModifiedError) as error:
            observed = blob.get_blob_properties(timeout=timeout)
            actual, _ = self._read(f"{self._jobs_prefix}{key}.json", observed.etag)
            if actual != body:
                raise ReleaseVerificationError(f"job record changed concurrently; write refused: {key}") from error


def storage_from_configuration(config: Mapping[str, Any]) -> RecordStorage:
    """Build storage from a configuration produced by configuration()."""
    limits = {key: config[key] for key in _LIMIT_KEYS if key in config}
    backend = config.get("backend")
    if backend == "local":
        _reject_unknown_keys(config, {"backend", "root", *_LIMIT_KEYS})
        return LocalRecordStorage(Path(config["root"]), **limits)
    if backend == "blob":
        _reject_unknown_keys(config, {"backend", "account_url", "container", "prefix", *_LIMIT_KEYS})
        account_url = _validate_account_url(config["account_url"])
        container_name = _validate_container_name(config["container"])
        prefix = _validate_prefix(config.get("prefix", ""))
        client = _blob_container_client(account_url, container_name)
        return BlobRecordStorage(
            client,
            account_url=account_url,
            container_name=container_name,
            prefix=prefix,
            **limits,
        )
    raise ValueError(f"unsupported record storage backend: {backend!r}")


def _reject_unknown_keys(config: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"unsupported record storage configuration keys: {sorted(unknown)}")
