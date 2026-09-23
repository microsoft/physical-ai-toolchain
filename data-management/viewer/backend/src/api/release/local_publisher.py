"""No-overwrite local release publication with marker-last visibility."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ..storage.review_base import validate_review_identifier
from .integrity import verify_release


class PublicationConflictError(RuntimeError):
    """Raised when a release identity or destination already exists."""


class LocalReleasePublisher:
    """Publish releases under an exclusive claim without overwriting destinations."""

    def __init__(self, releases_root: Path) -> None:
        self.releases_root = releases_root

    def publish(self, dataset_id: str, release_id: str, staging_root: Path, *, owner: str) -> Path:
        dataset_id = validate_review_identifier(dataset_id)
        release_id = validate_review_identifier(release_id)
        owner = validate_review_identifier(owner)
        if not staging_root.is_dir():
            raise FileNotFoundError(staging_root)
        evidence = self._verify_staging(staging_root)
        dataset_root = self.releases_root / dataset_id
        dataset_root.mkdir(parents=True, exist_ok=True)
        claims_root = dataset_root / ".claims"
        claims_root.mkdir(exist_ok=True)
        claim_path = claims_root / f"{release_id}.json"
        claim = _json_bytes({"owner": owner, "release_id": release_id})
        try:
            with claim_path.open("xb") as stream:
                stream.write(claim)
        except FileExistsError as exc:
            raise PublicationConflictError(f"Release already claimed: {release_id}") from exc

        destination = dataset_root / release_id
        if destination.exists():
            raise PublicationConflictError(f"Release destination already exists: {release_id}")
        try:
            shutil.copytree(staging_root, destination)
        except FileExistsError as exc:
            raise PublicationConflictError(f"Release destination appeared during promotion: {release_id}") from exc
        if self._verify_staging(destination) != evidence:
            raise RuntimeError(f"Copied release verification failed: {release_id}")
        if claim_path.read_bytes() != claim:
            raise PublicationConflictError(f"Release claim ownership changed: {release_id}")
        marker = _json_bytes(
            {
                "dataset_id": dataset_id,
                "evidence_sha256": evidence,
                "owner": owner,
                "release_id": release_id,
                "schema_version": "1.0.0",
            }
        )
        with (destination / ".published.json").open("xb") as stream:
            stream.write(marker)
        shutil.rmtree(staging_root, ignore_errors=True)
        return destination

    def list_published_releases(self) -> list[tuple[str, str]]:
        if not self.releases_root.exists():
            return []
        published: list[tuple[str, str]] = []
        for dataset_root in self.releases_root.iterdir():
            if not dataset_root.is_dir():
                continue
            for path in dataset_root.iterdir():
                marker = path / ".published.json"
                if not path.is_dir() or path.name == ".claims" or not marker.is_file():
                    continue
                try:
                    payload = json.loads(marker.read_bytes())
                except (OSError, ValueError):
                    continue
                if (
                    payload.get("dataset_id") == dataset_root.name
                    and payload.get("release_id") == path.name
                    and payload.get("owner")
                ):
                    published.append((dataset_root.name, path.name))
        return sorted(published)

    @staticmethod
    def _verify_staging(staging_root: Path) -> str:
        verify_release(staging_root)
        digest = hashlib.sha256()
        for path in sorted(candidate for candidate in staging_root.rglob("*") if candidate.is_file()):
            digest.update(path.relative_to(staging_root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


def _json_bytes(payload: dict[str, str]) -> bytes:
    return f"{json.dumps(payload, separators=(',', ':'), sort_keys=True)}\n".encode()
