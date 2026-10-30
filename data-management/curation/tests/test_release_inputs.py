"""Read-only release input freezing and finalized local source admission."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
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
from physical_ai_curation.saved_input import SavedResource


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
