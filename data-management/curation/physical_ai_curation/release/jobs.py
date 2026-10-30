"""Durable release job acceptance, fenced leases, capacity, effect ledger and commit arbitration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Any
from uuid import uuid4

from physical_ai_curation.release.canonical import normalize_release_path
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import MARKER_PATH, RECEIPT_PATH, REPORT_PATH
from physical_ai_curation.release.publication import PublicationAttempt, ownership_bytes, ownership_path
from physical_ai_curation.release.record_storage import BlobLocation, LocalRecordStorage, RecordLimits, RecordStorage
from physical_ai_curation.release.store import LocalReleaseStore
from physical_ai_curation.release.supervision import (
    ProcessIdentity,
    StopProof,
    SupervisedRuntime,
    bounded_seconds,
    recover_stop_proof,
)

EVENTS_NAME = "release-job-events.jsonl"
EVENT_SCHEMA = "release-job-event/v1"
SERVICE_NAME = "physical-ai-curation"
MAX_REQUEST_BYTES = 256 * 1024
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 3600
MIN_MEMORY_BYTES = 64 * 1024 * 1024
MAX_BYTES = 1 << 42
LEASE_ENV = "CURATION_RELEASE_LEASE_SECONDS"
RESERVE_ENV = "CURATION_RELEASE_RESERVE_BYTES"
MEMORY_ENV = "CURATION_RELEASE_MEMORY_BYTES"
GRACE_ENV = "CURATION_RELEASE_GRACE_SECONDS"
GUARDIAN_ENV = "CURATION_RELEASE_GUARDIAN_SECONDS"
STATE_TIMEOUT_ENV = "CURATION_RELEASE_STATE_TIMEOUT_SECONDS"
STATE_RECORD_ENV = "CURATION_RELEASE_STATE_MAX_RECORD_BYTES"
STATE_TOTAL_ENV = "CURATION_RELEASE_STATE_MAX_TOTAL_BYTES"
STATE_JOBS_ENV = "CURATION_RELEASE_STATE_MAX_JOBS"
RECOVERY_ENV = "CURATION_RELEASE_RECOVERY_SECONDS"
CLEANUP_ENV = "CURATION_RELEASE_CLEANUP_SECONDS"
CONTROL_PATHS = (RECEIPT_PATH, REPORT_PATH)
CLOSED_STATES = frozenset({"sealed", "committing", "committed"})
TERMINAL_STATES = frozenset({"committed", "cancelled"})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_EVENT_KEY = re.compile(r"[a-z][a-z_]{0,31}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
_READ_CHUNK = 1024 * 1024
BLOB_OWNER_CLEANUP_PATH = "release/cleanup-owner.json"
MAX_EVENT_PAGE = 1000
MAX_RUNTIME_RECOVERY_PAGE = 128
IO_COUNTERS = frozenset(
    {
        "storage_copy_bytes",
        "source_verification_bytes",
        "destination_verification_bytes",
        "affected_download_bytes",
        "generated_upload_bytes",
        "generated_output_bytes",
    }
)


class ReleaseJobError(ReleaseVerificationError):
    """A job transition was refused with a fixed, non-sensitive category."""


class JobNotFoundError(ReleaseJobError):
    """The job is absent or not visible to the caller's scope."""


class JobConflictError(ReleaseJobError):
    """The idempotency key or release identity is bound to different work."""


class AuthorityError(ReleaseJobError):
    """A stale fence, expired lease, unconfirmed predecessor, or unresolved effect blocks the step."""


class CapacityError(ReleaseJobError):
    """Reserved capacity or the reserved estimate cannot admit the work."""


class LedgerClosedError(ReleaseJobError):
    """Payload effects are closed for this job."""


class CancellationError(ReleaseJobError):
    """Cancellation blocks the requested step, or the step is too late to cancel."""


class EffectPending(Exception):
    """Raised by a payload writer whose dispatch outcome cannot be known yet."""


BLOB_RESERVATION_ENV = "CURATION_RELEASE_BLOB_RESERVATION_LIMIT_BYTES"


@dataclass(frozen=True)
class ReleaseRuntimeSettings:
    """Validated runtime bounds; protocol constants are not configurable."""

    lease_seconds: int = 60
    reserve_bytes: int = 1024**3
    memory_bytes: int = 8 * 1024**3
    grace_seconds: int = 5
    guardian_seconds: int = 30
    state_timeout_seconds: int = 30
    state_max_record_bytes: int = 16 * 1024**2
    state_max_total_bytes: int = 64 * 1024**2
    state_max_jobs: int = 4096
    recovery_seconds: int = 30
    cleanup_seconds: int = 30
    blob_reservation_limit_bytes: int = 1024**4

    def __post_init__(self) -> None:
        _require_int(self.lease_seconds, "lease seconds", MIN_LEASE_SECONDS, MAX_LEASE_SECONDS)
        _require_int(self.reserve_bytes, "reserve bytes", 0, MAX_BYTES)
        _require_int(self.memory_bytes, "memory bytes", MIN_MEMORY_BYTES, MAX_BYTES)
        _require_int(self.blob_reservation_limit_bytes, "blob reservation limit", 1, MAX_BYTES)
        _require_int(self.grace_seconds, "termination grace", 0, 3600)
        _require_int(self.guardian_seconds, "guardian liveness", 1, 86400)
        _require_int(self.state_timeout_seconds, "state timeout", 1, 300)
        try:
            RecordLimits(
                self.state_timeout_seconds, self.state_max_record_bytes, self.state_max_total_bytes, self.state_max_jobs
            )
        except ValueError as error:
            raise ReleaseJobError("state record limits are outside their validated ranges") from error
        _require_int(self.recovery_seconds, "recovery timeout", 0, 3600)
        _require_int(self.cleanup_seconds, "cleanup timeout", 1, 3600)
        if self.guardian_seconds < min(20, self.lease_seconds):
            raise ReleaseJobError("guardian liveness is shorter than the heartbeat safety window")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> ReleaseRuntimeSettings:
        defaults = cls()
        return cls(
            lease_seconds=_env_int(environ, LEASE_ENV, defaults.lease_seconds),
            reserve_bytes=_env_int(environ, RESERVE_ENV, defaults.reserve_bytes),
            memory_bytes=_env_int(environ, MEMORY_ENV, defaults.memory_bytes),
            grace_seconds=_env_int(environ, GRACE_ENV, defaults.grace_seconds),
            guardian_seconds=_env_int(environ, GUARDIAN_ENV, defaults.guardian_seconds),
            state_timeout_seconds=_env_int(environ, STATE_TIMEOUT_ENV, defaults.state_timeout_seconds),
            state_max_record_bytes=_env_int(environ, STATE_RECORD_ENV, defaults.state_max_record_bytes),
            state_max_total_bytes=_env_int(environ, STATE_TOTAL_ENV, defaults.state_max_total_bytes),
            state_max_jobs=_env_int(environ, STATE_JOBS_ENV, defaults.state_max_jobs),
            recovery_seconds=_env_int(environ, RECOVERY_ENV, defaults.recovery_seconds),
            cleanup_seconds=_env_int(environ, CLEANUP_ENV, defaults.cleanup_seconds),
            blob_reservation_limit_bytes=_env_int(environ, BLOB_RESERVATION_ENV, defaults.blob_reservation_limit_bytes),
        )


@dataclass(frozen=True)
class JobRequest:
    """Server-derived admission input; the frozen request bytes are persisted verbatim."""

    scope: str
    principal: str
    idempotency_key: str
    attempt: PublicationAttempt
    destination: Path | BlobLocation
    frozen_request: bytes
    reserved_bytes: int
    route: str = "local"

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.scope) or not _TOKEN.fullmatch(self.idempotency_key):
            raise ReleaseJobError("invalid scope or idempotency key")
        if not _TOKEN.fullmatch(self.route):
            raise ReleaseJobError("invalid route")
        if not isinstance(self.principal, str) or not 0 < len(self.principal) <= 512:
            raise ReleaseJobError("invalid principal")
        if not isinstance(self.attempt, PublicationAttempt) or not isinstance(self.destination, (Path, BlobLocation)):
            raise ReleaseJobError("invalid publication binding")
        expected_route = "local" if isinstance(self.destination, Path) else "blob"
        if self.route != expected_route:
            raise ReleaseJobError("destination route mismatch")
        if not isinstance(self.frozen_request, bytes) or not 0 < len(self.frozen_request) <= MAX_REQUEST_BYTES:
            raise ReleaseJobError("frozen request is empty or exceeds the bound")
        _require_int(self.reserved_bytes, "reserved bytes", 1, MAX_BYTES)


@dataclass(frozen=True)
class AcceptedJob:
    job_id: str
    replayed: bool


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    state: str
    cancel_requested: bool
    fence: int
    effects_stopped: bool
    cleanup_state: str
    capacity_state: str
    scratch_bytes: int = 0
    observed_scratch_bytes: int = 0
    peak_scratch_bytes: int = 0
    io_counters: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class JobPage:
    items: tuple[JobStatus, ...]
    next_cursor: int | None


class ReleaseJobStore:
    """Release state reconstructed from atomic local or conditional Blob job records."""

    def __init__(
        self,
        root: Path,
        *,
        settings: ReleaseRuntimeSettings | None = None,
        clock: Callable[[], float] = time.time,
        free_bytes: Callable[[Path], int] | None = None,
        storage: RecordStorage | None = None,
    ) -> None:
        self.root = root
        self.settings = settings or ReleaseRuntimeSettings()
        self.storage = storage or LocalRecordStorage(
            root,
            timeout_seconds=self.settings.state_timeout_seconds,
            max_record_bytes=self.settings.state_max_record_bytes,
            max_total_bytes=self.settings.state_max_total_bytes,
            max_jobs=self.settings.state_max_jobs,
        )
        self.events_path = root / EVENTS_NAME
        self._clock = clock
        self._free_bytes = free_bytes or _statvfs_free_bytes

    @contextmanager
    def _transaction(self) -> Iterator[dict[str, dict[str, Any]]]:
        with self.storage.transaction() as records:
            yield records

    def accept(self, request: JobRequest) -> AcceptedJob:
        principal = _digest(request.principal)
        destination = _destination_text(request.destination)
        request_digest = _fingerprint(request, destination)
        identity = _digest(f"{request.scope}\0{request.attempt.prefix}\0{destination}")
        volume = _destination_volume(request.destination)
        free = self._free_bytes(_existing_ancestor(Path(destination))) if isinstance(request.destination, Path) else 0
        with self._transaction() as conn:
            same_key = next(
                (
                    job
                    for job in conn.values()
                    if (job["scope"], job["principal_digest"], job["idempotency_key"])
                    == (request.scope, principal, request.idempotency_key)
                ),
                None,
            )
            if same_key is not None:
                if same_key["request_digest"] != request_digest:
                    raise JobConflictError("idempotency key is bound to a different request")
                return AcceptedJob(same_key["job_id"], replayed=True)
            live = next(
                (job for job in conn.values() if job["logical_identity"] == identity and job["state"] != "cancelled"),
                None,
            )
            if live is not None:
                if live["principal_digest"] == principal and live["request_digest"] == request_digest:
                    return AcceptedJob(live["job_id"], replayed=True)
                raise JobConflictError("release identity is already claimed")
            used = self._reserved_on(conn, volume)
            if isinstance(request.destination, BlobLocation):
                available = self.settings.blob_reservation_limit_bytes
            else:
                available = free - self.settings.reserve_bytes
            if request.reserved_bytes > available - used:
                raise CapacityError("insufficient capacity for the reserved estimate")
            if any(job["attempt_key"] == request.attempt.key for job in conn.values()):
                raise JobConflictError("publication attempt is already bound")
            job_id = uuid4().hex
            conn[job_id] = {
                "schema_version": "curated-release-job/v1",
                "job_id": job_id,
                "scope": request.scope,
                "principal_digest": principal,
                "idempotency_key": request.idempotency_key,
                "request_digest": request_digest,
                "logical_identity": identity,
                "attempt_prefix": request.attempt.prefix,
                "attempt_key": request.attempt.key,
                "destination": destination,
                "route": request.route,
                "volume_key": volume,
                "reserved_bytes": request.reserved_bytes,
                "frozen_request": base64.b64encode(request.frozen_request).decode("ascii"),
                "state": "accepted",
                "cancel_requested": False,
                "cleanup_state": "none",
                "fence": 0,
                "stopped_fence": 0,
                "lease_owner": None,
                "lease_expires": None,
                "commit_attempt": None,
                "commit_digest": None,
                "scratch_bytes": 0,
                "observed_scratch_bytes": 0,
                "peak_scratch_bytes": 0,
                "reservation": {"volume_key": volume, "bytes": request.reserved_bytes, "status": "reserved"},
                "scratch_reservation": None,
                "workspace": None,
                "effects": {},
                "controls": {},
                "runtimes": {},
                "temporaries": [],
                "io_counters": {},
                "io_observations": {},
                "events": [],
            }
            self._emit(conn, job_id, "job.accepted", "info", {"reserved_bytes": request.reserved_bytes})
            return AcceptedJob(job_id, replayed=False)

    def get(self, job_id: str, *, scope: str, principal: str) -> JobStatus:
        with self._transaction() as conn:
            row = self._owned(conn, job_id, scope, principal)
            return _status(conn, row)

    def page(self, *, scope: str, principal: str, cursor: int = 0, limit: int = 50) -> JobPage:
        """Return an initiator-scoped bounded page ordered by durable acceptance sequence."""
        _require_int(cursor, "job cursor", 0, (1 << 63) - 1)
        _require_int(limit, "job page size", 1, 100)
        with self._transaction() as conn:
            rows = sorted(
                (
                    job
                    for job in conn.values()
                    if job["scope"] == scope
                    and job["principal_digest"] == _digest(principal)
                    and job["events"][0]["seq"] > cursor
                ),
                key=lambda job: job["events"][0]["seq"],
            )
            selected = rows[:limit]
            return JobPage(
                tuple(_status(conn, row) for row in selected),
                selected[-1]["events"][0]["seq"] if len(rows) > limit else None,
            )

    def frozen_request(self, job_id: str) -> bytes:
        with self._transaction() as conn:
            return base64.b64decode(self._job(conn, job_id)["frozen_request"], validate=True)

    def validate_execution_binding(
        self,
        job_id: str,
        fence: int,
        destination: Path | BlobLocation,
        attempt: PublicationAttempt,
        *,
        route: str,
        recovery: bool = False,
        cancellation: bool = False,
    ) -> None:
        """Check the worker composition against the durable accepted publication binding."""
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=recovery)
            if recovery and self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            if (
                row["destination"] != _destination_text(destination)
                or row["attempt_prefix"] != attempt.prefix
                or row["attempt_key"] != attempt.key
                or row["route"] != route
            ):
                raise AuthorityError("execution does not match the accepted destination binding")
            if cancellation and (not recovery or not row["cancel_requested"] or row["state"] != "cancelling"):
                raise AuthorityError("cancellation recovery is not authorized")
            if row["cancel_requested"] and not cancellation:
                raise CancellationError("cancellation was requested")

    def claim(self, job_id: str, worker: str) -> int:
        if not _TOKEN.fullmatch(worker):
            raise ReleaseJobError("invalid worker identity")
        with self._transaction() as conn:
            row = self._job(conn, job_id)
            if row["state"] in TERMINAL_STATES:
                raise AuthorityError("job is terminal")
            now = self._clock()
            if row["lease_expires"] is not None and row["lease_expires"] > now:
                raise AuthorityError("lease is held")
            fence = row["fence"] + 1
            state = "running" if row["state"] == "accepted" else row["state"]
            row.update(fence=fence, lease_owner=worker, lease_expires=now + self.settings.lease_seconds, state=state)
            self._emit(conn, job_id, "lease.claimed", "info", {"fence": fence})
            return fence

    def heartbeat(self, job_id: str, fence: int) -> bool:
        """Renew the lease and report whether cancellation has been requested."""
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            row["lease_expires"] = self._clock() + self.settings.lease_seconds
            return bool(row["cancel_requested"])

    def reserve_scratch(self, job_id: str, fence: int, length: int) -> None:
        """Charge a bounded sequential native workspace against this job's shared-volume reservation."""
        _require_int(length, "scratch bytes", 1, MAX_BYTES)
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence)
            if row["state"] != "running" or row["cancel_requested"] or row["scratch_bytes"]:
                raise AuthorityError("native scratch is not available")
            volume = _volume_key(self.root)
            payload = sum(effect["length"] for effect in row["effects"].values())
            if volume == row["volume_key"] and payload + length > row["reserved_bytes"]:
                raise CapacityError("native scratch exceeds the durable reservation")
            if volume != row["volume_key"]:
                available = self._free_bytes(_existing_ancestor(self.root)) - self._reserved_on(conn, volume)
                if length > available - self.settings.reserve_bytes:
                    raise CapacityError("insufficient independently reserved scratch capacity")
                row["scratch_reservation"] = {"volume_key": volume, "bytes": length, "status": "reserved"}
            row["scratch_bytes"] = length

    def record_scratch_usage(self, job_id: str, fence: int, length: int) -> None:
        """Record measured private-workspace bytes separately from the admitted bound."""
        _require_int(length, "observed scratch bytes", 0, MAX_BYTES)
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            if length > row["scratch_bytes"]:
                raise CapacityError("observed scratch exceeds its admission")
            row.update(observed_scratch_bytes=length, peak_scratch_bytes=max(row["peak_scratch_bytes"], length))

    def record_io(
        self, job_id: str, fence: int, operation_id: str, counters: Mapping[str, int], *, duration_ms: int = 0
    ) -> None:
        """Record completed observed I/O once; partial or uncertain effects remain separate obligations."""
        if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{64}", operation_id) is None:
            raise ReleaseJobError("I/O operation identity is invalid")
        if not counters or set(counters) - IO_COUNTERS:
            raise ReleaseJobError("I/O counter category is invalid")
        _require_int(duration_ms, "observed duration", 0, MAX_BYTES)
        for value in counters.values():
            _require_int(value, "observed bytes", 0, MAX_BYTES)
        observation = {"counters": dict(counters), "duration_ms": duration_ms}
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            existing = row["io_observations"].get(operation_id)
            if existing is not None:
                if existing != observation:
                    raise JobConflictError("I/O observation conflicts with its recorded identity")
                return
            for name, value in counters.items():
                row["io_counters"][name] = row["io_counters"].get(name, 0) + value
            row["io_observations"][operation_id] = observation
            self._emit(conn, job_id, "io.observed", "info", {"duration_ms": duration_ms})

    def release_scratch(self, job_id: str, fence: int) -> None:
        """Release scratch only under current ownership and after registered native effects stopped."""
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            workspace = row["workspace"]
            if workspace is not None and workspace["status"] != "removed":
                raise AuthorityError("private scratch ownership is unresolved")
            row.update(scratch_bytes=0, observed_scratch_bytes=0)
            if row["scratch_reservation"] is not None:
                row["scratch_reservation"]["status"] = "released"

    def authorize_scratch_cleanup(self, job_id: str, fence: int) -> None:
        """Refuse private-workspace removal while any registered writer is unresolved."""
        with self._transaction() as conn:
            self._lease(conn, job_id, fence)
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved; scratch is retained")

    def create_scratch_workspace(self, job_id: str, fence: int, length: int) -> Path:
        """Persist creator identity before a private workspace can receive any bytes."""
        self.reserve_scratch(job_id, fence, length)
        name = uuid4().hex
        owner = ProcessIdentity.of(os.getpid()).to_dict()
        with self._transaction() as conn:
            self._lease(conn, job_id, fence)["workspace"] = {
                "fence": fence,
                "name": name,
                "owner": owner,
                "device": None,
                "inode": None,
                "status": "planned",
            }
        directory = self.root.resolve() / "scratch" / job_id / name
        descriptor = _open_root_nofollow(self.root.resolve())
        try:
            for segment in ("scratch", job_id, name):
                try:
                    os.mkdir(segment, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    if segment == name:
                        raise AuthorityError("private workspace name collided") from None
                child = os.open(segment, _DIRECTORY_FLAGS, dir_fd=descriptor)
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = child
            identity = os.fstat(descriptor)
            with self._transaction() as conn:
                workspace = self._lease(conn, job_id, fence)["workspace"]
                if workspace["name"] != name or workspace["fence"] != fence:
                    raise AuthorityError("workspace creation authority changed")
                workspace.update(device=identity.st_dev, inode=identity.st_ino, status="present")
        finally:
            os.close(descriptor)
        return directory

    def cleanup_scratch_workspace(self, job_id: str, fence: int, *, recover: bool = False) -> None:
        """Remove only a recorded inode after stopped children and creator authority are established."""
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved; scratch is retained")
            workspace = row["workspace"]
            if workspace is None or workspace["status"] == "removed":
                return
            owner = ProcessIdentity.from_dict(workspace["owner"])
            if recover:
                if owner.boot_id != ProcessIdentity.of(os.getpid()).boot_id:
                    raise AuthorityError("scratch creator host/boot is not independently observable")
                if not owner.has_exited():
                    raise AuthorityError("scratch creator is not independently stopped")
            elif workspace["fence"] != fence or owner != ProcessIdentity.of(os.getpid()):
                raise AuthorityError("private workspace belongs to another creator")
            if workspace["status"] != "present":
                raise AuthorityError("workspace creation outcome is unresolved")
            parent = self.root.resolve() / "scratch" / job_id
            parent_fd = _open_root_nofollow(parent)
            try:
                try:
                    descriptor = os.open(workspace["name"], _DIRECTORY_FLAGS, dir_fd=parent_fd)
                except FileNotFoundError:
                    descriptor = None
                if descriptor is not None:
                    try:
                        identity = os.fstat(descriptor)
                        if (identity.st_dev, identity.st_ino) != (workspace["device"], workspace["inode"]):
                            raise AuthorityError("private workspace identity changed")
                        _clear_scratch_directory(
                            descriptor, time.monotonic() + self.settings.cleanup_seconds, [16384], 0
                        )
                        current = os.stat(workspace["name"], dir_fd=parent_fd, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                            raise AuthorityError("private workspace identity changed")
                        os.rmdir(workspace["name"], dir_fd=parent_fd)
                    finally:
                        os.close(descriptor)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            if row["fence"] != fence:
                raise AuthorityError("scratch cleanup authority changed")
            workspace["status"] = "removed"
            self._emit(conn, job_id, "scratch.removed", "info", {"fence": fence})

    def confirm_stopped(self, job_id: str, fence: int) -> None:
        """Record that every process tree for this fence has been terminated and reaped."""
        with self._transaction() as conn:
            row = self._job(conn, job_id)
            if not 0 < fence <= row["fence"]:
                raise AuthorityError("unknown fence")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            if fence > row["stopped_fence"]:
                row["stopped_fence"] = fence
                self._emit(conn, job_id, "effects.stopped", "info", {"fence": fence})

    def runtime_proof_path(
        self, job_id: str, fence: int, *, effect_path: str | None = None, reconciliation: bool = False
    ) -> Path:
        """Allocate a unique internal proof name for one authorized runtime."""
        with self._transaction() as conn:
            if reconciliation:
                self._authorize_reconciliation_runtime(conn, job_id, fence, effect_path)
            else:
                self._authorize_runtime(conn, job_id, fence, effect_path)
        directory = self.root.resolve() / "runtimes" / job_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for parent in (self.root, directory.parent):
            descriptor = os.open(parent, _DIRECTORY_FLAGS)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return directory / f"{fence}-{uuid4().hex}.json"

    def register_runtime(
        self,
        job_id: str,
        fence: int,
        runtime: SupervisedRuntime,
        *,
        effect_path: str | None = None,
        reconciliation: bool = False,
    ) -> None:
        """Persist guardian/leader identity while the native start gate is still closed."""
        expected = self.root.resolve() / "runtimes" / job_id
        if runtime.proof_path is None or runtime.proof_path.parent != expected:
            raise AuthorityError("runtime proof destination is not confined")
        if not re.fullmatch(rf"{fence}-[0-9a-f]{{32}}\.json", runtime.proof_path.name):
            raise AuthorityError("runtime proof identity is invalid")
        encoded = json.dumps(runtime.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._transaction() as conn:
            row = (
                self._authorize_reconciliation_runtime(conn, job_id, fence, effect_path)
                if reconciliation
                else self._authorize_runtime(conn, job_id, fence, effect_path)
            )
            allowed = (
                row["state"] == "running"
                or (row["state"] == "sealed" and effect_path in CONTROL_PATHS)
                or (row["state"] == "committing" and effect_path == MARKER_PATH)
            )
            if not reconciliation and (not allowed or row["cancel_requested"]):
                raise AuthorityError("runtime start is not authorized")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime is already active")
            identity = _digest(encoded)
            if identity in row["runtimes"]:
                raise AuthorityError("runtime identity is already registered")
            row["runtimes"][identity] = {
                "fence": fence,
                "identity": identity,
                "runtime": runtime.to_dict(),
                "stopped": False,
            }
            row["stopped_fence"] = min(row["stopped_fence"], fence - 1)
            self._emit(conn, job_id, "runtime.registered", "info", {"fence": fence})

    def recover_runtimes(self, job_id: str, fence: int, *, timeout_seconds: float | None = None) -> None:
        """Recover only guardian-issued proofs; absent evidence leaves ownership and capacity unresolved."""
        timeout = self.settings.recovery_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + bounded_seconds(timeout, low=0, high=3600, name="runtime recovery budget")
        with self._transaction() as conn:
            job = self._job(conn, job_id)
            if not 0 < fence <= job["fence"]:
                raise AuthorityError("unknown fence")
            records = sorted(
                (record for record in job["runtimes"].values() if record["fence"] <= fence and not record["stopped"]),
                key=lambda record: (record["fence"], record["identity"]),
            )[:MAX_RUNTIME_RECOVERY_PAGE]
        for record in records:
            runtime = SupervisedRuntime.from_dict(record["runtime"])
            recover_stop_proof(runtime, timeout_seconds=max(0.0, deadline - time.monotonic()))
            with self._transaction() as conn:
                self._job(conn, job_id)["runtimes"][record["identity"]]["stopped"] = True
                self._emit(conn, job_id, "runtime.stopped", "info", {"fence": record["fence"]})

    def confirm_runtime_stopped(self, job_id: str, fence: int, proof: StopProof) -> None:
        """Independently recover the persisted proof before accepting a live worker's notification."""
        with self._transaction() as conn:
            records = [record for record in self._job(conn, job_id)["runtimes"].values() if record["fence"] == fence]
        for record in records:
            runtime = SupervisedRuntime.from_dict(record["runtime"])
            if runtime.guardian == proof.guardian and runtime.leader == proof.leader:
                if recover_stop_proof(runtime, timeout_seconds=0) != proof:
                    raise AuthorityError("runtime stop proof does not match")
                self.recover_runtimes(job_id, fence, timeout_seconds=0)
                return
        raise AuthorityError("runtime stop proof is not registered")

    def _active_runtimes(self, conn: Mapping[str, dict[str, Any]], job_id: str, fence: int) -> int:
        return sum(
            record["fence"] <= fence and not record["stopped"]
            for record in self._job(conn, job_id)["runtimes"].values()
        )

    def request_cancel(self, job_id: str, *, scope: str, principal: str) -> JobStatus:
        with self._transaction() as conn:
            row = self._owned(conn, job_id, scope, principal)
            if row["state"] in ("committing", "committed"):
                raise CancellationError("commit was already chosen; cancellation is too late")
            if row["state"] != "cancelled" and not row["cancel_requested"]:
                row.update(cancel_requested=True, state="cancelling")
                self._emit(conn, job_id, "cancel.requested", "info", {})
            return _status(conn, self._job(conn, job_id))

    def settle_cancellation(self, job_id: str, fence: int, *, output_removed: bool) -> JobStatus:
        """Release capacity only after stopped effects and removed owned output; otherwise retain it visibly."""
        with self._transaction() as conn:
            row = self._job(conn, job_id)
            if not row["cancel_requested"]:
                raise CancellationError("cancellation was not requested")
            if row["fence"] != fence:
                raise AuthorityError("stale worker fence")
            if row["fence"]:
                self._lease(conn, job_id, fence)
                if row["stopped_fence"] < row["fence"]:
                    raise AuthorityError("effects are not confirmed stopped")
            if self._open_effects(conn, job_id):
                raise AuthorityError("effects are still dispatched or unresolved")
            if output_removed and row["scratch_bytes"]:
                raise AuthorityError("scratch cleanup is unresolved")
            if output_removed:
                row["reservation"]["status"] = "released"
                row.update(state="cancelled", cleanup_state="completed")
                self._emit(conn, job_id, "cleanup.completed", "info", {})
            else:
                if row["reservation"]["status"] == "reserved":
                    row["reservation"]["status"] = "retained"
                row["cleanup_state"] = "retained"
                self._emit(conn, job_id, "cleanup.retained", "warning", {"retained_bytes": row["reservation"]["bytes"]})
            return _status(conn, self._job(conn, job_id))

    def seal(self, job_id: str, fence: int, controls: Mapping[str, tuple[int, str]]) -> None:
        """Close payload writers and journal the exact receipt and report identities."""
        if set(controls) != set(CONTROL_PATHS):
            raise ReleaseJobError("controls must be exactly the receipt and report")
        for length, digest in controls.values():
            _require_int(length, "control length", 0, MAX_BYTES)
            if not _DIGEST.fullmatch(digest):
                raise ReleaseJobError("control digest must be lowercase SHA-256")
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence)
            if row["cancel_requested"]:
                raise CancellationError("cancellation was requested before seal")
            if row["state"] == "sealed":
                if self._journal(conn, job_id) != dict(controls):
                    raise JobConflictError("ledger is sealed with different controls")
                return
            if row["state"] != "running":
                raise AuthorityError("job is not sealable")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            if row["scratch_bytes"]:
                raise AuthorityError("native scratch is still retained")
            if self._open_effects(conn, job_id):
                raise AuthorityError("payload effects are unresolved")
            row["controls"] = {
                path: {"length": length, "digest": digest} for path, (length, digest) in controls.items()
            }
            row["state"] = "sealed"
            self._emit(conn, job_id, "ledger.sealed", "info", {"fence": fence})

    def begin_effect(
        self, job_id: str, fence: int, attempt: PublicationAttempt, path: str, length: int, digest: str
    ) -> bool:
        """Persist intent before dispatch; return True when an acknowledged identical effect exists."""
        if path in (MARKER_PATH, BLOB_OWNER_CLEANUP_PATH):
            raise LedgerClosedError("marker requires a commit-bound marker intent")
        _require_int(length, "effect length", 0, MAX_BYTES)
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence)
            if row["attempt_prefix"] != attempt.prefix or row["attempt_key"] != attempt.key:
                raise AuthorityError("attempt is not owned by this job")
            if row["cancel_requested"]:
                raise CancellationError("cancellation was requested")
            control = row["controls"].get(path)
            if row["state"] in CLOSED_STATES and control is None:
                raise LedgerClosedError("payload ledger is closed")
            if control is not None and (control["length"], control["digest"]) != (length, digest):
                raise AuthorityError("control identity conflict")
            existing = row["effects"].get(path)
            if existing is not None:
                if (existing["length"], existing["digest"]) != (length, digest):
                    raise AuthorityError("effect identity conflict")
                if existing["status"] == "acknowledged":
                    return True
                raise AuthorityError("effect outcome is unresolved")
            total = sum(effect["length"] for effect in row["effects"].values())
            shared_scratch = row["scratch_bytes"] if _volume_key(self.root) == row["volume_key"] else 0
            if total + length + shared_scratch > row["reserved_bytes"]:
                raise CapacityError("effects exceed the reserved estimate")
            row["effects"][path] = {
                "path": path,
                "length": length,
                "digest": digest,
                "fence": fence,
                "status": "dispatched",
                "copy_id": None,
            }
            return False

    def register_local_temporary(self, job_id: str, fence: int, path: str, name: str, identity: os.stat_result) -> None:
        """Record an existing private inode from its registered writer before the first byte."""
        path = normalize_release_path(path)
        if (
            re.fullmatch(r"\.curation-copy-[0-9a-f]{32}\.partial", name) is None
            or not S_ISREG(identity.st_mode)
            or identity.st_size != 0
        ):
            raise AuthorityError("temporary ownership identity is invalid")
        caller = ProcessIdentity.of(os.getpid())
        with self._transaction() as conn:
            row = self._job(conn, job_id)
            effect = row["effects"].get(path)
            runtimes = [item for item in row["runtimes"].values() if item["fence"] == fence and not item["stopped"]]
            if (
                effect is None
                or effect["fence"] != fence
                or effect["status"] not in ("dispatched", "uncertain")
                or not any(SupervisedRuntime.from_dict(item["runtime"]).leader == caller for item in runtimes)
            ):
                raise AuthorityError("temporary creator is not a recorded effect writer")
            if any(item["path"] == path and item["name"] == name for item in row["temporaries"]):
                raise AuthorityError("temporary ownership is already recorded")
            row["temporaries"].append(
                {
                    "path": path,
                    "name": name,
                    "fence": fence,
                    "device": identity.st_dev,
                    "inode": identity.st_ino,
                    "removed": False,
                }
            )
            self._emit(conn, job_id, "local.temporary_owned", "info", {"fence": fence})

    def reconcile_local_outputs(self, job_id: str, fence: int) -> int:
        """Inspect at most 100 stopped local effects; never trust a client-supplied outcome."""
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            if (
                row["route"] != "local"
                or row["state"] not in ("running", "cancelling", "sealed")
                or self._active_runtimes(conn, job_id, fence)
            ):
                raise AuthorityError("local reconciliation is not authorized")
            root = Path(row["destination"]) / row["attempt_prefix"]
            effects = sorted(
                (effect for effect in row["effects"].values() if effect["status"] != "acknowledged"),
                key=lambda effect: effect["path"],
            )[:100]
        from physical_ai_curation.release.store import LocalReleaseStore

        destination = LocalReleaseStore(root)
        if os.path.lexists(root / MARKER_PATH):
            raise AuthorityError("published output is immutable")
        deadline = time.monotonic() + self.settings.recovery_seconds
        for effect in effects:
            if time.monotonic() >= deadline:
                raise AuthorityError("local reconciliation budget exhausted")
            with self._transaction() as conn:
                current = self._lease(conn, job_id, fence)
                temporaries = [
                    item for item in current["temporaries"] if item["path"] == effect["path"] and not item["removed"]
                ]
                if os.path.lexists(root):
                    descriptor = _open_root_nofollow(root)
                    try:
                        for temporary in temporaries:
                            relative = str(PurePosixPath(effect["path"]).parent / temporary["name"])
                            if not _remove_verified(
                                descriptor, relative, 0, "", expected_inode=(temporary["device"], temporary["inode"])
                            ):
                                raise AuthorityError("temporary output ownership is unresolved")
                    finally:
                        os.close(descriptor)
                for temporary in temporaries:
                    temporary["removed"] = True
            if os.path.lexists(root / effect["path"]):
                digest = hashlib.sha256()
                length = 0
                for chunk in destination.iter_bytes(effect["path"], chunk_size=_READ_CHUNK):
                    if time.monotonic() >= deadline:
                        raise AuthorityError("local reconciliation budget exhausted")
                    digest.update(chunk)
                    length += len(chunk)
                    if length > effect["length"]:
                        raise JobConflictError("observed local output conflicts with its intent")
                observed = (
                    "complete" if (length, digest.hexdigest()) == (effect["length"], effect["digest"]) else "conflict"
                )
            else:
                observed = "absent"
            self.reconcile_effect(
                job_id, fence, effect["path"], observed=observed, length=effect["length"], digest=effect["digest"]
            )
        with self._transaction() as conn:
            self._lease(conn, job_id, fence)
            return self._open_effects(conn, job_id)

    def finish_effect(self, job_id: str, fence: int, path: str, *, ok: bool) -> None:
        """Record the outcome of a dispatched effect; failures stay unresolved, never acknowledged."""
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            effect = row["effects"].get(path)
            if effect is None or effect["fence"] != fence or effect["status"] != "dispatched":
                raise AuthorityError("stale or unknown effect finish refused")
            effect["status"] = "acknowledged" if ok else "uncertain"

    def reconcile_effect(self, job_id: str, fence: int, path: str, *, observed: str, length: int, digest: str) -> str:
        """Resolve an unresolved effect from an observation of the output; pending keeps it blocked."""
        if observed not in ("absent", "complete", "pending", "conflict"):
            raise ReleaseJobError("reconciliation observation is invalid")
        _require_int(length, "effect length", 0, MAX_BYTES)
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            effect = row["effects"].get(path)
            if effect is None or effect["status"] == "acknowledged":
                raise AuthorityError("no unresolved effect to reconcile")
            if (effect["length"], effect["digest"]) != (length, digest):
                raise AuthorityError("effect identity does not match the recorded effect")
            if observed == "pending":
                raise AuthorityError("effect is still pending")
            if observed == "conflict":
                raise JobConflictError("observed content conflicts with the recorded effect")
            if observed == "absent":
                del row["effects"][path]
                outcome = "abandoned"
            else:
                effect.update(status="acknowledged", fence=fence)
                outcome = "acknowledged"
            self._emit(conn, job_id, "effect.reconciled", "info", {"outcome": outcome})
            return outcome

    def begin_marker_effect(
        self, job_id: str, fence: int, attempt: PublicationAttempt, manifest_digest: str, length: int, digest: str
    ) -> bool:
        """Persist the marker intent under the chosen commit before dispatch; True when already acknowledged."""
        _require_int(length, "marker length", 0, MAX_BYTES)
        if not _DIGEST.fullmatch(digest):
            raise ReleaseJobError("marker digest must be lowercase SHA-256")
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence)
            if row["attempt_prefix"] != attempt.prefix or row["attempt_key"] != attempt.key:
                raise AuthorityError("attempt is not owned by this job")
            if row["state"] != "committing":
                raise AuthorityError("marker requires a committing publication")
            if row["commit_attempt"] != attempt.prefix or row["commit_digest"] != manifest_digest:
                raise JobConflictError("commit decision belongs to another manifest")
            if row["cancel_requested"]:
                raise CancellationError("cancellation was requested")
            existing = row["effects"].get(MARKER_PATH)
            if existing is not None:
                if (existing["length"], existing["digest"]) != (length, digest):
                    raise AuthorityError("marker identity conflict")
                if existing["status"] == "acknowledged":
                    return True
                raise AuthorityError("effect outcome is unresolved")
            if self._open_effects(conn, job_id) or self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("effects or runtimes are unresolved")
            total = sum(effect["length"] for effect in row["effects"].values())
            shared_scratch = row["scratch_bytes"] if _volume_key(self.root) == row["volume_key"] else 0
            if total + length + shared_scratch > row["reserved_bytes"]:
                raise CapacityError("effects exceed the reserved estimate")
            row["effects"][MARKER_PATH] = {
                "path": MARKER_PATH,
                "length": length,
                "digest": digest,
                "fence": fence,
                "status": "dispatched",
                "copy_id": None,
            }
            self._emit(conn, job_id, "marker.dispatched", "info", {"fence": fence})
            return False

    def marker_intent(self, job_id: str, fence: int) -> dict[str, Any] | None:
        """Return the journaled marker intent after authorizing the fence, or None when none was begun."""
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            effect = row["effects"].get(MARKER_PATH)
            return None if effect is None else dict(effect)

    def authorize_marker_writer(
        self, job_id: str, fence: int, attempt: PublicationAttempt, manifest_digest: str, length: int, digest: str
    ) -> None:
        """Re-check the commit-bound marker intent inside the registered writer before it creates bytes."""
        with self._transaction() as conn:
            row = self._authorize_runtime(conn, job_id, fence, MARKER_PATH)
            if (
                row["state"] != "committing"
                or row["attempt_prefix"] != attempt.prefix
                or row["attempt_key"] != attempt.key
                or row["commit_attempt"] != attempt.prefix
                or row["commit_digest"] != manifest_digest
            ):
                raise AuthorityError("marker writer is not authorized by the commit decision")
            effect = row["effects"][MARKER_PATH]
            if (effect["length"], effect["digest"]) != (length, digest):
                raise AuthorityError("marker identity does not match the recorded intent")

    def authorize_blob_writer(
        self,
        job_id: str,
        fence: int,
        attempt: PublicationAttempt,
        path: str,
        length: int,
        digest: str,
        *,
        destination: str,
        reconciliation: bool = False,
        copy_id: str | None = None,
    ) -> None:
        """Re-check a dispatched Blob copy inside its registered child before any remote mutation."""
        path = normalize_release_path(path)
        caller = ProcessIdentity.of(os.getpid())
        with self._transaction() as conn:
            row = (
                self._authorize_reconciliation_runtime(conn, job_id, fence, path)
                if reconciliation
                else self._authorize_runtime(conn, job_id, fence, path)
            )
            if (
                row["route"] != "blob"
                or row["destination"] != destination
                or row["attempt_key"] != attempt.key
                or row["attempt_prefix"] != attempt.prefix
            ):
                raise AuthorityError("blob writer is not bound to this job")
            effect = row["effects"][path]
            if reconciliation and copy_id is not None and effect["copy_id"] != copy_id:
                raise AuthorityError("cleanup copy identity differs from its journal")
            if effect["fence"] != fence or (effect["length"], effect["digest"]) != (length, digest):
                raise AuthorityError("blob identity does not match the recorded intent")
            runtimes = [item for item in row["runtimes"].values() if item["fence"] == fence and not item["stopped"]]
            if not any(SupervisedRuntime.from_dict(item["runtime"]).leader == caller for item in runtimes):
                raise AuthorityError("blob writer is not a registered runtime")

    def remove_marker_temporaries(self, job_id: str, fence: int, root: Path) -> None:
        """Remove only inode-bound marker partials; refuse unregistered or unresolved residue."""
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            if (
                row["state"] != "committing"
                or row["route"] != "local"
                or root.absolute() != Path(row["destination"]) / row["attempt_prefix"]
            ):
                raise AuthorityError("marker residue cleanup is not authorized")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            deadline = time.monotonic() + self.settings.cleanup_seconds
            known = {item["name"] for item in row["temporaries"] if item["path"] == MARKER_PATH}
            pending = [item for item in row["temporaries"] if not item["removed"]]
            if any(item["path"] != MARKER_PATH for item in pending):
                raise AuthorityError("temporary output ownership is unresolved")
            if os.path.lexists(root):
                descriptor = _open_root_nofollow(root)
                try:
                    release = os.open(str(PurePosixPath(MARKER_PATH).parent), _DIRECTORY_FLAGS, dir_fd=descriptor)
                    try:
                        with os.scandir(release) as entries:
                            for count, entry in enumerate(entries, start=1):
                                if count > 16384 or time.monotonic() >= deadline:
                                    raise AuthorityError("marker cleanup budget exhausted")
                                if (
                                    re.fullmatch(r"\.curation-copy-[0-9a-f]{32}\.partial", entry.name)
                                    and entry.name not in known
                                ):
                                    raise AuthorityError("unregistered temporary output is present")
                    finally:
                        os.close(release)
                    for item in pending:
                        if time.monotonic() >= deadline:
                            raise AuthorityError("marker cleanup budget exhausted")
                        relative = str(PurePosixPath(MARKER_PATH).parent / item["name"])
                        if not _remove_verified(
                            descriptor, relative, 0, "", expected_inode=(item["device"], item["inode"])
                        ):
                            raise AuthorityError("temporary output ownership is unresolved")
                finally:
                    os.close(descriptor)
            for item in pending:
                if item["path"] == MARKER_PATH:
                    item["removed"] = True

    def resolve_marker_effect(self, job_id: str, fence: int, *, observed: str, length: int, digest: str) -> str:
        """Resolve the marker intent from an observation; a marker is never deleted here."""
        if observed not in ("absent", "complete", "pending", "conflict"):
            raise ReleaseJobError("marker observation is invalid")
        _require_int(length, "marker length", 0, MAX_BYTES)
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            if row["state"] != "committing":
                raise AuthorityError("marker resolution requires a committing publication")
            effect = row["effects"].get(MARKER_PATH)
            if effect is None or effect["status"] == "acknowledged":
                raise AuthorityError("no unresolved marker effect")
            if (effect["length"], effect["digest"]) != (length, digest):
                raise AuthorityError("marker identity does not match the recorded intent")
            if any(item["path"] == MARKER_PATH and not item["removed"] for item in row["temporaries"]):
                raise AuthorityError("marker temporaries are not removed")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            if observed == "pending":
                raise AuthorityError("marker is still pending")
            if observed == "conflict":
                raise JobConflictError("observed marker conflicts with the recorded intent")
            if observed == "absent":
                del row["effects"][MARKER_PATH]
                outcome = "abandoned"
            else:
                effect.update(status="acknowledged", fence=fence)
                outcome = "acknowledged"
            self._emit(conn, job_id, "marker.reconciled", "info", {"outcome": outcome})
            return outcome

    def begin_reconciliation(
        self,
        job_id: str,
        fence: int,
        attempt: PublicationAttempt,
        path: str,
        length: int,
        digest: str,
        *,
        copy_id: str | None,
        cancel: bool,
    ) -> None:
        """Journal observed copy identity before any abort/delete; no client-supplied outcome is accepted."""
        if copy_id is not None and (not isinstance(copy_id, str) or not 1 <= len(copy_id) <= 512):
            raise ReleaseJobError("copy identity is invalid")
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            if (
                row["state"] not in ("running", "cancelling")
                or row["attempt_prefix"] != attempt.prefix
                or row["attempt_key"] != attempt.key
            ):
                raise AuthorityError("reconciliation is not owned by this attempt")
            if cancel and not row["cancel_requested"]:
                raise CancellationError("cancellation was not requested")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            effect = row["effects"].get(path)
            if effect is None or (effect["length"], effect["digest"]) != (length, digest):
                raise AuthorityError("reconciliation identity is not journaled")
            if effect["copy_id"] is not None and copy_id is not None and effect["copy_id"] != copy_id:
                raise JobConflictError("observed copy identity conflicts with the journal")
            effect.update(status="uncertain", copy_id=effect["copy_id"] or copy_id, fence=fence, reconciliation=cancel)
            self._emit(conn, job_id, "effect.reconcile_requested", "info", {"fence": fence})

    def blob_cleanup_status(self, job_id: str, fence: int, *, failed: bool) -> None:
        """Expose cleanup uncertainty without releasing reservations or requiring stopped effects."""
        with self._transaction() as conn:
            row = self._lease(conn, job_id, fence)
            if row["route"] != "blob" or row["state"] != "cancelling" or not row["cancel_requested"]:
                raise AuthorityError("cancellation cleanup is not authorized")
            row["cleanup_state"] = "failed" if failed else "running"
            if failed:
                row["reservation"]["status"] = "retained"
            self._emit(
                conn, job_id, "cleanup.failed" if failed else "cleanup.running", "warning" if failed else "info", {}
            )

    def cancellation_effects(self, job_id: str, fence: int) -> tuple[dict[str, Any], ...]:
        """Read exact cancellation obligations after all registered writers have stopped."""
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            if row["state"] != "cancelling" or not row["cancel_requested"]:
                raise AuthorityError("cancellation cleanup is not authorized")
            if self._active_runtimes(conn, job_id, fence):
                raise AuthorityError("runtime stop proof is unresolved")
            return tuple(dict(effect) for effect in row["effects"].values())

    def begin_blob_owner_cleanup(self, job_id: str, fence: int, attempt: PublicationAttempt) -> tuple[int, str]:
        """Journal companion removal only after every payload/control obligation is resolved."""
        content = ownership_bytes(attempt)
        length, digest = len(content), hashlib.sha256(content).hexdigest()
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
            if (
                row["route"] != "blob"
                or row["state"] != "cancelling"
                or not row["cancel_requested"]
                or row["attempt_key"] != attempt.key
                or row["attempt_prefix"] != attempt.prefix
                or self._active_runtimes(conn, job_id, fence)
                or set(row["effects"]) - {BLOB_OWNER_CLEANUP_PATH}
            ):
                raise AuthorityError("companion cleanup is not authorized")
            row["effects"][BLOB_OWNER_CLEANUP_PATH] = {
                "path": BLOB_OWNER_CLEANUP_PATH,
                "length": length,
                "digest": digest,
                "fence": fence,
                "status": "uncertain",
                "copy_id": None,
                "reconciliation": True,
            }
            row["cleanup_state"] = "running"
            self._emit(conn, job_id, "cleanup.companion_requested", "info", {"fence": fence})
        return length, digest

    def cleanup_owned_outputs(self, job_id: str, fence: int, root: Path) -> bool:
        """Remove only acknowledged outputs whose identity still matches; refuse on any mismatch or marker."""
        with self._transaction() as conn:
            row = self._job(conn, job_id)
            if not row["cancel_requested"]:
                raise CancellationError("cancellation was not requested")
            expected_root = Path(row["destination"]) / row["attempt_prefix"]
            if row["state"] in TERMINAL_STATES:
                raise AuthorityError("terminal job no longer owns cleanup authority")
            if row["route"] != "local" or root.absolute() != expected_root:
                raise AuthorityError("cleanup destination is not the bound attempt root")
            if fence != row["fence"] or row["stopped_fence"] < fence:
                raise AuthorityError("effects are not confirmed stopped")
            if fence:
                self._lease(conn, job_id, fence)
            if self._open_effects(conn, job_id):
                raise AuthorityError("effects are still dispatched or unresolved")
            owned = [effect for effect in row["effects"].values() if effect["status"] == "acknowledged"]
            package_id, attempt_id = row["attempt_key"].split("/", 1)
            attempt = PublicationAttempt(package_id, attempt_id, row["attempt_prefix"])
            deadline = time.monotonic() + self.settings.cleanup_seconds
            content = ownership_bytes(attempt)
            companion = ownership_path(attempt.prefix)
            if os.path.lexists(root.parent / companion):
                try:
                    if LocalReleaseStore(root.parent).read_control_bytes(companion, max_bytes=4096) != content:
                        return False
                except (OSError, ReleaseVerificationError):
                    return False
            if os.path.lexists(root / MARKER_PATH):
                return False
            if os.path.lexists(root):
                try:
                    root_fd = _open_root_nofollow(root)
                except OSError:
                    return False
                try:
                    removed = all(
                        _remove_verified(
                            root_fd,
                            effect["path"],
                            effect["length"],
                            effect["digest"],
                            deadline=deadline,
                        )
                        for effect in owned
                    )
                    if not removed or not _remove_empty_output_tree(root_fd, [16384], 0, deadline):
                        return False
                    identity = os.fstat(root_fd)
                    parent_fd = _open_root_nofollow(root.parent)
                    try:
                        current = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                            return False
                        os.rmdir(root.name, dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                finally:
                    os.close(root_fd)
            if not os.path.lexists(root.parent):
                return True
            parent_fd = _open_root_nofollow(root.parent)
            try:
                return _remove_verified(
                    parent_fd,
                    companion,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    deadline=deadline,
                )
            finally:
                os.close(parent_fd)

    @contextmanager
    def publication(self, job_id: str, fence: int, attempt: PublicationAttempt, digest: str) -> Iterator[None]:
        self._begin_publication(job_id, fence, attempt, digest)
        yield
        self._complete_publication(job_id, attempt, digest)

    def _begin_publication(self, job_id: str, fence: int, attempt: PublicationAttempt, digest: str) -> None:
        if not _DIGEST.fullmatch(digest):
            raise ReleaseJobError("commit digest must be lowercase SHA-256")
        with self._transaction() as conn:
            row = self._authorize(conn, job_id, fence)
            if row["attempt_prefix"] != attempt.prefix or row["attempt_key"] != attempt.key:
                raise AuthorityError("attempt is not owned by this job")
            if row["cancel_requested"] and row["state"] not in ("committing", "committed"):
                raise CancellationError("cancellation was accepted before commit")
            if row["state"] == "sealed":
                if not self._controls_acknowledged(conn, job_id):
                    raise AuthorityError("control artifacts are not journaled")
                if self._open_effects(conn, job_id):
                    raise AuthorityError("effects are unresolved")
                row.update(state="committing", commit_attempt=attempt.prefix, commit_digest=digest)
                self._emit(conn, job_id, "commit.chosen", "info", {"fence": fence})
            elif row["state"] in ("committing", "committed"):
                if row["commit_attempt"] != attempt.prefix or row["commit_digest"] != digest:
                    raise JobConflictError("commit decision belongs to another manifest")
            else:
                raise AuthorityError("job is not sealed")

    def _complete_publication(self, job_id: str, attempt: PublicationAttempt, digest: str) -> None:
        with self._transaction() as conn:
            row = self._job(conn, job_id)
            if (row["state"], row["commit_attempt"], row["commit_digest"]) == ("committing", attempt.prefix, digest):
                if self._open_effects(conn, job_id) or self._active_runtimes(conn, job_id, row["fence"]):
                    raise AuthorityError("publication effects or runtime stop proof are unresolved")
                row["state"] = "committed"
                self._emit(conn, job_id, "job.committed", "info", {})

    def flush_events(self, *, max_events: int = 256) -> int:
        """Append up to max_events unexported events in sequence order, repairing a partial tail first."""
        _require_int(max_events, "event page size", 1, MAX_EVENT_PAGE)
        with self._transaction() as conn:
            last = self._repair_event_log(conn)
            rows = sorted(
                (event for job in conn.values() for event in job["events"] if event["seq"] > last),
                key=lambda event: event["seq"],
            )[:max_events]
            if not rows:
                return 0
            observed = self._clock()
            lines = []
            for row in rows:
                record = {
                    "schema": EVENT_SCHEMA,
                    "seq": row["seq"],
                    "event_id": row["event_id"],
                    "job_id": row["job_id"],
                    "event": row["event"],
                    "severity": row["severity"],
                    "occurred_at": _iso(row["occurred_at"]),
                    "observed_at": _iso(observed),
                    "service": SERVICE_NAME,
                    "body": row["body"],
                }
                lines.append(json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
            self.root.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("ab") as stream:
                stream.write("".join(lines).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            return len(rows)

    def _repair_event_log(self, conn: Mapping[str, dict[str, Any]]) -> int:
        if not self.events_path.exists():
            return 0
        identities = {event["seq"]: event["event_id"] for job in conn.values() for event in job["events"]}
        last = 0
        with self.events_path.open("r+b") as stream:
            while True:
                position = stream.tell()
                line = stream.readline(4097)
                if not line:
                    break
                if len(line) > 4096:
                    raise ReleaseJobError("event log is corrupt")
                if not line.endswith(b"\n"):
                    stream.truncate(position)
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
                try:
                    record = json.loads(line)
                    sequence = record["seq"]
                    if type(sequence) is not int or sequence != last + 1:
                        raise ValueError("invalid sequence")
                    if identities.get(sequence) != record["event_id"]:
                        raise ValueError("unknown event identity")
                    last = sequence
                except (ValueError, KeyError, TypeError) as error:
                    raise ReleaseJobError("event log is corrupt") from error
        return last

    def _emit(
        self, conn: Mapping[str, dict[str, Any]], job_id: str, event: str, severity: str, body: Mapping[str, Any]
    ) -> None:
        sequence = max((item["seq"] for job in conn.values() for item in job["events"]), default=0) + 1
        self._job(conn, job_id)["events"].append(
            {
                "seq": sequence,
                "event_id": uuid4().hex,
                "job_id": job_id,
                "event": event,
                "severity": severity,
                "occurred_at": self._clock(),
                "body": json.loads(_bounded_body(body)),
            }
        )

    def _job(self, conn: Mapping[str, dict[str, Any]], job_id: str) -> dict[str, Any]:
        row = conn.get(job_id)
        if row is None:
            raise JobNotFoundError("job not found")
        return row

    def _owned(self, conn: Mapping[str, dict[str, Any]], job_id: str, scope: str, principal: str) -> dict[str, Any]:
        row = self._job(conn, job_id)
        if row["scope"] != scope or row["principal_digest"] != _digest(principal):
            raise JobNotFoundError("job not found")
        return row

    def _lease(self, conn: Mapping[str, dict[str, Any]], job_id: str, fence: int) -> dict[str, Any]:
        row = self._job(conn, job_id)
        if fence <= 0 or row["fence"] != fence:
            raise AuthorityError("stale worker fence")
        if row["lease_expires"] is None or row["lease_expires"] <= self._clock():
            raise AuthorityError("lease expired")
        return row

    def _authorize_reconciliation_runtime(
        self, conn: Mapping[str, dict[str, Any]], job_id: str, fence: int, effect_path: str | None
    ) -> dict[str, Any]:
        row = self._authorize(conn, job_id, fence, allow_unresolved_effects=True)
        effect = row["effects"].get(effect_path)
        if (
            row["route"] != "blob"
            or row["state"] != "cancelling"
            or not row["cancel_requested"]
            or effect is None
            or effect["fence"] != fence
            or effect["status"] != "uncertain"
            or effect.get("reconciliation") is not True
        ):
            raise AuthorityError("runtime lacks a journaled cancellation reconciliation")
        return row

    def _authorize_runtime(
        self, conn: Mapping[str, dict[str, Any]], job_id: str, fence: int, effect_path: str | None
    ) -> dict[str, Any]:
        row = self._authorize(conn, job_id, fence, allow_unresolved_effects=effect_path is not None)
        if effect_path is not None:
            records = [effect for effect in row["effects"].values() if effect["status"] != "acknowledged"]
            if (
                len(records) != 1
                or records[0]["path"] != normalize_release_path(effect_path)
                or records[0]["fence"] != fence
                or records[0]["status"] != "dispatched"
            ):
                raise AuthorityError("runtime does not match the sole current effect intent")
        return row

    def _authorize(
        self,
        conn: Mapping[str, dict[str, Any]],
        job_id: str,
        fence: int,
        *,
        allow_unresolved_effects: bool = False,
    ) -> dict[str, Any]:
        row = self._lease(conn, job_id, fence)
        if row["stopped_fence"] < fence - 1:
            raise AuthorityError("predecessor fence is not confirmed stopped")
        if not allow_unresolved_effects and self._open_effects(conn, job_id):
            raise AuthorityError("effect outcome is unresolved; reconcile before dispatch")
        return row

    def _open_effects(self, conn: Mapping[str, dict[str, Any]], job_id: str) -> int:
        return sum(effect["status"] != "acknowledged" for effect in self._job(conn, job_id)["effects"].values())

    def _reserved_on(self, conn: Mapping[str, dict[str, Any]], volume: str) -> int:
        return sum(
            reservation["bytes"]
            for job in conn.values()
            for reservation in (job["reservation"], job["scratch_reservation"])
            if reservation is not None
            and reservation["volume_key"] == volume
            and reservation["status"] in ("reserved", "retained")
        )

    def _journal(self, conn: Mapping[str, dict[str, Any]], job_id: str) -> dict[str, tuple[int, str]]:
        return {
            path: (control["length"], control["digest"])
            for path, control in self._job(conn, job_id)["controls"].items()
        }

    def _controls_acknowledged(self, conn: Mapping[str, dict[str, Any]], job_id: str) -> bool:
        journaled = self._journal(conn, job_id)
        if set(journaled) != set(CONTROL_PATHS):
            return False
        effects = self._job(conn, job_id)["effects"]
        return all(path in effects and effects[path]["status"] == "acknowledged" for path in CONTROL_PATHS)


class ReleaseJobControl:
    """Publication control bound to one claimed fence; satisfies the P03 PublicationControl protocol."""

    def __init__(self, store: ReleaseJobStore, job_id: str, fence: int) -> None:
        self._store = store
        self._job_id = job_id
        self._fence = fence

    def seal(self, controls: Mapping[str, tuple[int, str]]) -> None:
        self._store.seal(self._job_id, self._fence, controls)

    @contextmanager
    def effect(self, attempt: PublicationAttempt, path: str, length: int, digest: str) -> Iterator[bool]:
        replay = self._store.begin_effect(self._job_id, self._fence, attempt, path, length, digest)
        if replay:
            yield True
            return
        try:
            yield False
        except EffectPending as error:
            self._store.finish_effect(self._job_id, self._fence, path, ok=False)
            raise AuthorityError("effect outcome is pending reconciliation") from error
        except BaseException:
            self._store.finish_effect(self._job_id, self._fence, path, ok=False)
            raise
        self._store.finish_effect(self._job_id, self._fence, path, ok=True)

    @contextmanager
    def publication(self, attempt: PublicationAttempt, digest: str) -> Iterator[None]:
        with self._store.publication(self._job_id, self._fence, attempt, digest):
            yield

    @contextmanager
    def reconciliation(
        self,
        attempt: PublicationAttempt,
        path: str,
        length: int,
        digest: str,
        *,
        copy_id: str | None,
        cancel: bool,
    ) -> Iterator[Callable[[str], None]]:
        self._store.begin_reconciliation(
            self._job_id,
            self._fence,
            attempt,
            path,
            length,
            digest,
            copy_id=copy_id,
            cancel=cancel,
        )
        observation = "pending"

        def observed(value: str) -> None:
            nonlocal observation
            if value not in ("absent", "complete", "pending"):
                raise ReleaseJobError("reconciliation observation is invalid")
            observation = value

        yield observed
        if observation != "pending":
            self._store.reconcile_effect(
                self._job_id,
                self._fence,
                path,
                observed=observation,
                length=length,
                digest=digest,
            )


def _status(conn: Mapping[str, dict[str, Any]], row: Mapping[str, Any]) -> JobStatus:
    reservation = row["reservation"]
    return JobStatus(
        job_id=row["job_id"],
        state=row["state"],
        cancel_requested=bool(row["cancel_requested"]),
        fence=row["fence"],
        effects_stopped=row["fence"] > 0 and row["stopped_fence"] >= row["fence"],
        cleanup_state=row["cleanup_state"],
        capacity_state=reservation["status"] if reservation is not None else "none",
        scratch_bytes=row["scratch_bytes"],
        observed_scratch_bytes=row["observed_scratch_bytes"],
        peak_scratch_bytes=row["peak_scratch_bytes"],
        io_counters=dict(row["io_counters"]),
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(request: JobRequest, destination: str) -> str:
    canonical = {
        "attempt": request.attempt.key,
        "dataset_name": request.attempt.prefix,
        "package_id": request.attempt.package_id,
        "destination": destination,
        "frozen": hashlib.sha256(request.frozen_request).hexdigest(),
        "reserved_bytes": request.reserved_bytes,
        "route": request.route,
        "scope": request.scope,
    }
    return _digest(json.dumps(canonical, sort_keys=True, separators=(",", ":")))


def _remove_empty_output_tree(descriptor: int, remaining: list[int], depth: int, deadline: float) -> bool:
    if depth > 32 or time.monotonic() >= deadline:
        return False
    with os.scandir(descriptor) as entries:
        for entry in entries:
            remaining[0] -= 1
            if remaining[0] < 0 or time.monotonic() >= deadline or not entry.is_dir(follow_symlinks=False):
                return False
            try:
                child = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except OSError:
                return False
            try:
                if not _remove_empty_output_tree(child, remaining, depth + 1, deadline):
                    return False
            finally:
                os.close(child)
            try:
                os.rmdir(entry.name, dir_fd=descriptor)
            except OSError:
                return False
    os.fsync(descriptor)
    return True


def _clear_scratch_directory(descriptor: int, deadline: float, remaining: list[int], depth: int) -> None:
    if depth > 32:
        raise AuthorityError("private scratch nesting exceeds the cleanup bound")
    with os.scandir(descriptor) as entries:
        for entry in entries:
            remaining[0] -= 1
            if remaining[0] < 0 or time.monotonic() >= deadline:
                raise AuthorityError("private scratch cleanup budget exhausted")
            identity = entry.stat(follow_symlinks=False)
            if S_ISDIR(identity.st_mode):
                child = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (identity.st_dev, identity.st_ino):
                        raise AuthorityError("private scratch directory changed")
                    _clear_scratch_directory(child, deadline, remaining, depth + 1)
                finally:
                    os.close(child)
                os.rmdir(entry.name, dir_fd=descriptor)
            elif S_ISREG(identity.st_mode):
                os.unlink(entry.name, dir_fd=descriptor)
            else:
                raise AuthorityError("private scratch contains an unowned special entry")
    os.fsync(descriptor)


def _remove_verified(
    root_fd: int,
    relative: str,
    length: int,
    digest: str,
    *,
    expected_inode: tuple[int, int] | None = None,
    deadline: float | None = None,
) -> bool:
    """Unlink one owned file through directory descriptors without following symlinks."""
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in ("", ".", "..") for part in parts):
        return False
    opened: list[int] = []
    try:
        current = root_fd
        for name in parts[:-1]:
            try:
                current = os.open(name, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                return True
            except OSError:
                return False
            opened.append(current)
        leaf = parts[-1]
        try:
            file_fd = os.open(leaf, _FILE_FLAGS, dir_fd=current)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            opened_info = os.fstat(file_fd)
            if expected_inode is None:
                if not _verify_open_file(file_fd, length, digest, deadline=deadline):
                    return False
            elif not S_ISREG(opened_info.st_mode) or (opened_info.st_dev, opened_info.st_ino) != expected_inode:
                return False
            named = os.stat(leaf, dir_fd=current, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (opened_info.st_dev, opened_info.st_ino):
                return False
            os.unlink(leaf, dir_fd=current)
            os.fsync(current)
            return True
        finally:
            os.close(file_fd)
    finally:
        for fd in opened:
            os.close(fd)


def _verify_open_file(fd: int, length: int, digest: str, *, deadline: float | None = None) -> bool:
    info = os.fstat(fd)
    if not S_ISREG(info.st_mode) or info.st_size != length:
        return False
    hasher = hashlib.sha256()
    while chunk := os.read(fd, _READ_CHUNK):
        if deadline is not None and time.monotonic() >= deadline:
            return False
        hasher.update(chunk)
    return hasher.hexdigest() == digest


def _open_root_nofollow(root: Path) -> int:
    absolute = root.absolute()
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for segment in absolute.parts[1:]:
            child = os.open(segment, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _bounded_body(body: Mapping[str, Any]) -> str:
    if len(body) > 8:
        raise ReleaseJobError("event body exceeds the bound")
    for key, value in body.items():
        if not _EVENT_KEY.fullmatch(key):
            raise ReleaseJobError("event body key is invalid")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ReleaseJobError("event body value is invalid")
        if isinstance(value, str) and len(value) > 64:
            raise ReleaseJobError("event body value exceeds the bound")
    return json.dumps(dict(sorted(body.items())), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _require_int(value: object, label: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ReleaseJobError(f"{label} is outside its validated range")


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    if not re.fullmatch(r"[0-9]{1,18}", raw):
        raise ReleaseJobError(f"{name} must be a base-10 integer")
    return int(raw)


def _existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            raise ReleaseJobError("destination has no existing ancestor")
        current = current.parent
    return current


def _volume_key(path: Path) -> str:
    return f"dev:{os.stat(_existing_ancestor(path)).st_dev}"


def _destination_text(destination: Path | BlobLocation) -> str:
    if isinstance(destination, BlobLocation):
        return destination.as_text()
    return str(destination.resolve())


def _destination_volume(destination: Path | BlobLocation) -> str:
    if isinstance(destination, BlobLocation):
        return f"blob:{destination.account_url}"
    return _volume_key(destination.resolve())


def _statvfs_free_bytes(path: Path) -> int:
    info = os.statvfs(path)
    return info.f_bavail * info.f_frsize
