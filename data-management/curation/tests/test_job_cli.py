"""Durable CLI lifecycle contracts using local storage and deterministic evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
from physical_ai_curation.adapters.local_storage import LocalCurationStorage
from physical_ai_curation.job_cli import add_job_arguments, execute_job_command, job_exit_code, read_sample_references
from physical_ai_curation.jobs import JudgeJobs
from physical_ai_curation.saved_input import SavedInputSnapshot
from physical_ai_dataset_io.lerobot import EpisodeRecord


@pytest.fixture
def jsonl_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "videos/front").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "fps": 12,
        "total_episodes": 2,
        "data_path": "data/{episode_index}.jsonl",
        "features": {"front": {"dtype": "video", "videos_path": "videos/front/{episode_index}.mp4"}},
    }
    (root / "meta/info.json").write_text(json.dumps(info))
    rows = [{"episode_index": index, "length": 12, "tasks": ["Pick the block"]} for index in (0, 1)]
    (root / "meta/episodes.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    for index in (0, 1):
        (root / f"videos/front/{index}.mp4").write_bytes(b"synthetic-media")
    return root


@pytest.fixture
def lifecycle(jsonl_dataset: Path, tmp_path: Path) -> tuple[JudgeJobs, dict[str, Any]]:
    roots = {"dataset": jsonl_dataset}

    async def infer(record: EpisodeRecord, snapshot: SavedInputSnapshot, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "episode_id": record.episode_id,
            "instruction": snapshot.instruction,
            "judge_model": config["execution"]["model_id"],
            "prompt_version": config["prompt_version"],
            "n_frames": 3,
            "outcome_success": True,
            "outcome_confidence": 1.0,
            "outcome_n_valid_votes": 1,
            "progress_per_frame": [0, 50, 100],
            "voc": 1.0,
        }

    jobs = JudgeJobs(
        LocalJobStore(tmp_path / "jobs"),
        LocalDatasetResolver(roots),
        infer,
        capacity_scope="synthetic-cpu",
        curation_storage=LocalCurationStorage(roots),
    )
    return jobs, {"execution": {"backend": "echo", "model_id": "echo"}, "prompt_version": "test"}


def _command(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], arguments: list[str], indices: list[int] | None = None
) -> tuple[int, dict[str, Any]]:
    parser = argparse.ArgumentParser()
    add_job_arguments(parser)
    args = parser.parse_args(arguments)
    jobs, config = lifecycle
    return asyncio.run(
        execute_job_command(jobs, args, dataset_id="dataset", actor="local-test", config=config, indices=indices)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"actor": ""},
        {"dataset_id": ""},
        {"idempotency_key": ""},
        {"idempotency_key": "x" * 201},
        {"episode_indices": []},
        {"episode_indices": [-1]},
        {"episode_indices": [True]},
        {"mode": "unsupported"},
        {"mode": "sample"},
        {"samples": {0: {}}},
        {"mode": "judge-and-label"},
        {"require_approval": True},
        {"expected_snapshots": {1: "foreign"}},
    ],
)
def test_given_invalid_durable_judge_submission_when_accepted_then_no_job_is_persisted(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], changes: dict[str, Any]
) -> None:
    jobs, config = lifecycle
    arguments = {
        "dataset_id": "dataset",
        "actor": "local-test",
        "episode_indices": [0],
        "config": config,
        "idempotency_key": "invalid-case",
        **changes,
    }

    with pytest.raises(ValueError):
        asyncio.run(jobs.submit(**arguments))

    assert asyncio.run(jobs.store.read())["jobs"] == {}


@pytest.mark.parametrize("outcome", ["live", "expired", "released"])
def test_given_durable_judge_claim_when_heartbeat_runs_then_only_live_execution_is_renewed(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], mocker: Any, outcome: str
) -> None:
    jobs, _ = lifecycle
    clock = mocker.patch.object(jobs, "clock", return_value=1000.0)
    _command(lifecycle, ["--single", "--detach"], [0])
    claim = asyncio.run(jobs.claim())
    assert claim is not None
    if outcome == "released":
        asyncio.run(jobs.release(claim))
    clock.return_value = 1000.0 + jobs.lease_seconds + 1 if outcome == "expired" else 1001.0

    assert asyncio.run(jobs.heartbeat(claim)) == (outcome == "live")

    state = asyncio.run(jobs.store.read())
    if outcome == "live":
        job = state["jobs"][claim["job_id"]]
        assert job["targets"][0]["expires_at"] == clock.return_value + jobs.lease_seconds
    elif outcome == "released":
        assert state["executions"] == {}


def test_given_empty_durable_judge_queue_when_claimed_then_no_execution_is_created(
    lifecycle: tuple[JudgeJobs, dict[str, Any]],
) -> None:
    jobs, _ = lifecycle

    assert asyncio.run(jobs.claim()) is None

    assert asyncio.run(jobs.store.read())["jobs"] == {}


@pytest.mark.parametrize(
    "changes",
    [{"capacity": 0}, {"capacity": 17}, {"lease_seconds": 4}, {"lease_seconds": 601}, {"result_kind": "unknown"}],
)
def test_given_invalid_durable_worker_limits_when_configured_then_refused(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], changes: dict[str, object]
) -> None:
    jobs, _config = lifecycle

    with pytest.raises(ValueError, match="Invalid judge"):
        JudgeJobs(jobs.store, jobs.resolver, jobs.infer, capacity_scope="synthetic-cpu", **changes)


@pytest.mark.parametrize("operation", ["resolve", "episode_indices"])
def test_given_unconfigured_dataset_when_resolved_then_saved_inputs_are_unavailable(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], operation: str
) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    jobs, _config = lifecycle
    arguments = ("unconfigured", 0) if operation == "resolve" else ("unconfigured",)

    with pytest.raises(SavedInputError) as caught:
        asyncio.run(getattr(jobs.resolver, operation)(*arguments, principal_scope_id="local-test"))

    assert caught.value.status_code == 404


def test_given_non_directory_media_root_when_validator_built_then_source_is_unavailable(tmp_path: Path) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    root = tmp_path / "not-a-dataset"
    root.write_bytes(b"not a directory")
    resolver = LocalDatasetResolver({"dataset": root})

    with pytest.raises(SavedInputError) as caught:
        resolver._video_validator("dataset")

    assert caught.value.status_code == 503


def test_given_foreign_video_path_when_local_validator_runs_then_media_is_refused(
    jsonl_dataset: Path, tmp_path: Path
) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    foreign = tmp_path / "foreign.mp4"
    foreign.write_bytes(b"foreign video")
    validate = LocalDatasetResolver({"dataset": jsonl_dataset})._video_validator("dataset")

    with pytest.raises(SavedInputError) as caught:
        validate(foreign)

    assert caught.value.status_code == 400
    assert foreign.read_bytes() == b"foreign video"


def test_given_unknown_camera_when_local_episode_resolved_then_no_snapshot_is_accepted(
    lifecycle: tuple[JudgeJobs, dict[str, Any]],
) -> None:
    from physical_ai_curation.saved_input import SavedInputError

    jobs, _config = lifecycle

    with pytest.raises(SavedInputError) as caught:
        asyncio.run(jobs.resolver.resolve("dataset", 0, principal_scope_id="local-test", views=("unavailable-camera",)))

    assert caught.value.status_code == 422


def test_given_media_changing_between_resolution_reads_when_snapshot_built_then_no_stale_identity_is_returned(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], mocker: Any, jsonl_dataset: Path
) -> None:
    from physical_ai_curation.saved_input import SavedInputError
    from physical_ai_dataset_io import media

    jobs, _config = lifecycle
    identity = media.local_media_identity
    path = jsonl_dataset / "videos/front/0.mp4"
    before = path.read_bytes()
    observations = []

    def changing_identity(candidate: Path) -> object:
        observed = identity(candidate)
        observations.append(observed)
        if len(observations) == 1:
            candidate.write_bytes(candidate.read_bytes() + b"changed during read")
        return observed

    mocker.patch.object(media, "local_media_identity", side_effect=changing_identity)

    with pytest.raises(SavedInputError, match="Media changed"):
        asyncio.run(jobs.resolver.resolve("dataset", 0, principal_scope_id="local-test"))

    assert len(observations) == 2
    assert observations[0] != observations[1]
    assert path.read_bytes() != before


def test_given_foreign_labels_identity_when_local_curation_loaded_then_dataset_binding_is_refused(
    jsonl_dataset: Path,
) -> None:
    from physical_ai_curation.models import DatasetLabelsFile

    storage = LocalCurationStorage({"dataset": jsonl_dataset})
    path = jsonl_dataset / "meta/episode_labels.json"
    path.write_text(DatasetLabelsFile(dataset_id="foreign-dataset").model_dump_json())
    before = path.read_bytes()

    with pytest.raises(ValueError, match="dataset identity mismatch"):
        asyncio.run(storage.load_versioned("dataset"))

    assert path.read_bytes() == before


@pytest.mark.parametrize("reason", ["queued", "succeeded", "withdrawn"])
def test_given_nonretryable_durable_job_when_retried_then_history_is_unchanged(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], reason: str
) -> None:
    jobs, _config = lifecycle
    _code, accepted = _command(lifecycle, ["--single", *(["--detach"] if reason != "succeeded" else [])], [0])
    if reason == "withdrawn":
        preview = asyncio.run(jobs.preview_reset("dataset", "local-test"))
        asyncio.run(jobs.confirm_reset("dataset", "local-test", preview["id"]))
    before = asyncio.run(jobs.store.read())

    with pytest.raises(ValueError, match=r"retried|retry"):
        asyncio.run(jobs.retry(accepted["id"], "local-test"))

    assert asyncio.run(jobs.store.read()) == before


@pytest.mark.parametrize(
    "reason", ["no_storage", "unknown", "foreign_principal", "foreign_dataset", "generation", "scope", "running"]
)
def test_given_changed_withdrawal_scope_when_confirmed_then_no_new_reset_is_persisted(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], reason: str
) -> None:
    jobs, _config = lifecycle
    _code, _accepted = _command(lifecycle, ["--single", "--detach"], [0])
    preview = asyncio.run(jobs.preview_reset("dataset", "local-test"))
    if reason == "no_storage":
        jobs.curation_storage = None
    elif reason == "scope":
        _command(lifecycle, ["--single", "--detach", "--request-id", "new-run"], [1])
    elif reason == "generation":

        async def change_generation() -> None:
            async with jobs.store.transaction() as state:
                state["generations"]["dataset"] = preview["generation"] + 1

        asyncio.run(change_generation())
    elif reason == "running":
        other = asyncio.run(jobs.preview_reset("dataset", "local-test"))
        asyncio.run(jobs.confirm_reset("dataset", "local-test", other["id"]))
    before = asyncio.run(jobs.store.read())
    error = (
        KeyError
        if reason in {"unknown", "foreign_dataset"}
        else PermissionError
        if reason == "foreign_principal"
        else ValueError
    )

    with pytest.raises(error):
        asyncio.run(
            jobs.confirm_reset(
                "foreign-dataset" if reason == "foreign_dataset" else "dataset",
                "different-principal" if reason == "foreign_principal" else "local-test",
                "unknown" if reason == "unknown" else preview["id"],
            )
        )

    assert asyncio.run(jobs.store.read()) == before


@pytest.mark.parametrize("reason", ["no_storage", "running"])
def test_given_unavailable_withdrawal_when_previewed_then_no_preview_is_written(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], reason: str
) -> None:
    jobs, _config = lifecycle
    if reason == "no_storage":
        jobs.curation_storage = None
    else:
        preview = asyncio.run(jobs.preview_reset("dataset", "local-test"))
        asyncio.run(jobs.confirm_reset("dataset", "local-test", preview["id"]))
    before = asyncio.run(jobs.store.read())

    with pytest.raises(ValueError, match=r"unavailable|running"):
        asyncio.run(jobs.preview_reset("dataset", "local-test"))

    assert asyncio.run(jobs.store.read()) == before


@pytest.mark.parametrize(
    "reason", ["no_adapter", "sample", "cancelled", "withdrawn", "empty", "unknown_episode", "unfinished"]
)
def test_given_ineligible_saved_results_when_application_requested_then_no_target_is_queued(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], mocker: Any, reason: str
) -> None:
    jobs, _config = lifecycle
    _code, accepted = _command(lifecycle, ["--single", *(["--detach"] if reason == "unfinished" else [])], [0])
    apply = mocker.AsyncMock()
    if reason != "no_adapter":
        jobs.apply_result = apply
    if reason == "cancelled":
        _other_code, accepted = _command(lifecycle, ["--single", "--detach", "--request-id", "cancel-target"], [1])
        asyncio.run(jobs.cancel(accepted["id"], "local-test"))
    elif reason in {"sample", "withdrawn"}:

        async def restore_state() -> None:
            async with jobs.store.transaction() as state:
                if reason == "sample":
                    state["jobs"][accepted["id"]]["mode"] = "sample"
                else:
                    state["generations"]["dataset"] = 1

        asyncio.run(restore_state())
    before = asyncio.run(jobs.store.read())

    with pytest.raises(ValueError):
        asyncio.run(
            jobs.request_application(
                accepted["id"], "local-test", [] if reason == "empty" else [99] if reason == "unknown_episode" else None
            )
        )

    apply.assert_not_called()
    assert asyncio.run(jobs.store.read()) == before


@pytest.mark.parametrize("missing", [False, True])
def test_given_missing_or_changed_validation_sample_when_approval_checked_then_refused(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], mocker: Any, missing: bool
) -> None:
    jobs, config = lifecycle
    sample = None if missing else {"reference": {"snapshot_id": "old"}, "input": "old"}
    job = {
        "dataset_id": "dataset",
        "actor": "local-test",
        "config": config,
        "targets": [{"episode_index": 0, "sample": sample}],
    }
    mocker.patch.object(jobs.resolver, "validation_sample", new=mocker.AsyncMock(return_value={"changed": True}))

    with pytest.raises(ValueError, match="sample evidence"):
        asyncio.run(jobs._validate_samples(job))


@pytest.mark.parametrize("operation", ["list", "approvals"])
@pytest.mark.parametrize(("offset", "limit"), [(-1, 25), (0, 0), (0, 101)])
def test_given_invalid_durable_page_when_requested_then_state_is_unchanged(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], operation: str, offset: int, limit: int
) -> None:
    jobs, _config = lifecycle
    before = asyncio.run(jobs.store.read())

    with pytest.raises(ValueError, match="page"):
        asyncio.run(getattr(jobs, operation)("dataset", "local-test", offset=offset, limit=limit))

    assert asyncio.run(jobs.store.read()) == before


@pytest.mark.parametrize(
    "damage", ["judge_mode", "incomplete", "no_targets", "missing_comparison", "unacknowledged_disagreement"]
)
def test_given_ineligible_sample_approval_when_requested_then_no_approval_is_persisted(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], damage: str
) -> None:
    jobs, _config = lifecycle
    _code, accepted = _command(lifecycle, ["--single"], [0])

    async def restore_sample() -> None:
        async with jobs.store.transaction() as state:
            job = state["jobs"][accepted["id"]]
            if damage != "judge_mode":
                job["mode"] = "sample"
            if damage == "incomplete":
                job["status"] = "running"
            elif damage == "no_targets":
                job["targets"] = []
            elif damage == "unacknowledged_disagreement":
                job["targets"][0]["comparison"] = "disagreement"

    asyncio.run(restore_sample())
    before = asyncio.run(jobs.store.read())

    with pytest.raises(ValueError, match=r"Approval|acknowledgment"):
        asyncio.run(jobs.approve(accepted["id"], "local-test"))

    assert asyncio.run(jobs.store.read()) == before
    assert before["approvals"] == {}


@pytest.mark.parametrize("method", ["get", "approve"])
def test_given_foreign_durable_job_when_requested_then_private_state_is_not_exposed(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], method: str
) -> None:
    jobs, _config = lifecycle
    _code, accepted = _command(lifecycle, ["--single", "--detach"], [0])
    before = asyncio.run(jobs.store.read())

    with pytest.raises(PermissionError, match="another principal"):
        asyncio.run(getattr(jobs, method)(accepted["id"], "different-principal"))

    assert asyncio.run(jobs.store.read()) == before


@pytest.mark.parametrize(
    ("arguments", "indices", "message"),
    [
        (["--offset", "-1"], None, "Invalid result page"),
        (["--page-size", "0"], None, "Invalid result page"),
        (["--page-size", "101"], None, "Invalid result page"),
        (["--operation", "status"], None, "requires --job-id"),
        (["--operation", "approve"], None, "requires --job-id"),
        (["--operation", "apply"], None, "requires --job-id"),
        (["--operation", "results"], None, "requires --episode-index"),
        (["--operation", "results", "--episode-index", "-1"], None, "requires --episode-index"),
        (["--operation", "reset-confirm"], None, "requires --preview-id"),
        (["--single"], [], "exactly one episode"),
        (["--single"], [0, 1], "exactly one episode"),
        (["--single", "--mode", "sample"], [0], "exactly one episode"),
    ],
)
def test_given_invalid_lifecycle_arguments_when_executing_then_rejects_before_work(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], arguments: list[str], indices: list[int] | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _command(lifecycle, arguments, indices)


@pytest.mark.parametrize("changed_resource", ["metadata", "video"])
def test_given_real_episode_when_submitting_then_persists_completed_job_and_current_evidence(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], jsonl_dataset: Path, changed_resource: str
) -> None:
    code, accepted = _command(lifecycle, ["--single", "--request-id", "single-episode"], [0])

    assert code == 0
    assert accepted["status"] == "succeeded"
    assert accepted["judged"] == 1
    code, status = _command(lifecycle, ["--operation", "status", "--job-id", accepted["id"], "--offset", "1"])
    assert code == 0 and status["targets"] == []
    _, listing = _command(lifecycle, ["--operation", "list"])
    assert listing["total"] == 1
    _, approvals = _command(lifecycle, ["--operation", "approvals"])
    assert approvals["total"] == 0
    _, evidence = _command(lifecycle, ["--operation", "results", "--episode-index", "0"])
    assert evidence["total"] == 1
    assert evidence["items"][0]["applicability"] == "current"

    if changed_resource == "metadata":
        path = jsonl_dataset / "meta/info.json"
        info = json.loads(path.read_text())
        info["robot_type"] = "changed-source"
        path.write_text(json.dumps(info))
    else:
        record, _ = asyncio.run(lifecycle[0].resolver.resolve("dataset", 0, principal_scope_id="local-test"))
        path = next(iter(record.video_paths.values()))
        path.write_bytes(path.read_bytes() + b"changed-media")
    _, stale = _command(lifecycle, ["--operation", "results", "--episode-index", "0"])
    assert stale["items"][0]["applicability"] == "stale"
    _, paged = _command(lifecycle, ["--operation", "results", "--episode-index", "0", "--offset", "1"])
    assert paged == {"items": [], "total": 1}


def test_given_detached_job_when_cancelling_and_retrying_then_preserves_lifecycle_state(
    lifecycle: tuple[JudgeJobs, dict[str, Any]],
) -> None:
    code, accepted = _command(lifecycle, ["--single", "--detach"], [0])

    assert code == 0 and accepted["status"] == "queued"
    assert "targets" not in accepted
    code, cancelled = _command(lifecycle, ["--operation", "cancel", "--job-id", accepted["id"]])
    assert code == 130 and cancelled["status"] == "cancelled"
    code, retried = _command(lifecycle, ["--operation", "retry", "--job-id", accepted["id"]])
    assert code == 0 and retried["status"] == "succeeded"


def test_given_completed_job_when_withdrawing_then_confirms_and_reports_durable_reset(
    lifecycle: tuple[JudgeJobs, dict[str, Any]],
) -> None:
    _command(lifecycle, ["--single"], [0])
    _, preview = _command(lifecycle, ["--operation", "reset-preview"])

    code, confirmed = _command(lifecycle, ["--operation", "reset-confirm", "--preview-id", preview["id"]])

    assert code == 0 and confirmed["status"] == "succeeded"
    _, status = _command(lifecycle, ["--operation", "reset-status"])
    assert status["id"] == confirmed["id"]
    code, retry = _command(lifecycle, ["--operation", "reset-retry"])
    assert code == 0 and retry["id"] == confirmed["id"]
    _, evidence = _command(lifecycle, ["--operation", "results", "--episode-index", "0"])
    assert evidence["items"][0]["applicability"] == "withdrawn"


@pytest.mark.parametrize("status", ["partial", "failed", "conflicted", "cancelled", "succeeded"])
def test_given_job_status_when_reporting_exit_then_returns_lifecycle_code(status: str) -> None:
    assert job_exit_code({"status": status}) == (130 if status == "cancelled" else 1 if status != "succeeded" else 0)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"invalid": {}},
        {"0": []},
        {"0": {"snapshot_id": "saved"}},
        {"0": {"annotation_author_id": " ", "annotation_revision": "revision", "snapshot_id": "saved"}},
        {
            key: {"annotation_author_id": "author", "annotation_revision": "revision", "snapshot_id": "saved"}
            for key in ("0", "00")
        },
    ],
)
def test_given_invalid_sample_references_when_reading_then_rejects_ambiguous_evidence(
    tmp_path: Path, payload: object
) -> None:
    path = tmp_path / "references.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        read_sample_references(path)


def test_given_explicit_sample_references_when_reading_then_preserves_identity(tmp_path: Path) -> None:
    reference = {"annotation_author_id": "author", "annotation_revision": "revision", "snapshot_id": "saved"}
    path = tmp_path / "references.json"
    path.write_text(json.dumps({"2": reference}))

    assert read_sample_references(path) == {2: reference}
    assert read_sample_references(None) == {}
