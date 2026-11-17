"""Source admission rejects malformed metadata before reading any payload."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from physical_ai_curation.release import inputs
from physical_ai_curation.release.inputs import BlobReleaseSource, LocalReleaseSource, ReleaseInputError
from physical_ai_curation.release.models import SourceObject
from test_release_inputs import FakePages


@pytest.mark.parametrize(
    "url",
    [
        "http://example.blob.core.windows.net/datasets",
        "https://user:password@example.blob.core.windows.net/datasets",
        "https://example.blob.core.windows.net/datasets?token=forbidden",
        "https://example.blob.core.windows.net/datasets#fragment",
        "https://example.blob.core.windows.net/",
        "https://example.blob.core.windows.net/datasets/child",
        "https://example.blob.core.windows.net/UPPERCASE",
    ],
)
def test_source_container_location_refuses_non_canonical_address(url: str) -> None:
    source = BlobReleaseSource(SimpleNamespace(url=url))

    with pytest.raises(ReleaseInputError, match=r"container URL|location is invalid"):
        _ = source.location


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "outside/meta/info.json", "outside configured prefix"),
        ("name", "source/../escape", "path is unsafe"),
        ("size", -1, "lacks a length"),
        ("size", True, "lacks a length"),
        ("etag", "", "expected revision"),
        ("etag", None, "expected revision"),
        ("version_id", "", "invalid version"),
        ("version_id", 17, "invalid version"),
        ("size", float("nan"), "metadata is invalid"),
    ],
)
def test_blob_listing_refuses_malformed_metadata_before_download(field: str, value: object, message: str) -> None:
    entry = {"name": "source/meta/info.json", "size": 2, "etag": '"v1"', "version_id": None, field: value}
    container = Mock()
    container.list_blobs.return_value = FakePages([[SimpleNamespace(**entry)]])

    with pytest.raises(ReleaseInputError, match=message):
        BlobReleaseSource(container, prefix="source").inventory()

    container.get_blob_client.assert_not_called()


@pytest.mark.parametrize("entries", [[], ["same", "same"], ["annotations/episode.json"]])
def test_blob_listing_requires_nonempty_unique_source_set(entries: list[str]) -> None:
    container = Mock()
    container.list_blobs.return_value = FakePages(
        [[SimpleNamespace(name="source/" + path, size=1, etag='"v1"', version_id=None) for path in entries]]
    )

    with pytest.raises(ReleaseInputError, match="empty or contains duplicate"):
        BlobReleaseSource(container, prefix="source").inventory()

    container.get_blob_client.assert_not_called()


def test_blob_listing_maps_provider_failure_without_reading_payload() -> None:
    from azure.core.exceptions import ServiceRequestError

    container = Mock()
    container.list_blobs.side_effect = ServiceRequestError("private provider detail")

    with pytest.raises(ReleaseInputError, match="source listing unavailable") as raised:
        BlobReleaseSource(container, prefix="source").inventory()

    assert "private" not in str(raised.value)
    container.get_blob_client.assert_not_called()


def test_content_bound_object_cannot_be_used_for_revision_range_reads() -> None:
    container = Mock()
    source = SourceObject(path="data/file.parquet", length=1, revision="v1", sha256="a" * 64)

    with pytest.raises(ReleaseInputError, match="revision-bound"):
        BlobReleaseSource(container).range_file(source)

    container.get_blob_client.assert_not_called()


@pytest.mark.parametrize("checkpoint_position", ["before_properties", "after_properties", "after_future"])
def test_scoped_inventory_checks_deadline_throughout_property_read(
    monkeypatch: pytest.MonkeyPatch, checkpoint_position: str
) -> None:
    container = Mock(url="https://example.blob.core.windows.net/datasets")
    container.get_blob_client.return_value.get_blob_properties.return_value = SimpleNamespace(
        size=1, etag='"v1"', version_id=None
    )
    source = BlobReleaseSource(container).scoped(["data/file.parquet"])
    ticks = {
        "before_properties": [0.0, 100.0],
        "after_properties": [0.0, 0.0, 100.0],
        "after_future": [0.0, 0.0, 0.0, 100.0],
    }[checkpoint_position]
    monkeypatch.setattr(inputs, "monotonic", Mock(side_effect=ticks))

    with pytest.raises(ReleaseInputError, match="elapsed bound"):
        source.inventory()

    container.list_blobs.assert_not_called()


@pytest.mark.parametrize("bound", [0, -1, True, 1])
def test_local_metadata_inventory_enforces_positive_byte_budget(tmp_path: Path, bound: object) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/info.json").write_bytes(b"{}")
    source = LocalReleaseSource(tmp_path, finalized=True)

    with pytest.raises(ReleaseInputError, match="source unavailable") as raised:
        source.metadata_inventory(max_bytes=bound)

    assert "configured byte bound" in str(raised.value.__cause__)


def test_local_source_requires_at_least_one_admitted_object(tmp_path: Path) -> None:
    (tmp_path / "annotations").mkdir()
    (tmp_path / "annotations/private.json").write_bytes(b"{}")

    with pytest.raises(ReleaseInputError, match="source unavailable") as raised:
        LocalReleaseSource(tmp_path, finalized=True).inventory()

    assert "inventory is empty" in str(raised.value.__cause__)
