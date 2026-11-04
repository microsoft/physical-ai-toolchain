"""Private source associations, independent of job retention and package authority."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, ValidationError, model_validator

from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import MANIFEST_PATH, NonEmptyStr, ReleaseModel, ReleasePath, Sha256Hex
from physical_ai_curation.release.publication import (
    ASSOCIATION_NAMESPACE,
    PublicationAttempt,
    ownership_path,
    parse_ownership,
)
from physical_ai_curation.release.record_storage import RecordLimits, _flock, _read_bounded, _write_local
from physical_ai_curation.release.store import LocalReleaseStore, _open_child_file
from physical_ai_curation.release.verification import MAX_CONTROL_BYTES, verify_published

_SCHEMA = "curated-release-association/v1"
_MAX_RECORD_BYTES = 16 * 1024
_RECORD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.json")


def source_association_key(source_identity: str) -> str:
    """Derive a stable lookup key from trusted source identity, never a display label."""
    if not isinstance(source_identity, str) or not source_identity:
        raise ReleaseVerificationError("invalid source association identity")
    return sha256_hex(canonical_json_bytes({"source_identity": source_identity}))


class SourceAssociationIntent(ReleaseModel):
    """Accepted private identity and existing access scope; this record grants no permission."""

    source_key: Sha256Hex
    source_identity: NonEmptyStr
    source_revision: NonEmptyStr
    scope_sha256: Sha256Hex
    destination_alias: NonEmptyStr
    source_relative_path: ReleasePath
    effective_destination: NonEmptyStr
    attempt: PublicationAttempt
    frozen_input_sha256: Sha256Hex
    display_name: NonEmptyStr
    description: str
    accepted_at: NonEmptyStr
    source_display_label: NonEmptyStr

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.source_key != source_association_key(self.source_identity):
            raise ValueError("source association key does not match identity")
        if datetime.fromisoformat(self.accepted_at).utcoffset() is None:
            raise ValueError("association acceptance time must include a timezone")
        return self


class _Checkpoint(ReleaseModel):
    manifest_sha256: Sha256Hex
    job_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    fence: Annotated[int, Field(gt=0, strict=True)]


class _AssociationRecord(ReleaseModel):
    schema_version: Literal["curated-release-association/v1"]
    intent: SourceAssociationIntent
    checkpoint: _Checkpoint | None


def source_association_binding(intent: SourceAssociationIntent) -> bytes:
    """Serialize the complete accepted intent for an opaque durable job execution binding."""
    record = _AssociationRecord(schema_version=_SCHEMA, intent=intent, checkpoint=None)
    content = canonical_json_bytes(record.model_dump(mode="json"))
    if len(content) > _MAX_RECORD_BYTES:
        raise ReleaseVerificationError("source association record exceeds its bound")
    return content


def restore_source_association(binding: bytes, *, accepted_at: float) -> SourceAssociationIntent | None:
    """Restore current headless or viewer intent using the job's durable acceptance time.

    Empty bindings are valid for core jobs without source association. Viewer v3
    carries deterministic intent fields; acceptance time lives in the same job's
    accepted event, keeping preview and idempotent request identity stable.
    """
    if not binding:
        return None
    try:
        raw = parse_strict_json(binding)
        if not isinstance(raw, dict):
            raise ValueError("association binding must be an object")
        if raw.get("schema_version") == _SCHEMA:
            record = _AssociationRecord.model_validate(raw)
            if record.checkpoint is not None:
                raise ValueError("accepted association must be pending")
            source_association_binding(record.intent)
            return record.intent
        if raw.get("schema") != "curated-release-execution/v3" or set(raw) != {
            "schema",
            "selection",
            "context",
            "destination",
            "effective_destination",
            "source_kind",
            "finalized",
            "association",
        }:
            raise ValueError("unsupported execution binding")
        association = raw["association"]
        if not isinstance(association, dict) or "accepted_at" in association:
            raise ValueError("invalid accepted association")
        intent = SourceAssociationIntent.model_validate(
            {**association, "accepted_at": datetime.fromtimestamp(accepted_at, UTC).isoformat()}
        )
        source_association_binding(intent)
        return intent
    except (ReleaseVerificationError, ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ReleaseVerificationError("unsupported accepted source association") from exc


@dataclass(frozen=True)
class VerifiedAssociation:
    """A published release admitted through its private association and verified bytes.

    The intent fields come from the accepted association record. Callers use them to
    confirm source and alias binding; they are not client-facing values.
    """

    root: Path
    manifest_sha256: str
    package_id: str
    source_relative_path: str
    destination_alias: str
    scope_sha256: str
    accepted_at: str


class LocalSourceAssociations:
    """Create-only intents and atomic final checkpoints under a source collection lock.

    The caller must hold current job authority while creating intent and commit
    authority while binding a manifest. A sidecar never authorizes publication.
    Bounds reuse the configured durable-record limits. Reads verify package bytes
    independently and do not consult job history or the source dataset.
    """

    def __init__(self, collection: Path, *, limits: RecordLimits) -> None:
        self.collection = collection
        self.limits = limits
        self._root = LocalReleaseStore(collection / ASSOCIATION_NAMESPACE)

    @contextmanager
    def _locked(self, *, create: bool, exclusive: bool) -> Iterator[int]:
        directory = self._root._open_root(create=create)
        try:
            _flock(directory, exclusive=exclusive, timeout=self.limits.timeout_seconds)
            yield directory
            self._root._check_chain([directory], [])
        finally:
            os.close(directory)

    def _read(self, directory: int, package_id: str) -> _AssociationRecord | None:
        name = f"{PublicationAttempt(package_id, 'association').package_id}.json"
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        descriptor = _open_child_file(directory, name)
        try:
            content = _read_bounded(descriptor, min(_MAX_RECORD_BYTES, self.limits.max_record_bytes), name)
            record = _AssociationRecord.model_validate(parse_strict_json(content))
            if record.intent.attempt.package_id != package_id:
                raise ReleaseVerificationError("association record identity conflict")
            return record
        except ValidationError as exc:
            raise ReleaseVerificationError("unsupported source association record") from exc
        finally:
            os.close(descriptor)

    def _names(self, directory: int) -> list[str]:
        names = []
        total = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                if not _RECORD_NAME.fullmatch(entry.name):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ReleaseVerificationError("non-regular source association record")
                total += info.st_size
                if (
                    info.st_size > min(_MAX_RECORD_BYTES, self.limits.max_record_bytes)
                    or total > self.limits.max_total_bytes
                    or len(names) >= self.limits.max_jobs
                ):
                    raise ReleaseVerificationError("source association bounds exceeded")
                names.append(entry.name)
        return sorted(names)

    def _write(self, directory: int, record: _AssociationRecord) -> None:
        content = canonical_json_bytes(record.model_dump(mode="json"))
        if len(content) > min(_MAX_RECORD_BYTES, self.limits.max_record_bytes):
            raise ReleaseVerificationError("source association record exceeds its bound")
        names = self._names(directory)
        name = f"{record.intent.attempt.package_id}.json"
        previous = os.stat(name, dir_fd=directory, follow_symlinks=False).st_size if name in names else 0
        total = sum(os.stat(item, dir_fd=directory, follow_symlinks=False).st_size for item in names)
        if total - previous + len(content) > self.limits.max_total_bytes:
            raise ReleaseVerificationError("source association bounds exceeded")
        if name not in names and len(names) >= self.limits.max_jobs:
            raise ReleaseVerificationError("source association bounds exceeded")
        _write_local(directory, record.intent.attempt.package_id, content)

    def _check_destination(self, intent: SourceAssociationIntent) -> None:
        if intent.effective_destination != str(self.collection.resolve()):
            raise ReleaseVerificationError("association destination conflict")

    def ensure_intent(self, intent: SourceAssociationIntent) -> None:
        """Persist an accepted identity once, or reconcile an identical existing record."""
        self._check_destination(intent)
        with self._locked(create=True, exclusive=True) as directory:
            existing = self._read(directory, intent.attempt.package_id)
            if existing is not None:
                if existing.intent != intent:
                    raise ReleaseVerificationError("association conflict")
                return
            self._write(directory, _AssociationRecord(schema_version=_SCHEMA, intent=intent, checkpoint=None))

    def remove_pending(self, intent: SourceAssociationIntent) -> None:
        """Remove matching pending intent after fenced cancellation has removed package output.

        The caller must retain current job authority until this operation finishes.
        Final checkpoints, conflicting intents and existing output are never removed.
        """
        self._check_destination(intent)
        try:
            with self._locked(create=False, exclusive=True) as directory:
                existing = self._read(directory, intent.attempt.package_id)
                if existing is None:
                    return
                if existing.intent != intent or existing.checkpoint is not None:
                    raise ReleaseVerificationError("association cleanup conflict")
                try:
                    (self.collection / intent.attempt.prefix).lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise ReleaseVerificationError("association cleanup requires removed output")
                os.unlink(f"{intent.attempt.package_id}.json", dir_fd=directory)
                os.fsync(directory)
        except FileNotFoundError:
            return

    def bind_manifest(self, intent: SourceAssociationIntent, digest: str, *, job_id: str, fence: int) -> None:
        """Bind the first commit digest atomically; same-job recovery preserves its original fence."""
        self._check_destination(intent)
        try:
            checkpoint = _Checkpoint(manifest_sha256=digest, job_id=job_id, fence=fence)
        except ValidationError as exc:
            raise ReleaseVerificationError("invalid association commit checkpoint") from exc
        with self._locked(create=False, exclusive=True) as directory:
            existing = self._read(directory, intent.attempt.package_id)
            if existing is None or existing.intent != intent:
                raise ReleaseVerificationError("association conflict")
            if existing.checkpoint is not None:
                bound = existing.checkpoint
                if bound.manifest_sha256 != digest or bound.job_id != job_id or bound.fence > fence:
                    raise ReleaseVerificationError("association conflict")
                return
            self._write(directory, existing.model_copy(update={"checkpoint": checkpoint}))

    def require_checkpoint(self, intent: SourceAssociationIntent, digest: str, *, job_id: str, fence: int) -> None:
        """Refuse marked-package adoption when its accepted final checkpoint is absent or different."""
        self._check_destination(intent)
        with self._locked(create=False, exclusive=False) as directory:
            existing = self._read(directory, intent.attempt.package_id)
            if existing is None or existing.intent != intent or existing.checkpoint is None:
                raise ReleaseVerificationError("association commit checkpoint is missing")
            bound = existing.checkpoint
            if bound.manifest_sha256 != digest or bound.job_id != job_id or bound.fence > fence:
                raise ReleaseVerificationError("association conflict")

    def _admit(
        self, directory: int, name: str, *, source_key: str | None, scope_sha256: str
    ) -> VerifiedAssociation | None:
        """Admit one record only when ownership and the bound published package verify."""
        try:
            record = self._read(directory, name[:-5])
            if record is None or record.checkpoint is None:
                return None
            intent = record.intent
            if intent.scope_sha256 != scope_sha256 or (source_key is not None and intent.source_key != source_key):
                return None
            self._check_destination(intent)
            owner = parse_ownership(
                LocalReleaseStore(self.collection).read_control_bytes(
                    ownership_path(intent.attempt.prefix), max_bytes=4096
                ),
                intent.attempt.prefix,
            )
            if (owner.key, owner.prefix) != (intent.attempt.key, intent.attempt.prefix):
                return None
            root = self.collection / owner.prefix
            store = LocalReleaseStore(root)
            verified = verify_published(store, record.checkpoint.manifest_sha256)
            manifest = parse_strict_json(store.read_control_bytes(MANIFEST_PATH, max_bytes=MAX_CONTROL_BYTES))
            if (
                verified.identity.package_id == owner.package_id
                and manifest["frozen_input_sha256"] == intent.frozen_input_sha256
            ):
                return VerifiedAssociation(
                    root,
                    record.checkpoint.manifest_sha256,
                    owner.package_id,
                    intent.source_relative_path,
                    intent.destination_alias,
                    intent.scope_sha256,
                    intent.accepted_at,
                )
        except (OSError, ReleaseVerificationError):
            return None
        return None

    def _admitted(self, *, scope_sha256: str, source_key: str | None) -> list[VerifiedAssociation]:
        try:
            with self._locked(create=False, exclusive=False) as directory:
                found = []
                for name in self._names(directory):
                    try:
                        admitted = self._admit(directory, name, source_key=source_key, scope_sha256=scope_sha256)
                    except (OSError, ReleaseVerificationError):
                        continue
                    if admitted is not None:
                        found.append(admitted)
                return found
        except (OSError, ReleaseVerificationError):
            return []

    def discover(self, *, source_key: str, scope_sha256: str) -> list[Path]:
        """List only scoped associations backed by matching ownership and verified publication."""
        return [item.root for item in self._admitted(scope_sha256=scope_sha256, source_key=source_key)]

    def discover_verified(self, *, scope_sha256: str) -> list[VerifiedAssociation]:
        """List every verified association in one access scope, independent of source identity."""
        return self._admitted(scope_sha256=scope_sha256, source_key=None)
