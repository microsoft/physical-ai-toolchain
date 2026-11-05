"""Trusted release-job service for the curated dataset release API.

Clients select destinations by alias only. Filesystem paths are resolved from
operator-owned configuration and never returned to, or accepted from, clients.
"""

from __future__ import annotations

import asyncio
import fnmatch
import functools
import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, TypeVar

from physical_ai_curation.release.azure_publication import BlobReleaseStore
from physical_ai_curation.release.blob_source_associations import BlobSourceAssociations, BlobVerifiedAssociation
from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.inputs import SavedSelection, StaleReleaseInputError
from physical_ai_curation.release.jobs import (
    AcceptedJob,
    CapacityError,
    JobConflictError,
    JobNotFoundError,
    JobRequest,
    JobStatus,
    ReleaseJobError,
    ReleaseJobStore,
    ReleaseRuntimeSettings,
)
from physical_ai_curation.release.models import INVENTORY_PATH, MANIFEST_PATH, Inventory, StorageRevisionEntry
from physical_ai_curation.release.projections import LABELS_PATH
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import (
    BlobLocation,
    BlobRecordStorage,
    RecordLimits,
    _blob_container_client,
    release_collection_location,
)
from physical_ai_curation.release.runner import ConfiguredExecution
from physical_ai_curation.release.source_associations import LocalSourceAssociations, VerifiedAssociation
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore
from physical_ai_curation.release.verification import MAX_CONTROL_BYTES
from physical_ai_curation.saved_input import SavedInputError
from physical_ai_dataset_io.lerobot import EpisodeRecord, iter_episodes

__all__ = [
    "CAPABILITY_SCHEMA",
    "PreparedRelease",
    "ReleaseApiError",
    "ReleaseApiSettings",
    "ReleasePlanner",
    "ReleaseService",
    "ReleaseSpec",
    "get_release_planner",
    "get_release_service",
    "load_release_api_settings",
]

CAPABILITY_SCHEMA = "curated-release-api/v1"
RELEASE_JOB_ROOT_ENV = "RELEASE_JOB_ROOT"
RELEASE_DESTINATIONS_ENV = "RELEASE_DESTINATIONS"

_ALIAS = re.compile(r"[a-z][a-z0-9-]{0,62}")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_DATASET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DATASET_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._\- ]{0,254}")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MAX_EPISODES = 10_000
_MAX_DETAIL_EPISODES = 100
_MAX_MEDIA_BYTES = 512 * 1024**2
_MAX_METADATA_BYTES = 64 * 1024**2
_T = TypeVar("_T")
_DEFAULT_LANE_CONCURRENCY = 4
_MAX_LANE_CONCURRENCY = 64
_DEFAULT_READ_INFLIGHT_BYTES = 1024**3
_DEFAULT_OPERATION_SECONDS = 30.0
_MAX_OPERATION_SECONDS = 600.0


class ReleaseApiError(Exception):
    """Client-safe release API failure carrying an HTTP status and fixed message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ReleaseOffloadSettings:
    """Bounds for synchronous release work run off the event loop."""

    read_concurrency: int = _DEFAULT_LANE_CONCURRENCY
    control_concurrency: int = _DEFAULT_LANE_CONCURRENCY
    read_inflight_bytes: int = _DEFAULT_READ_INFLIGHT_BYTES
    operation_timeout_seconds: float = _DEFAULT_OPERATION_SECONDS
    preparation_concurrency: int = 2


_SHUTDOWN_SETTLE_SECONDS = 5.0


class _OperationCancelled(Exception):
    """Raised at a checkpoint when the caller gave up or the operation deadline passed."""


class _CancelToken:
    """Cooperative stop signal shared by one release operation and the request awaiting it."""

    def __init__(self, deadline: float) -> None:
        self._deadline = deadline
        self._cancelled = threading.Event()
        self.on_cancel: Callable[[], None] | None = None

    def cancel(self) -> None:
        already_cancelled = self._cancelled.is_set()
        self._cancelled.set()
        if not already_cancelled and self.on_cancel is not None:
            self.on_cancel()

    def check(self) -> None:
        if self._cancelled.is_set() or time.monotonic() >= self._deadline:
            self._cancelled.set()
            raise _OperationCancelled


_ACTIVE_TOKEN = threading.local()


def check_cancelled() -> None:
    """Checkpoint for release work: stop when the owning request was abandoned or timed out."""
    token = getattr(_ACTIVE_TOKEN, "token", None)
    if token is not None:
        token.check()


def _run_with_token[Result](
    token: _CancelToken, operation: Callable[..., Result], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Result:
    previous = getattr(_ACTIVE_TOKEN, "token", None)
    _ACTIVE_TOKEN.token = token
    try:
        token.check()
        return operation(*args, **kwargs)
    finally:
        _ACTIVE_TOKEN.token = previous


class _Admission:
    """Process-wide admission for one lane name, shared by every offload generation.

    Slots, bytes, and tokens stay charged until their thread settles, so replacing the offload
    cannot reset capacity that running work still holds.
    """

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.admitted = 0
        self.charged_bytes = 0
        self.tokens: set[_CancelToken] = set()

    def release_locked(self, response_bytes: int, token: _CancelToken) -> None:
        self.admitted -= 1
        self.charged_bytes -= response_bytes
        self.tokens.discard(token)

    def release(self, response_bytes: int, token: _CancelToken) -> None:
        with self.condition:
            self.release_locked(response_bytes, token)
            self.condition.notify_all()

    def cancel_all(self) -> None:
        with self.condition:
            for token in self.tokens:
                token.cancel()

    def wait_idle(self, timeout: float) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: self.admitted == 0, timeout)


_ADMISSIONS: dict[str, _Admission] = {name: _Admission() for name in ("read", "control", "preparation")}


@dataclass(frozen=True)
class PreparationWork:
    """Owned preparation threads with a cooperative deadline and settlement before release."""

    token: _CancelToken
    executor: ThreadPoolExecutor

    def check(self) -> None:
        """Stop at a safe boundary when the owning preparation is cancelled or expired."""
        self.token.check()

    async def run(self, operation: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run a blocking step without abandoning its thread on task cancellation."""
        self.check()
        try:
            future = asyncio.get_running_loop().run_in_executor(
                self.executor, functools.partial(_run_with_token, self.token, operation, args, kwargs)
            )
        except RuntimeError as error:
            raise ReleaseApiError(503, "release_unavailable") from error
        try:
            result = await asyncio.shield(future)
            self.check()
            return result
        except asyncio.CancelledError:
            self.token.cancel()
            while not future.done():
                try:
                    await asyncio.wait({future})
                except asyncio.CancelledError:
                    continue
            if not future.cancelled():
                future.exception()
            raise


_PREPARATION_WORK: ContextVar[PreparationWork | None] = ContextVar("release_preparation", default=None)


def current_preparation_work() -> PreparationWork | None:
    """Return the preparation owner in this async request, if any."""
    return _PREPARATION_WORK.get()


class _Lane:
    """Admission gate and dedicated worker threads for one class of release work.

    Capacity is charged when a thread is submitted and released only when that thread settles,
    so an awaiting request that times out or disconnects never frees work still running.
    """

    def __init__(self, name: str, slots: int, byte_budget: int | None) -> None:
        self._slots = slots
        self._byte_budget = byte_budget
        self._admission = _ADMISSIONS[name]
        self._executor = ThreadPoolExecutor(max_workers=slots, thread_name_prefix=f"release-{name}")
        self._closed = False

    def submit(self, task: Callable[[], Any], response_bytes: int, token: _CancelToken) -> Future[Any]:
        admission = self._admission
        with admission.condition:
            if self._closed:
                raise ReleaseApiError(503, "release_unavailable")
            over_bytes = self._byte_budget is not None and admission.charged_bytes + response_bytes > self._byte_budget
            if admission.admitted >= self._slots or over_bytes:
                raise ReleaseApiError(503, "release_busy")
            admission.admitted += 1
            admission.charged_bytes += response_bytes
            admission.tokens.add(token)
            try:
                future = self._executor.submit(task)
            except RuntimeError as error:
                admission.release_locked(response_bytes, token)
                raise ReleaseApiError(503, "release_unavailable") from error
        future.add_done_callback(lambda _settled: admission.release(response_bytes, token))
        return future

    def close(self) -> None:
        with self._admission.condition:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


class ReleaseOffload:
    """Runs release operations on bounded lanes; reads and control work never share admission."""

    def __init__(self, settings: ReleaseOffloadSettings) -> None:
        self.settings = settings
        self._preparation_closed = False
        self._preparation_count = 0
        self._preparation_executor = ThreadPoolExecutor(
            max_workers=settings.preparation_concurrency, thread_name_prefix="release-preparation"
        )
        self._lanes = {
            "read": _Lane("read", settings.read_concurrency, settings.read_inflight_bytes),
            "control": _Lane("control", settings.control_concurrency, None),
        }

    async def run(
        self, lane: str, operation: Callable[..., _T], /, *args: Any, response_bytes: int = 0, **kwargs: Any
    ) -> _T:
        token = _CancelToken(time.monotonic() + self.settings.operation_timeout_seconds)
        future = self._lanes[lane].submit(
            functools.partial(_run_with_token, token, operation, args, kwargs), response_bytes, token
        )
        awaited = asyncio.wrap_future(future)
        # Retrieve late exceptions from abandoned work so they are not reported as unhandled.
        awaited.add_done_callback(lambda settled: settled.cancelled() or settled.exception())
        try:
            done, _pending = await asyncio.wait({awaited}, timeout=self.settings.operation_timeout_seconds)
        except asyncio.CancelledError:
            token.cancel()
            raise
        if not done:
            token.cancel()
            raise ReleaseApiError(504, "release_operation_timed_out")
        if awaited.cancelled():
            raise ReleaseApiError(503, "release_unavailable")
        try:
            return awaited.result()
        except _OperationCancelled as error:
            raise ReleaseApiError(503, "release_unavailable") from error

    async def prepare(self, operation: Callable[..., Awaitable[_T]], /, *args: Any) -> _T:
        """Bound an async preparation, retaining admission through all owned thread cleanup."""
        admission = _ADMISSIONS["preparation"]
        timeout_seconds = self.settings.operation_timeout_seconds
        token = _CancelToken(time.monotonic() + timeout_seconds)
        with admission.condition:
            if self._preparation_closed:
                raise ReleaseApiError(503, "release_unavailable")
            if admission.admitted >= self.settings.preparation_concurrency:
                raise ReleaseApiError(503, "release_busy")
            admission.admitted += 1
            admission.tokens.add(token)
            self._preparation_count += 1

        async def execute() -> _T:
            context = _PREPARATION_WORK.set(PreparationWork(token, self._preparation_executor))
            try:
                token.check()
                return await operation(*args)
            finally:
                _PREPARATION_WORK.reset(context)

        task = asyncio.create_task(execute())
        loop = asyncio.get_running_loop()
        with admission.condition:
            token.on_cancel = lambda: loop.call_soon_threadsafe(task.cancel)
            if token._cancelled.is_set():
                token.on_cancel()

        def settled(completed: asyncio.Task[_T]) -> None:
            token.on_cancel = None
            if not completed.cancelled():
                completed.exception()
            with admission.condition:
                self._preparation_count -= 1
                admission.release_locked(0, token)
                admission.condition.notify_all()
                if self._preparation_closed and self._preparation_count == 0:
                    self._preparation_executor.shutdown(wait=False, cancel_futures=True)

        task.add_done_callback(settled)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            token.cancel()
            raise
        if not done:
            token.cancel()
            raise ReleaseApiError(504, "release_operation_timed_out")
        if task.cancelled():
            raise ReleaseApiError(503, "release_unavailable")
        try:
            return task.result()
        except _OperationCancelled as error:
            if time.monotonic() >= token._deadline:
                raise ReleaseApiError(504, "release_operation_timed_out") from error
            raise ReleaseApiError(503, "release_unavailable") from error

    def close(self) -> None:
        with _ADMISSIONS["preparation"].condition:
            self._preparation_closed = True
            if self._preparation_count == 0:
                self._preparation_executor.shutdown(wait=False, cancel_futures=True)
        for lane in self._lanes.values():
            lane.close()


_OFFLOAD_LOCK = threading.Lock()
_offload: ReleaseOffload | None = None


def get_release_offload(settings: ReleaseOffloadSettings) -> ReleaseOffload:
    """Return the process-wide offload for these settings, replacing a stale one.

    The replaced offload stops admitting new work; its running threads stay charged to the shared ledger.
    """
    global _offload
    with _OFFLOAD_LOCK:
        if _offload is None or _offload.settings != settings:
            if _offload is not None:
                _offload.close()
            _offload = ReleaseOffload(settings)
        return _offload


def close_release_offload(settle_seconds: float = _SHUTDOWN_SETTLE_SECONDS) -> bool:
    """Refuse new admissions, cancel running release work, and wait for threads to settle.

    Returns False when work is still running after the wait; its capacity stays charged until it settles.
    """
    global _offload
    with _OFFLOAD_LOCK:
        offload = _offload
        _offload = None
    if offload is not None:
        offload.close()
    for admission in _ADMISSIONS.values():
        admission.cancel_all()
    deadline = time.monotonic() + settle_seconds
    settled = True
    for admission in _ADMISSIONS.values():
        settled = admission.wait_idle(max(0.0, deadline - time.monotonic())) and settled
    return settled


@dataclass(frozen=True)
class ReleaseApiSettings:
    """Validated release API configuration read from the process environment."""

    job_root: Path | None
    destinations: Mapping[str, Path | BlobLocation] = field(default_factory=dict)
    metadata_max_bytes: int = _MAX_METADATA_BYTES
    media_max_bytes: int = _MAX_MEDIA_BYTES
    job_storage: BlobLocation | None = None
    offload: ReleaseOffloadSettings = field(default_factory=ReleaseOffloadSettings)

    @property
    def enabled(self) -> bool:
        return self.job_root is not None


def load_release_api_settings(environ: Mapping[str, str]) -> ReleaseApiSettings:
    """Parse release settings; raise ValueError with a fixed message on invalid configuration."""
    raw_root = environ.get(RELEASE_JOB_ROOT_ENV, "").strip()
    job_root: Path | None = None
    if raw_root:
        job_root = Path(raw_root)
        if not job_root.is_absolute():
            raise ValueError(f"{RELEASE_JOB_ROOT_ENV} must be an absolute path")

    raw_destinations = environ.get(RELEASE_DESTINATIONS_ENV, "").strip()
    job_storage = None
    if environ.get("RELEASE_JOB_STORAGE", "").strip():
        try:
            state = json.loads(environ["RELEASE_JOB_STORAGE"])
            if not isinstance(state, dict) or set(state) != {"account_url", "container", "prefix"}:
                raise ValueError("invalid state location")
            job_storage = BlobLocation(**state)
        except (ValueError, TypeError) as error:
            raise ValueError("RELEASE_JOB_STORAGE must be a configured Blob location") from error
    destinations: dict[str, Path | BlobLocation] = {}
    if raw_destinations:
        try:
            parsed: Any = json.loads(raw_destinations)
        except json.JSONDecodeError as error:
            raise ValueError(f"{RELEASE_DESTINATIONS_ENV} must be a JSON object") from error
        if not isinstance(parsed, dict):
            raise ValueError(f"{RELEASE_DESTINATIONS_ENV} must be a JSON object")
        for alias, location in parsed.items():
            if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
                raise ValueError(f"{RELEASE_DESTINATIONS_ENV} contains an invalid alias")
            if isinstance(location, dict) and set(location) == {"account_url", "container", "prefix"}:
                destinations[alias] = BlobLocation(**location)
            elif isinstance(location, str) and Path(location).is_absolute():
                destinations[alias] = Path(location)
            else:
                raise ValueError(f"{RELEASE_DESTINATIONS_ENV} entries must be configured storage roots")
    limits = {}
    for name, default in (
        ("CURATION_RELEASE_READ_METADATA_BYTES", _MAX_METADATA_BYTES),
        ("CURATION_RELEASE_READ_MEDIA_BYTES", _MAX_MEDIA_BYTES),
    ):
        try:
            value = int(environ.get(name, str(default)))
        except ValueError as error:
            raise ValueError(f"{name} must be a positive integer") from error
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        limits[name] = value
    metadata_max_bytes = limits["CURATION_RELEASE_READ_METADATA_BYTES"]
    media_max_bytes = limits["CURATION_RELEASE_READ_MEDIA_BYTES"]
    offload = ReleaseOffloadSettings(
        preparation_concurrency=_bounded_int(environ, "RELEASE_PREPARATION_CONCURRENCY", 2, 1, _MAX_LANE_CONCURRENCY),
        read_concurrency=_bounded_int(
            environ, "RELEASE_READ_CONCURRENCY", _DEFAULT_LANE_CONCURRENCY, 1, _MAX_LANE_CONCURRENCY
        ),
        control_concurrency=_bounded_int(
            environ, "RELEASE_CONTROL_CONCURRENCY", _DEFAULT_LANE_CONCURRENCY, 1, _MAX_LANE_CONCURRENCY
        ),
        read_inflight_bytes=_bounded_int(
            environ, "RELEASE_READ_INFLIGHT_BYTES", _DEFAULT_READ_INFLIGHT_BYTES, 1, 2**63 - 1
        ),
        operation_timeout_seconds=_bounded_seconds(
            environ, "RELEASE_OPERATION_TIMEOUT_SECONDS", _DEFAULT_OPERATION_SECONDS, _MAX_OPERATION_SECONDS
        ),
    )
    if offload.read_inflight_bytes < max(metadata_max_bytes, media_max_bytes):
        raise ValueError("RELEASE_READ_INFLIGHT_BYTES must cover the largest retained read bound")
    return ReleaseApiSettings(
        job_root=job_root,
        destinations=destinations,
        metadata_max_bytes=metadata_max_bytes,
        media_max_bytes=media_max_bytes,
        job_storage=job_storage,
        offload=offload,
    )


def _bounded_int(environ: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = environ.get(name, "").strip() or str(default)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _bounded_seconds(environ: Mapping[str, str], name: str, default: float, maximum: float) -> float:
    raw = environ.get(name, "").strip() or str(default)
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number of seconds above 0 and at most {maximum:g}") from error
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{name} must be a number of seconds above 0 and at most {maximum:g}")
    return value


def open_release_job_store(settings: ReleaseApiSettings) -> ReleaseJobStore:
    """Compose shared durable state while keeping native scratch on the configured local root."""
    if settings.job_root is None:
        raise ReleaseApiError(503, "release job store is not configured")
    runtime = ReleaseRuntimeSettings.from_environment(os.environ)
    location = settings.job_storage
    storage = None
    if location is not None:
        storage = BlobRecordStorage(
            _default_blob_container(location),
            account_url=location.account_url,
            container_name=location.container,
            prefix=location.prefix,
            timeout_seconds=runtime.state_timeout_seconds,
            max_record_bytes=runtime.state_max_record_bytes,
            max_total_bytes=runtime.state_max_total_bytes,
            max_jobs=runtime.state_max_jobs,
        )
    return ReleaseJobStore(settings.job_root, settings=runtime, storage=storage)


@dataclass(frozen=True)
class ReleaseSpec:
    """Validated, client-supplied release selection. Contains no filesystem paths."""

    destination: str
    package_id: str
    attempt_id: str
    dataset_name: str
    dataset_id: str
    episode_indices: tuple[int, ...]
    resources: tuple[SavedSelection, ...] = ()
    finalized: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if len(self.description) > 2000:
            raise ReleaseApiError(422, "description exceeds its bound")
        if not _ALIAS.fullmatch(self.destination):
            raise ReleaseApiError(422, "destination alias is invalid")
        for value in (self.package_id, self.attempt_id):
            if not _IDENTITY.fullmatch(value):
                raise ReleaseApiError(422, "release identity is invalid")
        if not _DATASET_NAME.fullmatch(self.dataset_name) or "--" in self.dataset_name:
            raise ReleaseApiError(422, "dataset name is invalid")
        if not _DATASET_ID.fullmatch(self.dataset_id):
            raise ReleaseApiError(422, "dataset id is invalid")
        if not self.episode_indices or len(self.episode_indices) > _MAX_EPISODES:
            raise ReleaseApiError(422, "episode selection is outside its bounds")
        if any(type(index) is not int or index < 0 for index in self.episode_indices):
            raise ReleaseApiError(422, "episode indices must be non-negative")
        if len(set(self.episode_indices)) != len(self.episode_indices):
            raise ReleaseApiError(422, "episode selection contains duplicates")


@dataclass(frozen=True)
class PreparedRelease:
    """Advisory intent or captured generation, capacity estimate and client-safe summary."""

    frozen_request: bytes
    reserved_bytes: int
    summary: Mapping[str, Any]
    execution_binding: bytes = b""


class ReleasePlanner(Protocol):
    """Trusted composition supplied by the operator. ``prepare`` must be read-only."""

    async def prepare(self, scope: str, spec: ReleaseSpec, destination: Path | BlobLocation) -> PreparedRelease: ...

    def execution(self, job_id: str, frozen_request: bytes) -> ConfiguredExecution: ...


_PLANNER: ReleasePlanner | None = None


def get_release_planner() -> ReleasePlanner | None:
    """Return the operator-registered planner, or None when release planning is not configured."""
    return _PLANNER


def set_release_planner(planner: ReleasePlanner | None) -> None:
    """Register the trusted planner. Intended for composition roots and tests."""
    global _PLANNER
    _PLANNER = planner


class ReleaseService:
    """Owner-scoped release operations exposed by the HTTP API."""

    def __init__(
        self,
        settings: ReleaseApiSettings,
        *,
        store_factory: Callable[[Path], ReleaseJobStore] | None = None,
        planner: ReleasePlanner | None = None,
        blob_container_factory: Callable[[BlobLocation], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._store_factory = store_factory or (lambda _root: open_release_job_store(settings))
        self._planner = planner
        self._blob_container_factory = blob_container_factory or _default_blob_container
        self._offload = get_release_offload(settings.offload)

    @property
    def planner_configured(self) -> bool:
        return self._planner is not None

    @property
    def metadata_bytes(self) -> int:
        return self._settings.metadata_max_bytes

    @property
    def media_bytes(self) -> int:
        return self._settings.media_max_bytes

    async def control(self, operation: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run job-state work on the control lane so status and cancel stay responsive."""
        return await self._offload.run("control", operation, *args, **kwargs)

    async def read(self, operation: Callable[..., _T], /, *args: Any, response_bytes: int = 0, **kwargs: Any) -> _T:
        """Run retained-artifact reads on the read lane, charging the bytes they may return."""
        return await self._offload.run("read", operation, *args, response_bytes=response_bytes, **kwargs)

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema": CAPABILITY_SCHEMA,
            "job_store": self._settings.enabled,
            "planning": self.planner_configured,
            "destinations": self.destinations(),
            "operations": [
                "capabilities",
                "destinations",
                "inventory",
                "jobs",
                "status",
                "preview",
                "submit",
                "cancel",
                "recover",
                "result",
                "artifacts",
            ],
            "target": {"name": "lerobot", "version": "v3.0"},
            "routes": ["local-to-local", "blob-same-account"],
            "capacity": "unknown",
        }

    def destinations(self) -> list[dict[str, str]]:
        return [{"alias": alias} for alias in sorted(self._settings.destinations)]

    def jobs(self, scope: str, *, cursor: int, limit: int, dataset_id: str | None = None) -> dict[str, Any]:
        store = self._store()
        try:
            page = store.page(scope=scope, principal=scope, cursor=cursor, limit=limit)
        except ReleaseJobError as error:
            raise _map_job_error(error) from error
        items = []
        for item in page.items:
            content = store.execution_binding(item.job_id)
            binding = parse_strict_json(content) if content else {}
            source = binding.get("selection", {}).get("dataset_id")
            if dataset_id is None or source == dataset_id:
                items.append(
                    {
                        **_status_body(item),
                        "dataset_id": source,
                        "created_at": store.accepted_at(item.job_id),
                    }
                )
        return {
            "items": items,
            "next_cursor": page.next_cursor,
        }

    def accepted_request(self, scope: str, idempotency_key: str) -> dict[str, Any]:
        """Resolve a private accepted-request pointer without source reads or state mutation."""
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ReleaseApiError(422, "Idempotency-Key is invalid")
        try:
            accepted = self._store().accepted_request(scope=scope, principal=scope, idempotency_key=idempotency_key)
        except ReleaseJobError as error:
            raise _map_job_error(error) from error
        if accepted is None:
            raise ReleaseApiError(404, "release request was not found")
        return {"job_id": accepted.job_id, "replayed": True}

    def status(self, scope: str, job_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            return _status_body(store.get(job_id, scope=scope, principal=scope))
        except ReleaseJobError as error:
            raise _map_job_error(error) from error

    def cancel(self, scope: str, job_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            return _status_body(store.request_cancel(job_id, scope=scope, principal=scope))
        except ReleaseJobError as error:
            raise _map_job_error(error) from error

    async def preview(self, scope: str, spec: ReleaseSpec) -> dict[str, Any]:
        """Describe selected intent without persisting or freezing source data."""
        destination = await self.control(self._destination, spec.destination, spec.dataset_id)
        prepared = await self._prepare(scope, spec, destination)
        return {
            "schema": CAPABILITY_SCHEMA,
            "digest": _preview_digest(spec, prepared),
            "reserved_bytes": prepared.reserved_bytes,
            "summary": dict(prepared.summary),
        }

    async def submit(
        self,
        scope: str,
        spec: ReleaseSpec,
        idempotency_key: str,
        *,
        preview_digest: str,
        processing_digest: str,
        local_disk_acknowledged: bool,
    ) -> AcceptedJob:
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ReleaseApiError(422, "Idempotency-Key is invalid")
        store = await self.control(self._store)
        client_request_digest = hashlib.sha256(
            canonical_json_bytes(
                {
                    "selection": asdict(spec),
                    "preview_digest": preview_digest,
                    "processing_digest": processing_digest,
                    "local_disk_acknowledged": local_disk_acknowledged,
                }
            )
        ).hexdigest()
        try:
            accepted = await self.control(
                store.accepted_request,
                scope=scope,
                principal=scope,
                idempotency_key=idempotency_key,
                client_request_digest=client_request_digest,
            )
        except ReleaseJobError as error:
            raise _map_job_error(error) from error
        if accepted is not None:
            return accepted
        destination = await self.control(self._destination, spec.destination, spec.dataset_id)
        prepared = await self._prepare(scope, spec, destination)
        if _preview_digest(spec, prepared) != preview_digest:
            raise ReleaseApiError(409, "stale_preview")
        if processing_digest and prepared.summary.get("processing_digest") != processing_digest:
            raise ReleaseApiError(409, "processing_confirmation_required")
        if isinstance(destination, Path) and not local_disk_acknowledged:
            raise ReleaseApiError(422, "local_disk_acknowledgment_required")
        payload = parse_strict_json(prepared.frozen_request)
        if payload.get("schema_version") == "curated-release-intent/v1":
            payload["processing_confirmed"] = bool(processing_digest)
            prepared = replace(prepared, frozen_request=canonical_json_bytes(payload))
        request = JobRequest(
            scope=scope,
            principal=scope,
            idempotency_key=idempotency_key,
            attempt=PublicationAttempt(
                package_id=spec.package_id,
                attempt_id=spec.attempt_id,
                dataset_name=spec.dataset_name,
            ),
            destination=destination,
            frozen_request=prepared.frozen_request,
            reserved_bytes=max(1, prepared.reserved_bytes),
            route="blob" if isinstance(destination, BlobLocation) else "local",
            execution_binding=prepared.execution_binding,
            client_request_digest=client_request_digest,
        )
        try:
            return await self.control(store.accept, request)
        except CapacityError as error:
            raise ReleaseApiError(507, "insufficient capacity for the reserved estimate") from error
        except JobConflictError as error:
            raise ReleaseApiError(409, "release request conflicts with an existing job") from error
        except ReleaseJobError as error:
            raise ReleaseApiError(422, "release request was rejected") from error

    async def inventory(self, dataset_id: str, *, cursor: int, limit: int, scope: str = "localuser") -> dict[str, Any]:
        """Return a bounded page, cancelling unfinished reads on failure or deadline."""
        try:
            async with asyncio.timeout(self._offload.settings.operation_timeout_seconds):
                return await self._inventory_page(dataset_id, cursor=cursor, limit=limit, scope=scope)
        except TimeoutError as error:
            raise ReleaseApiError(504, "release_operation_timed_out") from error

    async def inventory_details(self, dataset_id: str, *, episodes: list[int], scope: str) -> dict[str, Any]:
        """Return bounded episode evidence; cancel unfinished reads on failure or deadline."""
        if not 1 <= len(episodes) <= _MAX_DETAIL_EPISODES or len(set(episodes)) != len(episodes):
            raise ReleaseApiError(422, "invalid_inventory_details")
        try:
            async with asyncio.timeout(self._offload.settings.operation_timeout_seconds):
                page = await self._inventory_page(
                    dataset_id, cursor=0, limit=len(episodes), scope=scope, episodes=episodes, detail=True
                )
        except TimeoutError as error:
            raise ReleaseApiError(504, "release_operation_timed_out") from error
        return {"schema": CAPABILITY_SCHEMA, "items": page["items"]}

    async def _inventory_page(
        self,
        dataset_id: str,
        *,
        cursor: int,
        limit: int,
        scope: str,
        episodes: list[int] | None = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        """Return labels and provenance for a population page, or full evidence when ``detail`` is set."""
        if episodes is None and (not cursor >= 0 or not 1 <= limit <= 100):
            raise ReleaseApiError(422, "invalid_inventory_page")
        datasets = getattr(self._planner, "datasets", None)
        if datasets is None or await datasets.get_dataset(dataset_id) is None:
            raise ReleaseApiError(404, "release source is unavailable")
        source_episodes = await datasets.list_episodes(dataset_id, limit=_MAX_EPISODES + 1, require_actual=True)
        if len(source_episodes) > _MAX_EPISODES:
            raise ReleaseApiError(422, "source_inventory_limit_exceeded")
        indices = sorted({episode.index for episode in source_episodes})
        if episodes is None:
            selected = indices[cursor : cursor + limit]
        else:
            population = set(indices)
            if any(index not in population for index in episodes):
                raise ReleaseApiError(422, "invalid_inventory_details")
            selected = list(episodes)
        reader_factory = getattr(self._planner, "reader_for_scope", None)
        reader = reader_factory(scope, inventory=True) if reader_factory else getattr(self._planner, "reader", None)

        async def read_item(index: int) -> dict[str, Any]:
            item: dict[str, Any] = {
                "episode_index": index,
                "detailed": detail,
                "resources": [],
                "annotation": None,
                "edits": None,
                "labels": None,
                "provenance": None,
                "judge": None,
            }
            if reader is not None:
                try:
                    annotation = await reader.annotation(dataset_id, index) if detail else None
                    edits = None
                    if detail:
                        source = await reader.source_revision(dataset_id, index)
                        edits = await reader.edits(dataset_id, index, scope, source)
                    labels = await reader.release_resource(dataset_id, SavedSelection("labels", index, "inventory"))
                    ledger_resource = await reader.release_resource(
                        dataset_id, SavedSelection("ledger", index, "inventory")
                    )
                    for kind, resource in (
                        ("annotation", annotation),
                        ("edits", edits),
                        ("labels", labels),
                        ("ledger", ledger_resource),
                    ):
                        if resource is not None and resource.value is not None and resource.etag:
                            item["resources"].append(
                                {"kind": kind, "episode_index": index, "expected_revision": resource.etag}
                            )
                    if annotation is not None:
                        item["annotation"] = annotation.value is not None
                    if edits is not None:
                        item["edits"] = edits.value is not None
                    values = labels.value or {}
                    item["labels"] = values.get("episodes", {}).get(str(index))
                    ledger = values.get("provenance", {}).get(str(index))
                    item["provenance"] = (
                        {
                            "origins": sorted(
                                {entry.get("origin", "unknown") for entry in (ledger or {}).get("contributions", [])}
                            )
                        }
                        if ledger
                        else None
                    )
                except (SavedInputError, *_release_read_errors()) as error:
                    raise ReleaseApiError(409, "saved_inventory_unavailable") from error
                if not detail:
                    return item
                try:
                    evidence = await reader.release_resource(dataset_id, SavedSelection("evidence", index, "inventory"))
                    if evidence.value is not None and evidence.etag:
                        result = evidence.value["target"]["result"]
                        success = result["outcome_success"]
                        item["judge"] = {
                            "status": "succeeded",
                            "freshness": "current",
                            "confidence": result["outcome_confidence"],
                            "outcome": "success" if success is True else "failure" if success is False else None,
                        }
                        item["resources"].append(
                            {"kind": "evidence", "episode_index": index, "expected_revision": evidence.etag}
                        )
                except (SavedInputError, *_release_read_errors()):
                    item["judge"] = {
                        "status": "unavailable",
                        "freshness": "unknown",
                        "confidence": None,
                        "outcome": None,
                    }
            return item

        semaphore = asyncio.Semaphore(self._offload.settings.read_concurrency)

        async def bounded_item(index: int) -> dict[str, Any]:
            async with semaphore:
                return await read_item(index)

        tasks = [asyncio.create_task(bounded_item(index)) for index in selected]
        try:
            items = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return {
            "schema": CAPABILITY_SCHEMA,
            "items": items,
            "total": len(indices),
            "next_cursor": cursor + limit if cursor + limit < len(indices) else None,
        }

    def recover(self, scope: str, job_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            self._execution(store, scope, job_id)
            return _status_body(store.request_recovery(job_id, scope=scope, principal=scope))
        except ReleaseJobError as error:
            raise _map_job_error(error) from error

    def _execution(self, store: ReleaseJobStore, scope: str, job_id: str) -> ConfiguredExecution:
        store.get(job_id, scope=scope, principal=scope)
        if self._planner is None:
            raise ReleaseApiError(503, "release worker is not configured")
        try:
            return self._planner.execution(job_id, store.frozen_request(job_id))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ReleaseApiError(409, "release execution binding is unavailable") from error

    def result(self, scope: str, job_id: str, artifact: str | None = None) -> dict[str, Any]:
        from physical_ai_curation.release.coordinator import _publication_runtime
        from physical_ai_curation.release.models import INVENTORY_PATH, MANIFEST_PATH, RECEIPT_PATH, REPORT_PATH
        from physical_ai_curation.release.verification import verify_published

        store = self._store()
        try:
            configured = self._execution(store, scope, job_id)
            status = store.get(job_id, scope=scope, principal=scope)
            if status.state != "committed":
                raise ReleaseApiError(409, "release_result_not_verified")
            record = store.storage.snapshot()[job_id]
            package_id, attempt_id = record["attempt_key"].split("/", 1)
            attempt = PublicationAttempt(package_id, attempt_id, record["attempt_prefix"])
            actual = (
                configured.destination.as_text()
                if isinstance(configured.destination, BlobLocation)
                else str(configured.destination.resolve())
            )
            if actual != record["destination"]:
                raise ReleaseApiError(409, "configured_destination_changed")
            with _publication_runtime(configured.destination, attempt, store, job_id, status.fence, None) as publisher:
                result = verify_published(publisher.store, record["commit_digest"])
                artifacts = {
                    "manifest": MANIFEST_PATH,
                    "inventory": INVENTORY_PATH,
                    "receipt": RECEIPT_PATH,
                    "report": REPORT_PATH,
                }
                if artifact is not None:
                    if artifact not in artifacts:
                        raise ReleaseApiError(404, "release_artifact_not_found")
                    content = publisher.store.read_control_bytes(artifacts[artifact], max_bytes=1024**2)
                    return {
                        "schema": CAPABILITY_SCHEMA,
                        "artifact": artifact,
                        "content": content.decode("utf-8") if artifact == "report" else json.loads(content),
                    }
                return {
                    "schema": CAPABILITY_SCHEMA,
                    "result": result.model_dump(mode="json"),
                    "artifacts": list(artifacts),
                }
        except ReleaseJobError as error:
            raise _map_job_error(error) from error
        except (ReleaseVerificationError, OSError, ValueError, KeyError) as error:
            raise ReleaseApiError(409, "release_verification_failed") from error

    def source_releases(self, scope: str, dataset_id: str) -> dict[str, Any]:
        """List verified releases of one source visible to the caller; no paths or aliases in output."""
        with ExitStack() as stack:
            items = self._verified_sources(scope, dataset_id, stack)
            return {
                "schema": CAPABILITY_SCHEMA,
                "items": [
                    {
                        "handle": item.handle,
                        "dataset_name": item.dataset_name,
                        "package_id": item.package_id,
                        "manifest_sha256": item.manifest_sha256,
                        "created_at": item.created_at,
                    }
                    for item in items
                ],
            }

    def source_release(self, scope: str, dataset_id: str, handle: str) -> dict[str, Any]:
        """Read one verified release by handle, including its saved annotations.

        The handle depends only on durable association intent and verified publication
        bytes, so it survives restarts and job-row removal.
        """
        with ExitStack() as stack:
            item = self._find_source(scope, dataset_id, handle, stack)
            try:
                files = _release_files(item, metadata_max_bytes=self._settings.metadata_max_bytes)
                info_bytes = files.read_bytes("meta/info.json")
                info = parse_strict_json(info_bytes)
                total = info["total_episodes"]
                manifest_bytes = item.store.read_control_bytes(MANIFEST_PATH, max_bytes=MAX_CONTROL_BYTES)
                if hashlib.sha256(manifest_bytes).hexdigest() != item.manifest_sha256:
                    raise ReleaseVerificationError("release manifest changed after admission")
                frozen_path = parse_strict_json(manifest_bytes)["frozen_input_path"]
                frozen_bytes = files.read_bytes(frozen_path)
                frozen_evidence = parse_strict_json(frozen_bytes)
                remaining = MAX_CONTROL_BYTES - len(info_bytes) - len(frozen_bytes)
                if remaining < 0:
                    raise ReleaseVerificationError("projection response exceeds bound")
                paths = files.paths()
                annotations = []
                labels = None
                for path in sorted(paths):
                    if path != LABELS_PATH and not re.fullmatch(r"annotations/episodes/episode_[0-9]{6}\.json", path):
                        continue
                    content = files.read_bytes(path)
                    if len(content) > remaining:
                        raise ReleaseVerificationError("projection response exceeds bound")
                    remaining -= len(content)
                    projection = parse_strict_json(content)
                    if path == LABELS_PATH:
                        labels = projection
                    else:
                        annotations.append(projection)
            except _release_read_errors() as error:
                raise ReleaseApiError(409, "release_verification_failed") from error
            if type(total) is not int:
                raise ReleaseApiError(409, "release_verification_failed")
            return {
                "schema": CAPABILITY_SCHEMA,
                "handle": handle,
                "dataset_name": item.dataset_name,
                "package_id": item.package_id,
                "manifest_sha256": item.manifest_sha256,
                "total_episodes": total,
                "frozen_evidence": frozen_evidence,
                "annotations": annotations,
                "labels": labels,
            }

    def source_episodes(self, scope: str, dataset_id: str, handle: str, *, cursor: int, limit: int) -> dict[str, Any]:
        """Page native episode metadata of one verified release, read through its store for local and Blob."""
        if not cursor >= 0 or not 1 <= limit <= 100:
            raise ReleaseApiError(422, "invalid_episode_page")
        with ExitStack() as stack:
            item = self._find_source(scope, dataset_id, handle, stack)
            records = _episode_records(_release_files(item, metadata_max_bytes=self._settings.metadata_max_bytes))
        return {
            "schema": CAPABILITY_SCHEMA,
            "items": [_episode_body(record) for record in records[cursor : cursor + limit]],
            "total": len(records),
            "next_cursor": cursor + limit if cursor + limit < len(records) else None,
        }

    def source_episode_media(
        self, scope: str, dataset_id: str, handle: str, *, episode_index: int, camera: str
    ) -> bytes:
        """Return the MP4 container that holds one episode's camera stream, bounded in size.

        LeRobot v3 packs several episodes into one container file; the episode's
        ``from_timestamp`` and ``to_timestamp`` locate it inside that file.
        """
        with ExitStack() as stack:
            item = self._find_source(scope, dataset_id, handle, stack)
            files = _release_files(item, metadata_max_bytes=self._settings.metadata_max_bytes)
            records = _episode_records(files, indices=[episode_index])
            if not records:
                raise ReleaseApiError(404, "release_episode_not_found")
            path = records[0].video_paths.get(camera)
            if path is None:
                raise ReleaseApiError(404, "release_media_not_found")
            return _release_media(files, path.as_posix(), media_max_bytes=self._settings.media_max_bytes)

    def _find_source(self, scope: str, dataset_id: str, handle: str, stack: ExitStack) -> _SourceRelease:
        for item in self._verified_sources(scope, dataset_id, stack):
            if item.handle == handle:
                return item
        raise ReleaseApiError(404, "release_not_found")

    def _verified_sources(self, scope: str, dataset_id: str, stack: ExitStack) -> list[_SourceRelease]:
        """Discover verified releases of one source from every configured destination alias.

        Each call re-verifies published bytes; nothing is cached. Blob containers opened here
        are closed when ``stack`` exits. Records whose intent does not match this source path
        and alias are hidden. Blob listing failures raise.
        """
        if not _DATASET_ID.fullmatch(dataset_id):
            raise ReleaseApiError(422, "dataset id is invalid")
        source_path = dataset_id.replace("--", "/")
        scope_sha256 = hashlib.sha256(scope.encode()).hexdigest()
        found: list[_SourceRelease] = []
        for alias, location in sorted(self._settings.destinations.items()):
            check_cancelled()
            try:
                collection = release_collection_location(location, source_path)
            except (OSError, ValueError, ReleaseVerificationError):
                continue
            if isinstance(collection, Path):
                local = LocalSourceAssociations(collection, limits=RecordLimits())
                for verified in local.discover_verified(scope_sha256=scope_sha256):
                    check_cancelled()
                    if not _intent_matches(verified, source_path, alias):
                        continue
                    found.append(
                        _source_release(
                            alias,
                            source_path,
                            scope_sha256,
                            dataset_name=verified.root.name,
                            package_id=verified.package_id,
                            manifest_sha256=verified.manifest_sha256,
                            store=LocalReleaseStore(verified.root),
                            local_root=verified.root,
                            created_at=verified.accepted_at,
                        )
                    )
                continue
            try:
                container = self._blob_container_factory(location)
                stack.callback(_close_container, container)
                associations = BlobSourceAssociations(container, collection, limits=RecordLimits())
                verified_blobs = associations.discover_verified(scope_sha256=scope_sha256)
            except ReleaseVerificationError as error:
                raise ReleaseApiError(409, "release_verification_failed") from error
            for verified in verified_blobs:
                check_cancelled()
                if not _intent_matches(verified, source_path, alias):
                    continue
                found.append(
                    _source_release(
                        alias,
                        source_path,
                        scope_sha256,
                        dataset_name=verified.root.rstrip("/").rsplit("/", 1)[-1],
                        package_id=verified.package_id,
                        manifest_sha256=verified.manifest_sha256,
                        store=BlobReleaseStore(container, verified.root),
                        local_root=None,
                        created_at=verified.accepted_at,
                    )
                )
        return found

    def _store(self) -> ReleaseJobStore:
        if self._settings.job_root is None:
            raise ReleaseApiError(503, "release job store is not configured")
        return self._store_factory(self._settings.job_root)

    def _destination(self, alias: str, dataset_id: str) -> Path | BlobLocation:
        location = self._settings.destinations.get(alias)
        if location is None:
            raise ReleaseApiError(404, "destination is not available")
        try:
            return release_collection_location(location, dataset_id.replace("--", "/"))
        except (OSError, ValueError, ReleaseVerificationError) as error:
            raise ReleaseApiError(422, "release destination is unavailable or unsupported") from error

    async def _prepare(self, scope: str, spec: ReleaseSpec, destination: Path | BlobLocation) -> PreparedRelease:
        if self._planner is None:
            raise ReleaseApiError(503, "release planning is not configured")
        try:
            return await self._offload.prepare(self._planner.prepare, scope, spec, destination)
        except StaleReleaseInputError as error:
            raise ReleaseApiError(409, "stale_preview") from error
        except (ReleaseVerificationError, SavedInputError) as error:
            raise ReleaseApiError(422, "release selection failed verification") from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ReleaseApiError(422, "release source is unavailable or unsupported") from error


@dataclass(frozen=True)
class _SourceRelease:
    """One verified release visible to a scope; ``local_root`` is None for Blob releases."""

    handle: str
    dataset_name: str
    package_id: str
    manifest_sha256: str
    store: LocalReleaseStore | BlobReleaseStore
    local_root: Path | None
    created_at: str | None = None


def _source_handle(
    *,
    alias: str,
    source_relative_path: str,
    scope_sha256: str,
    dataset_name: str,
    package_id: str,
    manifest_sha256: str,
) -> str:
    """Opaque handle bound to alias, source, scope, and verified publication identity."""
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "alias": alias,
                "source_relative_path": source_relative_path,
                "scope_sha256": scope_sha256,
                "dataset_name": dataset_name,
                "package_id": package_id,
                "manifest_sha256": manifest_sha256,
            }
        )
    ).hexdigest()


def _source_release(
    alias: str,
    source_relative_path: str,
    scope_sha256: str,
    *,
    dataset_name: str,
    package_id: str,
    manifest_sha256: str,
    store: LocalReleaseStore | BlobReleaseStore,
    local_root: Path | None,
    created_at: str | None = None,
) -> _SourceRelease:
    handle = _source_handle(
        alias=alias,
        source_relative_path=source_relative_path,
        scope_sha256=scope_sha256,
        dataset_name=dataset_name,
        package_id=package_id,
        manifest_sha256=manifest_sha256,
    )
    return _SourceRelease(handle, dataset_name, package_id, manifest_sha256, store, local_root, created_at)


def _intent_matches(
    verified: VerifiedAssociation | BlobVerifiedAssociation, source_relative_path: str, alias: str
) -> bool:
    """Admit only records whose accepted source path and destination alias match this request."""
    return verified.source_relative_path == source_relative_path and verified.destination_alias == alias


def _default_blob_container(location: BlobLocation) -> Any:
    """Open the configured container with the Entra credential chain used by release Blob storage."""
    return _blob_container_client(location.account_url, location.container)


def _close_container(container: Any) -> None:
    close = getattr(container, "close", None)
    if callable(close):
        close()


class _ReleaseFiles:
    """Dataset-relative LeRobot view over one verified release store.

    The accepted inventory binds membership, length and the declared content or
    storage-revision assurance. Metadata reads share one byte budget.
    """

    def __init__(
        self,
        store: LocalReleaseStore | BlobReleaseStore,
        *,
        name: str,
        manifest_sha256: str,
        metadata_max_bytes: int,
    ) -> None:
        self._store = store
        manifest_bytes = store.read_control_bytes(MANIFEST_PATH, max_bytes=MAX_CONTROL_BYTES)
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            raise ReleaseVerificationError("release manifest changed after admission")
        manifest = parse_strict_json(manifest_bytes)
        inventory_bytes = store.read_control_bytes(INVENTORY_PATH, max_bytes=MAX_CONTROL_BYTES)
        if hashlib.sha256(inventory_bytes).hexdigest() != manifest["inventory_sha256"]:
            raise ReleaseVerificationError("release inventory changed after admission")
        inventory = Inventory.model_validate(parse_strict_json(inventory_bytes))
        self._objects = {entry.path: entry for entry in inventory.entries}
        self._budget = metadata_max_bytes
        self.name = name

    def read_bytes(self, relative: str) -> bytes:
        if relative not in self._objects:
            raise FileNotFoundError(relative)
        entry = self._objects[relative]
        if entry.length > self._budget:
            raise ReleaseVerificationError("release metadata exceeds bound")
        check_cancelled()
        if entry.assurance != "content_sha256":
            if not isinstance(self._store, BlobReleaseStore):
                raise ReleaseVerificationError("release storage revision evidence unavailable")
            data = self._store.read_control_bytes(relative, max_bytes=self._budget, expected_revision=entry.revision)
        else:
            data = self._store.read_control_bytes(relative, max_bytes=self._budget)
        self._budget -= len(data)
        if len(data) != entry.length or (
            not isinstance(entry, StorageRevisionEntry) and hashlib.sha256(data).hexdigest() != entry.sha256
        ):
            raise ReleaseVerificationError("release metadata changed after admission")
        return data

    def paths(self) -> set[str]:
        return set(self._objects)

    def exists(self, relative: str) -> bool:
        return relative in self._objects

    def find(self, directory: str, pattern: str) -> list[str]:
        prefix = f"{directory}/"
        return sorted(
            path
            for path in self._objects
            if path.startswith(prefix) and fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern)
        )

    def path(self, relative: str) -> Path:
        return Path(relative)

    def size(self, relative: str) -> int | None:
        entry = self._objects.get(relative)
        return entry.length if entry is not None else None

    def stream(self, relative: str) -> Iterator[bytes]:
        entry = self._objects[relative]
        digest = hashlib.sha256()
        total = 0
        if entry.assurance != "content_sha256":
            if not isinstance(self._store, BlobReleaseStore):
                raise ReleaseVerificationError("release storage revision evidence unavailable")
            chunks = self._store.iter_bytes(relative, chunk_size=CHUNK_BYTES, expected_revision=entry.revision)
        else:
            chunks = self._store.iter_bytes(relative, chunk_size=CHUNK_BYTES)
        for chunk in chunks:
            check_cancelled()
            digest.update(chunk)
            total += len(chunk)
            if total > entry.length:
                raise ReleaseVerificationError("release media exceeds inventoried length")
            yield chunk
        if total != entry.length or (
            not isinstance(entry, StorageRevisionEntry) and digest.hexdigest() != entry.sha256
        ):
            raise ReleaseVerificationError("release media changed after admission")


def _release_read_errors() -> tuple[type[Exception], ...]:
    errors = (ReleaseVerificationError, OSError, ValueError, KeyError, TypeError)
    try:
        from azure.core.exceptions import AzureError
    except ImportError:
        return errors
    return (*errors, AzureError)


def _release_files(item: _SourceRelease, *, metadata_max_bytes: int = _MAX_METADATA_BYTES) -> _ReleaseFiles:
    try:
        return _ReleaseFiles(
            item.store,
            name=item.dataset_name,
            manifest_sha256=item.manifest_sha256,
            metadata_max_bytes=metadata_max_bytes,
        )
    except _release_read_errors() as error:
        raise ReleaseApiError(409, "release_verification_failed") from error


def _release_media(files: _ReleaseFiles, relative: str, *, media_max_bytes: int) -> bytes:
    """Stream one container through its store, bounded by its listed length."""
    size = files.size(relative)
    if size is None:
        raise ReleaseApiError(404, "release_media_not_found")
    if size > media_max_bytes:
        raise ReleaseApiError(422, "release_media_exceeds_bound")
    data = bytearray()
    try:
        for chunk in files.stream(relative):
            data.extend(chunk)
            if len(data) > size:
                raise ReleaseVerificationError(f"{relative} exceeds its published length")
        if len(data) != size:
            raise ReleaseVerificationError(f"{relative} is shorter than its published length")
    except _release_read_errors() as error:
        raise ReleaseApiError(409, "release_verification_failed") from error
    return bytes(data)


def _episode_records(files: _ReleaseFiles, indices: list[int] | None = None) -> list[EpisodeRecord]:
    """Resolve published episodes through the shared dataset-io reader over store-backed files."""
    try:
        records = list(iter_episodes(files, indices=indices))
    except _release_read_errors() as error:
        raise ReleaseApiError(409, "release_verification_failed") from error
    return sorted(records, key=lambda record: record.episode_index)


def _episode_body(record: EpisodeRecord) -> dict[str, Any]:
    return {
        "episode_index": record.episode_index,
        "episode_id": record.episode_id,
        "instruction": record.instruction,
        "fps": record.fps,
        "length": record.length,
        "from_timestamp": record.from_timestamp,
        "to_timestamp": record.to_timestamp,
        "cameras": sorted(record.video_paths),
    }


def _preview_digest(spec: ReleaseSpec, prepared: PreparedRelease) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "selection": asdict(spec),
                "generation": hashlib.sha256(prepared.frozen_request).hexdigest(),
            }
        )
    ).hexdigest()


def _map_job_error(error: ReleaseJobError) -> ReleaseApiError:
    if isinstance(error, JobNotFoundError):
        return ReleaseApiError(404, "release job was not found")
    if isinstance(error, JobConflictError):
        return ReleaseApiError(409, "release job state conflicts with this request")
    if isinstance(error, CapacityError):
        return ReleaseApiError(507, "insufficient capacity for the reserved estimate")
    return ReleaseApiError(422, "release job request was rejected")


def _status_body(status: JobStatus) -> dict[str, Any]:
    """Client-safe projection: no frozen request, paths, or diagnostics payloads."""
    return {
        "job_id": status.job_id,
        "state": status.state,
        "cancel_requested": status.cancel_requested,
        "effects_stopped": status.effects_stopped,
        "cleanup_state": status.cleanup_state,
        "capacity_state": status.capacity_state,
        "destination_current_bytes": status.destination_current_bytes,
        "destination_peak_bytes": status.destination_peak_bytes,
        "schema": CAPABILITY_SCHEMA,
        "scratch_bytes": status.scratch_bytes,
        "peak_scratch_bytes": status.peak_scratch_bytes,
        "io_counters": dict(status.io_counters),
        "diagnostics": dict(status.diagnostics),
    }


def get_release_service() -> ReleaseService:
    """Build the request-scoped service from current configuration and the registered planner."""
    try:
        settings = load_release_api_settings(os.environ)
    except ValueError as error:
        raise ReleaseApiError(503, "release configuration is invalid") from error
    from .viewer_release_planner import ViewerReleasePlanner

    return ReleaseService(settings, planner=get_release_planner() or ViewerReleasePlanner(settings))
