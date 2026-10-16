"""Durable judge lifecycle contracts using temporary storage and deterministic inference."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
from pathlib import Path

import pytest
from evaluation.vlm_judge.judge import JudgeResult
from physical_ai_curation.evidence import bind_job_evidence
from physical_ai_curation.saved_input import SavedInputSnapshot
from physical_ai_dataset_io.lerobot import EpisodeRecord

ECHO_CONFIG = {"execution": {"backend": "echo", "model_id": "echo"}, "agent": {}, "prompt_version": "test"}


class Resolver:
    async def episode_indices(self, dataset_id: str, *, principal_scope_id: str) -> list[int]:
        return [3, 1005]

    async def validation_sample(
        self,
        dataset_id: str,
        episode_index: int,
        *,
        principal_scope_id: str,
        reference: dict[str, str],
        views: tuple[str, ...] = (),
    ) -> dict[str, object]:
        _, snapshot = await self.resolve(dataset_id, episode_index, principal_scope_id=principal_scope_id)
        expected = {
            "annotation_author_id": "human",
            "annotation_revision": "annotation",
            "snapshot_id": snapshot.snapshot_id,
        }
        if reference != expected:
            raise ValueError("Invalid sample reference")
        return {"reference": reference, "input": snapshot.model_dump(mode="json"), "human_outcome": "failure"}

    async def resolve(
        self, dataset_id: str, episode_index: int, **kwargs: object
    ) -> tuple[EpisodeRecord, SavedInputSnapshot]:
        snapshot = SavedInputSnapshot(
            snapshot_id=f"snapshot-{episode_index}",
            dataset_id=dataset_id,
            episode_index=episode_index,
            principal_scope_id=str(kwargs["principal_scope_id"]),
            source_id="source",
            source_revision="revision",
            annotation_author_id="human",
            annotation_revision="annotation",
            edit_revision=None,
            instruction=f"Saved instruction {episode_index}",
            instruction_origin="annotation",
        )
        record = EpisodeRecord(
            f"{dataset_id}/episode_{episode_index:06d}", episode_index, snapshot.instruction, 30, 30, {}, None, None
        )
        return record, snapshot


async def infer(record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, object]) -> dict[str, object]:
    return JudgeResult(
        record.episode_id, snapshot.instruction, "echo", "test", 2, False, 1.0, 1, [0, 25], 1.0
    ).to_dict()


@pytest.mark.parametrize("kind", ["judge", "task-findings"])
async def test_given_versioned_evidence_when_read_then_preserves_payload_and_identity(kind: str) -> None:
    from physical_ai_curation.evidence import bind_evidence, read_evidence
    from physical_ai_curation.judge_port import JudgeInvocation

    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    payload = await infer(record, snapshot, {})
    if kind == "task-findings":
        payload = {
            "episode_id": record.episode_id,
            "instruction": snapshot.instruction,
            "judge_model": "echo",
            "prompt_version": "test",
            "findings": {
                "pick_from": "table",
                "object": "cup",
                "movement_quality": "smooth",
                "notes": "",
                "grasp_success": False,
                "place_success": False,
            },
        }
    invocation = JudgeInvocation(
        invocation_id="request",
        input_id=snapshot.snapshot_id,
        episode_id=record.episode_id,
        instruction=snapshot.instruction,
        views=(),
        options={},
        declared_identity={"model_id": "echo", "prompt_version": "test"},
    )
    envelope = bind_evidence(invocation, payload, kind=kind)
    restored = read_evidence(envelope.to_dict(), expected=invocation)

    assert restored.payload == payload
    assert restored.schema_version == 1
    with pytest.raises(ValueError, match="Missing evidence envelope"):
        read_evidence(payload)
    assert snapshot.model_dump(mode="json")["snapshot_id"] == "snapshot-3"


@pytest.mark.parametrize(
    "field", ["invocation_id", "input_id", "declared_identity", "observed_identity", "schema_version"]
)
async def test_given_mismatched_evidence_when_read_then_rejects_identity_or_version(field: str) -> None:
    from physical_ai_curation.evidence import bind_evidence, read_evidence
    from physical_ai_curation.judge_port import JudgeInvocation

    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    invocation = JudgeInvocation(
        invocation_id="request",
        input_id=snapshot.snapshot_id,
        episode_id=record.episode_id,
        instruction=snapshot.instruction,
        views=(),
        options={},
        declared_identity={"model_id": "echo", "prompt_version": "test"},
    )
    envelope = bind_evidence(invocation, await infer(record, snapshot, {})).to_dict()
    envelope[field] = (
        {"model_id": "other"} if field.endswith("identity") else 2 if field == "schema_version" else "other"
    )

    with pytest.raises(ValueError):
        read_evidence(envelope, expected=invocation)


@pytest.mark.parametrize("mismatch", [None, "judge_model", "prompt_version"])
async def test_given_cached_evidence_when_executed_then_validates_identity_before_media(
    tmp_path: Path, mismatch: str | None
) -> None:
    from evaluation.vlm_judge.prompts import PROMPT_VERSION
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from physical_ai_curation.adapters.evaluator import ServiceExecutor

    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo"), cache_dir=tmp_path))
    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")

    async def forbidden_media(record: EpisodeRecord, snapshot: SavedInputSnapshot) -> EpisodeRecord:
        pytest.fail("Cached evidence must be checked before media preparation")

    executor = ServiceExecutor(service, prepare_media=forbidden_media)
    config = executor.configuration({})
    cache = service.cache_for(None)
    key = cache.key(
        episode_id=record.episode_id,
        snapshot_id=snapshot.snapshot_id,
        video_paths=record.video_paths,
        instruction=snapshot.instruction,
        judge_model=service.model_id,
        prompt_version=PROMPT_VERSION,
        from_s=record.from_timestamp,
        to_s=record.to_timestamp,
        video_windows=record.video_windows,
        media_identity=record.media_identity,
        agent_config=service.config.agent,
    )
    payload = {**await infer(record, snapshot, {}), "prompt_version": PROMPT_VERSION}
    if mismatch:
        payload[mismatch] = "unexpected"
    cache.put(key, payload)

    if mismatch:
        with pytest.raises(ValueError, match="identity mismatch"):
            await executor(record, snapshot, config)
    else:
        assert await executor(record, snapshot, config) == {**payload, "_cache_hit": True}
    assert cache.get(key) == payload


async def test_given_pre_move_inputs_when_relocated_then_snapshot_config_and_cache_hashes_match() -> None:
    import hashlib
    import json

    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from physical_ai_curation.adapters.evaluator import ServiceExecutor
    from physical_ai_curation.saved_input import SavedResource, resolve_saved_input

    class Reader:
        async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
            return SavedResource(None, None)

        async def edits(
            self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
        ) -> SavedResource:
            return SavedResource(None, None)

        async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
            return ("source", "revision")

    identities = {"wrist": "wrist-v1", "front": "front-v1"}
    windows = {"wrist": (3.0, 4.0), "front": (7.0, 8.0)}
    snapshot = await resolve_saved_input(
        Reader(),
        "dataset",
        3,
        principal_scope_id="actor",
        source=("source", "revision"),
        dataset_instruction="Move the cube",
        media_identity=identities,
        video_windows=windows,
    )
    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo", model_id="echo")))
    config = ServiceExecutor(service).configuration({"process_method": "chronological", "views": ["wrist", "front"]})
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cache_key = service.cache_for(None).key(
        episode_id="dataset/episode_000003",
        snapshot_id=snapshot.snapshot_id,
        video_paths={},
        instruction=snapshot.instruction,
        judge_model=service.model_id,
        prompt_version=config["prompt_version"],
        video_windows=windows,
        media_identity=identities,
        agent_config=config["agent"],
    )

    assert snapshot.snapshot_id == "5c4af4b5b47420620d247d41859b6c8383704433891940e56d46af9a6f6b98dc"
    assert config_hash == "493eac88ef1936f0becf6fa042de863c3f1a70c1b327a8021703723a4cf7d6eb"
    assert bytes.fromhex(cache_key) == bytes.fromhex("9dd6791484adc5a3df5ae5d332d9cf0d4a23806be680ebee72ac668efba2e438")


@pytest.mark.parametrize(
    "invalid",
    [
        {"n_frames": True},
        {"outcome_n_valid_votes": -1},
        {"outcome_confidence": float("nan")},
        {"voc": float("inf")},
        {"outcome_success": 1},
        {"progress_per_frame": [False, 25]},
        {"outcome_confidence": "0.5"},
        {"milestones": [{"name": "", "completed": True}]},
    ],
)
async def test_given_invalid_payload_when_validated_then_both_contracts_reject(invalid: dict[str, object]) -> None:
    from physical_ai_curation.evidence import validate_payload

    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    payload = {**await infer(record, snapshot, {}), **invalid}

    with pytest.raises(ValueError):
        JudgeResult.from_dict(payload)
    with pytest.raises(ValueError):
        validate_payload(payload, "judge")


def test_withdrawal_removes_accepted_machine_values_but_preserves_human_and_motion() -> None:
    from physical_ai_curation.models import DatasetLabelsFile, EpisodeAnalysisRecord, MachineOrigin
    from physical_ai_curation.storage import plan_withdrawal

    labels = DatasetLabelsFile(dataset_id="dataset")
    origin = MachineOrigin(
        run_id="run",
        result_id="result",
        run_order=1,
        source_revision="source",
        input_revision="input",
        config_revision="config",
    )
    labels.apply_analysis(
        3, EpisodeAnalysisRecord(object="cup", grasp_success=True), author_id="actor", machine_origin=origin
    )
    machine_object = next(
        item for item in labels.provenance["3"].contributions if item.field == "analysis/object" and item.machine
    )
    labels.provenance["3"].accept(machine_object.id, "human")
    labels.apply_analysis(3, EpisodeAnalysisRecord(grasp_success=False, smoothness=0.7), author_id="human")
    labels.apply_analysis(4, EpisodeAnalysisRecord(object="cup"), author_id="human")
    labels.apply_analysis(4, EpisodeAnalysisRecord(object="cup"), author_id="human", machine_origin=origin)
    labels.analysis["5"] = EpisodeAnalysisRecord(object="unknown legacy", smoothness=0.9)

    projected, summary = plan_withdrawal(labels, {"run"})

    assert labels.analysis["3"].object == "cup"
    assert projected.analysis["3"].object is None
    assert projected.analysis["3"].grasp_success is False
    assert projected.analysis["3"].smoothness == 0.7
    assert projected.analysis["4"].object == "cup"
    assert projected.analysis["5"].object == "unknown legacy"
    assert summary["accepted_unchanged"] == 1
    assert summary["preserved_human"] >= 2
    assert summary["legacy_unknown"] >= 1
    assert machine_object.id in projected.provenance["3"].withdrawn
    repeated, second_summary = plan_withdrawal(projected, {"run"})
    assert repeated == projected
    assert second_summary["removable_fields"] == 0


def test_withdrawal_clears_derived_findings_without_reviving_overlapping_runs() -> None:
    from physical_ai_curation.models import DatasetLabelsFile, EpisodeAnalysisRecord, MachineOrigin
    from physical_ai_curation.storage import plan_withdrawal

    labels = DatasetLabelsFile(dataset_id="dataset")
    origin = MachineOrigin(
        run_id="older",
        result_id="first",
        run_order=1,
        source_revision="source",
        input_revision="input",
        config_revision="config",
    )
    labels.apply_analysis(3, EpisodeAnalysisRecord(object="older value"), author_id="actor", machine_origin=origin)
    labels.apply_analysis(
        3,
        EpisodeAnalysisRecord(object="new value"),
        author_id="actor",
        machine_origin=origin.model_copy(update={"run_id": "newer", "run_order": 2}),
    )
    ledger = labels.provenance["3"]
    parent = next(item for item in ledger.contributions if item.machine and item.machine.run_id == "newer")
    ledger.contributions.append(
        parent.model_copy(
            update={
                "id": "derived",
                "field": "analysis/notes",
                "origin": "template",
                "machine": None,
                "derived_from": [parent.id],
                "value": "derived note",
            }
        )
    )
    labels.analysis["3"].notes = "derived note"
    projected, summary = plan_withdrawal(labels, {"older", "newer"})
    assert projected.analysis["3"].object is None
    assert projected.analysis["3"].notes is None
    assert "derived" in projected.provenance["3"].withdrawn
    assert summary["conflicts"] == 0
    assert plan_withdrawal(projected, {"older", "newer"})[0] == projected


@pytest.mark.asyncio
async def test_given_withdrawn_outcome_label_when_previewed_again_then_absent_flags_are_not_listed(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.storage import plan_withdrawal

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    await storage.save("dataset", DatasetLabelsFile(dataset_id="dataset"), if_none_match=True)
    result = JudgeResult("dataset/episode_000002", "Pick", "echo", "test", 2, True, 1.0, 3, [0, 100], 1.0)
    job = {
        "id": "run",
        "run_order": 1,
        "config_revision": "config",
        "actor": "actor",
        "dataset_id": "dataset",
        "config": ECHO_CONFIG,
    }
    target = {
        "episode_index": 2,
        "result": result.to_dict(),
        "result_id": "result",
        "input_key": "input",
        "input": {"source_revision": "source", "snapshot_id": "snapshot", "instruction": "Pick"},
    }
    target["evidence"] = bind_job_evidence(job, target, target["result"]).to_dict()
    assert await apply_judge_result(storage, job, target)
    labels = (await storage.load_versioned("dataset")).value
    assert labels.episodes["2"] == ["SUCCESS"]

    withdrawn, summary = plan_withdrawal(labels, {"run"})
    assert withdrawn.episodes["2"] == []
    assert [item["field"] for item in summary["fields"]] == ["labels/SUCCESS"]
    assert summary["removable_fields"] == 1

    _, fresh = plan_withdrawal(withdrawn, {"run"})
    assert fresh["fields"] == []
    assert fresh["legacy_unknown"] == 0
    assert fresh["removable_fields"] == 0


def test_given_present_unprovenanced_label_when_previewed_then_it_remains_legacy_unknown() -> None:
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.storage import plan_withdrawal

    labels = DatasetLabelsFile(dataset_id="dataset", episodes={"2": ["SUCCESS"]})
    projected, summary = plan_withdrawal(labels, {"run"})
    assert projected.episodes["2"] == ["SUCCESS"]
    assert summary["fields"] == [
        {
            "episode_index": 2,
            "field": "labels/SUCCESS",
            "disposition": "legacy_unknown",
            "origin": "legacy-unknown",
            "contribution_ids": [],
            "run_ids": [],
        }
    ]


async def _apply_success(storage: object, run_id: str, run_order: int, episode: int = 3) -> None:
    from physical_ai_curation.application import apply_judge_result

    result = JudgeResult(f"dataset/episode_{episode:06d}", "Pick", "echo", "test", 2, True, 1.0, 3, [0, 100], 1.0)
    job = {
        "id": run_id,
        "run_order": run_order,
        "config_revision": "config",
        "actor": "actor",
        "dataset_id": "dataset",
        "config": ECHO_CONFIG,
    }
    target = {
        "episode_index": episode,
        "result": result.to_dict(),
        "result_id": f"result-{run_id}",
        "input_key": "input",
        "input": {"source_revision": "source", "snapshot_id": "snapshot", "instruction": "Pick"},
    }
    target["evidence"] = bind_job_evidence(job, target, target["result"]).to_dict()
    assert await apply_judge_result(storage, job, target)


@pytest.mark.asyncio
async def test_given_output_from_unlisted_run_when_withdrawing_then_conflict_is_explained_and_recoverable(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    jobs = manager(tmp_path / "jobs", curation_storage=storage)
    listed = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="listed")
    await _apply_success(storage, "lost-run", 99)
    await _apply_success(storage, listed["id"], 1, episode=1005)

    preview = await jobs.preview_reset("dataset", "actor")
    conflict = next(item for item in preview["summary"]["fields"] if item["disposition"] == "conflicts")
    assert conflict == {
        "episode_index": 3,
        "field": "labels/SUCCESS",
        "disposition": "conflicts",
        "origin": "machine",
        "contribution_ids": [],
        "run_ids": [],
        "reason": "unlisted_machine_run",
        "blocking_contribution_ids": conflict["blocking_contribution_ids"],
        "blocking_run_ids": ["lost-run"],
    }
    assert preview["summary"]["unlisted_run_ids"] == ["lost-run"]
    await jobs.confirm_reset("dataset", "actor", preview["id"])
    assert await jobs.reset_once()
    completed = await jobs.reset_status("dataset", "actor")
    assert completed["status"] == "conflicted"
    withdrawn = [item for item in completed["summary"]["fields"] if item["disposition"] == "removable_fields"]
    assert [item["episode_index"] for item in withdrawn] == [1005]
    saved = (await storage.load_versioned("dataset")).value
    assert saved.episodes["1005"] == [] and saved.episodes["3"] == ["SUCCESS"]
    assert (await jobs.submit("dataset", "actor", [1005], ECHO_CONFIG, idempotency_key="after"))["status"] == "queued"

    recovery = await jobs.preview_reset("dataset", "actor", include_unlisted_runs=True)
    assert "lost-run" in recovery["run_ids"]
    assert recovery["summary"]["conflicts"] == 0
    await jobs.confirm_reset("dataset", "actor", recovery["id"])
    assert await jobs.reset_once()
    assert (await jobs.reset_status("dataset", "actor"))["status"] == "succeeded"
    assert (await storage.load_versioned("dataset")).value.episodes["3"] == []


@pytest.mark.asyncio
async def test_given_unprovenanced_label_before_ai_application_when_withdrawn_then_it_is_restored_not_conflicted(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.storage import plan_withdrawal

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    legacy = DatasetLabelsFile(dataset_id="dataset", episodes={"3": ["SUCCESS"]})
    await storage.save("dataset", legacy, if_none_match=True)
    await _apply_success(storage, "run", 1)
    projected, summary = plan_withdrawal((await storage.load_versioned("dataset")).value, {"run"})
    assert summary["conflicts"] == 0
    restored = next(item for item in summary["fields"] if item["field"] == "labels/SUCCESS")
    assert restored["disposition"] == "legacy_unknown"
    assert restored["reason"] == "legacy_value_restored"
    assert restored["run_ids"] == ["run"]
    assert projected.episodes["3"] == ["SUCCESS"]


def manager(root: Path, **kwargs: object) -> object:
    from physical_ai_curation.adapters.job_storage import LocalJobStore
    from physical_ai_curation.jobs import JudgeJobs

    return JudgeJobs(LocalJobStore(root), Resolver(), infer, capacity_scope="test-device", **kwargs)


@pytest.mark.asyncio
async def test_reset_preview_is_revision_bound_and_fences_all_dataset_workers(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result
    from physical_ai_curation.models import DatasetLabelsFile

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    jobs = manager(tmp_path / "jobs", curation_storage=storage)
    completed = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="completed")
    await jobs.run_once()
    completed = await jobs.get(completed["id"], "actor")
    await apply_judge_result(storage, completed, completed["targets"][0])
    running = await jobs.submit("dataset", "another-actor", [1005], ECHO_CONFIG, idempotency_key="running")
    claim = await jobs.claim()
    preview = await jobs.preview_reset("dataset", "actor")
    assert set(preview["run_ids"]) == {completed["id"], running["id"]}
    assert preview["summary"]["removable_fields"] >= 1
    accepted = await jobs.confirm_reset("dataset", "actor", preview["id"])
    assert accepted["status"] == "running"
    with pytest.raises(ValueError, match="withdrawal"):
        await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="blocked")
    assert not await jobs.publish(claim, result={"late": True})

    restarted = manager(tmp_path / "jobs", curation_storage=storage)
    assert await restarted.reset_once()
    assert (await restarted.reset_status("dataset", "actor"))["status"] == "succeeded"
    assert not await restarted.reset_once()
    saved = (await storage.load_versioned("dataset")).value or DatasetLabelsFile(dataset_id="dataset")
    assert "FAILURE" not in saved.episodes["3"]
    with pytest.raises(ValueError, match="Withdrawn"):
        await jobs.retry(completed["id"], "actor")
    assert (await jobs.results("dataset", "actor", 3))[0]["applicability"] == "withdrawn"
    assert (await jobs.confirm_reset("dataset", "actor", preview["id"]))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_reset_rejects_new_jobs_or_human_saves_after_preview(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.models import DatasetLabelsFile

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    jobs = manager(tmp_path / "jobs", curation_storage=storage)
    await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="first")
    preview = await jobs.preview_reset("dataset", "actor")
    await jobs.submit("dataset", "actor", [1005], ECHO_CONFIG, idempotency_key="new")
    with pytest.raises(ValueError, match="preview"):
        await jobs.confirm_reset("dataset", "actor", preview["id"])
    preview = await jobs.preview_reset("dataset", "actor")
    labels = DatasetLabelsFile(dataset_id="dataset")
    labels.apply_labels(3, ["SUCCESS"], author_id="human")
    await storage.save("dataset", labels, if_none_match=True)
    with pytest.raises(ValueError, match="preview"):
        await jobs.confirm_reset("dataset", "actor", preview["id"])
    assert (await jobs.preview_reset("dataset", "actor"))["summary"]["preserved_human"] == 1


@pytest.mark.asyncio
async def test_reset_recovers_resource_publication_before_lost_job_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    jobs = manager(tmp_path / "jobs", curation_storage=storage)
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="first")
    await jobs.run_once()
    job = await jobs.get(job["id"], "actor")
    await apply_judge_result(storage, job, job["targets"][0])
    preview = await jobs.preview_reset("dataset", "actor")
    await jobs.confirm_reset("dataset", "actor", preview["id"])

    def interrupted_write(state: object) -> None:
        raise OSError("private interrupted checkpoint")

    monkeypatch.setattr(jobs.store, "_write", interrupted_write)
    with pytest.raises(OSError):
        await jobs.reset_once()
    saved = await storage.load_versioned("dataset")
    assert "FAILURE" not in saved.value.episodes["3"]
    restarted = manager(tmp_path / "jobs", curation_storage=storage)
    assert await restarted.reset_once()
    assert (await restarted.reset_status("dataset", "actor"))["status"] == "succeeded"
    assert (await storage.load_versioned("dataset")).etag == saved.etag


@pytest.mark.asyncio
async def test_reset_preserves_independent_human_sample_approval(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    jobs = manager(tmp_path / "jobs", curation_storage=storage)
    sample = await jobs.submit(
        "dataset",
        "actor",
        [3],
        ECHO_CONFIG,
        idempotency_key="sample",
        mode="sample",
        samples={
            3: {"annotation_author_id": "human", "annotation_revision": "annotation", "snapshot_id": "snapshot-3"},
        },
    )
    await jobs.run_once()
    approval = await jobs.approve(sample["id"], "actor")
    preview = await jobs.preview_reset("dataset", "actor")
    await jobs.confirm_reset("dataset", "actor", preview["id"])
    await jobs.reset_once()
    target = await jobs.submit(
        "dataset",
        "actor",
        [1005],
        ECHO_CONFIG,
        idempotency_key="target",
        approval_id=approval["id"],
        require_approval=True,
    )
    assert target["status"] == "queued"


def test_reset_http_requires_mutation_authority_and_returns_durable_status(tmp_path: Path) -> None:
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.api import build_job_router

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    jobs = manager(tmp_path / "jobs", curation_storage=storage)
    allowed = False

    def authorize() -> None:
        if not allowed:
            raise HTTPException(403)

    app = FastAPI()
    app.include_router(
        build_job_router(
            lambda: jobs,
            actor_dependency=lambda: "actor",
            prepare_config=lambda value: {**ECHO_CONFIG, **value},
            mutation_dependencies=[Depends(authorize)],
        )
    )
    with TestClient(app) as client:
        assert client.post("/resets/preview", json={"dataset_id": "dataset"}).status_code == 403
        allowed = True
        preview = client.post("/resets/preview", json={"dataset_id": "dataset"})
        assert preview.status_code == 201, preview.text
        assert "actor" not in preview.json()
        accepted = client.post("/resets", json={"dataset_id": "dataset", "preview_id": preview.json()["id"]})
        assert accepted.status_code == 202, accepted.text
        assert accepted.headers["Retry-After"] == "1"
        assert client.get(accepted.headers["Location"]).json()["status"] == "running"
        asyncio.run(jobs.reset_once())
        assert client.get(accepted.headers["Location"]).json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_reset_cli_preview_confirm_and_status_share_durable_owner(tmp_path: Path) -> None:
    import argparse

    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.job_cli import add_job_arguments, execute_job_command

    jobs = manager(tmp_path / "jobs", curation_storage=LocalCurationStorage({"dataset": tmp_path / "dataset"}))
    parser = argparse.ArgumentParser()
    add_job_arguments(parser)
    _, preview = await execute_job_command(
        jobs,
        parser.parse_args(["--operation", "reset-preview"]),
        dataset_id="dataset",
        actor="actor",
        config=ECHO_CONFIG,
    )
    code, reset = await execute_job_command(
        jobs,
        parser.parse_args(
            [
                "--operation",
                "reset-confirm",
                "--preview-id",
                preview["id"],
            ]
        ),
        dataset_id="dataset",
        actor="actor",
        config=ECHO_CONFIG,
    )
    assert code == 0 and reset["status"] == "succeeded"
    _, status = await execute_job_command(
        jobs,
        parser.parse_args(["--operation", "reset-status"]),
        dataset_id="dataset",
        actor="actor",
        config=ECHO_CONFIG,
    )
    assert status["id"] == reset["id"]


@pytest.mark.asyncio
async def test_accepted_work_survives_new_manager_and_uses_real_ids(tmp_path: Path) -> None:
    first = manager(tmp_path)
    job = await first.submit("dataset", "actor", [3, 1005], ECHO_CONFIG, idempotency_key="request")
    management = manager(tmp_path)
    management.infer = None
    assert not await management.run_once()
    assert (await management.get(job["id"], "actor"))["status"] == "queued"
    second = manager(tmp_path)

    assert (await second.get(job["id"], "actor"))["status"] == "queued"
    assert await second.run_once()
    assert await second.run_once()
    completed = await first.get(job["id"], "actor")

    assert completed["status"] == "succeeded"
    assert completed["judged"] == 2
    assert completed["applied"] == 0
    assert [target["episode_index"] for target in completed["targets"]] == [3, 1005]
    assert all(target["result"]["outcome_success"] is False for target in completed["targets"])


@pytest.mark.asyncio
async def test_idempotency_replays_same_request_and_rejects_changed_payload(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    first = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="request")
    replay = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="request")

    assert replay["id"] == first["id"]
    with pytest.raises(ValueError, match="Idempotency"):
        await jobs.submit("dataset", "actor", [1005], ECHO_CONFIG, idempotency_key="request")


@pytest.mark.asyncio
async def test_cancellation_during_inference_fences_late_result(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.job_storage import LocalJobStore
    from physical_ai_curation.jobs import JudgeJobs

    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(
        record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, object]
    ) -> dict[str, object]:
        entered.set()
        await release.wait()
        return await infer(record, snapshot, config)

    jobs = JudgeJobs(LocalJobStore(tmp_path), Resolver(), delayed, capacity_scope="test-device")
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="request")
    execution = asyncio.create_task(jobs.run_once())
    await asyncio.wait_for(entered.wait(), 5)
    await jobs.cancel(job["id"], "actor")
    await jobs.submit("other", "actor", [1005], ECHO_CONFIG, idempotency_key="other")
    assert await jobs.claim() is None
    release.set()
    assert await execution is True

    result = await manager(tmp_path).get(job["id"], "actor")
    assert result["status"] == "cancelled"
    assert result["judged"] == 0
    assert result["targets"][0].get("result") is None
    assert await jobs.claim() is not None


@pytest.mark.asyncio
async def test_expired_claim_cannot_publish_and_is_recovered(tmp_path: Path) -> None:
    now = [100.0]
    first = manager(tmp_path, clock=lambda: now[0], lease_seconds=10)
    second = manager(tmp_path, clock=lambda: now[0], lease_seconds=10)
    job = await first.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="request")
    old = await first.claim()
    now[0] += 11
    current = await second.claim()

    assert current["fence"] > old["fence"]
    assert not await first.publish(old, result={"unexpected": True})
    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    assert await second.publish(current, result=await infer(record, snapshot, {}))
    assert (await first.get(job["id"], "actor"))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_capacity_is_shared_across_instances_and_datasets(tmp_path: Path) -> None:
    first, second = manager(tmp_path), manager(tmp_path)
    await first.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="first")
    await second.submit("other", "actor", [1005], ECHO_CONFIG, idempotency_key="second")

    claimed = await asyncio.gather(first.claim(), second.claim())

    assert sum(claim is not None for claim in claimed) == 1


@pytest.mark.asyncio
async def test_failed_target_retry_preserves_successful_evidence(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    job = await jobs.submit("dataset", "actor", [3, 1005], ECHO_CONFIG, idempotency_key="request")
    await jobs.run_once()
    claim = await jobs.claim()
    await jobs.publish(claim, error="SyntheticFailure")
    partial = await jobs.get(job["id"], "actor")
    assert partial["status"] == "partial"

    await jobs.retry(job["id"], "actor")
    await jobs.run_once()
    finished = await jobs.get(job["id"], "actor")
    assert finished["status"] == "succeeded"
    assert finished["targets"][0]["result_id"] == partial["targets"][0]["result_id"]


@pytest.mark.asyncio
async def test_job_reads_do_not_expose_another_actor(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="request")
    with pytest.raises(PermissionError):
        await jobs.get(job["id"], "other-actor")


def _claim_process(root: str, output: object) -> None:
    output.put(asyncio.run(manager(Path(root)).claim()))


@pytest.mark.asyncio
async def test_competing_processes_share_one_durable_capacity_slot(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    await jobs.submit("dataset", "actor", [3, 1005], ECHO_CONFIG, idempotency_key="request")
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [context.Process(target=_claim_process, args=(str(tmp_path), output)) for _ in range(2)]
    for process in processes:
        process.start()
    claims = [await asyncio.to_thread(output.get, True, 20) for _ in processes]
    for process in processes:
        await asyncio.to_thread(process.join, 20)
        assert process.exitcode == 0
    output.close()

    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.asyncio
async def test_identical_inputs_reuse_evidence_without_another_inference(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.job_storage import LocalJobStore
    from physical_ai_curation.jobs import JudgeJobs

    calls = []

    async def counted(
        record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, object]
    ) -> dict[str, object]:
        calls.append(record.episode_index)
        return await infer(record, snapshot, config)

    jobs = JudgeJobs(LocalJobStore(tmp_path), Resolver(), counted, capacity_scope="test-device")
    first = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="first")
    await jobs.run_once()
    second = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="second")
    await jobs.run_once()

    assert calls == [3]
    assert (await jobs.get(second["id"], "actor"))["targets"][0]["cached"] is True
    history = await jobs.results("dataset", "actor", 3, snapshot_id="snapshot-3")
    assert [result["run_id"] for result in history] == [second["id"], first["id"]]
    assert all(result["applicability"] == "current" for result in history)


@pytest.mark.asyncio
async def test_wrong_episode_result_is_an_execution_error(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="request")
    claim = await jobs.claim()
    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    result = await infer(record, snapshot, {})
    result["episode_id"] = "dataset/episode_001005"
    await jobs.publish(claim, result=result)

    assert (await jobs.get(job["id"], "actor"))["status"] == "failed"


async def test_given_declared_runtime_when_reopened_and_reused_then_evidence_is_bound_without_identity_changes(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.jobs import fingerprint

    config = {"execution": {"backend": "echo", "model_id": "echo"}, "agent": {}, "prompt_version": "test"}
    jobs = manager(tmp_path)
    first = await jobs.submit("dataset", "actor", [3], config, idempotency_key="first")
    await jobs.run_once()
    stored = (await manager(tmp_path).get(first["id"], "actor"))["targets"][0]

    assert stored["result_id"] == fingerprint([first["id"], stored["input_key"]])
    assert stored["evidence"]["payload"] == stored["result"]
    assert stored["evidence"]["schema_version"] == 1
    assert stored["evidence"]["input_id"] == stored["input"]["snapshot_id"]
    assert stored["evidence"]["declared_identity"]["model_id"] == "echo"
    for missing_config in ({}, {"execution": {}}, {"execution": {"backend": "echo"}}):
        with pytest.raises(ValueError, match="Missing declared evidence identity"):
            bind_job_evidence({**first, "config": missing_config}, stored, stored["result"])

    async def forbidden_inference(*args: object) -> dict[str, object]:
        pytest.fail("Reusing evidence must not invoke inference")

    jobs.infer = forbidden_inference
    second = await jobs.submit("dataset", "actor", [3], config, idempotency_key="second")
    await jobs.run_once()
    reused = (await jobs.get(second["id"], "actor"))["targets"][0]
    assert reused["cached"] is True
    assert reused["result"] == stored["result"]
    assert reused["input_key"] == stored["input_key"]
    assert reused["evidence"]["invocation_id"] != stored["evidence"]["invocation_id"]
    history = await manager(tmp_path).results("dataset", "actor", 3, snapshot_id="snapshot-3")
    assert history[0]["evidence"] == reused["evidence"]
    assert history[1]["result_id"] == stored["result_id"]
    async with jobs.store.transaction() as state:
        state["jobs"][first["id"]]["targets"][0].pop("evidence")
    with pytest.raises(ValueError, match="Missing evidence envelope"):
        await manager(tmp_path).results("dataset", "actor", 3, snapshot_id="snapshot-3")


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("input_id", "other"), ("kind", "task-findings")])
async def test_given_invalid_stored_envelope_when_read_or_applied_then_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result

    jobs = manager(tmp_path / "jobs")
    config = {"execution": {"backend": "echo", "model_id": "echo"}, "agent": {}, "prompt_version": "test"}
    accepted = await jobs.submit("dataset", "actor", [3], config, idempotency_key="first")
    await jobs.run_once()
    async with jobs.store.transaction() as state:
        state["jobs"][accepted["id"]]["targets"][0]["evidence"][field] = value
    job = await jobs.get(accepted["id"], "actor")
    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})

    with pytest.raises(ValueError):
        await jobs.results("dataset", "actor", 3)
    with pytest.raises(ValueError):
        await apply_judge_result(storage, job, job["targets"][0])
    assert (await storage.load_versioned("dataset")).etag is None
    assert not (tmp_path / "dataset" / "meta" / "episode_labels.json").exists()


@pytest.mark.asyncio
async def test_force_recomputes_without_changing_execution_identity(tmp_path: Path) -> None:
    calls = []

    async def counted(
        record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, object]
    ) -> dict[str, object]:
        calls.append(record.episode_index)
        return await infer(record, snapshot, config)

    jobs = manager(tmp_path)
    jobs.infer = counted
    original = await jobs.submit("dataset", "actor", [3], {**ECHO_CONFIG, "force": False}, idempotency_key="original")
    await jobs.run_once()
    forced = await jobs.submit("dataset", "actor", [3], {**ECHO_CONFIG, "force": True}, idempotency_key="forced")
    await jobs.run_once()
    reused = await jobs.submit("dataset", "actor", [3], {**ECHO_CONFIG, "force": False}, idempotency_key="reused")
    await jobs.run_once()

    assert calls == [3, 3]
    assert original["config_revision"] == forced["config_revision"] == reused["config_revision"]
    assert original["targets"][0]["input_key"] == forced["targets"][0]["input_key"]
    assert (await jobs.get(forced["id"], "actor"))["targets"][0]["cached"] is False
    assert (await jobs.get(reused["id"], "actor"))["targets"][0]["cached"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_kind", ["local", "blob"])
async def test_history_is_read_only_and_generation_fences_publication_and_application(
    tmp_path: Path,
    storage_kind: str,
) -> None:
    from physical_ai_curation.adapters.job_storage import BlobJobStore, LocalJobStore
    from physical_ai_curation.jobs import JudgeJobs

    calls = []

    async def apply_result(job: dict[str, object], target: dict[str, object]) -> bool:
        calls.append(target["result_id"])
        return True

    store = LocalJobStore(tmp_path) if storage_kind == "local" else BlobJobStore(MemoryBlob())
    jobs = JudgeJobs(store, Resolver(), infer, capacity_scope="test-device", apply_result=apply_result)
    first = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="first")
    await jobs.run_once()
    second = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="second")
    await jobs.run_once()
    pending = await jobs.submit("dataset", "actor", [1005], ECHO_CONFIG, idempotency_key="pending")
    claim = await jobs.claim()
    await jobs.request_application(second["id"], "actor")
    before = await store.read()
    current = await jobs.results("dataset", "actor", 3, snapshot_id="snapshot-3")
    stale = await jobs.results("dataset", "actor", 3, snapshot_id="changed")
    assert [item["run_id"] for item in current] == [second["id"], first["id"]]
    assert all(item["applicability"] == "current" for item in current)
    assert all(item["applicability"] == "stale" for item in stale)
    assert await jobs.results("dataset", "other", 3, snapshot_id="snapshot-3") == []
    assert await store.read() == before

    async with store.transaction() as state:
        state["generations"]["dataset"] = 1
    record, snapshot = await Resolver().resolve("dataset", 1005, principal_scope_id="actor")
    assert not await jobs.publish(claim, result=await infer(record, snapshot, {}))
    assert not await jobs.apply_once()
    assert calls == []
    assert (await jobs.get(pending["id"], "actor"))["judged"] == 0
    withdrawn = await jobs.results("dataset", "actor", 3, snapshot_id="snapshot-3")
    assert all(item["applicability"] == "withdrawn" for item in withdrawn)
    assert [item["result_id"] for item in withdrawn] == [item["result_id"] for item in current]
    with pytest.raises(ValueError, match="withdrawn"):
        await jobs.request_application(first["id"], "actor")


def test_http_accepts_durable_work_without_request_held_inference(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from physical_ai_curation.api import build_job_router

    jobs = manager(tmp_path)
    app = FastAPI()
    app.include_router(
        build_job_router(
            lambda: jobs, actor_dependency=lambda: "actor", prepare_config=lambda value: {**ECHO_CONFIG, **value}
        )
    )
    payload = {
        "dataset_id": "dataset",
        "episode_indices": [3, 1005],
        "mode": "sample",
        "samples": {
            str(index): {
                "annotation_author_id": "human",
                "annotation_revision": "annotation",
                "snapshot_id": f"snapshot-{index}",
            }
            for index in (3, 1005)
        },
    }
    with TestClient(app) as client:
        accepted = client.post("/jobs", json=payload, headers={"Idempotency-Key": "request"})
        assert accepted.status_code == 202
        assert accepted.headers["Retry-After"] == "1"
        status = client.get(accepted.headers["Location"])
        assert status.json()["status"] == "queued"
        assert status.json()["judged"] == 0
        replay = client.post("/jobs", json=payload, headers={"Idempotency-Key": "request"})
        assert replay.json()["id"] == accepted.json()["id"]
        conflict = client.post(
            "/jobs", json={"dataset_id": "dataset", "episode_indices": [3]}, headers={"Idempotency-Key": "request"}
        )
        assert conflict.status_code == 409
        assert client.get("/jobs", params={"dataset_id": "dataset"}).json()["total"] == 1
        cancelled = client.post(f"/jobs/{accepted.json()['id']}/cancel")
        assert cancelled.json()["status"] == "cancelled"
        assert client.post(f"/jobs/{accepted.json()['id']}/retry").json()["status"] == "queued"


def test_http_job_reads_are_bounded_and_principal_scoped(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from physical_ai_curation.api import build_job_router

    jobs = manager(tmp_path)
    actor = ["actor"]
    app = FastAPI()
    app.include_router(
        build_job_router(
            lambda: jobs, actor_dependency=lambda: actor[0], prepare_config=lambda value: {**ECHO_CONFIG, **value}
        )
    )
    with TestClient(app) as client:
        accepted = client.post(
            "/jobs",
            json={
                "dataset_id": "dataset",
                "episode_indices": list(range(150)),
                "mode": "sample",
                "samples": {
                    str(index): {
                        "annotation_author_id": "human",
                        "annotation_revision": "annotation",
                        "snapshot_id": f"snapshot-{index}",
                    }
                    for index in range(150)
                },
            },
            headers={"Idempotency-Key": "request"},
        )
        assert accepted.status_code == 202
        assert "targets" not in accepted.json()
        status = client.get(accepted.headers["Location"])
        assert len(status.json()["targets"]) == 50
        assert status.json()["total"] == 150
        actor[0] = "other"
        assert client.get(accepted.headers["Location"]).status_code == 404
        assert client.get("/jobs", params={"dataset_id": "dataset"}).json()["total"] == 0


@pytest.mark.asyncio
async def test_expected_saved_snapshot_is_checked_before_acceptance(tmp_path: Path) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    class ChangedResolver(Resolver):
        async def resolve(
            self, dataset_id: str, episode_index: int, **kwargs: object
        ) -> tuple[EpisodeRecord, SavedInputSnapshot]:
            if kwargs.get("expected_snapshot_id") != "expected-snapshot":
                raise SavedInputError("Saved input changed")
            return await super().resolve(dataset_id, episode_index, **kwargs)

    jobs = manager(tmp_path)
    jobs.resolver = ChangedResolver()
    accepted = await jobs.submit(
        "dataset", "actor", [3], ECHO_CONFIG, idempotency_key="expected", expected_snapshots={3: "expected-snapshot"}
    )
    assert accepted["status"] == "queued"
    with pytest.raises(SavedInputError):
        await jobs.submit(
            "dataset", "actor", [3], ECHO_CONFIG, idempotency_key="changed", expected_snapshots={3: "stale-snapshot"}
        )
    assert (await jobs.list("dataset", "actor"))["total"] == 1


@pytest.mark.asyncio
async def test_idle_worker_does_not_create_or_rewrite_state(tmp_path: Path) -> None:
    jobs = manager(tmp_path / "not-created")
    assert not await jobs.run_once()
    assert not (tmp_path / "not-created").exists()


@pytest.mark.asyncio
async def test_service_executor_rejects_changed_runtime_before_inference() -> None:
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from physical_ai_curation.adapters.evaluator import ServiceExecutor

    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo", model_id="echo")))
    executor = ServiceExecutor(service)
    configuration = executor.configuration({})
    assert "api_key" not in str(configuration)
    configuration["execution"]["model_id"] = "changed-model"
    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    with pytest.raises(ValueError, match="runtime"):
        await executor(record, snapshot, configuration)


async def test_given_resolved_views_when_port_scores_then_preserves_order_windows_and_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.vlm_judge.prompts import PROMPT_VERSION
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from physical_ai_curation.adapters.evaluator import EvaluatorJudge
    from physical_ai_curation.judge_port import JudgeInvocation

    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo", model_id="echo")))
    adapter = EvaluatorJudge(service)
    calls = []

    def score(**kwargs: object) -> JudgeResult:
        calls.append(kwargs)
        return JudgeResult("episode", "Move the cup", "echo", PROMPT_VERSION, 2, False, 1.0, 1, [0, 25], 1.0)

    monkeypatch.setattr(service, "judge_episode", score)
    request = JudgeInvocation(
        invocation_id="request",
        input_id="snapshot",
        episode_id="episode",
        instruction="Move the cup",
        views=(
            {"name": "wrist", "path": "/synthetic/wrist.mp4", "window": (3, 4), "media_identity": "wrist-v1"},
            {"name": "front", "path": "/synthetic/front.mp4", "window": (7, 8), "media_identity": "front-v1"},
        ),
        options={"process_method": "chronological"},
        declared_identity=adapter.identity("chronological"),
    )
    result = await adapter.evaluate(request)

    assert result.input_id == "snapshot" and result.invocation_id == "request"
    assert result.payload["outcome_success"] is False
    assert list(calls[0]["video_paths"]) == ["wrist", "front"]
    assert calls[0]["video_windows"] == {"wrist": (3, 4), "front": (7, 8)}
    assert calls[0]["media_identity"] == {"wrist": "wrist-v1", "front": "front-v1"}
    assert "principal_scope_id" not in calls[0]


@pytest.mark.asyncio
async def test_service_executor_runs_blocking_inference_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from evaluation.vlm_judge.prompts import PROMPT_VERSION
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from physical_ai_curation.adapters.evaluator import ServiceExecutor

    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo", model_id="echo")))
    executor = ServiceExecutor(service)
    entered, release = threading.Event(), threading.Event()
    record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    result = JudgeResult.from_dict(await infer(record, snapshot, {}))
    result.prompt_version = PROMPT_VERSION

    def blocked(**kwargs: object) -> JudgeResult:
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(service, "judge_episode", blocked)
    execution = asyncio.create_task(executor(record, snapshot, executor.configuration({})))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
    finally:
        release.set()
    assert (await execution)["episode_id"] == record.episode_id


class MemoryBlob:
    def __init__(self, *, require_lease: bool = True) -> None:
        self.require_lease = require_lease
        self.content = None
        self.revision = 0
        self.leased = False
        self.reject_publication = False

    async def acquire_lease(self, **kwargs: object) -> object:
        from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

        if self.content is None:
            raise ResourceNotFoundError("Missing state")
        if self.leased:
            error = HttpResponseError("Lease held")
            error.status_code = 409
            raise error
        self.leased = True
        return self

    async def renew(self) -> None:
        assert self.leased

    async def release(self) -> None:
        self.leased = False

    async def download_blob(self, **kwargs: object) -> object:
        from types import SimpleNamespace

        from azure.core.exceptions import ResourceNotFoundError

        if self.content is None:
            raise ResourceNotFoundError("Missing state")
        content = self.content

        async def readall() -> bytes:
            return content

        return SimpleNamespace(properties=SimpleNamespace(etag=str(self.revision)), readall=readall)

    async def upload_blob(self, content: bytes, **kwargs: object) -> dict[str, str]:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

        if self.leased and kwargs.get("lease") is not self:
            raise ResourceModifiedError("Lease required")
        if kwargs.get("overwrite") is False and self.content is not None:
            raise ResourceExistsError("State exists")
        if kwargs.get("overwrite"):
            if self.require_lease:
                assert kwargs.get("lease") is self
            assert kwargs.get("match_condition") == MatchConditions.IfNotModified
            if kwargs.get("etag") != str(self.revision) or self.reject_publication:
                raise ResourceModifiedError("State changed")
        self.content = content
        self.revision += 1
        return {"etag": str(self.revision)}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["local", "blob"])
@pytest.mark.parametrize("failure", ["storage", "human-edit"])
async def test_reset_recovery_preserves_humans_and_reports_safe_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    backend: str,
    failure: str,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from physical_ai_curation.adapters.job_storage import BlobJobStore, LocalJobStore
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result
    from physical_ai_curation.jobs import JudgeJobs
    from physical_ai_curation.models import EpisodeAnalysisRecord

    from src.api.services.label_storage import BlobLabelStorage

    caplog.set_level(logging.INFO, logger="physical_ai_curation.jobs")
    if backend == "blob":
        client = MagicMock()
        client.get_container_client.return_value.get_blob_client.return_value = MemoryBlob(require_lease=False)
        storage = BlobLabelStorage(
            SimpleNamespace(container_name="synthetic", _get_client=AsyncMock(return_value=client))
        )
        store = BlobJobStore(MemoryBlob())
    else:
        storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
        store = LocalJobStore(tmp_path / "jobs")
    jobs = JudgeJobs(store, Resolver(), infer, capacity_scope="test-device", curation_storage=storage)
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="first")
    await jobs.run_once()
    job = await jobs.get(job["id"], "actor")
    await apply_judge_result(storage, job, job["targets"][0])
    preview = await jobs.preview_reset("dataset", "actor")
    await jobs.confirm_reset("dataset", "actor", preview["id"])
    if failure == "storage":
        original_save = storage.save
        monkeypatch.setattr(storage, "save", AsyncMock(side_effect=OSError("private provider content")))
    else:
        resource = await storage.load_versioned("dataset")
        resource.value.apply_analysis(3, EpisodeAnalysisRecord(notes="human revision"), author_id="human")
        await storage.save("dataset", resource.value, if_match=resource.etag)
    assert await jobs.reset_once()
    reset = await jobs.reset_status("dataset", "actor")
    assert reset["status"] == ("partial" if failure == "storage" else "conflicted")
    assert "private provider" not in caplog.text
    expected_category = "OSError" if failure == "storage" else "RevisionConflictError"
    assert any(
        record.levelno == logging.WARNING and expected_category in record.getMessage() for record in caplog.records
    )
    with pytest.raises(ValueError, match="withdrawal"):
        await jobs.submit("dataset", "actor", [1005], ECHO_CONFIG, idempotency_key="blocked")
    if failure == "storage":
        monkeypatch.setattr(storage, "save", original_save)
        await jobs.retry_reset("dataset", "actor")
        assert any(
            record.levelno == logging.INFO and "Withdrawal retry accepted" in record.getMessage()
            for record in caplog.records
        )
    else:
        with pytest.raises(ValueError, match="preview"):
            await jobs.retry_reset("dataset", "actor")
        refreshed = await jobs.preview_reset("dataset", "actor")
        await jobs.confirm_reset("dataset", "actor", refreshed["id"])
    restarted = JudgeJobs(store, Resolver(), infer, capacity_scope="test-device", curation_storage=storage)
    assert await restarted.reset_once()
    assert (await restarted.reset_status("dataset", "actor"))["status"] == "succeeded"
    assert "human revision" not in caplog.text
    assert "private provider" not in caplog.text
    saved = (await storage.load_versioned("dataset")).value
    assert "FAILURE" not in saved.episodes["3"]
    if failure == "human-edit":
        assert saved.analysis["3"].notes == "human revision"


@pytest.mark.asyncio
async def test_blob_job_lifecycle_matches_local_evidence_and_recovery() -> None:
    from physical_ai_curation.adapters.job_storage import BlobJobStore
    from physical_ai_curation.jobs import JudgeJobs

    blob = MemoryBlob()
    first = JudgeJobs(BlobJobStore(blob), Resolver(), infer, capacity_scope="shared")
    second = JudgeJobs(BlobJobStore(blob), Resolver(), infer, capacity_scope="shared")
    job = await first.submit("dataset", "actor", [3, 1005], ECHO_CONFIG, idempotency_key="request")
    await second.run_once()
    assert (await first.get(job["id"], "actor"))["judged"] == 1
    await second.cancel(job["id"], "actor")
    await first.retry(job["id"], "actor")
    await second.run_once()
    assert (await first.get(job["id"], "actor"))["status"] == "succeeded"
    evidence = await first.results("dataset", "actor", 3, snapshot_id="snapshot-3")
    assert evidence[0]["result"]["outcome_success"] is False
    assert evidence[0]["applicability"] == "current"
    assert evidence[0]["applied"] is False


@pytest.mark.asyncio
async def test_blob_transaction_waits_for_existing_lease_and_rejects_lost_revision() -> None:
    from azure.core.exceptions import ResourceModifiedError
    from physical_ai_curation.adapters.job_storage import BlobJobStore

    blob = MemoryBlob()
    first, second = BlobJobStore(blob), BlobJobStore(blob)
    entered = asyncio.Event()

    async def contender() -> None:
        entered.set()
        async with second.transaction() as state:
            state["sequence"] += 1

    async with first.transaction() as state:
        pending = asyncio.create_task(contender())
        await entered.wait()
        state["sequence"] += 1
    assert await pending is None
    assert (await first.read())["sequence"] == 2
    blob.reject_publication = True
    with pytest.raises(ResourceModifiedError):
        async with first.transaction() as state:
            state["sequence"] = 999
    assert (await first.read())["sequence"] == 2
    assert blob.leased is False


def test_shared_curation_preserves_human_ownership_and_machine_retry_identity() -> None:
    from physical_ai_curation.models import DatasetLabelsFile, MachineOrigin

    resource = DatasetLabelsFile(dataset_id="dataset", episodes={"3": ["REVIEW"]})
    resource.apply_labels(3, ["REVIEW", "SUCCESS"], author_id="human", origin="human")
    origin = MachineOrigin(
        run_id="run",
        result_id="result",
        run_order=1,
        source_revision="source",
        input_revision="input",
        config_revision="config",
    )
    resource.apply_labels(3, ["REVIEW", "SUCCESS"], author_id="human", machine_origin=origin)
    ledger = resource.provenance["3"]
    original_count = len(ledger.contributions)
    resource.apply_labels(3, ["REVIEW", "SUCCESS"], author_id="human", machine_origin=origin)
    assert len(ledger.contributions) == original_count
    machine_ids = [item.id for item in ledger.contributions if item.machine is not None]
    ledger.withdraw(machine_ids)
    resource.materialize_episode(3, author_id="human")
    assert resource.episodes["3"] == ["REVIEW", "SUCCESS"]


@pytest.mark.asyncio
async def test_application_is_durable_and_retries_without_duplicate_contributions(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result
    from physical_ai_curation.models import DatasetLabelsFile

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    labels = DatasetLabelsFile(dataset_id="dataset")
    labels.apply_labels(3, ["REVIEW"], author_id="actor", origin="human")
    await storage.save("dataset", labels, if_none_match=True)
    calls = []

    async def interrupted(job: dict[str, object], target: dict[str, object]) -> bool:
        applied = await apply_judge_result(storage, job, target)
        calls.append(target["result_id"])
        if len(calls) == 1:
            raise RuntimeError("Synthetic interruption after label publication")
        return applied

    jobs = manager(tmp_path / "jobs", apply_result=interrupted)
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="judge")
    await jobs.run_once()
    queued = await jobs.request_application(job["id"], "actor")
    assert queued["applied"] == 0
    assert (await storage.load_versioned("dataset")).value.episodes["3"] == ["REVIEW"]
    await jobs.run_once()
    interrupted_job = await jobs.get(job["id"], "actor")
    assert interrupted_job["application_errors"] == 1
    first = (await storage.load_versioned("dataset")).value
    assert first.episodes["3"] == ["REVIEW", "FAILURE"]
    first_ids = [item.id for item in first.provenance["3"].contributions]
    await jobs.retry(job["id"], "actor")
    recovered = manager(tmp_path / "jobs", apply_result=interrupted)
    await recovered.run_once()
    final = await jobs.get(job["id"], "actor")
    assert final["status"] == "succeeded"
    assert final["applied"] == 1
    saved = (await storage.load_versioned("dataset")).value
    assert [item.id for item in saved.provenance["3"].contributions] == first_ids
    assert all(item.machine.run_id == job["id"] for item in saved.provenance["3"].contributions if item.machine)


@pytest.mark.asyncio
async def test_cancelled_application_cannot_mutate_labels(tmp_path: Path) -> None:
    calls = []

    async def apply_result(job: dict[str, object], target: dict[str, object]) -> bool:
        calls.append(target["result_id"])
        return True

    jobs = manager(tmp_path, apply_result=apply_result)
    job = await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="judge")
    await jobs.run_once()
    await jobs.request_application(job["id"], "actor")
    await jobs.cancel(job["id"], "actor")
    assert not await jobs.run_once()
    assert calls == []


@pytest.mark.asyncio
async def test_given_sample_disagreement_when_approving_then_acknowledgment_and_current_evidence_required(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    class SampleResolver(Resolver):
        changed = False

        async def validation_sample(
            self,
            dataset_id: str,
            episode_index: int,
            *,
            principal_scope_id: str,
            reference: dict[str, str],
            views: tuple[str, ...] = (),
        ) -> dict[str, object]:
            if self.changed or reference["annotation_revision"] != "annotation":
                raise SavedInputError("Sample changed")
            _, snapshot = await self.resolve(dataset_id, episode_index, principal_scope_id=principal_scope_id)
            return {"reference": reference, "input": snapshot.model_dump(mode="json"), "human_outcome": "success"}

    jobs = manager(tmp_path)
    jobs.resolver = SampleResolver()
    references = {
        3: {"annotation_author_id": "human", "annotation_revision": "annotation", "snapshot_id": "snapshot-3"}
    }
    sample = await jobs.submit(
        "dataset", "actor", [3], ECHO_CONFIG, idempotency_key="sample", mode="sample", samples=references
    )
    with pytest.raises(ValueError, match="complete"):
        await jobs.approve(sample["id"], "actor")
    await jobs.run_once()
    evaluated = await jobs.get(sample["id"], "actor")
    assert evaluated["targets"][0]["comparison"] == "disagreement"
    with pytest.raises(ValueError, match="acknowledgment"):
        await jobs.approve(sample["id"], "actor")
    approved = await jobs.approve(sample["id"], "actor", acknowledge_exceptions=True)
    accepted = await jobs.submit(
        "dataset",
        "actor",
        [1005],
        ECHO_CONFIG,
        idempotency_key="targets",
        approval_id=approved["id"],
        require_approval=True,
    )
    assert accepted["targets"][0]["input"]["instruction"] == "Saved instruction 1005"
    with pytest.raises(ValueError, match="approval"):
        await jobs.submit(
            "dataset",
            "actor",
            [1005],
            {"views": ["other"]},
            idempotency_key="config-change",
            approval_id=approved["id"],
            require_approval=True,
        )
    jobs.resolver.changed = True
    with pytest.raises(SavedInputError, match="changed"):
        await jobs.submit(
            "dataset",
            "actor",
            [1005],
            ECHO_CONFIG,
            idempotency_key="stale",
            approval_id=approved["id"],
            require_approval=True,
        )
    await jobs.run_once()
    assert (await jobs.get(accepted["id"], "actor"))["targets"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_given_dataset_submission_when_approval_missing_then_no_job_is_accepted(tmp_path: Path) -> None:
    jobs = manager(tmp_path)
    with pytest.raises(ValueError, match="approval"):
        await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="unapproved", require_approval=True)
    with pytest.raises(ValueError, match="sample"):
        await jobs.submit("dataset", "actor", [3], ECHO_CONFIG, idempotency_key="empty-sample", mode="sample")
    assert (await jobs.list("dataset", "actor"))["total"] == 0


def test_given_http_dataset_work_when_submitting_then_explicit_approval_is_required(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from physical_ai_curation.api import build_job_router

    jobs = manager(tmp_path)
    app = FastAPI()
    app.include_router(
        build_job_router(
            lambda: jobs, actor_dependency=lambda: "actor", prepare_config=lambda value: {**ECHO_CONFIG, **value}
        )
    )
    with TestClient(app) as client:
        target = {"dataset_id": "dataset", "episode_indices": [1005]}
        assert client.post("/jobs", json=target, headers={"Idempotency-Key": "unapproved"}).status_code == 409
        sample = client.post(
            "/jobs",
            json={
                "dataset_id": "dataset",
                "episode_indices": [3],
                "mode": "sample",
                "samples": {
                    "3": {
                        "annotation_author_id": "human",
                        "annotation_revision": "annotation",
                        "snapshot_id": "snapshot-3",
                    },
                },
            },
            headers={"Idempotency-Key": "sample"},
        )
        assert sample.status_code == 202, sample.text
        approve_url = f"/jobs/{sample.json()['id']}/approve"
        assert client.post(approve_url, json={}).status_code == 409
        asyncio.run(jobs.run_once())
        approval = client.post(approve_url, json={})
        assert approval.status_code == 201, approval.text
        assert "actor" not in approval.json()
        target["approval_id"] = approval.json()["id"]
        accepted = client.post("/jobs", json=target, headers={"Idempotency-Key": "approved"})
        assert accepted.status_code == 202, accepted.text
        assert client.get("/approvals", params={"dataset_id": "dataset"}).json()["items"][0]["current"] is True


def test_given_no_withdrawal_history_when_reading_reset_status_then_successful_empty_state(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from physical_ai_curation.api import build_job_router

    jobs = manager(tmp_path)
    app = FastAPI()
    app.include_router(
        build_job_router(
            lambda: jobs, actor_dependency=lambda: "actor", prepare_config=lambda value: {**ECHO_CONFIG, **value}
        )
    )
    with TestClient(app) as client:
        response = client.get("/resets", params={"dataset_id": "dataset"})

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,rating,source,accepted",
    [
        ("human", "success", "human", True),
        ("human", "partial", "human", True),
        ("legacy-unknown", "success", "human", True),
        ("human", "unknown", "human", False),
        ("human", "success", "llm-generated", True),
        ("template", "success", "template", True),
    ],
)
async def test_given_saved_sample_when_resolving_then_saved_outcome_and_revision_are_required(
    tmp_path: Path,
    origin: str,
    rating: str,
    source: str,
    accepted: bool,
) -> None:
    import json

    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
    from physical_ai_curation.models import ContributionLedger
    from physical_ai_curation.saved_input import SavedInputError, resolve_validation_sample

    ledger = ContributionLedger()
    ledger.record_changes(
        {},
        {"language_instruction/instruction": "Saved instruction 3", "task_completeness/rating": rating},
        author_id="human",
        origin=origin,
    )
    annotation = {
        "dataset_id": "dataset",
        "episode_index": 3,
        "annotations": [
            {
                "annotator_id": "human",
                "task_completeness": {"rating": rating},
                "language_instruction": {"instruction": "Saved instruction 3", "source": source},
            }
        ],
        "provenance": {"human": ledger.model_dump(mode="json")},
    }
    path = tmp_path / "annotations" / "episodes" / "episode_000003.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(annotation), encoding="utf-8")
    reader = LocalSavedInputReader(tmp_path)
    resource = await reader.annotation("dataset", 3)
    _, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    snapshot = snapshot.model_copy(update={"annotation_revision": resource.etag})
    reference = {
        "annotation_author_id": "human",
        "annotation_revision": resource.etag,
        "snapshot_id": snapshot.snapshot_id,
    }

    if accepted:
        result = await resolve_validation_sample(reader, snapshot, reference)
        assert result["human_outcome"] == rating
        with pytest.raises(SavedInputError, match="revision"):
            await resolve_validation_sample(reader, snapshot, {**reference, "annotation_revision": "stale"})
    else:
        with pytest.raises(SavedInputError):
            await resolve_validation_sample(reader, snapshot, reference)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels,accepted",
    [
        (["SUCCESS"], True),
        (["FAILURE"], True),
        (["PARTIAL"], True),
        (["SUCCESS", "FAILURE"], False),
        (["CUSTOM"], False),
    ],
)
async def test_given_saved_labels_when_grounding_then_one_outcome_and_current_revision_are_required(
    tmp_path: Path,
    labels: list[str],
    accepted: bool,
) -> None:
    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.saved_input import (
        SavedInputError,
        label_baseline,
        resolve_validation_sample,
    )

    saved = DatasetLabelsFile(dataset_id="dataset", episodes={"3": labels})
    baseline = label_baseline(saved, 3)
    if not accepted:
        assert baseline is None
        return
    assert baseline is not None
    _, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
    snapshot = snapshot.model_copy(
        update={"annotation_author_id": None, "annotation_revision": None, "instruction_origin": "dataset"}
    )
    reference = {"snapshot_id": snapshot.snapshot_id, "labels_revision": baseline["revision"]}
    reader = LocalSavedInputReader(tmp_path)

    result = await resolve_validation_sample(reader, snapshot, reference, labels=saved)

    assert result["human_outcome"] == labels[0].lower()
    assert result["baseline_source"] == "label"
    changed = saved.model_copy(deep=True)
    changed.episodes["3"] = ["PARTIAL" if labels != ["PARTIAL"] else "SUCCESS"]
    with pytest.raises(SavedInputError, match="revision"):
        await resolve_validation_sample(reader, snapshot, reference, labels=changed)


@pytest.mark.asyncio
async def test_given_label_baseline_when_approved_then_only_grounding_label_changes_invalidate_it(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.models import DatasetLabelsFile, MachineOrigin
    from physical_ai_curation.saved_input import (
        SavedInputError,
        label_baseline,
        resolve_validation_sample,
    )

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    labels = DatasetLabelsFile(dataset_id="dataset")
    labels.apply_labels(
        3,
        ["FAILURE"],
        author_id="actor",
        machine_origin=MachineOrigin(
            run_id="prior",
            result_id="prior-result",
            run_order=1,
            source_revision="revision",
            input_revision="snapshot-3",
            config_revision="config",
        ),
    )
    await storage.save("dataset", labels, if_none_match=True)
    jobs = manager(tmp_path / "jobs")

    async def sample(
        dataset_id: str,
        episode_index: int,
        *,
        principal_scope_id: str,
        reference: dict[str, str],
        views: tuple[str, ...] = (),
    ) -> dict[str, object]:
        _, snapshot = await jobs.resolver.resolve(dataset_id, episode_index, principal_scope_id=principal_scope_id)
        saved = await storage.load_versioned(dataset_id)
        return await resolve_validation_sample(
            LocalSavedInputReader(tmp_path / "dataset"), snapshot, reference, labels=saved.value
        )

    jobs.resolver.validation_sample = sample
    reference = {"snapshot_id": "snapshot-3", "labels_revision": label_baseline(labels, 3)["revision"]}
    accepted = await jobs.submit(
        "dataset", "actor", [3], ECHO_CONFIG, mode="sample", samples={3: reference}, idempotency_key="labels"
    )
    await jobs.run_once()
    completed = await jobs.get(accepted["id"], "actor")
    assert completed["targets"][0]["sample"]["baseline_source"] == "label"
    assert completed["targets"][0]["comparison"] == "agreement"
    approval = await jobs.approve(accepted["id"], "actor")
    resource = await storage.load_versioned("dataset")
    labels = resource.value
    labels.apply_labels(1005, ["SUCCESS"], author_id="actor")
    await storage.save("dataset", labels, if_match=resource.etag)
    await jobs.submit(
        "dataset",
        "actor",
        [1005],
        ECHO_CONFIG,
        approval_id=approval["id"],
        require_approval=True,
        idempotency_key="targets",
    )
    resource = await storage.load_versioned("dataset")
    labels = resource.value
    labels.apply_labels(3, ["SUCCESS"], author_id="actor")
    await storage.save("dataset", labels, if_match=resource.etag)

    with pytest.raises(SavedInputError, match="revision"):
        await jobs.submit(
            "dataset",
            "actor",
            [1005],
            ECHO_CONFIG,
            approval_id=approval["id"],
            require_approval=True,
            idempotency_key="stale-label",
        )


@pytest.mark.asyncio
async def test_given_task_findings_when_executed_then_schema_and_human_ownership_remain_distinct(
    tmp_path: Path,
) -> None:
    from physical_ai_curation.adapters.local_storage import LocalCurationStorage
    from physical_ai_curation.application import apply_judge_result
    from physical_ai_curation.models import DatasetLabelsFile, EpisodeAnalysisRecord

    storage = LocalCurationStorage({"dataset": tmp_path / "dataset"})
    labels = DatasetLabelsFile(dataset_id="dataset")
    labels.apply_analysis(3, EpisodeAnalysisRecord(object="human object", smoothness=0.8), author_id="actor")
    await storage.save("dataset", labels, if_none_match=True)

    async def task_infer(
        record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, object]
    ) -> dict[str, object]:
        return {
            "episode_id": record.episode_id,
            "instruction": snapshot.instruction,
            "judge_model": "synthetic-task-model",
            "prompt_version": "task-v1",
            "findings": {
                "pick_from": "table",
                "object": "machine object",
                "grasp_success": True,
                "place_success": False,
                "movement_quality": "One pause",
                "notes": "",
            },
        }

    async def apply_result(job: dict[str, object], target: dict[str, object]) -> bool:
        return await apply_judge_result(storage, job, target)

    jobs = manager(tmp_path / "jobs", result_kind="task-findings", apply_result=apply_result)
    jobs.infer = task_infer
    config = {"model_id": "synthetic-task-model", "prompt_version": "task-v1"}
    sample = await jobs.submit(
        "dataset",
        "actor",
        [3],
        config,
        idempotency_key="sample",
        mode="sample",
        samples={
            3: {"annotation_author_id": "human", "annotation_revision": "annotation", "snapshot_id": "snapshot-3"},
        },
    )
    assert not await manager(tmp_path / "jobs").run_once()
    await jobs.run_once()
    assert (await jobs.get(sample["id"], "actor"))["targets"][0]["comparison"] == "inconclusive"
    with pytest.raises(ValueError, match="acknowledgment"):
        await jobs.approve(sample["id"], "actor")
    approval = await jobs.approve(sample["id"], "actor", acknowledge_exceptions=True)
    job = await jobs.submit(
        "dataset", "actor", [3], config, idempotency_key="task", mode="judge-and-label", approval_id=approval["id"]
    )
    await jobs.run_once()
    await jobs.run_once()
    assert (await jobs.get(job["id"], "actor"))["applied"] == 1
    saved = (await storage.load_versioned("dataset")).value
    assert saved.analysis["3"].object == "human object"
    assert saved.analysis["3"].smoothness == 0.8
    assert saved.analysis["3"].grasp_success is True
    assert saved.episodes == {}
    evidence = (await jobs.results("dataset", "actor", 3, snapshot_id="snapshot-3"))[0]
    assert evidence["result_kind"] == "task-findings"
    assert "outcome_success" not in evidence["result"]


@pytest.mark.asyncio
async def test_given_cli_lifecycle_when_detached_and_resumed_then_work_and_exit_status_are_durable(
    tmp_path: Path,
) -> None:
    import argparse

    from physical_ai_curation.job_cli import add_job_arguments, execute_job_command

    parser = argparse.ArgumentParser()
    add_job_arguments(parser)
    jobs = manager(tmp_path)
    args = parser.parse_args(["--single", "--detach", "--request-id", "cli-request"])
    code, accepted = await execute_job_command(
        jobs, args, dataset_id="dataset", actor="actor", config=ECHO_CONFIG, indices=[3]
    )
    assert code == 0
    assert accepted["status"] == "queued"
    assert accepted["judged"] == 0
    await manager(tmp_path).run_once()
    status_args = parser.parse_args(["--operation", "status", "--job-id", accepted["id"]])
    code, status = await execute_job_command(jobs, status_args, dataset_id="dataset", actor="actor", config=ECHO_CONFIG)
    assert code == 0
    assert status["status"] == "succeeded"
    assert status["targets"][0]["result"]["outcome_success"] is False
    with pytest.raises(ValueError, match="one"):
        await execute_job_command(
            jobs, args, dataset_id="dataset", actor="actor", config=ECHO_CONFIG, indices=[3, 1005]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["partial", "failed", "cancelled"])
async def test_given_cli_terminal_errors_when_reading_status_then_exit_is_nonzero(
    tmp_path: Path, terminal: str
) -> None:
    import argparse

    from physical_ai_curation.job_cli import add_job_arguments, execute_job_command

    parser = argparse.ArgumentParser()
    add_job_arguments(parser)
    jobs = manager(tmp_path)
    job = await jobs.submit("dataset", "actor", [3, 1005], ECHO_CONFIG, idempotency_key="request")
    if terminal == "cancelled":
        await jobs.cancel(job["id"], "actor")
    else:
        first = await jobs.claim()
        if terminal == "partial":
            record, snapshot = await Resolver().resolve("dataset", 3, principal_scope_id="actor")
            await jobs.publish(first, result=await infer(record, snapshot, {}))
        else:
            await jobs.publish(first, error="SyntheticError")
        await jobs.publish(await jobs.claim(), error="SyntheticError")
    args = parser.parse_args(["--operation", "status", "--job-id", job["id"]])
    code, status = await execute_job_command(jobs, args, dataset_id="dataset", actor="actor", config=ECHO_CONFIG)
    assert code != 0
    assert status["status"] == terminal
