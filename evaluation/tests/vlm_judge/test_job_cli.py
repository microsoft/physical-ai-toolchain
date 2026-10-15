"""Durable CLI lifecycle contracts using real local storage and echo inference."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from physical_ai_curation.adapters.evaluator import ServiceExecutor
from physical_ai_curation.adapters.local_storage import LocalCurationStorage
from vlm_judge.job_cli import add_job_arguments, execute_job_command, job_exit_code, read_sample_references
from vlm_judge.job_storage import LocalJobStore
from vlm_judge.jobs import JudgeJobs
from vlm_judge.saved_input import LocalDatasetResolver
from vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig


@pytest.fixture
def lifecycle(jsonl_dataset: Path, tmp_path: Path) -> tuple[JudgeJobs, dict[str, Any]]:
    roots = {"dataset": jsonl_dataset}
    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=3)))
    executor = ServiceExecutor(service)
    jobs = JudgeJobs(
        LocalJobStore(tmp_path / "jobs"),
        LocalDatasetResolver(roots),
        executor,
        capacity_scope="synthetic-cpu",
        curation_storage=LocalCurationStorage(roots),
    )
    return jobs, executor.configuration({})


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


def test_given_real_episode_when_submitting_then_persists_completed_job_and_current_evidence(
    lifecycle: tuple[JudgeJobs, dict[str, Any]], jsonl_dataset: Path
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

    path = jsonl_dataset / "meta/info.json"
    info = json.loads(path.read_text())
    info["robot_type"] = "changed-source"
    path.write_text(json.dumps(info))
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
