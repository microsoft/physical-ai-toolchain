"""Behavior tests for verified Azure Blob release publication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.release.blob_publisher import BlobReleasePublisher, PublicationVerificationError, ResourceExistsError


@pytest.fixture(autouse=True)
def verified_release(monkeypatch) -> None:
    monkeypatch.setattr("src.api.release.blob_publisher.verify_release", lambda _root: None)


class _Download:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def readall(self) -> bytes:
        return self._payload


class _Blob:
    def __init__(self, container: _Container, name: str) -> None:
        self._container = container
        self._name = name

    async def download_blob(self) -> _Download:
        return _Download(self._container.payloads[self._name])

    async def upload_blob(self, payload: bytes, *, overwrite: bool) -> None:
        assert overwrite is False
        if self._name in self._container.payloads:
            raise ResourceExistsError("exists")
        self._container.payloads[self._name] = bytes(payload)


class _Container:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}
        self.url = "https://storage.example/datasets"

    def get_blob_client(self, name: str) -> _Blob:
        return _Blob(self, name)

    async def list_blobs(self, *, name_starts_with: str):
        for name, payload in sorted(self.payloads.items()):
            if name.startswith(name_starts_with):
                yield SimpleNamespace(name=name, size=len(payload))


async def test_given_blob_publication_when_download_digest_differs_then_marker_is_not_created(tmp_path: Path) -> None:
    # Arrange
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "payload.bin").write_bytes(b"release")
    container = _Container()
    publisher = BlobReleasePublisher(container, export_prefix="exports")

    async def corrupt_download(name: str, expected_sha256: str) -> str:
        assert expected_sha256 == hashlib.sha256(b"release").hexdigest()
        return "0" * 64

    publisher._download_sha256 = corrupt_download

    # Act & Assert
    with pytest.raises(PublicationVerificationError, match="SHA-256"):
        await publisher.publish("sample-dataset", "release-1", staging, owner="job-1")
    assert "exports/releases/sample-dataset/release-1/.published.json" not in container.payloads


async def test_given_verified_blobs_when_marker_created_then_reader_exposes_release(tmp_path: Path) -> None:
    # Arrange
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "payload.bin").write_bytes(b"release")
    container = _Container()
    publisher = BlobReleasePublisher(container, export_prefix="exports")

    # Act
    await publisher.publish("sample-dataset", "release-1", staging, owner="job-1")

    # Assert
    assert await publisher.list_published_releases() == [("sample-dataset", "release-1")]
    marker_name = "exports/releases/sample-dataset/release-1/.published.json"
    assert list(container.payloads)[-1] == marker_name
    assert await publisher.read_published_file("sample-dataset", "release-1", "payload.bin") == b"release"
    assert (
        publisher.published_release_url("sample-dataset", "release-1")
        == "https://storage.example/datasets/exports/releases/sample-dataset/release-1"
    )


async def test_given_unmarked_blobs_when_published_file_read_then_payload_is_hidden() -> None:
    # Arrange
    container = _Container()
    container.payloads["exports/releases/sample-dataset/release-1/payload.bin"] = b"release"
    publisher = BlobReleasePublisher(container, export_prefix="exports")

    # Act & Assert
    with pytest.raises(KeyError):
        await publisher.read_published_file("sample-dataset", "release-1", "payload.bin")
