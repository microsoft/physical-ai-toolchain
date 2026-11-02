"""Attempt-owned local publication with explicit durable effect authority."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from physical_ai_curation.release.canonical import (
    canonical_json_bytes,
    normalize_release_path,
    parse_strict_json,
    sha256_hex,
)
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import MARKER_PATH, RECEIPT_PATH, REPORT_PATH, ReleaseResult, SourceObject
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore
from physical_ai_curation.release.verification import (
    CandidateVerification,
    build_marker,
    verify_candidate,
    verify_published,
)

RELEASE_NAMESPACE = "_releases"
ASSOCIATION_NAMESPACE = "_release_associations"
OPERATIONAL_RELEASE_NAMESPACES = frozenset({RELEASE_NAMESPACE, ASSOCIATION_NAMESPACE})


@dataclass(frozen=True)
class PublicationAttempt:
    """Attempt identity and the selected dataset name beneath a configured destination."""

    package_id: str
    attempt_id: str
    dataset_name: str | None = None

    def __post_init__(self) -> None:
        for value in (self.package_id, self.attempt_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
                raise ReleaseVerificationError("invalid publication identity")
        name = self.dataset_name or self.package_id
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name)
            or "--" in name
            or name == RELEASE_NAMESPACE
        ):
            raise ReleaseVerificationError("invalid release dataset name")

    @property
    def prefix(self) -> str:
        return self.dataset_name or self.package_id

    @property
    def key(self) -> str:
        return f"{self.package_id}/{self.attempt_id}"


def ownership_path(dataset_name: str) -> str:
    """Return the operational companion path, never a path within the published dataset."""
    name = PublicationAttempt("ownership", "record", dataset_name).prefix
    return f"{RELEASE_NAMESPACE}/{name}.json"


def ownership_bytes(attempt: PublicationAttempt) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": "curated-release-ownership/v1",
            "package_id": attempt.package_id,
            "attempt_id": attempt.attempt_id,
            "dataset_name": attempt.prefix,
        }
    )


def parse_ownership(content: bytes, dataset_name: str) -> PublicationAttempt:
    """Validate the independently read companion binding before admitting a named release."""
    if len(content) > 4096:
        raise ReleaseVerificationError("release ownership exceeds its bound")
    record = parse_strict_json(content)
    if (
        not isinstance(record, dict)
        or set(record) != {"schema_version", "package_id", "attempt_id", "dataset_name"}
        or record["schema_version"] != "curated-release-ownership/v1"
        or record["dataset_name"] != dataset_name
    ):
        raise ReleaseVerificationError("invalid release ownership")
    return PublicationAttempt(record["package_id"], record["attempt_id"], record["dataset_name"])


def local_dataset_admitted(root: Path) -> bool:
    """Admit ordinary source datasets; operational namespaces and recorded releases are separate."""
    if root.name in OPERATIONAL_RELEASE_NAMESPACES:
        return False
    companion = root.parent / RELEASE_NAMESPACE / f"{root.name}.json"
    try:
        companion.stat(follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


class PublicationControl(Protocol):
    """Persist intent before dispatch and serialize marker authority with cancellation.

    The implementation must fence every effect, retain unresolved intents on failure,
    and permit publication only after a durable commit decision and stopped writers.
    A replay is eligible only when this exact owned identity was already persisted.
    """

    def effect(
        self, attempt: PublicationAttempt, path: str, length: int, digest: str
    ) -> AbstractContextManager[bool]: ...

    def publication(self, attempt: PublicationAttempt, digest: str) -> AbstractContextManager[None]: ...

    def reconciliation(
        self,
        attempt: PublicationAttempt,
        path: str,
        length: int,
        digest: str,
        *,
        copy_id: str | None,
        cancel: bool,
    ) -> AbstractContextManager[Callable[[str], None]]: ...


class LocalPublisher:
    """Write once at a stable configured destination, never through a promotion copy."""

    def __init__(self, destination: Path, attempt: PublicationAttempt, control: PublicationControl) -> None:
        self.destination = destination
        self.attempt = attempt
        self.control = control
        self.root = destination / attempt.prefix
        self.store = LocalReleaseStore(self.root)

    def _claim_root(self) -> None:
        companion = ownership_path(self.attempt.prefix)
        store = LocalReleaseStore(self.destination)
        content = ownership_bytes(self.attempt)
        if os.path.lexists(self.destination / companion):
            if store.read_control_bytes(companion, max_bytes=4096) != content:
                raise ReleaseVerificationError("release root ownership conflict")
            return
        if os.path.lexists(self.root):
            raise ReleaseVerificationError("release dataset root already exists without ownership")
        store.write_verified_object(companion, (content,), length=len(content), sha256=sha256_hex(content))

    def write(self, path: str, chunks: Iterable[bytes], *, length: int, sha256: str) -> None:
        """Create verified bytes or recover only a previously recorded matching effect."""
        path = normalize_release_path(path)
        if path == MARKER_PATH:
            raise ReleaseVerificationError("marker requires publication arbitration")
        with self.control.effect(self.attempt, path, length, sha256) as replay:
            self._claim_root()
            listed = {item.path: item.length for item in self.store.list_objects()} if self.root.exists() else {}
            if MARKER_PATH in listed:
                raise ReleaseVerificationError("published package is immutable")
            if path in listed:
                if not replay or listed[path] != length:
                    raise ReleaseVerificationError("output collision or ownership conflict")
                digest = hashlib.sha256()
                for chunk in self.store.iter_bytes(path, chunk_size=CHUNK_BYTES):
                    digest.update(chunk)
                if digest.hexdigest() != sha256:
                    raise ReleaseVerificationError("owned output content conflict")
                return
            self.store.write_verified_object(path, chunks, length=length, sha256=sha256)

    def copy(self, source_root: Path, source: SourceObject) -> None:
        """Copy a frozen object independently while refusing source/destination overlap."""
        original = source_root.resolve()
        destination = self.root.resolve()
        if original == destination or original in destination.parents or destination in original.parents:
            raise ReleaseVerificationError("source and destination roots overlap")
        self.write(
            source.path,
            LocalReleaseStore(source_root).iter_bytes(source.path, chunk_size=CHUNK_BYTES),
            length=source.length,
            sha256=source.sha256,
        )

    def prepare(self, expected_manifest_sha256: str) -> CandidateVerification:
        """Verify destination content and journal derived controls before commit choice."""
        candidate = verify_candidate(self.store, expected_manifest_sha256)
        for path, content in ((RECEIPT_PATH, candidate.receipt_bytes), (REPORT_PATH, candidate.report_bytes)):
            self.write(path, (content,), length=len(content), sha256=sha256_hex(content))
        return verify_candidate(self.store, expected_manifest_sha256)

    def publish(self, expected_manifest_sha256: str) -> ReleaseResult:
        """Create the final marker only under coordinator-owned publication authority."""
        if any(item.path == MARKER_PATH for item in self.store.list_objects()):
            with self.control.publication(self.attempt, expected_manifest_sha256):
                return verify_published(self.store, expected_manifest_sha256)
        candidate = self.prepare(expected_manifest_sha256)
        marker = build_marker(candidate)
        with self.control.publication(self.attempt, expected_manifest_sha256):
            if any(item.path == MARKER_PATH for item in self.store.list_objects()):
                return verify_published(self.store, expected_manifest_sha256)
            self._write_marker(marker, expected_manifest_sha256)
            return verify_published(self.store, expected_manifest_sha256)

    def _write_marker(self, marker: bytes, expected_manifest_sha256: str) -> None:
        """Create the marker once; supervised publishers override this under a commit-bound intent."""
        self.store.write_verified_object(MARKER_PATH, (marker,), length=len(marker), sha256=sha256_hex(marker))

    def discover(self) -> list[Path]:
        """Admit only independently verified marked roots, leaving incomplete attempts hidden."""
        namespace = self.destination / RELEASE_NAMESPACE
        if not namespace.is_dir() or namespace.is_symlink():
            return []
        admitted = []
        for record in sorted(namespace.iterdir()):
            if record.suffix != ".json" or not record.is_file() or record.is_symlink():
                continue
            root = self.destination / record.stem
            if not root.is_dir() or root.is_symlink():
                continue
            try:
                owner = parse_ownership(
                    LocalReleaseStore(self.destination).read_control_bytes(ownership_path(record.stem), max_bytes=4096),
                    record.stem,
                )
                verified = verify_published(LocalReleaseStore(root))
            except (OSError, ReleaseVerificationError):
                continue
            if verified.identity.package_id == owner.package_id:
                admitted.append(root)
        return admitted
