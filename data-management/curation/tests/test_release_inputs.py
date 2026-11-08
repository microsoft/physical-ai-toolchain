"""Read-only release input freezing and finalized local source admission."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
from physical_ai_curation.evidence import bind_job_evidence
from physical_ai_curation.release import inputs as inputs_module
from physical_ai_curation.release.canonical import serialize_model, sha256_hex
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    LocalReleaseSource,
    ReleaseInputError,
    ReleaseSelection,
    SavedSelection,
    _settled_inventory,
    freeze_release_inputs,
    revalidate_release_inputs,
)
from physical_ai_curation.release.jobs import CancellationError
from physical_ai_curation.release.models import RevisionSourceObject, SourceObject
from physical_ai_curation.saved_input import SavedInputError, SavedInputSnapshot, SavedResource


class SavedReader:
    def __init__(self) -> None:
        self.source = ("private-source", "source-revision")
        self.annotations = SavedResource({"annotations": []}, "annotation-v1")
        self.edit = SavedResource(
            {"operations": {"removedFrames": [1]}, "author_id": "private-human"},
            "edit-v1",
        )
        self.reads = []

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return self.source

    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
        self.reads.append("annotation")
        return self.annotations

    async def edits(
        self,
        dataset_id: str,
        episode_index: int,
        principal_scope_id: str,
        source: tuple[str, str],
    ) -> SavedResource:
        self.reads.append("edits")
        return self.edit


def _validation_snapshot() -> SavedInputSnapshot:
    return SavedInputSnapshot(
        snapshot_id="saved-snapshot",
        dataset_id="sample",
        episode_index=0,
        principal_scope_id="principal",
        source_id="source",
        source_revision="source-revision",
        annotation_author_id="human",
        annotation_revision="annotation-v1",
        edit_revision=None,
        instruction="Move the cup",
        instruction_origin="annotation",
    )


@pytest.mark.parametrize(
    "damage",
    [
        "dataset",
        "episode",
        "revision",
        "missing_author",
        "duplicate_author",
        "instruction",
        "outcome",
        "second_revision",
        "valid",
    ],
)
async def test_given_saved_annotation_baseline_when_resolved_then_current_single_authority_is_required(
    damage: str, mocker: Any
) -> None:
    from physical_ai_curation.saved_input import resolve_validation_sample

    snapshot = _validation_snapshot()
    reader = SavedReader()
    entry = {
        "annotator_id": "human",
        "language_instruction": {"instruction": snapshot.instruction},
        "task_completeness": {"rating": "success"},
    }
    value = {"dataset_id": snapshot.dataset_id, "episode_index": snapshot.episode_index, "annotations": [entry]}
    if damage == "dataset":
        value["dataset_id"] = "different"
    elif damage == "episode":
        value["episode_index"] = 1
    elif damage == "missing_author":
        entry["annotator_id"] = "different"
    elif damage == "duplicate_author":
        value["annotations"].append(dict(entry))
    elif damage == "instruction":
        entry["language_instruction"]["instruction"] = "different"
    elif damage == "outcome":
        entry["task_completeness"]["rating"] = "unknown"
    reader.annotations = SavedResource(value, "different" if damage == "revision" else snapshot.annotation_revision)
    if damage == "second_revision":
        mocker.patch.object(
            reader,
            "annotation",
            new=mocker.AsyncMock(side_effect=[reader.annotations, replace(reader.annotations, etag="changed")]),
        )
    reference = {
        "snapshot_id": snapshot.snapshot_id,
        "annotation_author_id": snapshot.annotation_author_id,
        "annotation_revision": snapshot.annotation_revision,
    }

    if damage == "valid":
        sample = await resolve_validation_sample(reader, snapshot, reference)
        assert sample["human_outcome"] == "success"
        assert sample["input"] == snapshot.model_dump(mode="json")
        assert sample["reference"] == reference
        assert reader.reads == ["annotation", "annotation"]
    else:
        with pytest.raises(SavedInputError):
            await resolve_validation_sample(reader, snapshot, reference)


@pytest.mark.parametrize("damage", ["absent", "foreign_dataset", "ambiguous", "changed_revision", "valid"])
async def test_given_saved_label_baseline_when_validation_sample_resolved_then_exact_outcome_revision_is_required(
    damage: str,
) -> None:
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.saved_input import label_baseline, resolve_validation_sample

    snapshot = _validation_snapshot()
    reader = SavedReader()
    labels = DatasetLabelsFile(
        dataset_id="different" if damage == "foreign_dataset" else snapshot.dataset_id,
        episodes={"0": ["SUCCESS", "FAILURE"] if damage == "ambiguous" else ["SUCCESS"]},
    )
    baseline = label_baseline(labels, 0)
    reference = {
        "snapshot_id": snapshot.snapshot_id,
        "labels_revision": baseline["revision"] if baseline and damage != "changed_revision" else "changed",
    }

    if damage == "valid":
        sample = await resolve_validation_sample(reader, snapshot, reference, labels=labels)
        assert sample["baseline_source"] == "label"
        assert sample["human_outcome"] == "success"
        assert sample["reference"] == reference
    else:
        with pytest.raises(SavedInputError):
            await resolve_validation_sample(reader, snapshot, reference, labels=None if damage == "absent" else labels)

    assert reader.reads == []


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
        contributor_labels=(
            ("private-human", "curator-a"),
            ("private-machine", "automation-a"),
        ),
    )


def _selection(*resources: SavedSelection) -> ReleaseSelection:
    return ReleaseSelection(dataset_id="sample", episode_indices=(0,), resources=resources)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "unsupported"),
        ("episode_index", -1),
        ("episode_index", True),
        ("expected_revision", ""),
        ("annotation_author_id", ""),
        ("annotation_author_id", "not-for-edits"),
    ],
)
def test_given_invalid_saved_selection_when_constructed_then_refused(field: str, value: object) -> None:
    selection = SavedSelection("edits", 0, "r1")

    with pytest.raises(ReleaseInputError):
        replace(selection, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_id", ""),
        ("episode_indices", ()),
        ("episode_indices", (True,)),
        ("episode_indices", (-1,)),
        ("episode_indices", (0, 0)),
        ("resources", (SavedSelection("edits", 1, "r1"),)),
        ("resources", (SavedSelection("edits", 0, "r1"), SavedSelection("edits", 0, "r1"))),
    ],
)
def test_given_invalid_release_selection_when_constructed_then_refused(field: str, value: object) -> None:
    with pytest.raises(ReleaseInputError):
        replace(_selection(), **{field: value})


@pytest.mark.parametrize(
    "bindings",
    [(("first", "one"), ("first", "two")), (("first", "one"), ("second", "one")), (("", "one"),), (("first", ""),)],
)
def test_given_invalid_contributor_bindings_when_context_created_then_refused(
    bindings: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(ReleaseInputError):
        replace(_context(), contributor_labels=bindings)


@pytest.mark.parametrize("field", ["principal_scope_id", "source"])
def test_given_missing_private_context_when_frozen_then_refused_before_saved_reads(
    local_source: LocalReleaseSource, field: str
) -> None:
    reader = SavedReader()
    context = replace(_context(), **{field: "" if field == "principal_scope_id" else ("", "")})

    with pytest.raises(ReleaseInputError, match="context is required"):
        asyncio.run(freeze_release_inputs(local_source, reader, _selection(), context))

    assert reader.reads == []


def test_given_unsupported_extended_resource_when_frozen_then_refused(local_source: LocalReleaseSource) -> None:
    selection = _selection(SavedSelection("labels", 0, "labels-v1"))

    with pytest.raises(ReleaseInputError, match="not supported by this reader"):
        asyncio.run(freeze_release_inputs(local_source, SavedReader(), selection, _context()))


def test_given_contributor_alias_collision_when_frozen_then_distinct_portable_alias_is_allocated(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    reader.annotations = SavedResource({"annotations": [{"author_id": "third-private"}]}, "annotation-v1")
    context = FreezeContext(
        "first-private", reader.source, (("first-private", "contributor-3"), ("second-private", "contributor-1"))
    )
    selection = _selection(SavedSelection("annotation", 0, "annotation-v1"))

    frozen = asyncio.run(freeze_release_inputs(local_source, reader, selection, context))

    assert "contributor-4" in frozen.content.saved_resources[0].value_json
    assert "third-private" not in frozen.content.saved_resources[0].value_json


def test_given_unfinalized_local_source_when_inventory_then_refused(
    tmp_path: Path,
) -> None:
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
        {"annotations": [{"annotator_id": "first-private", "author_id": "second-private"}]},
        "annotation-v1",
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


def test_given_same_stat_changed_bytes_when_revalidate_then_stale(
    local_source: LocalReleaseSource,
) -> None:
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


def test_given_stale_saved_revision_when_freeze_then_refused(
    local_source: LocalReleaseSource,
) -> None:
    selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="old"))

    with pytest.raises(ReleaseInputError, match="revision"):
        asyncio.run(freeze_release_inputs(local_source, SavedReader(), selection, _context()))


def test_given_changed_principal_context_when_revalidate_then_refused(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))
    other = FreezeContext(principal_scope_id="another", source=_context().source)

    with pytest.raises(ReleaseInputError, match="context"):
        asyncio.run(revalidate_release_inputs(frozen, local_source, reader, _selection(), other))


def test_given_source_revision_changed_when_freeze_then_refused(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    reader.source = ("private-source", "new-revision")

    with pytest.raises(ReleaseInputError, match="context"):
        asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))


def test_given_saved_values_changed_without_revision_change_when_freeze_then_refused(
    local_source: LocalReleaseSource,
) -> None:
    class ChangingReader(SavedReader):
        async def edits(
            self,
            dataset_id: str,
            episode_index: int,
            principal_scope_id: str,
            source: tuple[str, str],
        ) -> SavedResource:
            result = await super().edits(dataset_id, episode_index, principal_scope_id, source)
            return SavedResource({"operations": {"removedFrames": [len(self.reads)]}}, result.etag)

    selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="edit-v1"))

    with pytest.raises(ReleaseInputError, match="changed"):
        asyncio.run(freeze_release_inputs(local_source, ChangingReader(), selection, _context()))


def test_given_machine_ledger_when_freeze_then_preserves_origin_revision(
    local_source: LocalReleaseSource,
) -> None:
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


def test_given_local_labels_file_when_release_resource_then_file_and_content_etag_returned(
    tmp_path: Path,
) -> None:
    labels = {
        "dataset_id": "sample",
        "available_labels": ["success"],
        "episodes": {"0": ["success"]},
    }
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/episode_labels.json").write_bytes(json.dumps(labels).encode())
    reader = LocalSavedInputReader(tmp_path)

    resource = asyncio.run(
        reader.release_resource(
            "sample",
            SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1"),
        )
    )
    other = asyncio.run(
        reader.release_resource(
            "sample",
            SavedSelection(kind="ledger", episode_index=0, expected_revision="ledger-v1"),
        )
    )

    assert resource.value == labels
    assert resource.etag is not None and resource.etag.startswith('"')
    assert other == SavedResource(None, None)


def test_given_local_labels_for_other_dataset_when_release_resource_then_identity_refused(
    tmp_path: Path,
) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/episode_labels.json").write_bytes(json.dumps({"dataset_id": "other"}).encode())

    with pytest.raises(SavedInputError, match="identity mismatch"):
        asyncio.run(
            LocalSavedInputReader(tmp_path).release_resource(
                "sample",
                SavedSelection(kind="labels", episode_index=0, expected_revision="labels-v1"),
            )
        )


def test_given_local_labels_provenance_when_release_ledger_then_episode_ledger_and_content_etag_returned(
    tmp_path: Path,
) -> None:
    ledger = {"schema_version": "1.0.0", "contributions": []}
    labels = {
        "dataset_id": "sample",
        "available_labels": ["success"],
        "episodes": {},
        "provenance": {"0": ledger},
    }
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta/episode_labels.json").write_bytes(json.dumps(labels).encode())
    reader = LocalSavedInputReader(tmp_path)

    resource = asyncio.run(
        reader.release_resource(
            "sample",
            SavedSelection(kind="ledger", episode_index=0, expected_revision="ledger-v1"),
        )
    )
    absent = asyncio.run(
        reader.release_resource(
            "sample",
            SavedSelection(kind="ledger", episode_index=1, expected_revision="ledger-v1"),
        )
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


def test_given_unbound_judge_authority_when_release_evidence_then_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(SavedInputError, match="not bound"):
        asyncio.run(
            LocalSavedInputReader(tmp_path).release_resource(
                "sample",
                SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1"),
            )
        )


def test_given_bound_judge_evidence_for_other_episode_when_release_evidence_then_scope_refused(
    tmp_path: Path,
) -> None:
    reader = LocalSavedInputReader(
        tmp_path,
        judge_evidence=_BoundJudgeAuthority(_bound_judge_evidence(episode_index=2)),
    )

    with pytest.raises(SavedInputError, match="scope mismatch"):
        asyncio.run(
            reader.release_resource(
                "sample",
                SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1"),
            )
        )


def test_given_bound_judge_evidence_when_release_evidence_then_validated_pair_and_content_etag_returned(
    tmp_path: Path,
) -> None:
    bound = _bound_judge_evidence()
    reader = LocalSavedInputReader(tmp_path, judge_evidence=_BoundJudgeAuthority(bound))

    resource = asyncio.run(
        reader.release_resource(
            "sample",
            SavedSelection(kind="evidence", episode_index=0, expected_revision="evidence-v1"),
        )
    )

    assert resource.value == bound
    assert resource.etag is not None and resource.etag.startswith('"')


def test_given_malformed_labels_when_freeze_then_refused_before_acceptance(
    local_source: LocalReleaseSource,
) -> None:
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
                else {
                    "original_episode_id": "imported-9",
                    "imported_timestamp": "2021-01-01T00:00:00Z",
                }
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


def test_given_deselected_edit_changed_when_revalidate_then_ignored(
    local_source: LocalReleaseSource,
) -> None:
    reader = SavedReader()
    frozen = asyncio.run(freeze_release_inputs(local_source, reader, _selection(), _context()))
    reader.edit = SavedResource({"operations": {"removedFrames": [7]}}, "edit-v2")

    assert asyncio.run(revalidate_release_inputs(frozen, local_source, reader, _selection(), _context())) is frozen
    assert not frozen.content.saved_resources


def test_given_unselected_configured_author_when_freeze_then_not_exported(
    local_source: LocalReleaseSource,
) -> None:
    frozen = asyncio.run(freeze_release_inputs(local_source, SavedReader(), _selection(), _context()))

    assert [(entry.contributor_label, entry.role) for entry in frozen.content.contributors] == [
        ("automation-a", "initiator")
    ]
    assert frozen.content.source_admission == "finalized_quiescent"


def test_given_unsupported_saved_kind_when_selection_created_then_refused() -> None:
    with pytest.raises(ReleaseInputError):
        SavedSelection(kind="draft", episode_index=0, expected_revision="1")


def test_given_blob_source_when_inventory_then_revision_only_entries_without_downloads() -> None:
    pytest.importorskip("azure.core")
    calls = []

    class Blob:
        def download_blob(self, **kwargs: object) -> Blob:
            calls.append(kwargs)
            return self

        def chunks(self) -> list[bytes]:
            return [b"payload"]

    class Container:
        url = "https://example.blob.core.windows.net/datasets"

        def list_blobs(self, **kwargs: object) -> FakePages:
            assert kwargs["name_starts_with"] == "source/"
            return FakePages(
                [
                    [
                        SimpleNamespace(
                            name="source/data.bin",
                            size=7,
                            etag='"revision-1"',
                            version_id="version-1",
                        )
                    ]
                ]
            )

        def get_blob_client(self, name: str, **kwargs: object) -> Blob:
            assert name == "source/data.bin"
            assert kwargs["version_id"] == "version-1"
            return Blob()

    inventory = BlobReleaseSource(Container(), prefix="source").inventory()

    assert [(item.path, item.length) for item in inventory] == [("data.bin", 7)]
    assert all(isinstance(item, RevisionSourceObject) for item in inventory)
    assert calls == []


def test_given_blob_revision_changes_during_read_when_inventory_then_refused() -> None:
    pytest.importorskip("azure.core")

    class Blob:
        def download_blob(self, **kwargs: object) -> Blob:
            return self

        def chunks(self) -> list[bytes]:
            return [b"payload"]

    class Container:
        url = "https://example.blob.core.windows.net/datasets"
        listings = 0

        def list_blobs(self, **kwargs: object) -> FakePages:
            self.listings += 1
            return FakePages(
                [
                    [
                        SimpleNamespace(
                            name="source/data.bin",
                            size=7,
                            etag=f'"revision-{self.listings}"',
                            version_id=None,
                        )
                    ]
                ]
            )

        def get_blob_client(self, name: str, **kwargs: object) -> Blob:
            return Blob()

    with pytest.raises(ReleaseInputError, match="changed"):
        BlobReleaseSource(Container(), prefix="source").inventory()


def test_given_listing_without_integer_length_when_inventory_then_refused() -> None:
    pytest.importorskip("azure.core")

    class Container:
        def list_blobs(self, **kwargs: object) -> FakePages:
            return FakePages(
                [
                    [
                        SimpleNamespace(
                            name="source/data.bin",
                            size="7",
                            etag='"revision-1"',
                            version_id=None,
                        )
                    ]
                ]
            )

    with pytest.raises(ReleaseInputError, match="lacks"):
        BlobReleaseSource(Container(), prefix="source").inventory()


class FakePages:
    """Azure-style pager: `by_page()` yields page iterators and records each requested page index."""

    def __init__(self, pages: list[Any], requests: list[int] | None = None) -> None:
        self._pages = pages
        self.requests: list[int] = [] if requests is None else requests

    def __iter__(self) -> Iterator[Any]:
        return (item for page in self._pages for item in page)

    def by_page(self, continuation_token: str | None = None) -> FakePageIterator:
        return FakePageIterator(self._pages, self.requests, int(continuation_token or 0))


class FakePageIterator:
    def __init__(self, pages: list[Any], requests: list[int], start: int) -> None:
        self._pages = pages
        self._requests = requests
        self._next = start
        self.continuation_token: str | None = str(start) if start < len(pages) else None

    def __iter__(self) -> FakePageIterator:
        return self

    def __next__(self) -> Iterator[Any]:
        if self._next >= len(self._pages):
            self.continuation_token = None
            raise StopIteration
        index = self._next
        self._requests.append(index)
        self._next += 1
        self.continuation_token = str(self._next) if self._next < len(self._pages) else None
        return iter(self._pages[index])


def _meta_item(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=f"source/meta/{name}", size=2, etag=f'"etag-{name}"', version_id=None)


def _meta_container(pages: list[Any], requests: list[int]) -> object:
    class Container:
        url = "https://example.blob.core.windows.net/datasets"

        def list_blobs(self, *, name_starts_with: str, results_per_page: int | None = None) -> FakePages:
            assert name_starts_with == "source/meta/"
            assert results_per_page is not None
            return FakePages(pages, requests)

    return Container()


def test_given_empty_listing_page_when_metadata_inventory_then_continuation_pages_are_read() -> None:
    pytest.importorskip("azure.core")
    requests: list[int] = []
    pages = [[], [_meta_item("a.json")], [], [_meta_item("b.json")]]

    listed = BlobReleaseSource(_meta_container(pages, requests), prefix="source").metadata_inventory()

    assert [item.path for item in listed] == ["meta/a.json", "meta/b.json"]
    assert requests[:4] == [0, 1, 2, 3]


def test_given_count_bound_across_pages_when_metadata_inventory_then_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("azure.core")
    monkeypatch.setenv("RELEASE_SOURCE_MAX_OBJECTS", "2")
    pages = [[_meta_item("a.json")], [_meta_item("b.json")], [_meta_item("c.json")]]
    source = BlobReleaseSource(_meta_container(pages, []), prefix="source")

    with pytest.raises(ReleaseInputError, match="count"):
        source.metadata_inventory()


def test_given_page_budget_when_metadata_inventory_then_no_page_past_limit_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("azure.core")
    pages = [[_meta_item("a.json")], [_meta_item("b.json")]]
    requests: list[int] = []
    monkeypatch.setenv("RELEASE_SOURCE_LISTING_MAX_PAGES", "1")

    with pytest.raises(ReleaseInputError, match="page bound"):
        BlobReleaseSource(_meta_container(pages, requests), prefix="source").metadata_inventory()
    assert requests == [0]

    monkeypatch.setenv("RELEASE_SOURCE_LISTING_MAX_PAGES", "2")
    requests.clear()
    listed = BlobReleaseSource(_meta_container(pages, requests), prefix="source").metadata_inventory()

    assert [item.path for item in listed] == ["meta/a.json", "meta/b.json"]
    assert requests[:2] == [0, 1]


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

    def get_blob_properties(self) -> SimpleNamespace:
        from azure.core.exceptions import ResourceNotFoundError

        version = self._version_id or self._store.current.get(self._name)
        if version is None or (self._name, version) not in self._store.versions:
            raise ResourceNotFoundError("blob version not found")
        data, etag = self._store.versions[(self._name, version)]
        return SimpleNamespace(size=len(data), etag=etag, version_id=version if self._store.versioned else None)

    def download_blob(
        self,
        *,
        offset: int | None = None,
        length: int | None = None,
        etag: str | None = None,
        match_condition: object = None,
        max_concurrency: int = 1,
    ) -> FakeDownload:
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError

        store = self._store
        store.calls.append(
            {
                "name": self._name,
                "version_id": self._version_id,
                "etag": etag,
                "match_condition": match_condition,
            }
        )
        version = self._version_id or store.current.get(self._name)
        if version is None or (self._name, version) not in store.versions:
            raise ResourceNotFoundError("blob version not found")
        data, current_etag = store.versions[(self._name, version)]
        if etag is not None and etag != current_etag:
            raise ResourceModifiedError("ETag condition failed")
        if offset is not None:
            data = data[offset:] if length is None else data[offset : offset + length]
        return FakeDownload(store, data)


class FakeBlobStore:
    """In-memory container with optional versioning and ETag-conditioned downloads."""

    url = "https://example.blob.core.windows.net/datasets"

    def __init__(self, *, versioned: bool = True) -> None:
        self.versioned = versioned
        self.versions: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.current: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []
        self.serve_transform: Callable[[bytes], bytes] | None = None
        self.fail_stream = False
        self.page_requests: list[int] = []
        self._counter = 0

    def put(self, name: str, data: bytes) -> str:
        self._counter += 1
        version = f"version-{self._counter}"
        self.versions[(name, version)] = (data, f'"etag-{version}"')
        self.current[name] = version
        return version

    def list_blobs(self, *, name_starts_with: str, results_per_page: int | None = None) -> FakePages:
        listing = []
        for name, version in sorted(self.current.items()):
            if not name.startswith(name_starts_with):
                continue
            data, etag = self.versions[(name, version)]
            listing.append(
                SimpleNamespace(
                    name=name,
                    size=len(data),
                    etag=etag.removeprefix('"').removesuffix('"'),
                    version_id=version if self.versioned else None,
                )
            )
        size = results_per_page or max(len(listing), 1)
        pages = [listing[start : start + size] for start in range(0, len(listing), size)] or [[]]
        return FakePages(pages, self.page_requests)

    def get_blob_client(self, name: str, *, version_id: str | None = None) -> FakeBlob:
        return FakeBlob(self, name, version_id)


@pytest.fixture
def blob_store() -> FakeBlobStore:
    pytest.importorskip("azure.core")
    return FakeBlobStore()


@pytest.mark.parametrize("changed", [False, True])
def test_given_unquoted_listing_when_binding_quoted_properties_then_only_real_changes_fail(changed: bool) -> None:
    store = FakeBlobStore(versioned=False)
    store.put("source/meta/info.json", b"{}")
    source = BlobReleaseSource(store, prefix="source")
    (listed,) = source.metadata_inventory()
    if changed:
        store.put("source/meta/info.json", b"{}")
        with pytest.raises(ReleaseInputError, match="revision changed"):
            source.binding(listed)
        assert store.calls == []
    else:
        assert source.scoped([listed.path]).inventory() == (listed,)
        assert source.binding(listed).etag.startswith('"')
        assert b"".join(source.iter_bytes(listed)) == b"{}"
        assert store.calls[0]["etag"] == source.binding(listed).etag


def test_given_metadata_inventory_when_listed_then_only_meta_prefix_is_enumerated(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data/chunk-000.parquet", b"rows")
    blob_store.put("source/videos/cam.mp4", b"frames")
    blob_store.put("source/meta/info.json", b"{}")
    prefixes: list[str] = []
    original_list = blob_store.list_blobs

    def recording_list(*, name_starts_with: str, **kwargs: object) -> FakePages:
        prefixes.append(name_starts_with)
        return original_list(name_starts_with=name_starts_with, **kwargs)

    monkeypatch.setattr(blob_store, "list_blobs", recording_list)

    listed = BlobReleaseSource(blob_store, prefix="source").metadata_inventory()

    assert [item.path for item in listed] == ["meta/info.json"]
    assert prefixes == ["source/meta/", "source/meta/"]
    assert blob_store.calls == []


def test_given_scoped_source_when_inventory_then_exact_properties_without_listing_or_download(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data/chunk-000.parquet", b"rows")
    blob_store.put("source/data/chunk-001.parquet", b"more")
    source = BlobReleaseSource(blob_store, prefix="source")
    full = source.inventory()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("scoped inventory must not list the dataset")

    scoped = source.scoped(["data/chunk-001.parquet", "data/chunk-000.parquet"])
    monkeypatch.setattr(blob_store, "list_blobs", forbidden)

    assert scoped.inventory() == full
    assert scoped.location == source.location
    assert blob_store.calls == []


def test_given_scoped_properties_when_read_then_bounded_parallelism_preserves_order(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from threading import Barrier, Lock

    monkeypatch.setenv("RELEASE_SOURCE_READ_CONCURRENCY", "2")
    paths = [f"data/episode-{index}.parquet" for index in range(4)]
    for path in paths:
        blob_store.put("source/" + path, b"rows")
    barrier, lock = Barrier(2), Lock()
    active = peak = 0
    original = FakeBlob.get_blob_properties

    def properties(blob: FakeBlob) -> SimpleNamespace:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=2)
            return original(blob)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(FakeBlob, "get_blob_properties", properties)
    listed = BlobReleaseSource(blob_store, prefix="source").scoped(reversed(paths)).inventory()
    assert [item.path for item in listed] == paths
    assert peak == 2
    assert active == 0
    assert blob_store.calls == []


def test_given_scoped_selection_when_missing_or_changed_then_refused(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data/chunk-000.parquet", b"rows")
    source = BlobReleaseSource(blob_store, prefix="source")

    with pytest.raises(ReleaseInputError, match="missing or unavailable"):
        source.scoped(["data/absent.parquet"]).inventory()

    reads = 0
    original_get = blob_store.get_blob_client

    def get_with_concurrent_write(name: str, *, version_id: str | None = None) -> FakeBlob:
        nonlocal reads
        if name == "source/data/chunk-000.parquet":
            reads += 1
            if reads == 2:
                blob_store.put(name, b"changed rows")
        return original_get(name, version_id=version_id)

    monkeypatch.setattr(blob_store, "get_blob_client", get_with_concurrent_write)

    with pytest.raises(ReleaseInputError, match="changed during read"):
        source.scoped(["data/chunk-000.parquet"]).inventory()
    assert blob_store.calls == []


def test_given_invalid_or_expanded_scope_when_scoped_then_refused(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data/chunk-000.parquet", b"rows")
    blob_store.put("source/data/chunk-001.parquet", b"more")
    source = BlobReleaseSource(blob_store, prefix="source")
    full = source.inventory()

    for invalid in (
        [],
        "data/chunk-000.parquet",
        ["data/chunk-000.parquet", "data/chunk-000.parquet"],
        ["data/../chunk.parquet"],
        ["annotations/label.json"],
    ):
        with pytest.raises(ReleaseInputError):
            source.scoped(invalid)

    narrowed = source.scoped(["data/chunk-000.parquet"])
    with pytest.raises(ReleaseInputError, match="expansion"):
        narrowed.scoped(["data/chunk-001.parquet"])
    with pytest.raises(ReleaseInputError, match="dataset-wide"):
        narrowed.metadata_inventory()
    with pytest.raises(ReleaseInputError, match="outside the scoped read"):
        narrowed.binding(full[1])

    monkeypatch.setenv("RELEASE_SOURCE_MAX_OBJECTS", "1")
    with pytest.raises(ReleaseInputError, match="object count"):
        BlobReleaseSource(blob_store, prefix="source").scoped(["data/chunk-000.parquet", "data/chunk-001.parquet"])

    monkeypatch.setenv("RELEASE_SOURCE_MAX_OBJECTS", "4096")
    monkeypatch.setenv("RELEASE_SOURCE_LISTING_BYTES", "1")
    with pytest.raises(ReleaseInputError, match="metadata byte bound"):
        BlobReleaseSource(blob_store, prefix="source").scoped(["data/chunk-000.parquet"]).inventory()
    assert blob_store.calls == []


@pytest.mark.parametrize("bound", ["objects", "bytes", "time"])
def test_given_blob_inventory_limit_when_exceeded_then_refused_without_download(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    blob_store.put("source/first.bin", b"payload")
    blob_store.put("source/second.bin", b"payload")
    if bound == "objects":
        monkeypatch.setenv("RELEASE_SOURCE_MAX_OBJECTS", "1")
    elif bound == "bytes":
        monkeypatch.setenv("RELEASE_SOURCE_LISTING_BYTES", "1")
    else:
        monkeypatch.setenv("RELEASE_SOURCE_LISTING_TIMEOUT_SECONDS", "1")
        ticks = iter([0.0, 2.0])
        monkeypatch.setattr(inputs_module, "monotonic", lambda: next(ticks), raising=False)

    with pytest.raises(ReleaseInputError, match=r"listing.*bound"):
        BlobReleaseSource(blob_store, prefix="source").inventory()
    assert blob_store.calls == []


@pytest.mark.parametrize("value", ["0", "invalid", "1000000000"])
@pytest.mark.parametrize(
    "name",
    [
        "RELEASE_SOURCE_MAX_OBJECTS",
        "RELEASE_SOURCE_LISTING_BYTES",
        "RELEASE_SOURCE_LISTING_TIMEOUT_SECONDS",
        "RELEASE_SOURCE_LISTING_PAGE_SIZE",
        "RELEASE_SOURCE_LISTING_MAX_PAGES",
        "RELEASE_SOURCE_METADATA_BYTES",
        "RELEASE_SOURCE_METADATA_ROWS",
    ],
)
def test_given_invalid_source_bound_when_configured_then_refused(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ReleaseInputError, match=name):
        BlobReleaseSource(blob_store)


def test_given_frozen_blob_when_binding_then_current_version_and_etag_returned(
    blob_store: FakeBlobStore,
) -> None:
    version = blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    binding = source.binding(frozen)

    assert (binding.name, binding.version_id) == ("source/data.bin", version)
    assert binding.etag == blob_store.versions[("source/data.bin", version)][1]


def test_given_frozen_blob_when_binding_then_no_inventory_scan(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data.bin", b"payload")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("binding must not list the dataset")

    monkeypatch.setattr(blob_store, "list_blobs", forbidden)

    assert source.binding(frozen).source == frozen
    assert blob_store.calls == []


def test_given_overwritten_blob_when_binding_then_refused_before_download(
    blob_store: FakeBlobStore,
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.calls.clear()
    blob_store.put("source/data.bin", b"abcdefghij")

    with pytest.raises(ReleaseInputError, match="revision"):
        source.binding(frozen)
    assert blob_store.calls == []


def test_given_stop_during_local_chunk_read_when_inventory_then_stop_propagates_unwrapped(
    local_source: LocalReleaseSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inputs_module, "CHUNK_BYTES", 4)
    calls: list[int] = []

    def checkpoint() -> None:
        calls.append(len(calls))
        if len(calls) == 3:
            raise CancellationError("cancellation requested")

    with pytest.raises(CancellationError):
        local_source.inventory(checkpoint=checkpoint)

    assert len(calls) == 3


def test_given_blob_source_when_inventory_then_no_chunk_downloads_are_issued(
    blob_store: FakeBlobStore,
) -> None:
    blob_store.put("source/data.bin", b"0123456789abcdef")
    source = BlobReleaseSource(blob_store, prefix="source")

    inventory = source.inventory()

    assert [item.path for item in inventory] == ["data.bin"]
    assert blob_store.calls == []


def test_given_stop_during_blob_listing_when_inventory_then_listing_stops_before_download(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    entries = blob_store.list_blobs(name_starts_with="source/")
    listed = []

    def listing(*, name_starts_with: str, **kwargs: object) -> FakePages:
        def page() -> Iterator[SimpleNamespace]:
            for item in entries:
                listed.append(item.name)
                yield item
            pytest.fail("Listing must not advance after cancellation")

        return FakePages([page()])

    def checkpoint() -> None:
        if listed:
            raise CancellationError("cancellation requested")

    monkeypatch.setattr(blob_store, "list_blobs", listing)
    with pytest.raises(CancellationError):
        BlobReleaseSource(blob_store, prefix="source").inventory(checkpoint)
    assert blob_store.calls == []


def test_given_verified_blob_stream_when_cancelled_between_chunks_then_no_more_bytes_returned(
    blob_store: FakeBlobStore,
) -> None:
    blob_store.put("source/data.bin", b"0123456789abcdef")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    cancelled = threading.Event()

    def checkpoint() -> None:
        if cancelled.is_set():
            raise CancellationError("cancellation requested")

    stream = source.iter_bytes(frozen, chunk_size=4, checkpoint=checkpoint)
    assert next(stream) == b"0123"
    cancelled.set()
    with pytest.raises(CancellationError):
        next(stream)


def test_given_repeated_cancellation_while_settling_inventory_when_awaited_then_thread_settled_first() -> None:
    finished = threading.Event()
    started = threading.Event()

    class SlowSource:
        admission = "finalized_quiescent"

        def inventory(self, checkpoint: Callable[[], None] | None = None) -> tuple[()]:
            started.set()
            time.sleep(0.3)
            finished.set()
            return ()

    async def scenario() -> None:
        task = asyncio.create_task(_settled_inventory(SlowSource()))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(task)
        assert finished.is_set()

    asyncio.run(scenario())


def test_given_missing_or_resized_blob_when_binding_then_refused(
    blob_store: FakeBlobStore,
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    with pytest.raises(ReleaseInputError, match="length"):
        source.binding(frozen.model_copy(update={"length": 99}))
    with pytest.raises(ReleaseInputError, match="identity"):
        source.binding(frozen.model_copy(update={"path": "absent.bin"}))
    del blob_store.current["source/data.bin"]
    with pytest.raises(ReleaseInputError, match="missing"):
        source.binding(frozen)


def test_given_pinned_blob_when_iter_bytes_then_rechunked_and_conditioned(
    blob_store: FakeBlobStore,
) -> None:
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
    listed = source.inventory()[0]
    frozen = SourceObject(
        assurance="content_sha256",
        path=listed.path,
        revision=listed.revision,
        length=listed.length,
        sha256=sha256_hex(b"0123456789"),
    )
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


def test_given_stream_failure_when_iter_bytes_then_mapped_to_release_input_error(
    blob_store: FakeBlobStore,
) -> None:
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


def test_given_non_positive_chunk_size_when_iter_bytes_then_refused(
    blob_store: FakeBlobStore,
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]

    with pytest.raises(ReleaseInputError, match="chunk_size"):
        source.iter_bytes(frozen, chunk_size=0)


def test_given_range_read_returns_extra_bytes_when_read_then_refused(blob_store: FakeBlobStore) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    blob_store.serve_transform = lambda data: data + b"extra"
    range_file = source.range_file(frozen)
    range_file.allow(4)

    with pytest.raises(ReleaseInputError, match="more bytes than requested"):
        range_file.read(4)


def test_given_same_length_change_between_ranges_when_read_then_etag_condition_refuses() -> None:
    store = FakeBlobStore(versioned=False)
    store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(store, prefix="source")
    frozen = source.inventory()[0]
    range_file = source.range_file(frozen)
    range_file.allow(10)
    first_version = store.current["source/data.bin"]
    first_etag = store.versions[("source/data.bin", first_version)][1]
    assert range_file.read(4) == b"0123"

    store.put("source/data.bin", b"abcdefghij")
    store.calls.clear()

    with pytest.raises(ReleaseInputError, match="unavailable or changed"):
        range_file.read(4)
    assert store.calls[-1]["etag"] == first_etag
    assert store.calls[-1]["match_condition"] is not None


def test_given_range_read_past_deadline_when_read_then_refused(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    clock = iter([0.0])
    monkeypatch.setattr(inputs_module, "monotonic", lambda: next(clock, 1000.0))
    range_file = source.range_file(frozen)
    range_file.allow(4)

    with pytest.raises(ReleaseInputError, match="deadline"):
        range_file.read(4)


def test_given_elapsed_range_session_when_next_range_requested_then_no_new_download(
    blob_store: FakeBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store.put("source/data.bin", b"0123456789")
    source = BlobReleaseSource(blob_store, prefix="source")
    frozen = source.inventory()[0]
    clock = [0.0]
    monkeypatch.setattr(inputs_module, "monotonic", lambda: clock[0])
    range_file = source.range_file(frozen)
    range_file.allow(8)
    assert range_file.read(4) == b"0123"
    requests = len(blob_store.calls)

    clock[0] = 1000.0

    with pytest.raises(ReleaseInputError, match="deadline"):
        range_file.read(4)
    assert len(blob_store.calls) == requests
