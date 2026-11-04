"""Blob-backed source associations with the same semantics as LocalSourceAssociations.

Records live at `<collection prefix>/_release_associations/<package-id>.json`. A lease on a sibling
lock blob bounds global record count and bytes. Each record write is conditional: creation is
create-only and checkpoints replace the record only when its etag is unchanged.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from physical_ai_curation.release.azure_publication import BlobReleaseStore
from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import MANIFEST_PATH
from physical_ai_curation.release.publication import ASSOCIATION_NAMESPACE, ownership_path, parse_ownership
from physical_ai_curation.release.record_storage import (
    _BLOB_POLL_SECONDS,
    _LEASE_SECONDS,
    BlobLocation,
    RecordLimits,
    _child,
    _LeaseMonitor,
)
from physical_ai_curation.release.source_associations import (
    _MAX_RECORD_BYTES,
    _RECORD_NAME,
    _SCHEMA,
    SourceAssociationIntent,
    _AssociationRecord,
    _Checkpoint,
)
from physical_ai_curation.release.verification import MAX_CONTROL_BYTES, verify_published

_LOCK_NAME = ".lock"
_MAX_OWNERSHIP_BYTES = 4096


def _azure_errors() -> Any:
    import azure.core.exceptions as errors

    return errors


def _match_conditions() -> Any:
    from azure.core import MatchConditions

    return MatchConditions


@dataclass(frozen=True)
class BlobVerifiedAssociation:
    """A published Blob release admitted through its association and verified bytes.

    ``root`` ends with ``/`` and is relative to the container. Intent fields are
    for server-side binding checks only and are never returned to clients.
    """

    root: str
    manifest_sha256: str
    package_id: str
    source_relative_path: str
    destination_alias: str
    scope_sha256: str
    accepted_at: str


def _bounded_content(chunks: Iterable[bytes], limit: int) -> bytes:
    data = bytearray()
    for chunk in chunks:
        data.extend(chunk)
        if len(data) > limit:
            raise ReleaseVerificationError("association record exceeds its bound")
    return bytes(data)


class BlobSourceAssociations:
    """Persist accepted source intent and commit checkpoints in a Blob collection."""

    def __init__(self, container: Any, collection: BlobLocation, *, limits: RecordLimits) -> None:
        if not isinstance(collection, BlobLocation):
            raise ReleaseVerificationError("unsupported association collection")
        self.container = container
        self.collection = collection
        self.limits = limits
        self._namespace = _child(collection.prefix, ASSOCIATION_NAMESPACE)
        self._lock_name = f"{self._namespace}/{_LOCK_NAME}"
        self._record_bound = min(_MAX_RECORD_BYTES, limits.max_record_bytes)

    def ensure_intent(self, intent: SourceAssociationIntent) -> None:
        """Persist an accepted identity once, or reconcile an identical existing record."""
        self._check_destination(intent)
        with self._locked() as monitor:
            existing = self._read(intent.attempt.package_id)
            if existing is not None:
                if existing[0].intent != intent:
                    raise ReleaseVerificationError("association conflict")
                return
            record = _AssociationRecord(schema_version=_SCHEMA, intent=intent, checkpoint=None)
            self._put(intent.attempt.package_id, record, etag=None, monitor=monitor)

    def remove_pending(self, intent: SourceAssociationIntent) -> None:
        """Remove a matching pending intent once its package output is absent.

        Final checkpoints, conflicting intents and existing output are never removed.
        """
        self._check_destination(intent)
        with self._locked() as monitor:
            existing = self._read(intent.attempt.package_id)
            if existing is None:
                return
            record, etag = existing
            if record.intent != intent or record.checkpoint is not None:
                raise ReleaseVerificationError("association cleanup conflict")
            if self._output_present(intent):
                raise ReleaseVerificationError("association cleanup requires removed output")
            monitor.check()
            errors = _azure_errors()
            try:
                self.container.get_blob_client(self._name(intent.attempt.package_id)).delete_blob(
                    etag=etag,
                    match_condition=_match_conditions().IfNotModified,
                    timeout=self.limits.timeout_seconds,
                )
            except errors.ResourceNotFoundError:
                return
            except errors.ResourceModifiedError as error:
                raise ReleaseVerificationError("association cleanup conflict") from error

    def bind_manifest(self, intent: SourceAssociationIntent, digest: str, *, job_id: str, fence: int) -> None:
        """Bind the first commit digest conditionally; same-job recovery preserves its original fence."""
        self._check_destination(intent)
        try:
            checkpoint = _Checkpoint(manifest_sha256=digest, job_id=job_id, fence=fence)
        except ValidationError as exc:
            raise ReleaseVerificationError("invalid association commit checkpoint") from exc
        with self._locked() as monitor:
            existing = self._read(intent.attempt.package_id)
            if existing is None or existing[0].intent != intent:
                raise ReleaseVerificationError("association conflict")
            record, etag = existing
            if record.checkpoint is not None:
                bound = record.checkpoint
                if bound.manifest_sha256 != digest or bound.job_id != job_id or bound.fence > fence:
                    raise ReleaseVerificationError("association conflict")
                return
            self._put(
                intent.attempt.package_id,
                record.model_copy(update={"checkpoint": checkpoint}),
                etag=etag,
                monitor=monitor,
            )

    def require_checkpoint(self, intent: SourceAssociationIntent, digest: str, *, job_id: str, fence: int) -> None:
        """Refuse adoption when the accepted final checkpoint is absent or different."""
        self._check_destination(intent)
        existing = self._read(intent.attempt.package_id)
        if existing is None or existing[0].intent != intent or existing[0].checkpoint is None:
            raise ReleaseVerificationError("association commit checkpoint is missing")
        bound = existing[0].checkpoint
        if bound.manifest_sha256 != digest or bound.job_id != job_id or bound.fence > fence:
            raise ReleaseVerificationError("association conflict")

    def discover(self, *, source_key: str, scope_sha256: str) -> list[str]:
        """List scoped roots whose checkpoint, ownership and marker verification all match.

        Returned roots end with `/`, matching `BlobPublisher.root`. Any failure hides that record.
        """
        errors = _azure_errors()
        try:
            names = sorted(self._listing())
        except (ReleaseVerificationError, errors.AzureError):
            return []
        admitted = []
        for name in names:
            try:
                found = self._admit_verified(name[: -len(".json")], source_key=source_key, scope_sha256=scope_sha256)
            except (ReleaseVerificationError, errors.AzureError):
                continue
            if found is not None:
                admitted.append(found.root)
        return sorted(admitted)

    def discover_verified(self, *, scope_sha256: str) -> list[BlobVerifiedAssociation]:
        """List every verified association in one scope, independent of source identity.

        Unlike ``discover``, listing failures raise so a read never presents an unavailable
        container as an empty release list. Records that fail verification are hidden.
        """
        errors = _azure_errors()
        try:
            names = sorted(self._listing())
        except errors.AzureError as error:
            raise ReleaseVerificationError("association listing unavailable") from error
        admitted = []
        for name in names:
            try:
                found = self._admit_verified(name[: -len(".json")], source_key=None, scope_sha256=scope_sha256)
            except (ReleaseVerificationError, errors.AzureError):
                continue
            if found is not None:
                admitted.append(found)
        return sorted(admitted, key=lambda item: item.root)

    def _admit_verified(
        self, package_id: str, *, source_key: str | None, scope_sha256: str
    ) -> BlobVerifiedAssociation | None:
        existing = self._read(package_id)
        if existing is None or existing[0].checkpoint is None:
            return None
        record = existing[0]
        intent = record.intent
        if (source_key is not None and intent.source_key != source_key) or intent.scope_sha256 != scope_sha256:
            return None
        self._check_destination(intent)
        ownership_name = _child(self.collection.prefix, ownership_path(intent.attempt.prefix))
        owner = parse_ownership(
            self._read_control(ownership_name, limit=_MAX_OWNERSHIP_BYTES),
            intent.attempt.prefix,
        )
        if (owner.key, owner.prefix) != (intent.attempt.key, intent.attempt.prefix):
            return None
        root = f"{_child(self.collection.prefix, owner.prefix)}/"
        store = BlobReleaseStore(self.container, root)
        verified = verify_published(store, record.checkpoint.manifest_sha256)
        manifest = parse_strict_json(store.read_control_bytes(MANIFEST_PATH, max_bytes=MAX_CONTROL_BYTES))
        if verified.identity.package_id != owner.package_id:
            return None
        if not isinstance(manifest, dict) or manifest.get("frozen_input_sha256") != intent.frozen_input_sha256:
            return None
        return BlobVerifiedAssociation(
            root=root,
            manifest_sha256=record.checkpoint.manifest_sha256,
            package_id=owner.package_id,
            source_relative_path=intent.source_relative_path,
            destination_alias=intent.destination_alias,
            scope_sha256=intent.scope_sha256,
            accepted_at=intent.accepted_at,
        )

    def _check_destination(self, intent: SourceAssociationIntent) -> None:
        if intent.effective_destination != self.collection.as_text():
            raise ReleaseVerificationError("association destination conflict")

    def _name(self, package_id: str) -> str:
        return f"{self._namespace}/{package_id}.json"

    def _output_present(self, intent: SourceAssociationIntent) -> bool:
        prefix = f"{_child(self.collection.prefix, intent.attempt.prefix)}/"
        return next(iter(self.container.list_blobs(name_starts_with=prefix)), None) is not None

    def _listing(self) -> dict[str, int]:
        """Return admitted record names and sizes, failing closed on count or byte bound excess."""
        sizes = {}
        total = 0
        for item in self.container.list_blobs(name_starts_with=f"{self._namespace}/"):
            relative = item.name.removeprefix(f"{self._namespace}/")
            if "/" in relative or not _RECORD_NAME.fullmatch(relative):
                continue
            if (
                item.size > self._record_bound
                or total + item.size > self.limits.max_total_bytes
                or len(sizes) >= self.limits.max_jobs
            ):
                raise ReleaseVerificationError("association bounds exceeded")
            total += item.size
            sizes[relative] = item.size
        return sizes

    def _admit(self, package_id: str, size: int) -> None:
        sizes = self._listing()
        name = f"{package_id}.json"
        previous = sizes.get(name, 0)
        if sum(sizes.values()) - previous + size > self.limits.max_total_bytes:
            raise ReleaseVerificationError("association bounds exceeded")
        if name not in sizes and len(sizes) >= self.limits.max_jobs:
            raise ReleaseVerificationError("association bounds exceeded")

    def _read(self, package_id: str) -> tuple[_AssociationRecord, str] | None:
        """Read one record under its etag, returning the record and etag or None when absent."""
        errors = _azure_errors()
        blob = self.container.get_blob_client(self._name(package_id))
        try:
            properties = blob.get_blob_properties()
        except errors.ResourceNotFoundError:
            return None
        if properties.size > self._record_bound:
            raise ReleaseVerificationError("association record exceeds its bound")
        try:
            download = blob.download_blob(etag=properties.etag, match_condition=_match_conditions().IfNotModified)
        except errors.ResourceModifiedError as error:
            raise ReleaseVerificationError("association record changed during read") from error
        content = _bounded_content(download.chunks(), self._record_bound)
        try:
            record = _AssociationRecord.model_validate(parse_strict_json(content))
        except ValidationError as exc:
            raise ReleaseVerificationError("unsupported source association record") from exc
        if record.intent.attempt.package_id != package_id:
            raise ReleaseVerificationError("association record identity conflict")
        return record, properties.etag

    def _read_control(self, name: str, *, limit: int) -> bytes:
        blob = self.container.get_blob_client(name)
        properties = blob.get_blob_properties()
        if properties.size > limit:
            raise ReleaseVerificationError("release ownership exceeds its bound")
        download = blob.download_blob(etag=properties.etag, match_condition=_match_conditions().IfNotModified)
        return _bounded_content(download.chunks(), limit)

    def _put(
        self,
        package_id: str,
        record: _AssociationRecord,
        *,
        etag: str | None,
        monitor: _LeaseMonitor,
    ) -> None:
        """Create or conditionally replace one record; an uncertain response is inspected, never retried blindly."""
        errors = _azure_errors()
        content = canonical_json_bytes(record.model_dump(mode="json"))
        if len(content) > self._record_bound:
            raise ReleaseVerificationError("source association record exceeds its bound")
        self._admit(package_id, len(content))
        monitor.check()
        blob = self.container.get_blob_client(self._name(package_id))
        timeout = self.limits.timeout_seconds
        try:
            if etag is None:
                blob.upload_blob(content, overwrite=False, length=len(content), timeout=timeout)
            else:
                blob.upload_blob(
                    content,
                    overwrite=True,
                    etag=etag,
                    match_condition=_match_conditions().IfNotModified,
                    timeout=timeout,
                )
        except errors.AzureError as error:
            existing = self._read(package_id)
            if existing is not None and existing[0] == record:
                return
            raise ReleaseVerificationError(
                "association write refused or outcome unknown; retry after inspection"
            ) from error

    @contextmanager
    def _locked(self) -> Iterator[_LeaseMonitor]:
        """Hold the namespace lock lease for one bounded mutation."""
        errors = _azure_errors()
        lock = self.container.get_blob_client(self._lock_name)
        try:
            lock.get_blob_properties()
        except errors.ResourceNotFoundError:
            with suppress(errors.ResourceExistsError):
                lock.upload_blob(b"", overwrite=False, timeout=self.limits.timeout_seconds)
        monitor = self._acquire(lock)
        monitor.start()
        try:
            yield monitor
        finally:
            monitor.stop()
            monitor.release()

    def _acquire(self, lock: Any) -> _LeaseMonitor:
        errors = _azure_errors()
        timeout = self.limits.timeout_seconds
        deadline = time.monotonic() + timeout
        while True:
            started = time.monotonic()
            try:
                lease = lock.acquire_lease(lease_duration=_LEASE_SECONDS, timeout=timeout)
            except errors.HttpResponseError as error:
                if error.status_code != 409:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("association lock contention exceeded timeout") from error
                time.sleep(_BLOB_POLL_SECONDS)
            else:
                return _LeaseMonitor(lease, started=started, timeout=timeout)
