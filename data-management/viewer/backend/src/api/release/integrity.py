"""Canonical release ledgers, manifests, and application-owned checksums."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from ..models.releases import ReleaseFile, ReleaseManifest, canonical_json_bytes
from ..models.reviews import ImmutableContract, ReviewDecision, ReviewDecisionValue

_ACCEPTED_PATH = "metadata/accepted.json"
_REJECTED_PATH = "metadata/rejected.json"
_MANIFEST_PATH = "metadata/release-manifest.json"
_CHECKSUMS_PATH = "checksums.sha256"
_PUBLICATION_MARKER = ".published.json"


class DecisionLedger(ImmutableContract):
    """Canonical decisions for one release disposition."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    disposition: Literal["accept", "reject"]
    decisions: tuple[ReviewDecision, ...]

    @model_validator(mode="after")
    def validate_disposition(self) -> DecisionLedger:
        if any(decision.decision.value != self.disposition for decision in self.decisions):
            raise ValueError("ledger decisions must match disposition")
        decision_ids = [decision.decision_id for decision in self.decisions]
        if decision_ids != sorted(decision_ids) or len(decision_ids) != len(set(decision_ids)):
            raise ValueError("ledger decisions must be unique and sorted by decision_id")
        return self


def finalize_release(
    package_root: Path,
    manifest: ReleaseManifest,
    accepted: tuple[ReviewDecision, ...],
    rejected: tuple[ReviewDecision, ...],
) -> ReleaseManifest:
    """Write canonical ledgers, complete the manifest inventory, and write checksums."""
    if not package_root.is_dir():
        raise FileNotFoundError(package_root)
    accepted = tuple(sorted(accepted, key=lambda decision: decision.decision_id))
    rejected = tuple(sorted(rejected, key=lambda decision: decision.decision_id))
    accepted_ids = tuple(decision.decision_id for decision in accepted)
    accepted_ids_match = len(accepted_ids) == len(manifest.accepted_decision_ids) and set(accepted_ids) == set(
        manifest.accepted_decision_ids
    )
    if not accepted_ids_match:
        raise ValueError("accepted ledger must match manifest accepted_decision_ids")
    if any(decision.decision is not ReviewDecisionValue.ACCEPT for decision in accepted):
        raise ValueError("accepted ledger contains a non-accepted decision")
    if any(decision.decision is not ReviewDecisionValue.REJECT for decision in rejected):
        raise ValueError("rejected ledger contains a non-rejected decision")

    _write_canonical(
        package_root / _ACCEPTED_PATH,
        DecisionLedger(disposition="accept", decisions=accepted),
    )
    _write_canonical(
        package_root / _REJECTED_PATH,
        DecisionLedger(disposition="reject", decisions=rejected),
    )
    excluded = {_MANIFEST_PATH, _CHECKSUMS_PATH, _PUBLICATION_MARKER}
    files = tuple(
        _release_file(path, package_root)
        for path in sorted(candidate for candidate in package_root.rglob("*") if candidate.is_file())
        if path.relative_to(package_root).as_posix() not in excluded
    )
    finalized = manifest.model_copy(update={"files": files})
    _write_canonical(package_root / _MANIFEST_PATH, finalized)

    covered_paths = (_MANIFEST_PATH, *(entry.path for entry in files))
    checksum_lines = [f"{_sha256_file(package_root / path)}  {path}\n" for path in covered_paths]
    _write_exclusive(package_root / _CHECKSUMS_PATH, "".join(checksum_lines).encode())
    return finalized


def verify_release(package_root: Path) -> ReleaseManifest:
    """Verify exact release inventory, sizes, and application-owned SHA-256 values."""
    manifest_path = package_root / _MANIFEST_PATH
    checksums_path = package_root / _CHECKSUMS_PATH
    try:
        manifest = ReleaseManifest.model_validate_json(manifest_path.read_bytes())
        checksum_lines = checksums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid release metadata: {exc}") from exc

    checksums: dict[str, str] = {}
    for line in checksum_lines:
        digest, separator, relative_path = line.partition("  ")
        _validate_relative_path(relative_path)
        if separator != "  " or len(digest) != 64 or relative_path in checksums:
            raise ValueError("Invalid checksums.sha256 entry")
        checksums[relative_path] = digest

    expected = {_MANIFEST_PATH, *(entry.path for entry in manifest.files)}
    if set(checksums) != expected:
        raise ValueError("Checksum inventory does not match release manifest")
    actual_files = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.name not in {_CHECKSUMS_PATH, _PUBLICATION_MARKER}
    }
    if actual_files != expected:
        raise ValueError("Package inventory does not match release manifest")
    manifest_files = {entry.path: entry for entry in manifest.files}
    for relative_path, expected_digest in checksums.items():
        path = package_root / relative_path
        if relative_path in manifest_files and path.stat().st_size != manifest_files[relative_path].size_bytes:
            raise ValueError(f"Size mismatch for {relative_path}")
        if _sha256_file(path) != expected_digest:
            raise ValueError(f"SHA-256 mismatch for {relative_path}")
    return manifest


def _release_file(path: Path, package_root: Path) -> ReleaseFile:
    relative_path = path.relative_to(package_root).as_posix()
    _validate_relative_path(relative_path)
    return ReleaseFile(path=relative_path, size_bytes=path.stat().st_size, sha256=_sha256_file(path))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_canonical(path: Path, contract: ImmutableContract) -> None:
    _write_exclusive(path, canonical_json_bytes(contract))


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or ".." in path.parts:
        raise ValueError("Release file path must be a normalized relative POSIX path")
