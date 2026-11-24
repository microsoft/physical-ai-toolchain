"""Association discovery and recovery fail closed without losing accepted records."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from physical_ai_curation.release.azure_publication import BlobPublisher
from physical_ai_curation.release.blob_source_associations import BlobSourceAssociations
from physical_ai_curation.release.canonical import canonical_json_bytes, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.publication import LocalPublisher, PublicationAttempt, ownership_bytes, ownership_path
from physical_ai_curation.release.record_storage import RecordLimits
from physical_ai_curation.release.source_associations import (
    LocalSourceAssociations,
    SourceAssociationIntent,
    restore_source_association,
)
from physical_ai_curation.release.store import LocalReleaseStore
from pydantic import ValidationError
from test_release_contracts import _manifest_for, _payload_files, _write_package
from test_release_publishers import (
    BLOB_COLLECTION,
    OwnedControl,
    _association_intent,
    _azure_errors,
    _blob_intent,
    _blob_record,
    _FakeBlobClient,
    _FakeContainer,
)


@pytest.fixture
def container() -> _FakeContainer:
    return _FakeContainer("dest-account", "release", {})


@pytest.mark.parametrize("route", ["local", "blob"])
@pytest.mark.parametrize("failure", ["missing", "foreign", "bad_digest", "bad_job", "old_fence"])
def test_association_checkpoint_recovery_preserves_original_binding(
    tmp_path: Path, container: _FakeContainer, route: str, failure: str
) -> None:
    intent = _blob_intent(tmp_path) if route == "blob" else _association_intent(tmp_path)
    associations = (
        BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
        if route == "blob"
        else LocalSourceAssociations(tmp_path, limits=RecordLimits())
    )
    associations.ensure_intent(intent)
    if failure not in {"missing", "foreign"}:
        associations.bind_manifest(intent, "a" * 64, job_id="b" * 32, fence=2)
    candidate = intent.model_copy(update={"source_revision": "foreign"}) if failure == "foreign" else intent

    with pytest.raises(ReleaseVerificationError):
        associations.require_checkpoint(
            candidate,
            "c" * 64 if failure == "bad_digest" else "a" * 64,
            job_id="c" * 32 if failure == "bad_job" else "b" * 32,
            fence=1 if failure == "old_fence" else 2,
        )

    if failure not in {"missing", "foreign"}:
        associations.require_checkpoint(intent, "a" * 64, job_id="b" * 32, fence=3)


@pytest.mark.parametrize("route", ["local", "blob"])
@pytest.mark.parametrize("failure", ["invalid_checkpoint", "missing_intent", "foreign_intent"])
def test_binding_manifest_requires_valid_checkpoint_and_exact_pending_intent(
    tmp_path: Path, container: _FakeContainer, route: str, failure: str
) -> None:
    intent = _blob_intent(tmp_path) if route == "blob" else _association_intent(tmp_path)
    associations = (
        BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
        if route == "blob"
        else LocalSourceAssociations(tmp_path, limits=RecordLimits())
    )
    associations.ensure_intent(intent)
    candidate = intent
    if failure == "missing_intent":
        candidate = intent.model_copy(
            update={
                "attempt": PublicationAttempt(
                    "pkg-002", "attempt", "release-b", source_dataset_id=intent.source_dataset_id
                )
            }
        )
    elif failure == "foreign_intent":
        candidate = intent.model_copy(update={"source_revision": "changed"})

    with pytest.raises(ReleaseVerificationError):
        associations.bind_manifest(
            candidate, "invalid" if failure == "invalid_checkpoint" else "a" * 64, job_id="b" * 32, fence=1
        )

    with pytest.raises(ReleaseVerificationError, match="missing"):
        associations.require_checkpoint(intent, "a" * 64, job_id="b" * 32, fence=1)


@pytest.mark.parametrize("failure", ["key", "dataset", "timezone"])
def test_accepted_association_model_rejects_inconsistent_identity(tmp_path: Path, failure: str) -> None:
    document = _association_intent(tmp_path).model_dump(mode="json")
    document[{"key": "source_key", "dataset": "source_dataset_id", "timezone": "accepted_at"}[failure]] = {
        "key": "a" * 64,
        "dataset": "another-dataset",
        "timezone": "2026-11-02T12:00:00",
    }[failure]

    with pytest.raises(ValidationError):
        SourceAssociationIntent.model_validate(document)


@pytest.mark.parametrize("binding", [b"[]", b'{"schema":"curated-release-intent/v1"}'])
def test_restore_distinguishes_unassociated_intent_from_invalid_container(binding: bytes) -> None:
    if binding == b"[]":
        with pytest.raises(ReleaseVerificationError, match="unsupported accepted"):
            restore_source_association(binding, accepted_at=0)
    else:
        assert restore_source_association(binding, accepted_at=0) is None


@pytest.mark.parametrize("route", ["local", "blob"])
@pytest.mark.parametrize("corruption", ["schema", "identity", "oversized"])
def test_malformed_association_records_are_not_replayed_or_discovered(
    tmp_path: Path, container: _FakeContainer, route: str, corruption: str
) -> None:
    intent = _blob_intent(tmp_path) if route == "blob" else _association_intent(tmp_path)
    associations = (
        BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
        if route == "blob"
        else LocalSourceAssociations(tmp_path, limits=RecordLimits())
    )
    associations.ensure_intent(intent)
    if corruption == "schema":
        content = canonical_json_bytes({"schema_version": "unsupported"})
    elif corruption == "oversized":
        content = b"x" * (16 * 1024 + 1)
    else:
        altered = intent.model_copy(
            update={
                "attempt": PublicationAttempt(
                    "pkg-002", "attempt", "release-b", source_dataset_id=intent.source_dataset_id
                )
            }
        )
        content = canonical_json_bytes(
            {
                "schema_version": "curated-release-association/v1",
                "intent": altered.model_dump(mode="json"),
                "checkpoint": None,
            }
        )
    record = tmp_path / "_release_associations/pkg-001.json"
    if route == "blob":
        container.put(_blob_record(), content)
    else:
        record.write_bytes(content)

    with pytest.raises(ReleaseVerificationError):
        associations.ensure_intent(intent)

    if route == "blob" and corruption == "oversized":
        with pytest.raises(ReleaseVerificationError, match="bounds exceeded"):
            associations.discover_verified(scope_sha256=intent.scope_sha256)
    else:
        assert associations.discover_verified(scope_sha256=intent.scope_sha256) == []
    assert (container.blobs[_blob_record()].data if route == "blob" else record.read_bytes()) == content


@pytest.mark.parametrize("route", ["local", "blob"])
def test_oversized_new_association_never_creates_a_record(
    tmp_path: Path, container: _FakeContainer, route: str
) -> None:
    intent = _blob_intent(tmp_path) if route == "blob" else _association_intent(tmp_path)
    associations = (
        BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
        if route == "blob"
        else LocalSourceAssociations(tmp_path, limits=RecordLimits())
    )

    with pytest.raises(ReleaseVerificationError, match="exceeds its bound"):
        associations.ensure_intent(intent.model_copy(update={"description": "x" * (16 * 1024)}))

    assert not (tmp_path / "_release_associations/pkg-001.json").exists()
    assert _blob_record() not in container.blobs


@pytest.mark.parametrize("protection", ["directory", "oversized", "count", "total"])
def test_local_association_scan_enforces_bounds_without_removing_records(tmp_path: Path, protection: str) -> None:
    associations = LocalSourceAssociations(tmp_path, limits=RecordLimits())
    intent = _association_intent(tmp_path)
    associations.ensure_intent(intent)
    namespace = tmp_path / "_release_associations"
    original = (namespace / "pkg-001.json").read_bytes()
    limits = RecordLimits()
    if protection == "directory":
        (namespace / "pkg-002.json").mkdir()
    elif protection == "oversized":
        (namespace / "pkg-002.json").write_bytes(b"x" * (16 * 1024 + 1))
    else:
        (namespace / "pkg-002.json").write_bytes(original)
        limits = (
            RecordLimits(max_jobs=1)
            if protection == "count"
            else RecordLimits(max_record_bytes=len(original), max_total_bytes=len(original))
        )
    bounded = LocalSourceAssociations(tmp_path, limits=limits)

    assert bounded.discover_verified(scope_sha256=intent.scope_sha256) == []
    assert (namespace / "pkg-001.json").read_bytes() == original


@pytest.mark.parametrize("bound", ["count", "total"])
def test_local_association_admission_does_not_evict_existing_package(tmp_path: Path, bound: str) -> None:
    intent = _association_intent(tmp_path)
    associations = LocalSourceAssociations(tmp_path, limits=RecordLimits())
    associations.ensure_intent(intent)
    original = (tmp_path / "_release_associations/pkg-001.json").read_bytes()
    limits = (
        RecordLimits(max_jobs=1)
        if bound == "count"
        else RecordLimits(max_record_bytes=len(original), max_total_bytes=len(original))
    )
    second = intent.model_copy(
        update={
            "attempt": PublicationAttempt(
                "pkg-002", "attempt-2", "release-b", source_dataset_id=intent.source_dataset_id
            )
        }
    )

    with pytest.raises(ReleaseVerificationError, match="bounds exceeded"):
        LocalSourceAssociations(tmp_path, limits=limits).ensure_intent(second)

    assert (tmp_path / "_release_associations/pkg-001.json").read_bytes() == original
    assert not (tmp_path / "_release_associations/pkg-002.json").exists()


def test_blob_verified_discovery_reports_provider_unavailability(
    container: _FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    associations = BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
    monkeypatch.setattr(container, "list_blobs", Mock(side_effect=_azure_errors().ServiceRequestError("unavailable")))

    assert associations.discover(source_key="a" * 64, scope_sha256="b" * 64) == []
    with pytest.raises(ReleaseVerificationError, match="listing unavailable"):
        associations.discover_verified(scope_sha256="b" * 64)


def test_blob_record_revision_race_is_not_adopted(
    tmp_path: Path, container: _FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = _blob_intent(tmp_path)
    associations = BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
    associations.ensure_intent(intent)
    original = _FakeBlobClient.download_blob

    def raced_download(client: _FakeBlobClient, *args: object, **kwargs: object) -> object:
        if client.name == _blob_record():
            container.blobs[client.name].etag = '"concurrent-revision"'
        return original(client, *args, **kwargs)

    monkeypatch.setattr(_FakeBlobClient, "download_blob", raced_download)

    with pytest.raises(ReleaseVerificationError, match="changed during read"):
        associations.ensure_intent(intent)
    assert associations.discover_verified(scope_sha256=intent.scope_sha256) == []


@pytest.mark.parametrize("route", ["local", "blob"])
@pytest.mark.parametrize("tamper", ["none", "ownership", "frozen"])
def test_verified_association_discovery_requires_complete_matching_publication(
    tmp_path: Path, container: _FakeContainer, route: str, tamper: str
) -> None:
    collection = tmp_path / "collection"
    intent = _blob_intent(tmp_path) if route == "blob" else _association_intent(collection)
    source = tmp_path / "source"
    files = _payload_files()
    manifest = _manifest_for(files)
    digest = _write_package(source, files, manifest)
    intent = intent.model_copy(
        update={"frozen_input_sha256": "a" * 64 if tamper == "frozen" else manifest.frozen_input_sha256}
    )
    associations = (
        BlobSourceAssociations(container, BLOB_COLLECTION, limits=RecordLimits())
        if route == "blob"
        else LocalSourceAssociations(collection, limits=RecordLimits())
    )
    associations.ensure_intent(intent)
    control = OwnedControl()
    publisher = (
        BlobPublisher(container, intent.attempt, control, root_prefix=BLOB_COLLECTION.prefix)
        if route == "blob"
        else LocalPublisher(collection, intent.attempt, control)
    )
    for item in LocalReleaseStore(source).list_objects():
        content = (source / item.path).read_bytes()
        publisher.write(item.path, (content,), length=len(content), sha256=sha256_hex(content))
    associations.bind_manifest(intent, digest, job_id="b" * 32, fence=1)
    control.commit = True
    publisher.publish(digest)
    if tamper == "ownership":
        other = PublicationAttempt(
            "pkg-001", "other-attempt", intent.attempt.prefix, source_dataset_id=intent.source_dataset_id
        )
        content = ownership_bytes(other)
        if route == "blob":
            container.put(f"{BLOB_COLLECTION.prefix}/{ownership_path(other.prefix)}", content)
        else:
            (collection / ownership_path(other.prefix)).write_bytes(content)

    found = associations.discover_verified(scope_sha256=intent.scope_sha256)

    if tamper == "none":
        assert len(found) == 1
        assert found[0].root == publisher.root
        assert found[0].manifest_sha256 == digest
        assert found[0].destination_alias == intent.destination_alias
    else:
        assert found == []
    assert associations.discover_verified(scope_sha256="c" * 64) == []
