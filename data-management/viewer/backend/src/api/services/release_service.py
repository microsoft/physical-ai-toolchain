"""Trusted release-job service for the curated dataset release API.

Clients select destinations by alias only. Filesystem paths are resolved from
operator-owned configuration and never returned to, or accepted from, clients.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from physical_ai_curation.release.canonical import canonical_json_bytes
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.inputs import SavedSelection
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
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.runner import ConfiguredExecution

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


class ReleaseApiError(Exception):
    """Client-safe release API failure carrying an HTTP status and fixed message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ReleaseApiSettings:
    """Validated release API configuration read from the process environment."""

    job_root: Path | None
    destinations: Mapping[str, Path | BlobLocation] = field(default_factory=dict)

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
    return ReleaseApiSettings(job_root=job_root, destinations=destinations)


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

    def __post_init__(self) -> None:
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
    """Planner output: frozen request bytes, reserved estimate, and a client-safe summary."""

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
    ) -> None:
        self._settings = settings
        self._store_factory = store_factory or (
            lambda root: ReleaseJobStore(root, settings=ReleaseRuntimeSettings.from_environment(os.environ))
        )
        self._planner = planner

    @property
    def planner_configured(self) -> bool:
        return self._planner is not None

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

    def jobs(self, scope: str, *, cursor: int, limit: int) -> dict[str, Any]:
        store = self._store()
        try:
            page = store.page(scope=scope, principal=scope, cursor=cursor, limit=limit)
        except ReleaseJobError as error:
            raise _map_job_error(error) from error
        return {
            "items": [_status_body(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

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
        """Describe the frozen request without persisting anything."""
        destination = self._destination(spec.destination)
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
        store = self._store()
        destination = self._destination(spec.destination)
        prepared = await self._prepare(scope, spec, destination)
        if _preview_digest(spec, prepared) != preview_digest:
            raise ReleaseApiError(409, "stale_preview")
        if prepared.summary.get("processing_digest") != processing_digest:
            raise ReleaseApiError(409, "processing_confirmation_required")
        if isinstance(destination, Path) and not local_disk_acknowledged:
            raise ReleaseApiError(422, "local_disk_acknowledgment_required")
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
            reserved_bytes=prepared.reserved_bytes,
            route="blob" if isinstance(destination, BlobLocation) else "local",
            execution_binding=prepared.execution_binding,
        )
        try:
            return store.accept(request)
        except CapacityError as error:
            raise ReleaseApiError(507, "insufficient capacity for the reserved estimate") from error
        except JobConflictError as error:
            raise ReleaseApiError(409, "release request conflicts with an existing job") from error
        except ReleaseJobError as error:
            raise ReleaseApiError(422, "release request was rejected") from error

    async def inventory(self, dataset_id: str, *, cursor: int, limit: int) -> dict[str, Any]:
        if not cursor >= 0 or not 1 <= limit <= 100:
            raise ReleaseApiError(422, "invalid_inventory_page")
        datasets = getattr(self._planner, "datasets", None)
        if datasets is None or await datasets.get_dataset(dataset_id) is None:
            raise ReleaseApiError(404, "release source is unavailable")
        episodes = await datasets.list_episodes(dataset_id, limit=_MAX_EPISODES + 1, require_actual=True)
        if len(episodes) > _MAX_EPISODES:
            raise ReleaseApiError(422, "source_inventory_limit_exceeded")
        indices = sorted({episode.index for episode in episodes})
        selected = indices[cursor : cursor + limit]
        return {
            "schema": CAPABILITY_SCHEMA,
            "items": [{"episode_index": index} for index in selected],
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

    def _store(self) -> ReleaseJobStore:
        if self._settings.job_root is None:
            raise ReleaseApiError(503, "release job store is not configured")
        return self._store_factory(self._settings.job_root)

    def _destination(self, alias: str) -> Path | BlobLocation:
        location = self._settings.destinations.get(alias)
        if location is None:
            raise ReleaseApiError(404, "destination is not available")
        return location

    async def _prepare(self, scope: str, spec: ReleaseSpec, destination: Path | BlobLocation) -> PreparedRelease:
        if self._planner is None:
            raise ReleaseApiError(503, "release planning is not configured")
        try:
            return await self._planner.prepare(scope, spec, destination)
        except ReleaseVerificationError as error:
            raise ReleaseApiError(422, "release selection failed verification") from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ReleaseApiError(422, "release source is unavailable or unsupported") from error


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
