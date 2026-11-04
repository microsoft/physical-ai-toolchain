"""Read-only release input freezing and finalized local source admission."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
from physical_ai_curation.evidence import bind_job_evidence
from physical_ai_curation.release.canonical import serialize_model
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    LocalReleaseSource,
    ReleaseInputError,
    ReleaseSelection,
    SavedSelection,
    freeze_release_inputs,
    revalidate_release_inputs,
)
from physical_ai_curation.saved_input import SavedInputError, SavedResource


class SavedReader:
    def __init__(self) -> None:
        self.source = ("private-source", "source-revision")
        self.annotations = SavedResource({"annotations": []}, "annotation-v1")
        self.edit = SavedResource({"operations": {"removedFrames": [1]}, "author_id": "private-human"}, "edit-v1")
        self.reads = []

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return self.source

    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
        self.reads.append("annotation")
        return self.annotations

    async def edits(
        self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
    ) -> SavedResource:
        self.reads.append("edits")
        return self.edit


@pytest.fixture
def local_source(tmp_path: Path) -> LocalReleaseSource:
    (tmp_path / "data").mkdir()
    (tmp_path / "data/frames.jsonl").write_bytes(b'{"frame_index":0}\n')
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/info.json").write_bytes(b'{"total_episodes":1}')
    return LocalReleaseSource(tmp_path, finalized=True)


def _context() -> FreezeContext:
    return FreezeContext(
        principal_scope_id="private-machine",
        source=("private-source", "source-revision"),
        contributor_labels=(("private-human", "curator-a"), ("private-machine", "automation-a")),
    )


def _selection(*resources: SavedSelection) -> ReleaseSelection:
    return ReleaseSelection(dataset_id="sample", episode_indices=(0,), resources=resources)


def test_given_unfinalized_local_source_when_inventory_then_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseInputError, match="finalized"):
        LocalReleaseSource(tmp_path, finalized=False).inventory()


def test_given_localuser_when_no_contributor_configuration_then_author_and_initiator_are_localuser(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    reader.annotations = SavedResource({"annotations": [{"annotator_id": "localuser"}]}, "annotation-v1")
    context = FreezeContext(principal_scope_id="localuser", source=reader.source)
    selection = _selection(SavedSelection(kind="annotation", episode_index=0, expected_revision="annotation-v1"))

    frozen = asyncio.run(freeze_release_inputs(local_source, reader, selection, context))

    assert '"annotator_id":"localuser"' in frozen.content.saved_resources[0].value_json
    assert {(item.contributor_label, item.role) for item in frozen.content.contributors} == {
        ("localuser", "author"),
        ("localuser", "initiator"),
    }


@pytest.mark.parametrize("field", ["author_id", "annotator_id", "acceptances"])
def test_given_unmapped_author_when_freezing_then_assigns_portable_aliases(
    local_source: LocalReleaseSource, field: str
) -> None:
    reader = SavedReader()
    value = {"contribution": ["unmapped-author"]} if field == "acceptances" else "unmapped-author"
    reader.annotations = SavedResource({"annotations": [{field: value}]}, "annotation-v1")
    selection = _selection(SavedSelection(kind="annotation", episode_index=0, expected_revision="annotation-v1"))

    frozen = asyncio.run(freeze_release_inputs(local_source, reader, selection, _context()))
    repeated = asyncio.run(freeze_release_inputs(local_source, reader, selection, _context()))

    assert b"unmapped-author" not in serialize_model(frozen.content)
    assert b"private-" not in serialize_model(frozen.content)
    assert "contributor-" in frozen.content.saved_resources[0].value_json
    assert serialize_model(frozen.content) == serialize_model(repeated.content)
    assert reader.annotations.value == {"annotations": [{field: value}]}


def test_given_distinct_saved_authors_when_freezing_then_preserves_distinct_attribution(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    reader.annotations = SavedResource(
        {"annotations": [{"annotator_id": "first-private", "author_id": "second-private"}]}, "annotation-v1"
    )
    selection = _selection(SavedSelection(kind="annotation", episode_index=0, expected_revision="annotation-v1"))

    frozen = asyncio.run(freeze_release_inputs(local_source, reader, selection, _context()))

    projected = json.loads(frozen.content.saved_resources[0].value_json)["annotations"][0]
    assert projected["annotator_id"] != projected["author_id"]
    assert "first-private" not in serialize_model(frozen.content).decode()
    assert "second-private" not in serialize_model(frozen.content).decode()


def test_given_no_judge_or_instruction_when_freeze_then_saved_selection_succeeds(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()

    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))

    assert frozen.content.selected_episode_ids == ("sample/episode_000000",)
    assert frozen.content.source_objects
    assert reader.reads == []
    assert b"private-" not in serialize_model(frozen.content)


def test_given_explicit_saved_media_edit_when_freeze_then_values_retained_without_judge_gate(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="edit-v1"))

    frozen = asyncio.run(freeze_release_inputs(local_source, reader, selection, _context()))
    reader.edit.value["operations"]["removedFrames"].append(2)

    assert '"removedFrames":[1]' in frozen.content.saved_resources[0].value_json
    assert "private-human" not in frozen.content.saved_resources[0].value_json


def test_given_same_stat_changed_bytes_when_revalidate_then_stale(local_source: LocalReleaseSource) -> None:
    reader = SavedReader()
    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))
    path = local_source.root / "data/frames.jsonl"
    before = path.stat()
    path.write_bytes(b'{"frame_index":1}\n')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(ReleaseInputError, match="changed"):
        asyncio.run(revalidate_release_inputs(frozen, local_source, reader, _selection(), _context()))


@pytest.mark.parametrize("mutation", ["add", "remove", "replace", "symlink"])
def test_given_source_inventory_changed_when_revalidate_then_stale(
    local_source: LocalReleaseSource, mutation: str
) -> None:
    reader = SavedReader()
    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))
    path = local_source.root / "data/frames.jsonl"
    if mutation == "add":
        (local_source.root / "new.bin").write_bytes(b"new")
    elif mutation == "remove":
        path.unlink()
    elif mutation == "replace":
        other = path.with_suffix(".replacement")
        other.write_bytes(path.read_bytes())
        other.replace(path)
    else:
        path.rename(path.with_suffix(".original"))
        path.symlink_to(path.with_suffix(".original"))

    with pytest.raises(ReleaseInputError):
        asyncio.run(revalidate_release_inputs(frozen, local_source, reader, _selection(), _context()))


def test_given_stale_saved_revision_when_freeze_then_refused(local_source: LocalReleaseSource) -> None:
    selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="old"))

    with pytest.raises(ReleaseInputError, match="revision"):
        asyncio.run(freeze_release_inputs(local_source, SavedReader(), selection, _context()))


def test_given_changed_principal_context_when_revalidate_then_refused(local_source: LocalReleaseSource) -> None:
    reader = SavedReader()
    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))
    other = FreezeContext(principal_scope_id="another", source=_context().source)

    with pytest.raises(ReleaseInputError, match="context"):
        asyncio.run(revalidate_release_inputs(frozen, local_source, reader, _selection(), other))


def test_given_source_revision_changed_when_freeze_then_refused(local_source: LocalReleaseSource) -> None:
    reader = SavedReader()
    reader.source = ("private-source", "new-revision")

    with pytest.raises(ReleaseInputError, match="context"):
        asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))


def test_given_saved_values_changed_without_revision_change_when_freeze_then_refused(
    local_source: LocalReleaseSource,
) -> None:
    class ChangingReader(SavedReader):
        async def edits(
            self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
        ) -> SavedResource:
            result = await super().edits(dataset_id, episode_index, principal_scope_id, source)
            return SavedResource({"operations": {"removedFrames": [len(self.reads)]}}, result.etag)

    selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="edit-v1"))

    with pytest.raises(ReleaseInputError, match="changed"):
        asyncio.run(freeze_release_inputs(local_source, ChangingReader(), selection, _context()))


def test_given_machine_ledger_when_freeze_then_preserves_origin_revision(local_source: LocalReleaseSource) -> None:
    class LedgerReader(SavedReader):
        async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
            return SavedResource(
                {
                    "contributions": [
                        {
                            "id": "finding-1",
                            "field": "labels/failure",
                            "origin": "machine",
                            "value": True,
                            "sequence": 0,
                            "timestamp": "2026-10-29T00:00:00Z",
                            "machine": {
                                "run_id": "run-1",
                                "result_id": "result-1",
                                "run_order": 0,
                                "source_revision": "bound-source-content",
                                "input_revision": "input-1",
                                "config_revision": "config-1",
                            },
                        }
                    ],
                },
                "ledger-v1",
            )

    selection = _selection(SavedSelection(kind="ledger", episode_index=0, expected_revision="ledger-v1"))
    frozen = asyncio.run(freeze_release_inputs(local_source, LedgerReader(), selection, _context()))

    assert '"source_revision":"bound-source-content"' in frozen.content.saved_resources[0].value_json
    assert frozen.content.ledger_contribution_ids == ("finding-1",)


def test_given_labels_for_other_episodes_when_freeze_then_only_selected_episode_retained(
    local_source: LocalReleaseSource,
) -> None:
    class LabelsReader(SavedReader):
        async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
            return SavedResource(
                {
                    "dataset_id": "sample",
                    "available_labels": ["success", "failure"],
                    "episodes": {"0": ["success"], "1": ["failure"]},
                    "analysis": {"0": {"duration_s": 1.0}, "1": {"duration_s": 2.0}},
                    "provenance": {
                        "0": {"schema_version": "1.0.0", "contributions": []},
                        "1": {"schema_version": "1.0.0", "contributions": []},
                    },
                },
                "labels-v1",
            )

    selection = _selection(SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1"))
    frozen = asyncio.run(freeze_release_inputs(local_source, LabelsReader(), selection, _context()))

    value = json.loads(frozen.content.saved_resources[0].value_json)
    assert value["episodes"] == {"0": ["success"]}
    assert value["analysis"] == {"0": {"duration_s": 1.0}}
    assert set(value["provenance"]) == {"0"}
    assert value["available_labels"] == ["success", "failure"]
    assert value["dataset_id"] == "sample"


def test_given_local_labels_file_when_release_resource_then_file_and_content_etag_returned(tmp_path: Path) -> None:
    labels = {"dataset_id": "sample", "available_labels": ["success"], "episodes": {"0": ["success"]}}
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/episode_labels.json").write_bytes(json.dumps(labels).encode())
    reader = LocalSavedInputReader(tmp_path)

    resource = asyncio.run(
        reader.release_resource("sample", SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1"))
    )
    other = asyncio.run(
        reader.release_resource("sample", SavedSelection(kind="ledger", episode_index=0, expected_revision="ledger-v1"))
    )

    assert resource.value == labels
    assert resource.etag is not None and resource.etag.startswith('"')
    assert other == SavedResource(None, None)


def test_given_local_labels_for_other_dataset_when_release_resource_then_identity_refused(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/episode_labels.json").write_bytes(json.dumps({"dataset_id": "other"}).encode())

    with pytest.raises(SavedInputError, match="identity mismatch"):
        asyncio.run(
            LocalSavedInputReader(tmp_path).release_resource(
                "sample", SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1")
            )
        )


def test_given_local_labels_provenance_when_release_ledger_then_episode_ledger_and_content_etag_returned(
    tmp_path: Path,
) -> None:
    ledger = {"schema_version": "1.0.0", "contributions": []}
    labels = {"dataset_id": "sample", "available_labels": ["success"], "episodes": {}, "provenance": {"0": ledger}}
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/episode_labels.json").write_bytes(json.dumps(labels).encode())
    reader = LocalSavedInputReader(tmp_path)

    resource = asyncio.run(
        reader.release_resource("sample", SavedSelection(kind="ledger", episode_index=0, expected_revision="ledger-v1"))
    )
    absent = asyncio.run(
        reader.release_resource("sample", SavedSelection(kind="ledger", episode_index=1, expected_revision="ledger-v1"))
    )

    assert resource.value == ledger
    assert resource.etag is not None and resource.etag.startswith('"')
    assert absent == SavedResource(None, None)


class _BoundJudgeAuthority:
    def __init__(self, bound: dict[str, object] | None) -> None:
        self.bound = bound

    async def evidence(self, dataset_id: str, episode_index: int) -> dict[str, object] | None:
        return self.bound


def _bound_judge_evidence(episode_index: int = 0) -> dict[str, object]:
    job = {
        "id": "run-1",
        "dataset_id": "sample",
        "config": {"execution": {"backend": "echo"}, "prompt_version": "test-1"},
    }
    target = {
        "input_key": "input-1",
        "episode_index": episode_index,
        "input": {"snapshot_id": "input-1", "instruction": "saved task"},
    }
    payload = {
        "episode_id": f"sample/episode_{episode_index:06d}",
        "instruction": "saved task",
        "judge_model": "echo",
        "prompt_version": "test-1",
        "n_frames": 2,
        "outcome_success": False,
        "outcome_confidence": 0.9,
        "outcome_n_valid_votes": 1,
        "progress_per_frame": [0, 0],
        "voc": 0.0,
    }
    record = bind_job_evidence(job, target, payload)
    target.update({"result": record.payload, "evidence": record.model_dump(mode="json")})
    return {"job": job, "target": target}


def test_given_unbound_judge_authority_when_release_evidence_then_refused(tmp_path: Path) -> None:
    with pytest.raises(SavedInputError, match="not bound"):
        asyncio.run(
            LocalSavedInputReader(tmp_path).release_resource(
                "sample", SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1")
            )
        )


def test_given_bound_judge_evidence_for_other_episode_when_release_evidence_then_scope_refused(tmp_path: Path) -> None:
    reader = LocalSavedInputReader(
        tmp_path, judge_evidence=_BoundJudgeAuthority(_bound_judge_evidence(episode_index=2))
    )

    with pytest.raises(SavedInputError, match="scope mismatch"):
        asyncio.run(
            reader.release_resource(
                "sample", SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1")
            )
        )


def test_given_bound_judge_evidence_when_release_evidence_then_validated_pair_and_content_etag_returned(
    tmp_path: Path,
) -> None:
    bound = _bound_judge_evidence()
    reader = LocalSavedInputReader(tmp_path, judge_evidence=_BoundJudgeAuthority(bound))

    resource = asyncio.run(
        reader.release_resource(
            "sample", SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1")
        )
    )

    assert resource.value == bound
    assert resource.etag is not None and resource.etag.startswith('"')


def test_given_malformed_labels_when_freeze_then_refused_before_acceptance(local_source: LocalReleaseSource) -> None:
    class MalformedLabelsReader(SavedReader):
        async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
            return SavedResource({"episodes": {"0": ["success"]}}, "labels-v1")

    selection = _selection(SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1"))

    with pytest.raises(ReleaseInputError, match="invalid portable evidence"):
        asyncio.run(freeze_release_inputs(local_source, MalformedLabelsReader(), selection, _context()))


def test_given_no_recording_receipt_when_freeze_then_origin_explicitly_unknown(
    local_source: LocalReleaseSource,
) -> None:
    frozen = asyncio.run(freeze_release_inputs(local_source, SavedReader(), _selection(), _context()))

    assert [(claim.episode_id, claim.origin) for claim in frozen.content.recording_claims] == [
        ("sample/episode_000000", "unknown")
    ]


def test_given_symlink_ancestor_when_inventory_then_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    root = real / "source"
    root.mkdir(parents=True)
    (root / "payload.bin").write_bytes(b"data")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ReleaseInputError):
        LocalReleaseSource(alias / "source", finalized=True).inventory()


def test_given_adverse_bound_evidence_when_explicitly_selected_then_retained_without_gating(
    local_source: LocalReleaseSource,
) -> None:
    job = {
        "id": "run-1",
        "dataset_id": "sample",
        "config": {"execution": {"backend": "echo"}, "prompt_version": "test-1"},
    }
    target = {
        "input_key": "input-1",
        "episode_index": 0,
        "input": {"snapshot_id": "input-1", "instruction": "saved task"},
    }
    payload = {
        "episode_id": "sample/episode_000000",
        "instruction": "saved task",
        "judge_model": "echo",
        "prompt_version": "test-1",
        "n_frames": 2,
        "outcome_success": False,
        "outcome_confidence": 0.9,
        "outcome_n_valid_votes": 1,
        "progress_per_frame": [0, 0],
        "voc": 0.0,
    }
    record = bind_job_evidence(job, target, payload)
    target.update({"result": record.payload, "evidence": record.model_dump(mode="json")})

    class EvidenceReader(SavedReader):
        async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
            return SavedResource({"job": job, "target": target}, "evidence-v1")

    selection = _selection(SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1"))
    frozen = asyncio.run(freeze_release_inputs(local_source, EvidenceReader(), selection, _context()))

    assert '"outcome_success":false' in frozen.content.saved_resources[0].value_json
    assert len(frozen.content.evidence_references) == 1
    assert '"job"' not in frozen.content.saved_resources[0].value_json


def test_given_receipt_and_import_mapping_when_selected_then_claims_preserved_and_preview_read_only(
    local_source: LocalReleaseSource,
) -> None:
    class ReceiptReader(SavedReader):
        async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
            value = (
                {"claimed_timestamp": "2020-01-01T00:00:00Z"}
                if selection.kind == "recording_receipt"
                else {"original_episode_id": "imported-9", "imported_timestamp": "2021-01-01T00:00:00Z"}
            )
            return SavedResource(value, "v1")

    selection = _selection(
        SavedSelection(kind="recording_receipt", episode_index=0, expected_revision="v1"),
        SavedSelection(kind="import_mapping", episode_index=0, expected_revision="v1"),
    )
    before = {
        path.relative_to(local_source.root): path.read_bytes()
        for path in local_source.root.rglob("*")
        if path.is_file()
    }
    frozen = asyncio.run(freeze_release_inputs(local_source, ReceiptReader(), selection, _context()))
    after = {
        path.relative_to(local_source.root): path.read_bytes()
        for path in local_source.root.rglob("*")
        if path.is_file()
    }

    assert before == after
    assert frozen.content.recording_claims[0].origin == "supplied_receipt"
    assert "imported_timestamp" in frozen.content.saved_resources[1].value_json
    assert "observed" not in serialize_model(frozen.content).decode()


def test_given_deselected_edit_changed_when_revalidate_then_ignored(local_source: LocalReleaseSource) -> None:
    reader = SavedReader()
    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))
    reader.edit = SavedResource({"operations": {"removedFrames": [7]}}, "edit-v2")

    assert asyncio.run(revalidate_release_inputs(frozen, local_source, reader, _selection(), _context())) is frozen
    assert not frozen.content.saved_resources


def test_given_unselected_configured_author_when_freeze_then_not_exported(local_source: LocalReleaseSource) -> None:
    frozen = asyncio.run(freeze_release_inputs(local_source, SavedReader(), _selection(), _context()))

    assert [(entry.contributor_label, entry.role) for entry in frozen.content.contributors] == [
        ("automation-a", "initiator")
    ]
    assert frozen.content.source_admission == "finalized_quiescent"


def test_given_unsupported_saved_kind_when_selection_created_then_refused() -> None:
    with pytest.raises(ReleaseInputError):
        SavedSelection(kind="draft", episode_index=0, expected_revision="1")


def test_given_blob_source_when_inventory_then_downloads_are_revision_conditioned() -> None:
    pytest.importorskip("azure.core")
    calls = []

    class Blob:
        def download_blob(self, **kwargs: object) -> Blob:
            calls.append(kwargs)
            return self

        def chunks(self) -> list[bytes]:
            return [b"payload"]

    class Container:
        def list_blobs(self, **kwargs: object) -> list[SimpleNamespace]:
            assert kwargs["name_starts_with"] == "source/"
            return [SimpleNamespace(name="source/data.bin", size=7, etag='"revision-1"', version_id="version-1")]

        def get_blob_client(self, name: str, **kwargs: object) -> Blob:
            assert name == "source/data.bin"
            assert kwargs["version_id"] == "version-1"
            return Blob()

    inventory = BlobReleaseSource(Container(), prefix="source").inventory()

    assert inventory[0].path == "data.bin"
    assert calls[0]["etag"] == '"revision-1"'
    assert calls[0]["match_condition"].name == "IfNotModified"


def test_given_blob_revision_changes_during_read_when_inventory_then_refused() -> None:
    pytest.importorskip("azure.core")

    class Blob:
        def download_blob(self, **kwargs: object) -> Blob:
            return self

        def chunks(self) -> list[bytes]:
            return [b"payload"]

    class Container:
        listings = 0

        def list_blobs(self, **kwargs: object) -> list[SimpleNamespace]:
            self.listings += 1
            return [
                SimpleNamespace(name="source/data.bin", size=7, etag=f'"revision-{self.listings}"', version_id=None)
            ]

        def get_blob_client(self, name: str, **kwargs: object) -> Blob:
            return Blob()

    with pytest.raises(ReleaseInputError, match="changed"):
        BlobReleaseSource(Container(), prefix="source").inventory()


def test_given_listing_without_integer_length_when_inventory_then_refused() -> None:
    pytest.importorskip("azure.core")

    class Container:
        def list_blobs(self, **kwargs: object) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="source/data.bin", size="7", etag='"revision-1"', version_id=None)]

    with pytest.raises(ReleaseInputError, match="lacks"):
        BlobReleaseSource(Container(), prefix="source").inventory()


class FakeDownload:
    def __init__(self, store: FakeBlobStore, data: bytes) -> None:
        self._store = store
        self._data = data

    def chunks(self) -> Iterator[bytes]:
        from azure.core.exceptions import HttpResponseError

        if self._store.fail_stream:
            raise HttpResponseError("stream failed")
        data = self._data
        if self._store.serve_transform is not None:
            data = self._store.serve_transform(data)
        yield data


class FakeBlob:
    def __init__(self, store: FakeBlobStore, name: str, version_id: str | None) -> None:
        self._store = store
        self._name = name
        self._version_id = version_id

    def download_blob(
        self, *, etag: str | None = None, match_condition: object = None, max_concurrency: int = 1
    ) -> FakeDownload:
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError

        store = self._store
        store.calls.append(
            {"name": self._name, "version_id": self._version_id, "etag": etag, "match_condition": match_condition}
        )
        version = self._version_id or store.current.get(self._name)
        if version is None or (self._name, version) not in store.versions:
            raise ResourceNotFoundError("blob version not found")
        data, current_etag = store.versions[(self._name, version)]
        if etag is not None and etag != current_etag:
            raise ResourceModifiedError("ETag condition failed")
        return FakeDownload(store, data)


class FakeBlobStore:
    """In-memory container with optional versioning and ETag-conditioned downloads."""

    def __init__(self, *, versioned: bool = True) -> None:
        self.versioned = versioned
        self.versions: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.current: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []
        self.serve_transform: Callable[[bytes], bytes] | None = None
        self.fail_stream = False
        self._counter = 0

    def put(self, name: str, data: bytes) -> str:
        self._counter += 1
        version = f"version-{self._counter}"
        self.versions[(name, version)] = (data, f'"etag-{version}"')
        self.current[name] = version
        return version

    def list_blobs(self, *, name_starts_with: str) -> list[SimpleNamespace]:
        listing = []
        for name, version in sorted(self.current.items()):
            if not name.startswith(name_starts_with):
                continue
            data, etag = self.versions[(name, version)]
            listing.append(
                SimpleNamespace(
                    name=name,
                    size=len(data),
                    etag=etag,
                    version_id=version if self.versioned else None,
                )
            )
        return listing

    def get_blob_client(self, name: str, *, version_id: str | None = None) -> FakeBlob:
        return FakeBlob(self, name, version_id)


@pytest.fixture
def blob_store() -> FakeBlobStore:
    pytest.importorskip("azure.core")
    return FakeBlobStore()


def test_given_frozen_blob_when_binding_then_current_version_and_etag_returned(blob_store: FakeBlobStore) -> None:
    version = blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    binding = source.binding(frozen)

    assert (binding.name, binding.version_id) == ("source/data.bin", version)
    assert binding.etag == blob_store.versions[("source/data.bin", version)][1]


def test_given_overwritten_blob_when_binding_then_refused_before_download(blob_store: FakeBlobStore) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.calls.clear()
    blob_store.put("source/data.bin", b"abcdefghij")

    with pytest.raises(ReleaseInputError, match="revision"):
        source.binding(frozen)
    assert blob_store.calls == []


def test_given_missing_or_resized_blob_when_binding_then_refused(blob_store: FakeBlobStore) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    with pytest.raises(ReleaseInputError, match="length"):
        source.binding(frozen.model_copy(update={"length": 99}))
    with pytest.raises(ReleaseInputError, match="missing"):
        source.binding(frozen.model_copy(update={"path": "absent.bin"}))


def test_given_pinned_blob_when_iter_bytes_then_rechunked_and_conditioned(blob_store: FakeBlobStore) -> None:
    version = blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.calls.clear()

    chunks = list(source.iter_bytes(frozen, chunk_size=4))

    assert b"".join(chunks) == b"0123456789"
    assert len(chunks) == 3
    assert max(len(chunk) for chunk in chunks) <= 4
    assert blob_store.calls[0]["version_id"] == version
    assert blob_store.calls[0]["etag"] == blob_store.versions[("source/data.bin", version)][1]


def test_given_same_length_content_change_when_iter_bytes_then_digest_mismatch_refused(
    blob_store: FakeBlobStore,
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.serve_transform = lambda data: data[:-1] + b"X"

    with pytest.raises(ReleaseInputError, match="content changed"):
        list(source.iter_bytes(frozen, chunk_size=4))


@pytest.mark.parametrize(
    "transform",
    [lambda data: data + b"extra", lambda data: data[:-2]],
    ids=["growth", "truncation"],
)
def test_given_blob_length_differs_from_frozen_when_iter_bytes_then_refused(
    blob_store: FakeBlobStore, transform: Callable[[bytes], bytes]
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.serve_transform = transform

    with pytest.raises(ReleaseInputError, match="length"):
        list(source.iter_bytes(frozen, chunk_size=4))


def test_given_stream_failure_when_iter_bytes_then_mapped_to_release_input_error(blob_store: FakeBlobStore) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.fail_stream = True

    with pytest.raises(ReleaseInputError, match="unavailable"):
        list(source.iter_bytes(frozen))


def test_given_unversioned_blob_overwritten_before_first_read_when_iter_bytes_then_refused() -> None:
    store = FakeBlobStore(versioned=False)
    store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(store, prefix="source")
    frozen = source.inventory()[0]
    stream = source.iter_bytes(frozen, chunk_size=4)
    store.put("source/data.bin", b"abcdefghij")

    with pytest.raises(ReleaseInputError, match="unavailable"):
        next(stream)


def test_given_non_positive_chunk_size_when_iter_bytes_then_refused(blob_store: FakeBlobStore) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    with pytest.raises(ReleaseInputError, match="chunk_size"):
        source.iter_bytes(frozen, chunk_size=0)
