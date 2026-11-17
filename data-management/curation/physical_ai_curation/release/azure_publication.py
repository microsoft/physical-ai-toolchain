"""Azure Blob publication adapter over injected synchronous container clients.

Azure SDK types are imported inside functions, so importing this module needs no
cloud package, credential, or configuration. Copies are create-only, conditioned
on the source revision, and admitted using explicit storage-side copy evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from physical_ai_curation.release.canonical import canonical_json_bytes, normalize_release_path, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import (
    CHECKSUMS_PATH,
    CONTROL_PATHS,
    MARKER_PATH,
    RECEIPT_PATH,
    ReleaseResult,
    SourceObject,
    SourceObjectRecord,
)
from physical_ai_curation.release.publication import (
    PublicationAttempt,
    PublicationControl,
    ownership_bytes,
    ownership_path,
    parse_ownership,
)
from physical_ai_curation.release.store import CHUNK_BYTES, ObjectStat, blob_revision, blob_source_identity
from physical_ai_curation.release.verification import (
    CandidateVerification,
    build_marker,
    verify_candidate,
    verify_published,
)

OWNER_PREFIX = "curation_"
OWNER_PACKAGE = "curation_package"
OWNER_ATTEMPT = "curation_attempt"
OWNER_OPERATION = "curation_operation"
OWNER_SHA256 = "curation_sha256"
OWNER_EFFECT = "curation_effect_digest"
OWNER_LENGTH = "curation_length"
OWNER_SOURCE_REVISION = "curation_source_revision"
OWNER_SOURCE_IDENTITY = "curation_source_identity"

_BEARER = re.compile(r"Bearer \S+")


@dataclass(frozen=True)
class CopyProgress:
    """Copy state; only a complete state may count toward verification."""

    state: Literal["complete", "pending"]
    copy_id: str | None


class _CopyPending(Exception):
    def __init__(self, progress: CopyProgress) -> None:
        super().__init__("copy is pending")
        self.progress = progress


def _azure_errors() -> Any:
    import azure.core.exceptions as errors

    return errors


def _match_conditions() -> Any:
    from azure.core import MatchConditions

    return MatchConditions


def _properties(blob: Any) -> Any | None:
    try:
        return blob.get_blob_properties()
    except _azure_errors().ResourceNotFoundError:
        return None


def _copy_status(properties: Any) -> str | None:
    return getattr(getattr(properties, "copy", None), "status", None)


def _copy_id(properties: Any) -> str | None:
    return getattr(getattr(properties, "copy", None), "id", None)


def _operation_id(attempt: PublicationAttempt, path: str, length: int, digest: str, source_etag: str = "") -> str:
    identity = {
        "package": attempt.package_id,
        "attempt": attempt.attempt_id,
        "path": path,
        "length": length,
        "effect_digest": digest,
        "source_etag": source_etag,
    }
    return sha256_hex(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())


def source_effect_digest(source: SourceObjectRecord) -> str:
    """Return the journaled identity for a source.

    Content-bound sources use their byte SHA-256. Revision-bound sources use a fingerprint of
    the storage revision, which is never presented as a content digest.
    """
    if isinstance(source, SourceObject):
        return source.sha256
    return sha256_hex(
        canonical_json_bytes(
            {"assurance": source.assurance, "revision": source.revision, "storage_identity": source.storage_identity}
        )
    )


def _ownership(
    attempt: PublicationAttempt,
    length: int,
    digest: str,
    operation: str,
    *,
    source_revision: str | None = None,
    source_identity: str | None = None,
) -> dict[str, str]:
    metadata = {
        OWNER_PACKAGE: attempt.package_id,
        OWNER_ATTEMPT: attempt.attempt_id,
        OWNER_OPERATION: operation,
        OWNER_EFFECT: digest,
        OWNER_LENGTH: str(length),
    }
    if source_revision is not None:
        metadata[OWNER_SOURCE_REVISION] = source_revision
        if source_identity is not None:
            metadata[OWNER_SOURCE_IDENTITY] = source_identity
    else:
        metadata[OWNER_SHA256] = digest
    return metadata


def _is_attempt_owned(metadata: dict[str, str], attempt: PublicationAttempt) -> bool:
    return metadata.get(OWNER_PACKAGE) == attempt.package_id and metadata.get(OWNER_ATTEMPT) == attempt.attempt_id


def _is_owned(metadata: dict[str, str], attempt: PublicationAttempt, operation: str) -> bool:
    return _is_attempt_owned(metadata, attempt) and metadata.get(OWNER_OPERATION) == operation


def _verified_stream(chunks: Iterable[bytes], length: int, sha256: str) -> Iterator[bytes]:
    """Stream bytes to the service; a mismatch aborts before the blob is committed."""
    digest = hashlib.sha256()
    total = 0
    pending = None
    for chunk in chunks:
        if len(chunk) > CHUNK_BYTES or total + len(chunk) > length:
            raise ReleaseVerificationError("object stream exceeds its declared bound")
        total += len(chunk)
        digest.update(chunk)
        if pending is not None:
            yield pending
        pending = chunk
    if total != length or digest.hexdigest() != sha256:
        raise ReleaseVerificationError("streamed bytes do not match declared identity")
    if pending is not None:
        yield pending


def _copy_operation_digest(properties: Any) -> str | None:
    """Bind a listed destination to its ownership operation and server copy ID without reading payload."""
    metadata = getattr(properties, "metadata", None) or {}
    operation = metadata.get(OWNER_OPERATION)
    if not all(
        metadata.get(key) for key in (OWNER_OPERATION, OWNER_PACKAGE, OWNER_ATTEMPT, OWNER_LENGTH, OWNER_EFFECT)
    ):
        return None
    identity = {
        "copy_id": _copy_id(properties),
        "operation": operation,
        "package": metadata.get(OWNER_PACKAGE),
        "attempt": metadata.get(OWNER_ATTEMPT),
        "length": metadata.get(OWNER_LENGTH),
        "sha256": metadata.get(OWNER_SHA256),
        "effect_digest": metadata.get(OWNER_EFFECT),
    }
    if OWNER_SOURCE_REVISION in metadata:
        identity["source_revision"] = metadata[OWNER_SOURCE_REVISION]
        identity["source_identity"] = metadata.get(OWNER_SOURCE_IDENTITY)
    return sha256_hex(canonical_json_bytes(identity))


def _copy_evidence_matches(properties: Any, source: SourceObjectRecord) -> bool:
    """Accept a completed copy on revision-bound properties; payload bytes are not re-read."""
    metadata = properties.metadata
    expected_revision = None if isinstance(source, SourceObject) else source.revision
    return (
        properties.size == source.length
        and metadata.get(OWNER_LENGTH) == str(source.length)
        and metadata.get(OWNER_EFFECT) == source_effect_digest(source)
        and metadata.get(OWNER_SHA256) == (source.sha256 if isinstance(source, SourceObject) else None)
        and metadata.get(OWNER_SOURCE_REVISION) == expected_revision
        and metadata.get(OWNER_SOURCE_IDENTITY)
        == (None if isinstance(source, SourceObject) else source.storage_identity)
        and _copy_id(properties) is not None
    )


class BlobReleaseStore:
    """Listing and bounded streaming over one attempt root in a Blob container."""

    def __init__(self, container: Any, root: str) -> None:
        self.container = container
        self.root = normalize_release_path(root.rstrip("/")) + "/"

    def list_objects(self) -> list[ObjectStat]:
        """List the closed object set, refusing incomplete copies rather than hiding them."""
        objects = []
        for item in self.container.list_blobs(name_starts_with=self.root, include=["copy", "metadata"]):
            if not item.name.startswith(self.root):
                raise ReleaseVerificationError("listing escaped the attempt root")
            if _copy_status(item) not in (None, "success"):
                raise ReleaseVerificationError("release object copy is not complete")
            metadata = getattr(item, "metadata", None) or {}
            uploaded = _copy_id(item) is None and OWNER_OPERATION in metadata and OWNER_SOURCE_REVISION not in metadata
            objects.append(
                ObjectStat(
                    path=normalize_release_path(item.name[len(self.root) :]),
                    length=item.size,
                    revision=blob_revision(item.etag, getattr(item, "version_id", None)),
                    copy_operation_sha256=_copy_operation_digest(item),
                    source_revision=metadata.get(OWNER_SOURCE_REVISION),
                    uploaded_sha256=metadata.get(OWNER_SHA256) if uploaded else None,
                    source_identity=metadata.get(OWNER_SOURCE_IDENTITY),
                )
            )
        return objects

    def iter_bytes(self, path: str, *, chunk_size: int, expected_revision: str | None = None) -> Iterator[bytes]:
        path = normalize_release_path(path)
        if not 0 < chunk_size <= CHUNK_BYTES:
            raise ReleaseVerificationError("invalid streaming buffer size")
        blob = self.container.get_blob_client(self.root + path)
        properties = _properties(blob)
        if properties is None:
            raise ReleaseVerificationError("release object is missing")
        if (
            expected_revision is not None
            and blob_revision(properties.etag, getattr(properties, "version_id", None)) != expected_revision
        ):
            raise ReleaseVerificationError("release object revision changed after admission")
        if _copy_status(properties) not in (None, "success"):
            raise ReleaseVerificationError("release object copy is not complete")
        download = blob.download_blob(
            etag=properties.etag, match_condition=_match_conditions().IfNotModified, max_concurrency=1
        )
        total = 0
        for chunk in download.chunks():
            for start in range(0, len(chunk), chunk_size):
                piece = chunk[start : start + chunk_size]
                total += len(piece)
                yield piece
        if total != properties.size:
            raise ReleaseVerificationError("release object changed during read")

    def read_control_bytes(self, path: str, *, max_bytes: int, expected_revision: str | None = None) -> bytes:
        data = bytearray()
        for chunk in self.iter_bytes(path, chunk_size=CHUNK_BYTES, expected_revision=expected_revision):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ReleaseVerificationError("control artifact exceeds bound")
        return bytes(data)


class BlobPublisher:
    """Attempt-owned writes, server-side copies, and marker publication in one container."""

    _store_type = BlobReleaseStore

    def __init__(
        self,
        container: Any,
        attempt: PublicationAttempt,
        control: PublicationControl,
        *,
        root_prefix: str = "",
        source_authorization: str | None = None,
    ) -> None:
        if source_authorization is not None and _BEARER.fullmatch(source_authorization) is None:
            raise ReleaseVerificationError("source authorization must be a bearer token")
        prefix = normalize_release_path(root_prefix) if root_prefix else ""
        self.container = container
        self.attempt = attempt
        self.control = control
        self.source_authorization = source_authorization
        self.parent_prefix = prefix + "/" if prefix else ""
        self.root = f"{prefix}/{attempt.prefix}/" if prefix else f"{attempt.prefix}/"
        self.store = self._store_type(container, self.root)

    def _claim_root(self) -> None:
        path = self.parent_prefix + ownership_path(self.attempt.prefix)
        blob = self.container.get_blob_client(path)
        existing = _properties(blob)
        content = ownership_bytes(self.attempt)
        if existing is None:
            if next(iter(self.container.list_blobs(name_starts_with=self.root)), None) is not None:
                raise ReleaseVerificationError(
                    "output collision: release dataset root already exists without ownership"
                )
            try:
                blob.upload_blob(content, overwrite=False, length=len(content))
                return
            except _azure_errors().AzureError:
                existing = _properties(blob)
                if existing is None:
                    raise ReleaseVerificationError("release ownership outcome is unresolved") from None
        if existing.size != len(content):
            raise ReleaseVerificationError("release root ownership conflict")
        download = blob.download_blob(etag=existing.etag, match_condition=_match_conditions().IfNotModified)
        observed = b"".join(_verified_stream(download.chunks(), len(content), sha256_hex(content)))
        if observed != content:
            raise ReleaseVerificationError("release root ownership conflict")

    def write(self, path: str, chunks: Iterable[bytes], *, length: int, sha256: str) -> None:
        """Create verified bytes once, or recover only a recorded and matching owned object."""
        path = normalize_release_path(path)
        if path == MARKER_PATH:
            raise ReleaseVerificationError("marker requires publication arbitration")
        operation = _operation_id(self.attempt, path, length, sha256)
        with self.control.effect(self.attempt, path, length, sha256) as replay:
            self._claim_root()
            if self._marker_exists():
                raise ReleaseVerificationError("published package is immutable")
            blob = self.container.get_blob_client(self.root + path)
            existing = _properties(blob)
            if existing is not None:
                if not replay or existing.size != length or not _is_owned(existing.metadata, self.attempt, operation):
                    raise ReleaseVerificationError("output collision or ownership conflict")
                return
            self._create(blob, path, _verified_stream(chunks, length, sha256), length, sha256, operation)

    def copy(
        self, source_container: Any, source: SourceObjectRecord, source_etag: str, *, source_path: str | None = None
    ) -> CopyProgress:
        """Copy against the source revision and verify storage-side completion evidence."""
        path = normalize_release_path(source.path)
        if path in CONTROL_PATHS:
            raise ReleaseVerificationError("control artifacts are not copied")
        original = normalize_release_path(source_path) if source_path is not None else path
        source_url = self._source_url(source_container, original)
        if not isinstance(source, SourceObject) and source.storage_identity != blob_source_identity(
            source_container.url, original
        ):
            raise ReleaseVerificationError("source identity changed")
        source_properties = _properties(source_container.get_blob_client(original))
        if (
            source_properties is None
            or source_properties.etag != source_etag
            or source_properties.size != source.length
            or sha256_hex(
                canonical_json_bytes(
                    {"etag": source_properties.etag, "version_id": getattr(source_properties, "version_id", None)}
                )
            )
            != source.revision
        ):
            raise ReleaseVerificationError("source revision changed or is missing")
        digest = source_effect_digest(source)
        operation = _operation_id(self.attempt, path, source.length, digest, source_etag)
        metadata = self._destination_metadata(source_properties.metadata, source, operation)
        try:
            with self.control.effect(self.attempt, path, source.length, digest) as replay:
                self._claim_root()
                if self._marker_exists():
                    raise ReleaseVerificationError("published package is immutable")
                blob = self.container.get_blob_client(self.root + path)
                if _properties(blob) is None:
                    self._dispatch_copy(blob, source_url, metadata, source_etag)
                elif not replay:
                    raise ReleaseVerificationError("output collision or ownership conflict")
                progress = self._settle(blob, path, source, operation)
                if progress.state == "pending":
                    raise _CopyPending(progress)
                return progress
        except _CopyPending as pending:
            return pending.progress

    def reconcile_copy(
        self, source: SourceObjectRecord, source_etag: str, *, cancel: bool = False
    ) -> CopyProgress | None:
        """Observe an exact journaled copy; durably record its ID before cancellation effects."""
        path = normalize_release_path(source.path)
        if path in CONTROL_PATHS or self._marker_exists():
            raise ReleaseVerificationError("published or control objects are not reconciled as payload")
        blob = self.container.get_blob_client(self.root + path)
        properties = _properties(blob)
        digest = source_effect_digest(source)
        operation = _operation_id(self.attempt, path, source.length, digest, source_etag)
        if properties is not None and not _is_owned(properties.metadata, self.attempt, operation):
            raise ReleaseVerificationError("output collision or ownership conflict")
        copy_id = _copy_id(properties) if properties is not None else None
        with self.control.reconciliation(
            self.attempt,
            path,
            source.length,
            digest,
            copy_id=copy_id,
            cancel=cancel,
        ) as observed:
            if properties is None:
                observed("absent")
                return None
            if cancel:
                if _copy_status(properties) == "pending":
                    try:
                        blob.abort_copy(copy_id)
                    except _azure_errors().HttpResponseError as error:
                        if error.status_code != 409:
                            raise
                self._delete_owned_residue(blob, copy_id, operation)
                observed("absent")
                return None
            progress = self._settle(blob, path, source, operation)
            observed("complete" if progress.state == "complete" else "pending")
            return progress

    def abort(self, path: str, copy_id: str) -> None:
        """Abort one owned pending copy by its recorded identifier, then remove owned residue."""
        path = normalize_release_path(path)
        if path in CONTROL_PATHS:
            raise ReleaseVerificationError("control artifacts are not aborted")
        if self._marker_exists():
            raise ReleaseVerificationError("published package is immutable")
        blob = self.container.get_blob_client(self.root + path)
        properties = _properties(blob)
        if properties is None:
            return
        if _copy_status(properties) != "pending" or _copy_id(properties) != copy_id:
            raise ReleaseVerificationError("copy id does not match the pending copy")
        if not _is_attempt_owned(properties.metadata, self.attempt):
            raise ReleaseVerificationError("output collision or ownership conflict")
        digest = properties.metadata.get(OWNER_EFFECT)
        length_text = properties.metadata.get(OWNER_LENGTH, "")
        if digest is None or not length_text.isdigit():
            raise ReleaseVerificationError("owned copy identity is incomplete")
        operation = properties.metadata.get(OWNER_OPERATION)
        if not operation:
            raise ReleaseVerificationError("owned copy operation is incomplete")
        with self.control.effect(self.attempt, path, int(length_text), digest) as replay:
            if not replay:
                raise ReleaseVerificationError("abort requires a persisted intent")
            try:
                blob.abort_copy(copy_id)
            except _azure_errors().HttpResponseError as error:
                if error.status_code != 409:
                    raise
            self._delete_owned_residue(blob, copy_id, operation)

    def prepare(self, expected_manifest_sha256: str) -> CandidateVerification:
        """Verify destination bytes and persist the canonical derived controls."""
        self._require_settled()
        candidate = verify_candidate(self.store, expected_manifest_sha256)
        for path, content in ((RECEIPT_PATH, candidate.receipt_bytes), (CHECKSUMS_PATH, candidate.checksums_bytes)):
            self.write(path, (content,), length=len(content), sha256=sha256_hex(content))
        return verify_candidate(self.store, expected_manifest_sha256)

    def publish(self, expected_manifest_sha256: str) -> ReleaseResult:
        """Publish only a verified package under the durable coordinator's commit guard."""
        if self._marker_exists():
            with self.control.publication(self.attempt, expected_manifest_sha256):
                return verify_published(self.store, expected_manifest_sha256)
        candidate = self.prepare(expected_manifest_sha256)
        if self.attempt.source_dataset_id is None:
            raise ReleaseVerificationError("publication attempt has no source dataset identity")
        marker = build_marker(
            candidate,
            release_name=self.attempt.prefix,
            source_dataset_id=self.attempt.source_dataset_id,
        )
        self._publish_marker(marker, expected_manifest_sha256=expected_manifest_sha256)
        return verify_published(self.store, expected_manifest_sha256)

    def discover(self) -> list[str]:
        """Discover named datasets through ownership records and verified publication markers."""
        prefix = self.parent_prefix + "_releases/"
        admitted = []
        for item in self.container.list_blobs(name_starts_with=prefix):
            relative = item.name[len(prefix) :]
            if not relative.endswith(".json") or "/" in relative or item.size > 4096:
                continue
            try:
                blob = self.container.get_blob_client(item.name)
                content = b"".join(
                    blob.download_blob(
                        etag=item.etag,
                        match_condition=_match_conditions().IfNotModified,
                    ).chunks()
                )
                owner = parse_ownership(content, relative[:-5])
                root = self.parent_prefix + owner.prefix + "/"
                result = verify_published(BlobReleaseStore(self.container, root))
                if result.identity.package_id != owner.package_id:
                    continue
            except ReleaseVerificationError:
                continue
            admitted.append(root)
        return sorted(admitted)

    def _require_settled(self) -> None:
        for item in self.container.list_blobs(name_starts_with=self.root, include=["copy"]):
            if _copy_status(item) not in (None, "success"):
                raise ReleaseVerificationError("unsettled copy prevents package verification")

    def _publish_marker(self, marker: bytes, *, expected_manifest_sha256: str) -> None:
        """Create the final marker once under publication authority, inspecting uncertain outcomes."""
        errors = _azure_errors()
        operation = _operation_id(self.attempt, MARKER_PATH, len(marker), sha256_hex(marker))
        with self.control.publication(self.attempt, expected_manifest_sha256):
            blob = self.container.get_blob_client(self.root + MARKER_PATH)
            try:
                blob.upload_blob(
                    marker,
                    overwrite=False,
                    length=len(marker),
                    metadata=_ownership(self.attempt, len(marker), sha256_hex(marker), operation),
                )
            except errors.ResourceExistsError as error:
                if self._marker_matches(blob, marker):
                    return
                raise ReleaseVerificationError("incompatible existing marker") from error
            except errors.AzureError as error:
                if self._marker_matches(blob, marker):
                    return
                raise ReleaseVerificationError("marker outcome unknown; retry after inspection") from error

    def _source_url(self, source_container: Any, path: str) -> str:
        source_url = source_container.get_blob_client(path).url
        parts = urlsplit(source_url)
        if parts.query or parts.fragment:
            raise ReleaseVerificationError("source URL must not carry query parameters")
        destination = urlsplit(self.container.url)
        if (parts.scheme, parts.netloc) != (destination.scheme, destination.netloc):
            raise ReleaseVerificationError("cross-account copy refused")
        return source_url

    def _destination_metadata(
        self, source_metadata: dict[str, str], source: SourceObjectRecord, operation: str
    ) -> dict[str, str]:
        if any(key.casefold().startswith(OWNER_PREFIX) for key in source_metadata):
            raise ReleaseVerificationError("source metadata uses reserved ownership keys")
        source_revision = None if isinstance(source, SourceObject) else source.revision
        return {
            **source_metadata,
            **_ownership(
                self.attempt,
                source.length,
                source_effect_digest(source),
                operation,
                source_revision=source_revision,
                source_identity=None if isinstance(source, SourceObject) else source.storage_identity,
            ),
        }

    def _create(self, blob: Any, path: str, stream: Iterator[bytes], length: int, sha256: str, operation: str) -> None:
        errors = _azure_errors()
        try:
            blob.upload_blob(
                stream,
                overwrite=False,
                length=length,
                metadata=_ownership(self.attempt, length, sha256, operation),
            )
        except errors.ResourceExistsError as error:
            raise ReleaseVerificationError("output collision or ownership conflict") from error
        except errors.AzureError as error:
            properties = _properties(blob)
            if (
                properties is not None
                and properties.size == length
                and _is_owned(properties.metadata, self.attempt, operation)
            ):
                return
            raise ReleaseVerificationError("write outcome unknown; retry after inspection") from error

    def _dispatch_copy(self, blob: Any, source_url: str, metadata: dict[str, str], source_etag: str) -> None:
        conditions = _match_conditions()
        options: dict[str, Any] = {
            "source_etag": source_etag,
            "source_match_condition": conditions.IfNotModified,
            "match_condition": conditions.IfMissing,
        }
        if self.source_authorization is not None:
            options["requires_sync"] = True
            options["source_authorization"] = self.source_authorization
        try:
            blob.start_copy_from_url(source_url, metadata=metadata, **options)
        except _azure_errors().ResourceExistsError:
            return

    def _settle(self, blob: Any, path: str, source: SourceObjectRecord, operation: str) -> CopyProgress:
        properties = _properties(blob)
        if properties is None or not _is_owned(properties.metadata, self.attempt, operation):
            raise ReleaseVerificationError("output collision or ownership conflict")
        status = _copy_status(properties)
        if status == "pending":
            return CopyProgress("pending", _copy_id(properties))
        if status != "success":
            raise ReleaseVerificationError("copy did not complete")
        if not _copy_evidence_matches(properties, source):
            raise ReleaseVerificationError(f"copy evidence conflict: {path}")
        return CopyProgress("complete", _copy_id(properties))

    def _delete_owned_residue(self, blob: Any, copy_id: str, operation: str) -> None:
        properties = _properties(blob)
        if properties is None:
            return
        if not _is_owned(properties.metadata, self.attempt, operation) or _copy_id(properties) != copy_id:
            raise ReleaseVerificationError("output collision or ownership conflict")
        if _copy_status(properties) == "pending":
            raise ReleaseVerificationError("copy still pending after abort")
        try:
            blob.delete_blob(etag=properties.etag, match_condition=_match_conditions().IfNotModified)
        except _azure_errors().ResourceModifiedError as error:
            raise ReleaseVerificationError("owned output changed; cleanup refused") from error

    def _marker_exists(self) -> bool:
        return _properties(self.container.get_blob_client(self.root + MARKER_PATH)) is not None

    def _marker_matches(self, blob: Any, marker: bytes) -> bool:
        properties = _properties(blob)
        if properties is None or properties.size != len(marker):
            return False
        digest = hashlib.sha256()
        total = 0
        download = blob.download_blob(
            etag=properties.etag, match_condition=_match_conditions().IfNotModified, max_concurrency=1
        )
        for chunk in download.chunks():
            total += len(chunk)
            if total > len(marker):
                return False
            digest.update(chunk)
        return total == len(marker) and digest.hexdigest() == sha256_hex(marker)
