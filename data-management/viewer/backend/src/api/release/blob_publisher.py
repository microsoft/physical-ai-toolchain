"""Create-only Azure Blob release publication with downloaded SHA verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from ..storage.review_base import validate_review_identifier
from .integrity import verify_release
from .local_publisher import PublicationConflictError, _json_bytes

_PUBLISHED_MARKER = ".published.json"

try:
    from azure.core.exceptions import ResourceExistsError
except ImportError:

    class ResourceExistsError(Exception):
        """Sentinel used when the Azure SDK is unavailable."""


class PublicationVerificationError(RuntimeError):
    """Raised when uploaded Blob content differs from staged content."""


class BlobReleasePublisher:
    """Publish immutable release blobs and create the visibility marker last."""

    def __init__(self, container_client: Any, *, export_prefix: str) -> None:
        prefix = PurePosixPath(export_prefix)
        if not export_prefix or prefix.is_absolute() or "\\" in export_prefix or ".." in prefix.parts:
            raise ValueError("Invalid Blob export prefix")
        self._container = container_client
        self._prefix = prefix.as_posix().rstrip("/")

    async def publish(self, dataset_id: str, release_id: str, staging_root: Path, *, owner: str) -> None:
        dataset_id = validate_review_identifier(dataset_id)
        release_id = validate_review_identifier(release_id)
        owner = validate_review_identifier(owner)
        if not staging_root.is_dir():
            raise FileNotFoundError(staging_root)
        verify_release(staging_root)
        claim_name = f"{self._prefix}/claims/{dataset_id}/{release_id}.json"
        claim = _json_bytes({"owner": owner, "release_id": release_id})
        await self._upload_create_only(claim_name, claim, release_id)

        release_prefix = f"{self._prefix}/releases/{dataset_id}/{release_id}/"
        expected: dict[str, tuple[int, str]] = {}
        for path in sorted(candidate for candidate in staging_root.rglob("*") if candidate.is_file()):
            relative_path = path.relative_to(staging_root).as_posix()
            payload = path.read_bytes()
            name = f"{release_prefix}{relative_path}"
            expected[name] = (len(payload), hashlib.sha256(payload).hexdigest())
            await self._upload_create_only(name, payload, release_id)

        listed: dict[str, int] = {}
        async for item in self._container.list_blobs(name_starts_with=release_prefix):
            listed[item.name] = item.size
        if set(listed) != set(expected):
            raise PublicationVerificationError("Blob listing does not match staged release")
        for name, (size, digest) in expected.items():
            if listed[name] != size:
                raise PublicationVerificationError(f"Blob size mismatch for {name}")
            if await self._download_sha256(name, digest) != digest:
                raise PublicationVerificationError(f"Blob SHA-256 mismatch for {name}")

        claim_download = await self._container.get_blob_client(claim_name).download_blob()
        if await claim_download.readall() != claim:
            raise PublicationConflictError(f"Release claim ownership changed: {release_id}")
        marker_name = f"{release_prefix}{_PUBLISHED_MARKER}"
        marker = _json_bytes(
            {"dataset_id": dataset_id, "owner": owner, "release_id": release_id, "schema_version": "1.0.0"}
        )
        await self._upload_create_only(marker_name, marker, release_id)

    async def list_published_releases(self) -> list[tuple[str, str]]:
        releases_prefix = f"{self._prefix}/releases/"
        published: list[tuple[str, str]] = []
        async for item in self._container.list_blobs(name_starts_with=releases_prefix):
            if not item.name.endswith(f"/{_PUBLISHED_MARKER}"):
                continue
            parts = PurePosixPath(item.name).parts
            dataset_id = parts[-3]
            release_id = parts[-2]
            try:
                download = await self._container.get_blob_client(item.name).download_blob()
                payload = json.loads(await download.readall())
            except (OSError, ValueError, KeyError):
                continue
            if (
                payload.get("dataset_id") == dataset_id
                and payload.get("release_id") == release_id
                and payload.get("owner")
            ):
                published.append((dataset_id, release_id))
        return sorted(set(published))

    async def read_published_file(self, dataset_id: str, release_id: str, relative_path: str) -> bytes:
        """Read one file from a marker-visible published release."""
        dataset_id = validate_review_identifier(dataset_id)
        release_id = validate_review_identifier(release_id)
        path = PurePosixPath(relative_path)
        if not relative_path or path.is_absolute() or "\\" in relative_path or ".." in path.parts:
            raise ValueError("Invalid published release path")
        release_prefix = f"{self._prefix}/releases/{dataset_id}/{release_id}"
        if path.as_posix() != _PUBLISHED_MARKER:
            marker = self._container.get_blob_client(f"{release_prefix}/{_PUBLISHED_MARKER}")
            await marker.download_blob()
        name = f"{release_prefix}/{path.as_posix()}"
        download = await self._container.get_blob_client(name).download_blob()
        return await download.readall()

    def published_release_url(self, dataset_id: str, release_id: str) -> str:
        """Return the canonical HTTPS folder URL for a published release."""
        dataset_id = validate_review_identifier(dataset_id)
        release_id = validate_review_identifier(release_id)
        container_url = str(self._container.url).rstrip("/")
        encoded_prefix = "/".join(quote(part, safe="") for part in PurePosixPath(self._prefix).parts)
        return f"{container_url}/{encoded_prefix}/releases/{quote(dataset_id, safe='')}/{quote(release_id, safe='')}"

    async def _upload_create_only(self, name: str, payload: bytes, release_id: str) -> None:
        try:
            await self._container.get_blob_client(name).upload_blob(payload, overwrite=False)
        except ResourceExistsError as exc:
            raise PublicationConflictError(f"Release blob already exists: {release_id}") from exc

    async def _download_sha256(self, name: str, expected_sha256: str) -> str:
        del expected_sha256
        download = await self._container.get_blob_client(name).download_blob()
        return hashlib.sha256(await download.readall()).hexdigest()
