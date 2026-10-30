"""Attempt-owned local publication with explicit durable effect authority."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from physical_ai_curation.release.canonical import normalize_release_path, sha256_hex
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


@dataclass(frozen=True)
class PublicationAttempt:
    """Stable package/attempt namespace; durable state owns authorization and fencing."""

    package_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        for value in (self.package_id, self.attempt_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
                raise ReleaseVerificationError("invalid publication identity")

    @property
    def prefix(self) -> str:
        return f"{RELEASE_NAMESPACE}/{self.package_id}/{self.attempt_id}"


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


class LocalPublisher:
    """Write once at a stable configured destination, never through a promotion copy."""

    def __init__(self, destination: Path, attempt: PublicationAttempt, control: PublicationControl) -> None:
        self.destination = destination
        self.attempt = attempt
        self.control = control
        self.root = destination / attempt.prefix
        self.store = LocalReleaseStore(self.root)

    def write(self, path: str, chunks: Iterable[bytes], *, length: int, sha256: str) -> None:
        """Create verified bytes or recover only a previously recorded matching effect."""
        path = normalize_release_path(path)
        if path == MARKER_PATH:
            raise ReleaseVerificationError("marker requires publication arbitration")
        with self.control.effect(self.attempt, path, length, sha256) as replay:
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
            self.store.write_verified_object(MARKER_PATH, (marker,), length=len(marker), sha256=sha256_hex(marker))
            return verify_published(self.store, expected_manifest_sha256)

    def discover(self) -> list[Path]:
        """Admit only independently verified marked roots, leaving incomplete attempts hidden."""
        namespace = self.destination / RELEASE_NAMESPACE
        if not namespace.is_dir() or namespace.is_symlink():
            return []
        admitted = []
        for package in sorted(namespace.iterdir()):
            if not package.is_dir() or package.is_symlink():
                continue
            for attempt in sorted(package.iterdir()):
                if not attempt.is_dir() or attempt.is_symlink():
                    continue
                try:
                    verify_published(LocalReleaseStore(attempt))
                except (OSError, ReleaseVerificationError):
                    continue
                admitted.append(attempt)
        return admitted
