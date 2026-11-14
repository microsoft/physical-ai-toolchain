"""Blob child protocols must retain exact authorization and ownership boundaries."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from physical_ai_curation.release import blob_worker
from physical_ai_curation.release.azure_publication import BlobPublisher, _operation_id, _ownership
from physical_ai_curation.release.blob_worker import SupervisedBlobPublisher
from physical_ai_curation.release.canonical import sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.jobs import BLOB_OWNER_CLEANUP_PATH, ReleaseRuntimeSettings
from physical_ai_curation.release.models import MARKER_PATH, SourceObject
from physical_ai_curation.release.publication import PublicationAttempt, ownership_bytes, ownership_path
from physical_ai_curation.release.record_storage import BlobLocation, LocalRecordStorage
from test_release_publishers import (
    ATTEMPT,
    PATH,
    PAYLOAD,
    OwnedControl,
    _azure_errors,
    _FakeBlobClient,
    _FakeContainer,
)
from test_release_publishers import (
    _source as _copy_source,
)


@pytest.fixture
def publisher(tmp_path: Path) -> SupervisedBlobPublisher:
    jobs = Mock()
    jobs.root = tmp_path
    jobs.settings = ReleaseRuntimeSettings()
    jobs.storage = LocalRecordStorage(tmp_path / "ledger")
    location = BlobLocation("https://example.blob.core.windows.net", "release-state", "selected")
    return SupervisedBlobPublisher(_FakeContainer("example", "release-state", {}), location, ATTEMPT, jobs, "a" * 32, 1)


def _source() -> SourceObject:
    return SourceObject(path=PATH, revision="v1", length=len(PAYLOAD), sha256=sha256_hex(PAYLOAD))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.blob.core.windows.net/source/data/file.parquet",
        "https://other.blob.core.windows.net/source/data/file.parquet",
        "https://example.blob.core.windows.net/source/data/file.parquet?token=forbidden",
        "https://example.blob.core.windows.net/source/data/file.parquet#fragment",
        "https://user@example.blob.core.windows.net/source/data/file.parquet",
        "https://example.blob.core.windows.net/source",
    ],
)
def test_copy_item_refuses_unbound_source_address_before_client_creation(
    publisher: SupervisedBlobPublisher, url: str
) -> None:
    factory = Mock()
    source = _source()

    with pytest.raises((ReleaseVerificationError, ValueError)):
        blob_worker._copy_item(
            publisher,
            factory,
            publisher.location,
            source.model_dump(mode="json"),
            url,
            "etag",
            PATH,
            source.length,
            source.sha256,
        )

    factory.assert_not_called()


def test_copy_item_refuses_traversal_before_copy_dispatch(publisher: SupervisedBlobPublisher) -> None:
    source = _source()
    publisher.copy = Mock()
    with pytest.raises(ReleaseVerificationError):
        blob_worker._copy_item(
            publisher,
            Mock(),
            publisher.location,
            source.model_dump(mode="json"),
            "https://example.blob.core.windows.net/source/../escape",
            "etag",
            PATH,
            source.length,
            source.sha256,
        )

    publisher.copy.assert_not_called()


@pytest.mark.parametrize("changed", ["path", "length", "digest"])
def test_copy_item_requires_exact_authorized_source_identity(publisher: SupervisedBlobPublisher, changed: str) -> None:
    source = _source()
    factory = Mock()
    with pytest.raises(ReleaseVerificationError, match="authorized effect"):
        blob_worker._copy_item(
            publisher,
            factory,
            publisher.location,
            source.model_dump(mode="json"),
            "https://example.blob.core.windows.net/source/data/file.parquet",
            "etag",
            "different.parquet" if changed == "path" else PATH,
            source.length + 1 if changed == "length" else source.length,
            "b" * 64 if changed == "digest" else source.sha256,
        )

    factory.assert_not_called()


def test_generated_inline_payload_streams_exact_bytes_across_chunk_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blob_worker, "CHUNK_BYTES", 3)

    chunks = list(blob_worker._generated_chunks({"mode": "inline", "content": base64.b64encode(b"abcdefg")}))

    assert chunks == [b"abc", b"def", b"g"]


def test_unknown_generated_payload_mode_is_refused() -> None:
    with pytest.raises(ReleaseVerificationError, match="unsupported generated"):
        blob_worker._generated_chunks({"mode": "unknown"})


@pytest.mark.parametrize("items", [None, {}, [], [None] * 5])
def test_batch_protocol_enforces_item_count(publisher: SupervisedBlobPublisher, items: object) -> None:
    publisher.jobs.settings = ReleaseRuntimeSettings(blob_batch_size=4)

    with pytest.raises(ReleaseVerificationError, match="group exceeds"):
        blob_worker._batch_entries({"items": items}, publisher.jobs)


@pytest.mark.parametrize("damage", ["missing", "size", "contents"])
def test_root_inspection_does_not_claim_foreign_output(publisher: SupervisedBlobPublisher, damage: str) -> None:
    container = publisher.container
    companion = publisher.parent_prefix + ownership_path(publisher.attempt.prefix)
    if damage == "missing":
        container.put(publisher.root + PATH, PAYLOAD)
    else:
        content = ownership_bytes(publisher.attempt)
        container.put(companion, b"x" if damage == "size" else b"x" * len(content))
    before = {name: record.data for name, record in container.blobs.items()}

    with pytest.raises(ReleaseVerificationError, match=r"ownership conflict|without ownership"):
        blob_worker._inspect_root_ownership(publisher)

    assert {name: record.data for name, record in container.blobs.items()} == before


@pytest.mark.parametrize("damage", ["missing", "size", "owner"])
def test_generated_upload_replay_requires_matching_owned_output(
    publisher: SupervisedBlobPublisher, damage: str
) -> None:
    if damage != "missing":
        publisher.container.put(
            publisher.root + PATH,
            PAYLOAD + b"x" if damage == "size" else PAYLOAD,
            _ownership(ATTEMPT, len(PAYLOAD), sha256_hex(PAYLOAD), "foreign-operation"),
        )

    with pytest.raises(ReleaseVerificationError, match="ownership conflict"):
        publisher._verify_generated({"path": PATH, "length": len(PAYLOAD), "sha256": sha256_hex(PAYLOAD)})


@pytest.mark.parametrize("observed", ["absent", "complete", "foreign"])
def test_given_interrupted_generated_upload_when_recovered_then_only_owned_bytes_are_acknowledged(
    publisher: SupervisedBlobPublisher, observed: str
) -> None:
    digest = sha256_hex(PAYLOAD)
    publisher.jobs.recovery_effects.return_value = [{"path": PATH, "length": len(PAYLOAD), "digest": digest}]
    if observed != "absent":
        operation = _operation_id(ATTEMPT, PATH, len(PAYLOAD), digest)
        publisher.container.put(publisher.parent_prefix + ownership_path(ATTEMPT.prefix), ownership_bytes(ATTEMPT))
        publisher.container.put(
            publisher.root + PATH,
            PAYLOAD,
            _ownership(ATTEMPT, len(PAYLOAD), digest, operation if observed == "complete" else "foreign"),
        )

    if observed == "foreign":
        with pytest.raises(ReleaseVerificationError, match="ownership conflict"):
            publisher.recover_outputs({})
        publisher.jobs.reconcile_effect.assert_not_called()
        publisher.jobs.record_io.assert_not_called()
    else:
        publisher.recover_outputs({})
        publisher.jobs.reconcile_effect.assert_called_once_with(
            publisher.job_id, publisher.fence, PATH, observed=observed, length=len(PAYLOAD), digest=digest
        )
        assert publisher.jobs.record_io.call_count == (1 if observed == "complete" else 0)


def test_given_interrupted_copy_when_recovered_then_source_revision_is_retained(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    source = _source()
    publisher.jobs.recovery_effects.return_value = [{"path": PATH}]
    reconcile = mocker.patch.object(publisher, "reconcile_copy")

    publisher.recover_outputs({PATH: (source, "source-etag")})

    reconcile.assert_called_once_with(source, "source-etag")
    publisher.jobs.reconcile_effect.assert_not_called()


@pytest.mark.parametrize("replay", [True, False])
def test_given_generated_upload_batch_when_flushed_then_only_fresh_items_are_dispatched(
    publisher: SupervisedBlobPublisher, mocker: Any, replay: bool
) -> None:
    digest = sha256_hex(PAYLOAD)
    publisher.jobs.begin_effects.return_value = [replay]
    execute = mocker.patch.object(publisher, "_execute_batch")

    def upload(*args: object) -> None:
        publisher.container.put(publisher.parent_prefix + ownership_path(ATTEMPT.prefix), ownership_bytes(ATTEMPT))
        publisher.container.put(
            publisher.root + PATH,
            PAYLOAD,
            _ownership(ATTEMPT, len(PAYLOAD), digest, _operation_id(ATTEMPT, PATH, len(PAYLOAD), digest)),
        )

    execute.side_effect = upload
    if replay:
        upload()
    with publisher.write_batch():
        publisher.write(PATH, (PAYLOAD,), length=len(PAYLOAD), sha256=digest)

    assert execute.call_count == (0 if replay else 1)
    assert publisher.jobs.finish_effects.call_count == (0 if replay else 1)
    assert publisher.container.blobs[publisher.root + PATH].data == PAYLOAD
    assert publisher._generated is None


def test_given_empty_generated_upload_batch_when_flushed_then_no_authority_or_dispatch(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    execute = mocker.patch.object(publisher, "_execute_batch")

    with publisher.write_batch():
        pass

    execute.assert_not_called()
    publisher.jobs.begin_effects.assert_not_called()


def test_given_oversized_generated_batch_item_when_queued_then_pending_items_flush_first(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch, mocker: Any
) -> None:
    monkeypatch.setattr(blob_worker, "_UPLOAD_BATCH_BYTES", 1)
    flush = mocker.patch.object(publisher, "_flush_generated")
    write = mocker.patch.object(publisher, "_write")
    calls = mocker.Mock()
    calls.attach_mock(flush, "flush")
    calls.attach_mock(write, "write")

    with publisher.write_batch():
        publisher._generate(PATH, len(PAYLOAD), sha256_hex(PAYLOAD), {"mode": "inline", "content": "bounded"})

    assert [call[0] for call in calls.mock_calls] == ["flush", "write", "flush"]
    write.assert_called_once_with(PATH, len(PAYLOAD), sha256_hex(PAYLOAD), {"mode": "inline", "content": "bounded"})


def test_nested_upload_batch_does_not_destroy_outer_batch(publisher: SupervisedBlobPublisher) -> None:
    publisher._flush_generated = Mock()

    with publisher.write_batch():
        outer = publisher._generated
        with pytest.raises(ReleaseVerificationError, match="already open"), publisher.write_batch():
            pass
        assert publisher._generated is outer

    assert publisher._generated is None
    publisher._flush_generated.assert_called_once()


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_upload_batch_failure_discards_pending_payloads(
    publisher: SupervisedBlobPublisher, failure_type: type[BaseException]
) -> None:
    publisher._flush_generated = Mock()
    caller = Mock(side_effect=failure_type("caller failed"))
    with pytest.raises(failure_type, match="caller failed"), publisher.write_batch():
        publisher.write(PATH, (PAYLOAD,), length=len(PAYLOAD), sha256=sha256_hex(PAYLOAD))
        assert publisher._generated is not None and len(publisher._generated) == 1
        assert publisher._generated_bytes > 0
        caller()

    assert publisher._generated is None
    assert publisher._generated_bytes == 0
    publisher._flush_generated.assert_not_called()


@pytest.mark.parametrize("mode", ["marker", "oversized_chunk", "oversized_payload"])
def test_inline_write_refuses_unbounded_or_marker_payload_before_dispatch(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(blob_worker, "CHUNK_BYTES", 4)
    monkeypatch.setattr(blob_worker, "_UPLOAD_BATCH_BYTES", 6)
    publisher._generate = Mock()
    path = MARKER_PATH if mode == "marker" else PATH
    chunks = (b"12345",) if mode == "oversized_chunk" else (b"1234", b"1234")

    with pytest.raises(ReleaseVerificationError, match=r"marker|protocol bound"):
        publisher.write(path, chunks, length=8, sha256="a" * 64)

    publisher._generate.assert_not_called()


@pytest.mark.parametrize("operation", ["abort", "delete", "copy", "create"])
def test_unsupervised_blob_mutations_are_refused(publisher: SupervisedBlobPublisher, operation: str) -> None:
    with pytest.raises(ReleaseVerificationError, match=r"supervised|active source"):
        if operation == "abort":
            publisher.abort(PATH, "copy-1")
        elif operation == "delete":
            publisher._delete_owned_residue(Mock(), "copy-1", "operation")
        elif operation == "copy":
            publisher._dispatch_copy(Mock(), "https://example.invalid/source", {}, "etag")
        else:
            publisher._create(Mock(), PATH, iter(()), 0, sha256_hex(b""), "operation")

    assert publisher.container.calls == []


def test_generated_upload_protocol_is_bounded_before_dispatch(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(blob_worker, "MAX_PROTOCOL_BYTES", 32)

    with pytest.raises(ReleaseVerificationError, match="protocol exceeds"):
        publisher._protocol(PATH, len(PAYLOAD), sha256_hex(PAYLOAD), {"mode": "inline", "content": "x" * 64})

    assert publisher.container.calls == []


@pytest.mark.parametrize("mode", ["upload_batch", "blob_copy_batch"])
def test_given_default_blob_client_factory_when_child_runs_then_clients_and_credentials_close(
    publisher: SupervisedBlobPublisher, mocker: Any, mode: str
) -> None:
    registry = {}
    destination = _FakeContainer("example", publisher.location.container, registry)
    source_container = _FakeContainer("example", "source", registry)
    source, source_etag = _copy_source(source_container)
    digest = sha256_hex(PAYLOAD)
    item = {
        "path": PATH,
        "length": len(PAYLOAD),
        "sha256": digest,
        "mode": "inline",
        "content": base64.b64encode(PAYLOAD).decode("ascii"),
    }
    if mode == "blob_copy_batch":
        digest = blob_worker.source_effect_digest(source)
        item.update(
            sha256=digest,
            source=source.model_dump(mode="json"),
            source_url=f"https://example.blob.core.windows.net/source/{PATH}",
            source_etag=source_etag,
        )
    content = publisher._protocol(PATH, len(PAYLOAD), digest, {"mode": mode, "items": [item]})
    credential = mocker.MagicMock()
    credential.__enter__.return_value = credential
    credential.get_token.return_value = SimpleNamespace(token="test-token")
    clients = []

    def container_client(account: str, name: str, **kwargs: Any) -> Any:
        client = mocker.MagicMock()
        client.__enter__.return_value = destination if name == publisher.location.container else source_container
        clients.append(client)
        return client

    mocker.patch("azure.identity.DefaultAzureCredential", return_value=credential)
    mocker.patch("azure.storage.blob.ContainerClient", side_effect=container_client)
    mocker.patch.object(blob_worker, "ReleaseJobStore", return_value=publisher.jobs)
    descriptor = os.memfd_create("test-owned-blob-child", os.MFD_CLOEXEC)
    try:
        os.write(descriptor, content)
        os.lseek(descriptor, 0, os.SEEK_SET)
        blob_worker._process(os.dup(descriptor))
        assert os.fstat(descriptor).st_size == len(content)
    finally:
        os.close(descriptor)

    assert destination.blobs[publisher.root + PATH].data == PAYLOAD
    credential.__exit__.assert_called_once()
    assert len(clients) == (2 if mode == "blob_copy_batch" else 1)
    for client in clients:
        client.__exit__.assert_called_once()
    publisher.jobs.authorize_blob_writers.assert_called_once()


@pytest.mark.parametrize("arguments", [["worker"], ["worker", "not-an-integer"], ["worker", "12"]])
def test_given_blob_worker_arguments_when_dispatched_then_exit_status_and_descriptor_are_checked(
    mocker: Any, arguments: list[str]
) -> None:
    mocker.patch.object(sys, "argv", arguments)
    process = mocker.patch.object(blob_worker, "_process")

    assert blob_worker.main() == (0 if arguments == ["worker", "12"] else 2 if len(arguments) != 2 else 1)
    assert process.call_count == (1 if arguments == ["worker", "12"] else 0)


def test_given_blob_worker_process_failure_when_dispatched_then_fixed_failure_status(mocker: Any) -> None:
    mocker.patch.object(sys, "argv", ["worker", "12"])
    mocker.patch.object(blob_worker, "_process", side_effect=OSError("fixed test failure"))

    assert blob_worker.main() == 1


@pytest.mark.parametrize("damage", ["version", "mode", "destination", "root", "blob_name", "bound"])
def test_child_protocol_rejects_tampering_before_ledger_or_azure_access(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    spec = json.loads(publisher._protocol(PATH, len(PAYLOAD), sha256_hex(PAYLOAD), {"mode": "inline", "content": ""}))
    if damage == "version":
        spec["version"] = 2
    elif damage == "mode":
        spec["mode"] = "unknown"
    elif damage == "bound":
        monkeypatch.setattr(blob_worker, "MAX_PROTOCOL_BYTES", 32)
    else:
        spec[damage] = "foreign"
    factory = Mock()
    descriptor = os.memfd_create("test-blob-protocol", os.MFD_CLOEXEC)
    with os.fdopen(os.dup(descriptor), "wb") as stream:
        stream.write(json.dumps(spec).encode())
    os.lseek(descriptor, 0, os.SEEK_SET)

    with pytest.raises(ReleaseVerificationError, match=r"protocol|authorized root"):
        blob_worker._process(descriptor, container_factory=factory)

    factory.assert_not_called()


@pytest.mark.parametrize("returncode", [1, -9])
def test_supervised_child_failure_is_not_acknowledged(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    execute = Mock(return_value=Mock(reason="exited", returncode=returncode))
    monkeypatch.setattr(blob_worker, "run_supervised", execute)
    content = publisher._protocol(PATH, 1, "a" * 64, {"mode": "inline", "content": ""})

    with pytest.raises(ReleaseVerificationError, match="did not complete"):
        publisher._run_child(content, 1, effect_path=PATH)

    publisher.jobs.finish_effect.assert_not_called()
    descriptor = execute.call_args.kwargs["inherit_fds"][0]
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_expired_cleanup_budget_refuses_child_dispatch(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = Mock()
    monkeypatch.setattr(blob_worker, "run_supervised", execute)
    monkeypatch.setattr(blob_worker.time, "monotonic", lambda: 100)
    publisher._cleanup_deadline = 99
    content = publisher._protocol(PATH, 1, "a" * 64, {"mode": "blob_remove"})

    with pytest.raises(ReleaseVerificationError, match="budget exhausted"):
        publisher._run_child(content, 1, effect_path=PATH)

    execute.assert_not_called()


def _cleanup_case(*, pending: bool = False) -> tuple[BlobPublisher, Mock, dict[str, Any]]:
    registry = {}
    destination = _FakeContainer("dest-account", "release", registry)
    publisher = BlobPublisher(destination, ATTEMPT, OwnedControl())
    if pending:
        source_container = _FakeContainer("dest-account", "source", registry)
        source, etag = _copy_source(source_container)
        destination.copy_outcome = "pending"
        publisher.copy(source_container, source, etag)
    else:
        publisher.write(PATH, (PAYLOAD,), length=len(PAYLOAD), sha256=sha256_hex(PAYLOAD))
    properties = destination.get_blob_client(publisher.root + PATH).get_blob_properties()
    item = {
        "path": PATH,
        "length": len(PAYLOAD),
        "sha256": sha256_hex(PAYLOAD),
        "operation": properties.metadata[blob_worker.OWNER_OPERATION],
        "copy_id": None if properties.copy is None else properties.copy.id,
    }
    jobs = Mock()
    jobs.settings.blob_batch_size = 16
    spec = {
        "job_id": "a" * 32,
        "fence": 1,
        "destination": "configured-destination",
        "items": [item],
    }
    return publisher, jobs, spec


@pytest.mark.parametrize("damage", ["length", "digest", "operation", "copy_id", "foreign_owner"])
def test_batch_cleanup_refuses_unbound_residue_before_mutation(damage: str) -> None:
    publisher, jobs, spec = _cleanup_case()
    item = spec["items"][0]
    if damage == "foreign_owner":
        publisher.container.blobs[publisher.root + PATH].metadata["curation_attempt"] = "foreign"
    else:
        item[{"digest": "sha256"}.get(damage, damage)] = 999 if damage == "length" else "foreign"
    original = publisher.container.blobs[publisher.root + PATH].data

    with pytest.raises(ReleaseVerificationError, match="ownership prevents cleanup"):
        blob_worker._remove_blobs(publisher, jobs, spec)

    assert publisher.container.blobs[publisher.root + PATH].data == original
    jobs.authorize_blob_writers.assert_not_called()


@pytest.mark.parametrize("outcome", ["removed", "partial", "wrong_count", "marker_race"])
def test_batch_cleanup_reauthorizes_before_conditional_provider_delete(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    publisher, jobs, spec = _cleanup_case()
    destination = publisher.container
    calls = []

    def delete_blobs(*items: dict[str, Any], **kwargs: object) -> list[SimpleNamespace]:
        jobs.authorize_blob_writers.assert_called_once()
        calls.extend(items)
        if outcome == "wrong_count":
            return []
        if outcome == "partial":
            return [SimpleNamespace(status_code=412)]
        for item in items:
            destination.get_blob_client(item["name"]).delete_blob(
                etag=item["etag"], match_condition=item["match_condition"]
            )
        return [SimpleNamespace(status_code=202) for _ in items]

    monkeypatch.setattr(destination, "delete_blobs", delete_blobs, raising=False)
    if outcome == "marker_race":
        jobs.authorize_blob_writers.side_effect = lambda *args, **kwargs: destination.put(
            publisher.root + MARKER_PATH, b"published"
        )

    if outcome == "removed":
        blob_worker._remove_blobs(publisher, jobs, spec)
        assert publisher.root + PATH not in destination.blobs
    else:
        with pytest.raises(
            ReleaseVerificationError, match="marked package" if outcome == "marker_race" else "partially"
        ):
            blob_worker._remove_blobs(publisher, jobs, spec)
        assert publisher.root + PATH in destination.blobs
    assert bool(calls) is (outcome != "marker_race")


@pytest.mark.parametrize("outcome", ["aborted", "already_complete", "gone", "still_pending", "provider_failure"])
def test_batch_cleanup_inspects_pending_copy_again_after_abort(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    publisher, jobs, spec = _cleanup_case(pending=True)
    destination = publisher.container
    original = _FakeBlobClient.abort_copy
    deletions = Mock(return_value=[SimpleNamespace(status_code=202)])
    monkeypatch.setattr(destination, "delete_blobs", deletions, raising=False)

    def abort(client: _FakeBlobClient, copy_id: str, **kwargs: object) -> None:
        if outcome == "gone":
            del destination.blobs[client.name]
            return
        if outcome in {"already_complete", "still_pending", "provider_failure"}:
            if outcome == "already_complete":
                destination.finish_copy(client.name)
            error = _azure_errors().HttpResponseError("copy abort response")
            error.status_code = 503 if outcome == "provider_failure" else 409
            raise error
        original(client, copy_id, **kwargs)

    monkeypatch.setattr(_FakeBlobClient, "abort_copy", abort)
    if outcome in {"still_pending", "provider_failure"}:
        with pytest.raises(
            ReleaseVerificationError if outcome == "still_pending" else _azure_errors().HttpResponseError
        ):
            blob_worker._remove_blobs(publisher, jobs, spec)
        jobs.authorize_blob_writers.assert_not_called()
        deletions.assert_not_called()
    else:
        blob_worker._remove_blobs(publisher, jobs, spec)
        jobs.authorize_blob_writers.assert_called_once()
        assert deletions.call_count == (0 if outcome == "gone" else 1)


def test_batch_cleanup_of_already_absent_payload_is_repeat_safe() -> None:
    publisher, jobs, spec = _cleanup_case()
    del publisher.container.blobs[publisher.root + PATH]

    blob_worker._remove_blobs(publisher, jobs, spec)

    jobs.authorize_blob_writers.assert_called_once()


@pytest.mark.parametrize("damage", ["length", "digest", "operation", "copy_id", "foreign_owner"])
def test_given_single_cleanup_with_foreign_identity_when_removed_then_no_mutation(damage: str) -> None:
    publisher, jobs, spec = _cleanup_case()
    spec.update(spec.pop("items")[0], mode="blob_remove", blob_name=publisher.root + PATH)
    if damage == "foreign_owner":
        publisher.container.blobs[publisher.root + PATH].metadata["curation_attempt"] = "foreign"
    else:
        spec[{"digest": "sha256"}.get(damage, damage)] = 999 if damage == "length" else "foreign"
    before = publisher.container.blobs[publisher.root + PATH].data

    with pytest.raises(ReleaseVerificationError, match="ownership prevents cleanup"):
        blob_worker._remove_blob(publisher, jobs, spec)

    assert publisher.container.blobs[publisher.root + PATH].data == before
    jobs.authorize_blob_writer.assert_not_called()


@pytest.mark.parametrize("outcome", ["removed", "already_absent", "marked", "marker_race"])
def test_given_single_cleanup_when_reauthorized_then_publication_marker_is_respected(outcome: str) -> None:
    publisher, jobs, spec = _cleanup_case()
    spec.update(spec.pop("items")[0], mode="blob_remove", blob_name=publisher.root + PATH)
    if outcome == "already_absent":
        del publisher.container.blobs[publisher.root + PATH]
    elif outcome == "marked":
        publisher.container.put(publisher.root + MARKER_PATH, b"published")
    elif outcome == "marker_race":
        jobs.authorize_blob_writer.side_effect = lambda *args, **kwargs: publisher.container.put(
            publisher.root + MARKER_PATH, b"published"
        )

    if outcome in {"marked", "marker_race"}:
        with pytest.raises(ReleaseVerificationError, match="marked package"):
            blob_worker._remove_blob(publisher, jobs, spec)
        assert publisher.root + PATH in publisher.container.blobs
    else:
        blob_worker._remove_blob(publisher, jobs, spec)
        assert publisher.root + PATH not in publisher.container.blobs
    assert jobs.authorize_blob_writer.call_count == (0 if outcome in {"already_absent", "marked"} else 1)


@pytest.mark.parametrize("outcome", ["aborted", "already_complete", "gone", "still_pending", "provider_failure"])
def test_given_single_pending_copy_when_cleanup_runs_then_abort_outcome_is_rechecked(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    publisher, jobs, spec = _cleanup_case(pending=True)
    spec.update(spec.pop("items")[0], mode="blob_remove", blob_name=publisher.root + PATH)
    original = _FakeBlobClient.abort_copy

    def abort(client: _FakeBlobClient, copy_id: str, **kwargs: object) -> None:
        if outcome == "gone":
            del publisher.container.blobs[client.name]
            return
        if outcome in {"already_complete", "still_pending", "provider_failure"}:
            if outcome == "already_complete":
                publisher.container.finish_copy(client.name)
            error = _azure_errors().HttpResponseError("copy abort response")
            error.status_code = 503 if outcome == "provider_failure" else 409
            raise error
        original(client, copy_id, **kwargs)

    monkeypatch.setattr(_FakeBlobClient, "abort_copy", abort)
    if outcome in {"still_pending", "provider_failure"}:
        with pytest.raises(
            ReleaseVerificationError if outcome == "still_pending" else _azure_errors().HttpResponseError
        ):
            blob_worker._remove_blob(publisher, jobs, spec)
        assert publisher.root + PATH in publisher.container.blobs
        assert jobs.authorize_blob_writer.call_count == (1 if outcome == "still_pending" else 0)
    else:
        blob_worker._remove_blob(publisher, jobs, spec)
        assert publisher.root + PATH not in publisher.container.blobs
        jobs.authorize_blob_writer.assert_called_once()


@pytest.mark.parametrize("outcome", ["removed", "already_absent", "payload_remains", "wrong_path"])
def test_companion_cleanup_removes_only_matching_empty_release_root(outcome: str) -> None:
    publisher, jobs, spec = _cleanup_case()
    del publisher.container.blobs[publisher.root + PATH]
    companion = publisher.parent_prefix + ownership_path(publisher.attempt.prefix)
    if outcome == "already_absent":
        del publisher.container.blobs[companion]
    elif outcome == "payload_remains":
        publisher.container.put(publisher.root + PATH, PAYLOAD)
    spec.update(mode="blob_owner_cleanup", path=PATH if outcome == "wrong_path" else BLOB_OWNER_CLEANUP_PATH)

    if outcome in {"payload_remains", "wrong_path"}:
        with pytest.raises(ReleaseVerificationError):
            blob_worker._remove_blob(publisher, jobs, spec)
        assert companion in publisher.container.blobs
    else:
        blob_worker._remove_blob(publisher, jobs, spec)
        assert companion not in publisher.container.blobs


OTHER_ATTEMPT = PublicationAttempt("pkg-other", "attempt-other", source_dataset_id="org-dataset")


def _put_owned(publisher: SupervisedBlobPublisher, *, owner: PublicationAttempt = ATTEMPT) -> str:
    digest = sha256_hex(PAYLOAD)
    operation = _operation_id(ATTEMPT, PATH, len(PAYLOAD), digest)
    publisher.container.put(publisher.root + PATH, PAYLOAD, _ownership(owner, len(PAYLOAD), digest, operation))
    publisher.container.put(publisher.parent_prefix + ownership_path(ATTEMPT.prefix), ownership_bytes(ATTEMPT))
    return digest


def _spec(
    publisher: SupervisedBlobPublisher, operation: dict[str, Any], *, digest: str | None = None
) -> dict[str, Any]:
    return json.loads(publisher._protocol(PATH, len(PAYLOAD), digest or sha256_hex(PAYLOAD), operation))


def _patch_process(mocker: Any, publisher: SupervisedBlobPublisher) -> None:
    mocker.patch.object(blob_worker, "ReleaseJobStore", return_value=publisher.jobs)
    mocker.patch.object(blob_worker, "storage_from_configuration", return_value=publisher.jobs.storage)


def _run_process(content: bytes, publisher: SupervisedBlobPublisher) -> None:
    source_fd = os.memfd_create("blob-protocol", os.MFD_CLOEXEC)
    try:
        os.write(source_fd, content)
        os.lseek(source_fd, 0, os.SEEK_SET)
        blob_worker._process(os.dup(source_fd), container_factory=Mock(return_value=publisher.container))
    finally:
        os.close(source_fd)


@pytest.mark.parametrize("damage", ["missing", "size", "foreign_owner"])
def test_given_uploaded_output_when_ownership_fails_then_create_refuses(
    publisher: SupervisedBlobPublisher, mocker: Any, damage: str
) -> None:
    # Arrange
    digest = sha256_hex(PAYLOAD)
    operation = _operation_id(ATTEMPT, PATH, len(PAYLOAD), digest)
    if damage == "size":
        publisher.container.put(
            publisher.root + PATH, PAYLOAD + b"x", _ownership(ATTEMPT, len(PAYLOAD), digest, operation)
        )
    elif damage == "foreign_owner":
        publisher.container.put(
            publisher.root + PATH, PAYLOAD, _ownership(ATTEMPT, len(PAYLOAD), digest, "foreign-operation")
        )
    publisher._active_write = {"mode": "inline", "content": ""}
    mocker.patch.object(publisher, "_execute")

    # Act / Assert
    with pytest.raises(ReleaseVerificationError, match="uploaded output ownership conflict"):
        publisher._create(None, PATH, iter([PAYLOAD]), len(PAYLOAD), digest, operation)
    publisher.jobs.record_io.assert_not_called()


def test_given_cancelled_copy_residue_when_reconciled_then_refuses(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    _put_owned(publisher)
    publisher.control = MagicMock()
    mocker.patch.object(publisher, "_execute")

    with pytest.raises(ReleaseVerificationError, match="cancelled copy residue remains unresolved"):
        publisher.reconcile_copy(_source(), "etag", cancel=True)


def test_given_cleanup_budget_exhausted_when_cancelled_then_refuses_and_marks_failed(
    publisher: SupervisedBlobPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_owned(publisher)
    publisher.jobs.cancellation_effects.return_value = [
        {"path": PATH, "length": len(PAYLOAD), "digest": sha256_hex(PAYLOAD), "copy_id": None}
    ]
    ticks = iter([0, 10**9])
    monkeypatch.setattr(blob_worker, "time", SimpleNamespace(monotonic=lambda: next(ticks, 10**9)))

    with pytest.raises(ReleaseVerificationError, match="blob cleanup budget exhausted"):
        publisher.cleanup_cancelled()
    publisher.jobs.blob_cleanup_status.assert_called_with("a" * 32, 1, failed=True)


@pytest.mark.parametrize("damage", ["length", "digest", "foreign_owner"])
def test_given_cancelled_output_identity_differs_when_cleaning_then_refuses(
    publisher: SupervisedBlobPublisher, damage: str
) -> None:
    digest = _put_owned(publisher, owner=OTHER_ATTEMPT if damage == "foreign_owner" else ATTEMPT)
    effect = {"path": PATH, "length": len(PAYLOAD), "digest": digest, "copy_id": None}
    if damage == "length":
        effect["length"] += 1
    if damage == "digest":
        effect["digest"] = "b" * 64
    publisher.jobs.cancellation_effects.return_value = [effect]

    with pytest.raises(ReleaseVerificationError, match="output ownership prevents cleanup"):
        publisher.cleanup_cancelled()
    publisher.jobs.settle_cancellation.assert_not_called()


def test_given_cleanup_outcome_unresolved_when_batch_finishes_then_refuses(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    digest = _put_owned(publisher)
    publisher.jobs.cancellation_effects.return_value = [
        {"path": PATH, "length": len(PAYLOAD), "digest": digest, "copy_id": None}
    ]
    mocker.patch.object(publisher, "_execute")

    with pytest.raises(ReleaseVerificationError, match="blob cleanup outcome remains unresolved"):
        publisher.cleanup_cancelled()
    publisher.jobs.finish_blob_removals.assert_not_called()


@pytest.mark.parametrize("residue", ["companion", "stray_blob"])
def test_given_cleanup_closure_residue_when_owner_removed_then_refuses(
    publisher: SupervisedBlobPublisher, mocker: Any, residue: str
) -> None:
    publisher.jobs.cancellation_effects.return_value = []
    publisher.jobs.begin_blob_owner_cleanup.return_value = (1, "d" * 64)
    if residue == "companion":
        publisher.container.put(publisher.parent_prefix + ownership_path(ATTEMPT.prefix), ownership_bytes(ATTEMPT))
        mocker.patch.object(publisher, "_execute")
    else:
        mocker.patch.object(
            publisher,
            "_execute",
            side_effect=lambda *_: publisher.container.put(publisher.root + "stray.bin", b"x"),
        )

    with pytest.raises(ReleaseVerificationError, match="blob cleanup closure remains unresolved"):
        publisher.cleanup_cancelled()
    publisher.jobs.reconcile_effect.assert_not_called()


def test_given_marker_replay_recorded_when_published_then_marker_is_not_written(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    publisher.control = MagicMock()
    publisher.jobs.begin_marker_effect.return_value = True
    execute = mocker.patch.object(publisher, "_execute")

    publisher._publish_marker(b"marker", expected_manifest_sha256="c" * 64)

    execute.assert_not_called()
    publisher.jobs.finish_effect.assert_not_called()


def test_given_marker_readback_differs_when_published_then_effect_fails(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    marker = b"m" * 6
    publisher.control = MagicMock()
    publisher.jobs.begin_marker_effect.return_value = False
    publisher.container.put(publisher.root + MARKER_PATH, b"x" * 6)
    mocker.patch.object(publisher, "_execute")

    with pytest.raises(ReleaseVerificationError, match="commit decision"):
        publisher._publish_marker(marker, expected_manifest_sha256="c" * 64)
    publisher.jobs.finish_effect.assert_called_once_with("a" * 32, 1, MARKER_PATH, ok=False)


@pytest.mark.parametrize(
    ("stored", "verify", "observed"),
    [
        (None, None, "absent"),
        (b"marker-bytes", None, "complete"),
        (b"x" * 12, None, "conflict"),
        (b"marker-bytes", ReleaseVerificationError("unverified"), "conflict"),
    ],
)
def test_given_pending_marker_intent_when_published_then_resolves_observed_state(
    publisher: SupervisedBlobPublisher, mocker: Any, stored: bytes | None, verify: Any, observed: str
) -> None:
    marker = b"marker-bytes"
    publisher.jobs.marker_intent.return_value = {
        "status": "pending",
        "length": len(marker),
        "digest": sha256_hex(marker),
    }
    if stored is not None:
        publisher.container.put(publisher.root + MARKER_PATH, stored)
    mocker.patch.object(blob_worker, "verify_published", side_effect=verify)
    mocker.patch.object(BlobPublisher, "publish", return_value="published")

    assert publisher.publish("c" * 64) == "published"
    publisher.jobs.resolve_marker_effect.assert_called_once_with(
        "a" * 32, 1, observed=observed, length=len(marker), digest=sha256_hex(marker)
    )


@pytest.mark.parametrize(
    ("mode", "copy_id", "request_copy_id", "cancel"),
    [
        ("blob_cleanup", "expected", "other", True),
        ("blob_copy", None, None, True),
        ("blob_cleanup", None, None, False),
    ],
)
def test_given_copy_cleanup_identity_differs_when_reconciled_then_refuses(
    mode: str, copy_id: str | None, request_copy_id: str | None, cancel: bool
) -> None:
    jobs = Mock()
    control = blob_worker._RegisteredCopyControl(
        jobs, {"mode": mode, "copy_id": copy_id, "job_id": "a" * 32, "fence": 1, "destination": "dest"}
    )

    with (
        pytest.raises(ReleaseVerificationError, match="copy cleanup identity differs"),
        control.reconciliation(
            ATTEMPT, PATH, len(PAYLOAD), sha256_hex(PAYLOAD), copy_id=request_copy_id, cancel=cancel
        ),
    ):
        pass
    jobs.authorize_blob_writer.assert_not_called()


@pytest.mark.parametrize("spec", [{}, {"marker": False}])
def test_given_marker_without_commit_binding_when_published_then_refuses(spec: dict[str, Any]) -> None:
    jobs = Mock()
    control = blob_worker._RegisteredCopyControl(
        jobs, {**spec, "copy_id": None, "job_id": "a" * 32, "fence": 1, "destination": "dest"}
    )

    with pytest.raises(ReleaseVerificationError, match="commit-bound"), control.publication(ATTEMPT, "c" * 64):
        pass
    jobs.authorize_marker_writer.assert_not_called()


@pytest.mark.parametrize("mode", ["blob_remove_batch", "blob_remove"])
def test_given_marker_present_when_removing_outputs_then_refuses(publisher: SupervisedBlobPublisher, mode: str) -> None:
    digest = _put_owned(publisher)
    publisher.container.put(publisher.root + MARKER_PATH, b"marker")
    item = {
        "path": PATH,
        "length": len(PAYLOAD),
        "sha256": digest,
        "copy_id": None,
        "operation": _operation_id(ATTEMPT, PATH, len(PAYLOAD), digest),
    }
    if mode == "blob_remove_batch":
        spec = _spec(publisher, {"mode": mode, "items": [item]}, digest=digest)
        remover = blob_worker._remove_blobs
    else:
        spec = _spec(publisher, {"mode": mode, **item}, digest=digest)
        remover = blob_worker._remove_blob

    with pytest.raises(ReleaseVerificationError):
        remover(publisher, publisher.jobs, spec)
    assert publisher.root + PATH in publisher.container.blobs
    publisher.jobs.authorize_blob_writer.assert_not_called()


@pytest.mark.parametrize("field", ["path", "length", "digest"])
def test_given_cleanup_source_identity_differs_when_processed_then_refuses(
    publisher: SupervisedBlobPublisher, mocker: Any, field: str
) -> None:
    source = _source().model_dump(mode="json")
    if field == "path":
        source["path"] = "other/file.bin"
    if field == "length":
        source["length"] += 1
    if field == "digest":
        source["sha256"] = "b" * 64
    content = publisher._protocol(
        PATH,
        len(PAYLOAD),
        blob_worker.source_effect_digest(_source()),
        {"mode": "blob_cleanup", "source": source, "source_etag": "etag", "copy_id": None},
    )
    _patch_process(mocker, publisher)

    with pytest.raises(ReleaseVerificationError, match="cleanup identity does not match"):
        _run_process(content, publisher)


def test_given_marker_differs_from_verified_candidate_when_processed_then_refuses(
    publisher: SupervisedBlobPublisher, mocker: Any
) -> None:
    marker = b"published-marker"
    content = publisher._protocol(
        MARKER_PATH,
        len(marker),
        sha256_hex(marker),
        {
            "mode": "inline",
            "content": base64.b64encode(marker).decode("ascii"),
            "marker": True,
            "manifest_digest": "c" * 64,
        },
    )
    _patch_process(mocker, publisher)
    mocker.patch.object(blob_worker, "verify_candidate", return_value=object())
    mocker.patch.object(blob_worker, "build_marker", return_value=b"different-marker")

    with pytest.raises(ReleaseVerificationError, match="differs from the verified candidate"):
        _run_process(content, publisher)
