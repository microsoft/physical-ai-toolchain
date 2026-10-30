"""Owned publication and marker-gated admission of independent release packages."""

from __future__ import annotations

import itertools
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from physical_ai_curation.release.azure_publication import BlobPublisher, BlobReleaseStore
from physical_ai_curation.release.canonical import canonical_json_bytes, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import MARKER_PATH, SourceObject
from physical_ai_curation.release.publication import LocalPublisher, PublicationAttempt
from physical_ai_curation.release.store import LocalReleaseStore
from test_release_contracts import _manifest_for, _payload_files, _write_package


class OwnedControl:
    def __init__(self) -> None:
        self.intents = {}
        self.commit = False
        self.active = True

    @contextmanager
    def effect(self, attempt: PublicationAttempt, path: str, length: int, digest: str) -> Iterator[bool]:
        if not self.active:
            raise ReleaseVerificationError("effect authority lost")
        identity = (attempt, path, length, digest)
        previous = self.intents.get(path)
        if previous is not None and previous != identity:
            raise ReleaseVerificationError("effect identity conflict")
        self.intents[path] = identity
        yield previous is not None

    @contextmanager
    def publication(self, attempt: PublicationAttempt, digest: str) -> Iterator[None]:
        if not self.active or not self.commit:
            raise ReleaseVerificationError("durable commit decision required")
        yield


def test_given_candidate_when_publish_then_independent_marker_gated_same_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    files = _payload_files()
    digest = _write_package(source, files, _manifest_for(files))
    control = OwnedControl()
    publisher = LocalPublisher(tmp_path / "destination", PublicationAttempt("pkg-001", "attempt-1"), control)

    for item in LocalReleaseStore(source).list_objects():
        content = (source / item.path).read_bytes()
        publisher.write(item.path, (content,), length=len(content), sha256=sha256_hex(content))
    assert publisher.discover() == []
    with pytest.raises(ReleaseVerificationError, match="commit"):
        publisher.publish(digest)
    assert not (publisher.root / MARKER_PATH).exists()
    control.commit = True
    result = publisher.publish(digest)
    source.rename(tmp_path / "source-unavailable")

    assert result.publication_state == "published"
    assert publisher.discover() == [publisher.root]
    assert publisher.publish(digest) == result
    assert (publisher.root / "media/front/episode-000.mp4").read_bytes() == files["media/front/episode-000.mp4"]


def test_given_replay_when_write_then_only_matching_owned_bytes_reused(tmp_path: Path) -> None:
    control = OwnedControl()
    publisher = LocalPublisher(tmp_path, PublicationAttempt("pkg-001", "attempt-1"), control)
    content = b"independent bytes"
    publisher.write("data/item", (content,), length=len(content), sha256=sha256_hex(content))

    publisher.write("data/item", (content,), length=len(content), sha256=sha256_hex(content))
    (publisher.root / "data/item").write_bytes(b"corrupt")

    with pytest.raises(ReleaseVerificationError):
        publisher.write("data/item", (content,), length=len(content), sha256=sha256_hex(content))


def test_given_chosen_dataset_name_when_written_then_layout_has_no_attempt_segment(tmp_path: Path) -> None:
    publisher = LocalPublisher(tmp_path, PublicationAttempt("plate-release", "try-a"), OwnedControl())
    publisher.write("data/item", (b"x",), length=1, sha256=sha256_hex(b"x"))

    assert publisher.root == tmp_path / "plate-release"
    assert (publisher.root / "data/item").read_bytes() == b"x"
    assert (tmp_path / "_releases" / "plate-release.json").is_file()
    assert not (publisher.root / "try-a").exists()


def test_given_named_root_owned_when_other_attempt_writes_then_foreign_attempt_refused(tmp_path: Path) -> None:
    first = LocalPublisher(tmp_path, PublicationAttempt("plate-release", "try-a"), OwnedControl())
    first.write("data/first", (b"x",), length=1, sha256=sha256_hex(b"x"))
    other = LocalPublisher(tmp_path, PublicationAttempt("plate-release", "try-b"), OwnedControl())

    with pytest.raises(ReleaseVerificationError, match="ownership"):
        other.write("data/second", (b"y",), length=1, sha256=sha256_hex(b"y"))
    assert not (first.root / "data/second").exists()


def test_given_existing_legacy_dataset_when_release_claimed_then_no_files_added(tmp_path: Path) -> None:
    root = tmp_path / "plate-release"
    root.mkdir()
    (root / "original").write_bytes(b"keep")
    publisher = LocalPublisher(tmp_path, PublicationAttempt("plate-release", "try-a"), OwnedControl())

    with pytest.raises(ReleaseVerificationError, match="already exists"):
        publisher.write("data/item", (b"x",), length=1, sha256=sha256_hex(b"x"))
    assert [path.name for path in root.iterdir()] == ["original"]


@pytest.mark.parametrize("package,attempt", [("../escape", "safe"), ("safe", "a/b"), ("safe", "..")])
def test_given_unsafe_attempt_when_construct_then_refused(package: str, attempt: str) -> None:
    with pytest.raises(ReleaseVerificationError):
        PublicationAttempt(package, attempt)


def test_given_lost_authority_when_write_then_no_output(tmp_path: Path) -> None:
    control = OwnedControl()
    control.active = False
    publisher = LocalPublisher(tmp_path, PublicationAttempt("pkg", "attempt"), control)

    with pytest.raises(ReleaseVerificationError, match="authority"):
        publisher.write("data/item", (b"x",), length=1, sha256=sha256_hex(b"x"))

    assert not publisher.root.exists()


def test_given_new_directories_when_write_then_parent_entries_are_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synchronized = set()
    original = os.fsync

    def observe(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            synchronized.add(info.st_ino)
        original(descriptor)

    monkeypatch.setattr(os, "fsync", observe)
    publisher = LocalPublisher(tmp_path, PublicationAttempt("pkg", "attempt"), OwnedControl())
    publisher.write("data/item", (b"x",), length=1, sha256=sha256_hex(b"x"))

    assert tmp_path.stat().st_ino in synchronized
    assert (publisher.root / "data").stat().st_ino in synchronized


_ETAGS = itertools.count()
ATTEMPT = PublicationAttempt("pkg-001", "attempt-1")
PATH = "media/front/episode-000.mp4"
PAYLOAD = b"front camera bytes"


def _azure_errors() -> Any:
    import azure.core.exceptions as errors

    return errors


def _match_conditions() -> Any:
    from azure.core import MatchConditions

    return MatchConditions


def _new_etag() -> str:
    return f'"etag-{next(_ETAGS)}"'


class _FakeCopy:
    def __init__(self, status: str, copy_id: str) -> None:
        self.status = status
        self.id = copy_id


class _FakeRecord:
    def __init__(self, data: bytes, metadata: dict[str, str], copy: _FakeCopy | None) -> None:
        self.data = data
        self.metadata = metadata
        self.copy = copy
        self.etag = _new_etag()


class _FakeProperties:
    def __init__(self, name: str, record: _FakeRecord) -> None:
        self.name = name
        self.size = len(record.data)
        self.etag = record.etag
        self.metadata = dict(record.metadata)
        self.copy = record.copy


class _FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def chunks(self) -> Iterator[bytes]:
        yield self._data


class _FakeBlobClient:
    def __init__(self, container: _FakeContainer, name: str) -> None:
        self.container = container
        self.name = name

    @property
    def url(self) -> str:
        return f"{self.container.url}/{self.name}{self.container.query}"

    def _record(self) -> _FakeRecord:
        record = self.container.blobs.get(self.name)
        if record is None:
            raise _azure_errors().ResourceNotFoundError("missing")
        return record

    def get_blob_properties(self) -> _FakeProperties:
        return _FakeProperties(self.name, self._record())

    def start_copy_from_url(self, source_url: str, metadata: dict[str, str] | None = None, **kwargs: Any) -> dict:
        container = self.container
        container.calls.append(("start_copy_from_url", {"source_url": source_url, "metadata": metadata, **kwargs}))
        container.fault("start_copy_from_url", after=False)
        if kwargs.get("match_condition") == _match_conditions().IfMissing and self.name in container.blobs:
            raise _azure_errors().ResourceExistsError("destination exists")
        source_container, source_name = _resolve(container.registry, source_url)
        source = source_container.blobs.get(source_name)
        if source is None:
            raise _azure_errors().ResourceNotFoundError("source missing")
        if "source_etag" in kwargs and kwargs["source_etag"] != source.etag:
            raise _azure_errors().ResourceModifiedError("source changed")
        data = source.data + (b"x" if container.corrupt_copy else b"")
        container.copy_count += 1
        copy_id = f"copy-{container.copy_count}"
        status = container.copy_outcome
        if status == "success":
            copied_metadata = dict(metadata if metadata is not None else source.metadata)
            container.blobs[self.name] = _FakeRecord(data, copied_metadata, _FakeCopy("success", copy_id))
        else:
            container.blobs[self.name] = _FakeRecord(b"", dict(metadata or {}), _FakeCopy(status, copy_id))
            container.pending_data[self.name] = data
        container.fault("start_copy_from_url", after=True)
        return {"copy_id": copy_id, "copy_status": status}

    def upload_blob(
        self, data: Any, overwrite: bool = False, metadata: dict[str, str] | None = None, **kwargs: Any
    ) -> dict:
        container = self.container
        container.calls.append(("upload_blob", {"overwrite": overwrite, "metadata": metadata, **kwargs}))
        container.fault("upload_blob", after=False)
        if self.name in container.blobs and not overwrite:
            raise _azure_errors().ResourceExistsError("exists")
        payload = data if isinstance(data, bytes) else b"".join(data)
        container.blobs[self.name] = _FakeRecord(payload, dict(metadata or {}), None)
        container.fault("upload_blob", after=True)
        return {}

    def download_blob(self, etag: str | None = None, match_condition: Any = None, **kwargs: Any) -> _FakeDownload:
        record = self._record()
        if etag is not None and etag != record.etag:
            raise _azure_errors().ResourceModifiedError("changed")
        return _FakeDownload(record.data)

    def delete_blob(self, etag: str | None = None, match_condition: Any = None, **kwargs: Any) -> None:
        self.container.fault("delete_blob", after=False)
        record = self._record()
        if etag is not None and etag != record.etag:
            raise _azure_errors().ResourceModifiedError("changed")
        del self.container.blobs[self.name]
        self.container.fault("delete_blob", after=True)

    def abort_copy(self, copy_id: str, **kwargs: Any) -> None:
        self.container.fault("abort_copy", after=False)
        if self.container.complete_during_abort:
            self.container.finish_copy(self.name)
        record = self._record()
        if record.copy is None or record.copy.status != "pending" or record.copy.id != copy_id:
            error = _azure_errors().HttpResponseError(message="NoPendingCopyOperation")
            error.status_code = 409
            raise error
        record.data = b""
        record.copy = _FakeCopy("aborted", copy_id)
        record.etag = _new_etag()
        self.container.pending_data.pop(self.name, None)
        self.container.fault("abort_copy", after=True)


def _resolve(registry: dict[str, _FakeContainer], url: str) -> tuple[_FakeContainer, str]:
    for container in registry.values():
        prefix = f"{container.url}/"
        if url.startswith(prefix):
            return container, url[len(prefix) :]
    raise _azure_errors().ResourceNotFoundError("unknown source url")


class _FakeContainer:
    def __init__(self, account: str, name: str, registry: dict[str, _FakeContainer]) -> None:
        self.url = f"https://{account}.blob.core.windows.net/{name}"
        self.query = ""
        self.blobs: dict[str, _FakeRecord] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.faults: dict[str, tuple[bool, Exception]] = {}
        self.copy_outcome = "success"
        self.complete_during_abort = False
        self.corrupt_copy = False
        self.pending_data: dict[str, bytes] = {}
        self.copy_count = 0
        self.registry = registry
        registry[self.url] = self

    def get_blob_client(self, name: str) -> _FakeBlobClient:
        return _FakeBlobClient(self, name)

    def list_blobs(self, name_starts_with: str | None = None, include: Any = None) -> Iterator[_FakeProperties]:
        for name in sorted(self.blobs):
            if name_starts_with is None or name.startswith(name_starts_with):
                yield _FakeProperties(name, self.blobs[name])

    def put(self, name: str, data: bytes, metadata: dict[str, str] | None = None) -> None:
        self.blobs[name] = _FakeRecord(data, dict(metadata or {}), None)

    def inject(self, operation: str, error: Exception, *, after: bool) -> None:
        self.faults[operation] = (after, error)

    def fault(self, operation: str, *, after: bool) -> None:
        injected = self.faults.get(operation)
        if injected is not None and injected[0] == after:
            del self.faults[operation]
            raise injected[1]

    def finish_copy(self, name: str) -> None:
        record = self.blobs[name]
        record.data = self.pending_data.pop(name)
        record.copy = _FakeCopy("success", record.copy.id)
        record.etag = _new_etag()


@pytest.fixture
def registry() -> dict[str, _FakeContainer]:
    pytest.importorskip("azure.core.exceptions")
    return {}


@pytest.fixture
def source_container(registry: dict[str, _FakeContainer]) -> _FakeContainer:
    return _FakeContainer("dest-account", "source", registry)


@pytest.fixture
def destination(registry: dict[str, _FakeContainer]) -> _FakeContainer:
    return _FakeContainer("dest-account", "release", registry)


@pytest.fixture
def control() -> OwnedControl:
    return OwnedControl()


def _publisher(destination: _FakeContainer, control: OwnedControl, **kwargs: Any) -> BlobPublisher:
    return BlobPublisher(destination, ATTEMPT, control, root_prefix="releases", **kwargs)


def _source(
    source_container: _FakeContainer, metadata: dict[str, str] | None = None, data: bytes = PAYLOAD
) -> tuple[SourceObject, str]:
    source_container.put(PATH, data, metadata)
    etag = source_container.get_blob_client(PATH).get_blob_properties().etag
    revision = sha256_hex(canonical_json_bytes({"etag": etag, "version_id": None}))
    return SourceObject(path=PATH, revision=revision, length=len(data), sha256=sha256_hex(data)), etag


@pytest.mark.parametrize("changed_field", ["etag", "version_id"])
def test_given_frozen_blob_revision_mismatch_when_copy_then_no_effect_dispatched(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, changed_field: str
) -> None:
    source, etag = _source(source_container)
    identity = {"etag": etag, "version_id": None}
    identity[changed_field] = "different-revision"
    source = source.model_copy(update={"revision": sha256_hex(canonical_json_bytes(identity))})
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError, match="revision"):
        publisher.copy(source_container, source, etag)

    assert control.intents == {}
    assert destination.blobs == {}
    assert destination.copy_count == 0


def _name(publisher: BlobPublisher, path: str = PATH) -> str:
    return publisher.root + path


def test_given_blob_candidate_when_publish_then_verified_and_immutable(
    tmp_path: Path, destination: _FakeContainer, control: OwnedControl
) -> None:
    source = tmp_path / "source"
    files = _payload_files()
    digest = _write_package(source, files, _manifest_for(files))
    publisher = _publisher(destination, control)
    for item in LocalReleaseStore(source).list_objects():
        content = (source / item.path).read_bytes()
        publisher.write(item.path, (content,), length=len(content), sha256=sha256_hex(content))

    assert publisher.discover() == []
    publisher.prepare(digest)
    control.commit = True
    result = publisher.publish(digest)

    assert result.publication_state == "published"
    assert publisher.discover() == [publisher.root]
    assert publisher.publish(digest) == result
    with pytest.raises(ReleaseVerificationError, match="immutable"):
        publisher.write("data/extra", (b"x",), length=1, sha256=sha256_hex(b"x"))
    destination.blobs[_name(publisher, MARKER_PATH)].data = b"corrupt"
    assert publisher.discover() == []


def test_given_pre_dispatch_failure_when_copy_then_retry_issues_single_copy(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    errors = _azure_errors()
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.inject("start_copy_from_url", errors.ServiceRequestError("unreachable"), after=False)

    with pytest.raises(errors.ServiceRequestError):
        publisher.copy(source_container, source, etag)
    assert destination.copy_count == 0
    assert _name(publisher) not in destination.blobs
    assert PATH in control.intents

    progress = publisher.copy(source_container, source, etag)

    assert progress.state == "complete"
    assert destination.copy_count == 1
    assert destination.blobs[_name(publisher)].data == PAYLOAD


def test_given_response_lost_after_accept_when_retry_then_reconciled_without_second_copy(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    errors = _azure_errors()
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.inject("start_copy_from_url", errors.ServiceRequestError("reset"), after=True)

    with pytest.raises(errors.ServiceRequestError):
        publisher.copy(source_container, source, etag)
    progress = publisher.copy(source_container, source, etag)

    assert progress.state == "complete"
    assert destination.copy_count == 1
    assert destination.blobs[_name(publisher)].data == PAYLOAD


def test_given_pending_copy_when_listed_then_not_complete_until_reconciled(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.copy_outcome = "pending"

    progress = publisher.copy(source_container, source, etag)

    assert progress.state == "pending"
    assert progress.copy_id == "copy-1"
    with pytest.raises(ReleaseVerificationError, match="not complete"):
        BlobReleaseStore(destination, publisher.root).list_objects()

    destination.finish_copy(_name(publisher))
    progress = publisher.copy(source_container, source, etag)
    listed = [item.path for item in BlobReleaseStore(destination, publisher.root).list_objects()]

    assert progress.state == "complete"
    assert destination.copy_count == 1
    assert PATH in listed


def test_given_failed_copy_when_copy_then_completion_refused(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.copy_outcome = "failed"

    with pytest.raises(ReleaseVerificationError, match="did not complete"):
        publisher.copy(source_container, source, etag)
    with pytest.raises(ReleaseVerificationError, match="not complete"):
        BlobReleaseStore(destination, publisher.root).list_objects()


def test_given_durable_control_when_copy_pending_then_ledger_remains_unresolved(
    tmp_path: Path, destination: _FakeContainer, source_container: _FakeContainer
) -> None:
    from physical_ai_curation.release.jobs import AuthorityError, JobRequest, ReleaseJobControl, ReleaseJobStore

    ledger = ReleaseJobStore(tmp_path / "jobs")
    accepted = ledger.accept(JobRequest("scope", "user", "key", ATTEMPT, tmp_path, b"frozen", 4096))
    fence = ledger.claim(accepted.job_id, "worker")
    control = ReleaseJobControl(ledger, accepted.job_id, fence)
    publisher = BlobPublisher(destination, ATTEMPT, control)
    source, etag = _source(source_container)
    destination.copy_outcome = "pending"

    assert publisher.copy(source_container, source, etag).state == "pending"
    with pytest.raises(AuthorityError, match="unresolved"):
        ledger.begin_effect(accepted.job_id, fence, ATTEMPT, PATH, source.length, source.sha256)


@pytest.mark.parametrize("cancel,complete_during_abort", [(False, False), (True, False), (True, True)])
def test_given_durable_pending_copy_when_reconciled_then_real_observation_settles_ledger(
    tmp_path: Path,
    destination: _FakeContainer,
    source_container: _FakeContainer,
    cancel: bool,
    complete_during_abort: bool,
) -> None:
    from physical_ai_curation.release.jobs import JobRequest, ReleaseJobControl, ReleaseJobStore

    ledger = ReleaseJobStore(tmp_path / "jobs")
    accepted = ledger.accept(JobRequest("scope", "user", "key", ATTEMPT, tmp_path, b"frozen", 4096))
    fence = ledger.claim(accepted.job_id, "worker")
    publisher = BlobPublisher(destination, ATTEMPT, ReleaseJobControl(ledger, accepted.job_id, fence))
    source, etag = _source(source_container)
    destination.copy_outcome = "pending"
    publisher.copy(source_container, source, etag)
    reopened = ReleaseJobStore(ledger.root)
    publisher = BlobPublisher(destination, ATTEMPT, ReleaseJobControl(reopened, accepted.job_id, fence))
    if cancel:
        reopened.request_cancel(accepted.job_id, scope="scope", principal="user")
        destination.complete_during_abort = complete_during_abort
    else:
        destination.finish_copy(_name(publisher))
    progress = publisher.reconcile_copy(source, etag, cancel=cancel)
    assert destination.copy_count == 1
    if cancel:
        assert progress is None
        assert _name(publisher) not in destination.blobs
        reopened.confirm_stopped(accepted.job_id, fence)
        assert reopened.settle_cancellation(accepted.job_id, fence, output_removed=True).state == "cancelled"
    else:
        assert progress.state == "complete"
        assert reopened.begin_effect(accepted.job_id, fence, ATTEMPT, PATH, source.length, source.sha256)


def test_given_unowned_destination_when_copy_then_collision_refused_and_bytes_kept(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.put(_name(publisher), b"someone else")

    with pytest.raises(ReleaseVerificationError, match="collision"):
        publisher.copy(source_container, source, etag)

    assert destination.blobs[_name(publisher)].data == b"someone else"
    assert destination.copy_count == 0


@pytest.mark.parametrize("operation", ["abort_copy", "delete_blob"])
def test_given_lost_copy_cleanup_response_when_reopened_then_absence_reconciled_without_redispatch(
    tmp_path: Path,
    destination: _FakeContainer,
    source_container: _FakeContainer,
    operation: str,
) -> None:
    from physical_ai_curation.release.jobs import JobRequest, ReleaseJobControl, ReleaseJobStore

    ledger = ReleaseJobStore(tmp_path / "jobs")
    accepted = ledger.accept(JobRequest("scope", "user", "key", ATTEMPT, tmp_path, b"frozen", 4096))
    fence = ledger.claim(accepted.job_id, "worker")
    publisher = BlobPublisher(destination, ATTEMPT, ReleaseJobControl(ledger, accepted.job_id, fence))
    source, etag = _source(source_container)
    destination.copy_outcome = "pending"
    publisher.copy(source_container, source, etag)
    ledger.request_cancel(accepted.job_id, scope="scope", principal="user")
    destination.inject(operation, _azure_errors().ServiceRequestError("lost cleanup response"), after=True)
    with pytest.raises(_azure_errors().ServiceRequestError):
        publisher.reconcile_copy(source, etag, cancel=True)
    reopened = ReleaseJobStore(ledger.root)
    assert reopened.get(accepted.job_id, scope="scope", principal="user").capacity_state == "reserved"
    recovered = BlobPublisher(destination, ATTEMPT, ReleaseJobControl(reopened, accepted.job_id, fence))
    assert recovered.reconcile_copy(source, etag, cancel=True) is None
    reopened.confirm_stopped(accepted.job_id, fence)
    assert reopened.settle_cancellation(accepted.job_id, fence, output_removed=True).state == "cancelled"
    assert destination.copy_count == 1


def test_given_changed_source_revision_when_copy_then_refused_before_effect(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, _ = _source(source_container)
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError, match="revision"):
        publisher.copy(source_container, source, '"stale"')

    assert control.intents == {}
    assert destination.copy_count == 0


def test_given_source_metadata_when_copy_then_preserved_with_ownership_keys(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container, metadata={"camera": "front"})
    publisher = _publisher(destination, control)

    publisher.copy(source_container, source, etag)
    metadata = destination.blobs[_name(publisher)].metadata

    assert metadata["camera"] == "front"
    assert metadata["curation_attempt"] == "attempt-1"
    assert metadata["curation_package"] == "pkg-001"
    assert "curation_operation" in metadata


def test_given_reserved_source_metadata_when_copy_then_refused_before_effect(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container, metadata={"curation_attempt": "other"})
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError, match="reserved"):
        publisher.copy(source_container, source, etag)

    assert control.intents == {}
    assert destination.copy_count == 0


def test_given_corrupted_copy_when_copy_then_independent_verification_refuses(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.corrupt_copy = True

    with pytest.raises(ReleaseVerificationError, match="copied content"):
        publisher.copy(source_container, source, etag)


def test_given_cross_account_source_when_copy_then_refused_before_effect(
    destination: _FakeContainer, registry: dict, control: OwnedControl
) -> None:
    other = _FakeContainer("other-account", "source", registry)
    source, etag = _source(other)
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError, match="cross-account"):
        publisher.copy(other, source, etag)

    assert control.intents == {}
    assert destination.copy_count == 0


def test_given_query_bearing_source_url_when_copy_then_refused_before_effect(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    source_container.query = "?sig=redacted"
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError, match="query"):
        publisher.copy(source_container, source, etag)

    assert control.intents == {}


def test_given_bearer_source_authorization_when_copy_then_synchronous_copy_requested(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    publisher = _publisher(destination, control, source_authorization="Bearer token-value")

    publisher.copy(source_container, source, etag)
    _, kwargs = next(call for call in destination.calls if call[0] == "start_copy_from_url")

    assert kwargs["requires_sync"] is True
    assert kwargs["source_authorization"] == "Bearer token-value"


def test_given_non_bearer_source_authorization_when_construct_then_refused(
    destination: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    with pytest.raises(ReleaseVerificationError, match="bearer"):
        _publisher(destination, control, source_authorization="token-value")


def test_given_pending_copy_when_abort_with_other_id_then_refused_and_matching_id_removes_residue(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.copy_outcome = "pending"
    progress = publisher.copy(source_container, source, etag)

    with pytest.raises(ReleaseVerificationError, match="copy id"):
        publisher.abort(PATH, "other-copy")
    assert destination.blobs[_name(publisher)].copy.status == "pending"

    publisher.abort(PATH, progress.copy_id)

    assert _name(publisher) not in destination.blobs


def test_given_owned_residue_when_conditional_delete_conflicts_then_cleanup_refused(
    destination: _FakeContainer, source_container: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    errors = _azure_errors()
    source, etag = _source(source_container)
    publisher = _publisher(destination, control)
    destination.copy_outcome = "pending"
    progress = publisher.copy(source_container, source, etag)
    destination.inject("delete_blob", errors.ResourceModifiedError("changed"), after=False)

    with pytest.raises(ReleaseVerificationError, match="cleanup"):
        publisher.abort(PATH, progress.copy_id)

    assert _name(publisher) in destination.blobs


def test_given_wrong_streamed_bytes_when_write_then_no_object_committed(
    destination: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError):
        publisher.write(PATH, [b"abc"], length=3, sha256=sha256_hex(b"xyz"))

    assert _name(publisher) not in destination.blobs


def test_given_existing_owned_write_when_different_bytes_then_collision_refused(
    destination: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    publisher = _publisher(destination, control)
    publisher.write(PATH, [b"first"], length=5, sha256=sha256_hex(b"first"))

    with pytest.raises(ReleaseVerificationError):
        publisher.write(PATH, [b"other"], length=5, sha256=sha256_hex(b"other"))

    assert destination.blobs[_name(publisher)].data == b"first"


def test_given_uncertain_marker_response_when_publish_then_inspection_precedes_retry(
    destination: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    errors = _azure_errors()
    control.commit = True
    publisher = _publisher(destination, control)
    marker = b'{"marker": "published"}'
    destination.inject("upload_blob", errors.ServiceRequestError("connection reset"), after=True)

    publisher._publish_marker(marker, expected_manifest_sha256=sha256_hex(marker))

    assert destination.blobs[_name(publisher, MARKER_PATH)].data == marker
    with pytest.raises(ReleaseVerificationError, match="incompatible"):
        publisher._publish_marker(b'{"marker": "other"}', expected_manifest_sha256=sha256_hex(marker))
    assert destination.blobs[_name(publisher, MARKER_PATH)].data == marker


def test_given_no_commit_when_publish_marker_then_refused_without_marker(
    destination: _FakeContainer, control: OwnedControl, registry: dict
) -> None:
    publisher = _publisher(destination, control)

    with pytest.raises(ReleaseVerificationError, match="commit"):
        publisher._publish_marker(b"marker", expected_manifest_sha256=sha256_hex(b"marker"))

    assert _name(publisher, MARKER_PATH) not in destination.blobs
