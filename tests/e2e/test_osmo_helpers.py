"""Infrastructure-free unit tests for the shared OSMO e2e helpers in ``tests/e2e/_osmo.py``.

These exercise the node-disruption restart state machine (``_await_osmo_status_with_restarts`` and
the ``wait_until_osmo_*`` wrappers) plus the command-failure guards by patching the status and
command seams. They give the branch-new restart logic fast regression coverage without a live
OSMO backend, complementing the ``@pytest.mark.e2e`` lifecycle tests that run it end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.e2e import _osmo
from tests.e2e._common import delete_blob_prefix
from tests.e2e._osmo import (
    _OSMO_MAX_NODE_DISRUPTION_RESTARTS,
    OSMOWorkflow,
    cancel_osmo_workflow,
    wait_until_osmo_completed,
    wait_until_osmo_started,
)


def _make_workflow() -> OSMOWorkflow:
    return OSMOWorkflow(
        workflow_id="workflow-1",
        workflow_name="workflow",
        experiment_name="experiment",
        correlation_id="correlation",
    )


def _script_statuses(monkeypatch: pytest.MonkeyPatch, statuses: list[str]) -> None:
    values = iter(statuses)
    monkeypatch.setattr(_osmo, "_current_osmo_status", lambda workflow, repo_root: next(values))


@pytest.fixture
def restart_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    # Stub the live `osmo workflow restart` and its settle poll so the await loop's disruption
    # classification and per-phase budget can be exercised without infrastructure; count restarts.
    calls = [0]

    def _record_restart(workflow: OSMOWorkflow, repo_root: Path) -> None:
        calls[0] += 1

    monkeypatch.setattr(_osmo, "_restart_osmo_workflow", _record_restart)
    monkeypatch.setattr(_osmo, "_wait_until_osmo_restarted", lambda workflow, repo_root, *, poll_interval_seconds: None)
    return calls


def test_delete_blob_prefix_raises_on_failed_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run_command(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="cleanup out", stderr="cleanup err")

    monkeypatch.setattr("tests.e2e._common.run_command", fake_run_command)

    with pytest.raises(AssertionError, match="Failed to delete staged data"):
        delete_blob_prefix(tmp_path, "account", "container", "prefix", description="staged data")


def test_cancel_osmo_workflow_raises_on_failed_cancel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run_command(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="cancel out", stderr="cancel err")

    monkeypatch.setattr("tests.e2e._osmo.run_command", fake_run_command)
    workflow = _make_workflow()

    with pytest.raises(AssertionError, match="Failed to cancel OSMO workflow"):
        cancel_osmo_workflow(workflow, tmp_path)


def test_wait_until_started_restarts_after_startup_disruption(
    monkeypatch: pytest.MonkeyPatch, restart_calls: list[int]
) -> None:
    # A node disruption before the workflow reaches RUNNING is recovered by an in-place restart.
    _script_statuses(monkeypatch, ["FAILED_PREEMPTED", "RUNNING"])
    workflow = _make_workflow()

    wait_until_osmo_started(workflow, Path("."), timeout_minutes=1, poll_interval_seconds=0)

    assert restart_calls[0] == 1
    assert workflow.is_terminal is False


def test_wait_until_started_exhausts_restart_budget(monkeypatch: pytest.MonkeyPatch, restart_calls: list[int]) -> None:
    # A workflow that never schedules exhausts the per-phase budget and fails instead of hanging.
    _script_statuses(monkeypatch, ["FAILED_PREEMPTED"] * (_OSMO_MAX_NODE_DISRUPTION_RESTARTS + 1))
    workflow = _make_workflow()

    with pytest.raises(AssertionError, match="kept failing with node-disruption status"):
        wait_until_osmo_started(workflow, Path("."), timeout_minutes=1, poll_interval_seconds=0)

    assert restart_calls[0] == _OSMO_MAX_NODE_DISRUPTION_RESTARTS
    assert workflow.is_terminal is True


def test_wait_until_completed_marks_terminal_on_success(
    monkeypatch: pytest.MonkeyPatch, restart_calls: list[int]
) -> None:
    _script_statuses(monkeypatch, ["SUCCEEDED"])
    workflow = _make_workflow()

    wait_until_osmo_completed(workflow, Path("."), timeout_minutes=1, poll_interval_seconds=0)

    assert restart_calls[0] == 0
    assert workflow.is_terminal is True
    assert workflow.terminal_status == "SUCCEEDED"


def test_wait_until_completed_raises_on_application_failure(
    monkeypatch: pytest.MonkeyPatch, restart_calls: list[int]
) -> None:
    # A non-disruption FAILED status is a real defect: fail fast and mark terminal, no restart.
    _script_statuses(monkeypatch, ["FAILED"])
    workflow = _make_workflow()

    with pytest.raises(AssertionError, match="failed with status 'FAILED'"):
        wait_until_osmo_completed(workflow, Path("."), timeout_minutes=1, poll_interval_seconds=0)

    assert restart_calls[0] == 0
    assert workflow.is_terminal is True


def test_wait_until_completed_restarts_after_disruption(
    monkeypatch: pytest.MonkeyPatch, restart_calls: list[int]
) -> None:
    _script_statuses(monkeypatch, ["FAILED_EVICTED", "COMPLETED"])
    workflow = _make_workflow()

    wait_until_osmo_completed(workflow, Path("."), timeout_minutes=1, poll_interval_seconds=0)

    assert restart_calls[0] == 1
    assert workflow.is_terminal is True
    assert workflow.terminal_status == "COMPLETED"


def test_restart_settle_fails_fast_on_application_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # A genuine failure surfacing during the restart-settle window fails fast and marks terminal
    # instead of polling until the settle timeout expires.
    _script_statuses(monkeypatch, ["FAILED"])
    workflow = _make_workflow()

    with pytest.raises(AssertionError, match="failed with status 'FAILED'"):
        _osmo._wait_until_osmo_restarted(workflow, Path("."), poll_interval_seconds=0)

    assert workflow.is_terminal is True


def test_restart_settle_tolerates_stale_disruption_status(monkeypatch: pytest.MonkeyPatch) -> None:
    # The restart query briefly still reports the prior node-disruption terminal; the settle wait
    # must poll past it to an alive status rather than treating it as a failure, which would spin
    # the outer loop into a tight restart cycle.
    _script_statuses(monkeypatch, ["FAILED_PREEMPTED", "RUNNING"])
    workflow = _make_workflow()

    _osmo._wait_until_osmo_restarted(workflow, Path("."), poll_interval_seconds=0)

    assert workflow.is_terminal is False
