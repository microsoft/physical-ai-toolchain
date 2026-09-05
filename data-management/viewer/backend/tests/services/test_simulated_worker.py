"""Behavior tests for the simulated operator worker entry point."""

from __future__ import annotations

import json
import signal
import sys
from io import StringIO

import pytest

from src.api.operator import simulated_worker
from src.api.operator.protocol import WorkerCommand


def _command(session_id: str, command_id: str, action: str) -> str:
    return WorkerCommand(
        session_id=session_id,
        command_id=command_id,
        action=action,
    ).model_dump_json()


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    behavior: str,
    commands: list[str],
) -> list[dict[str, object]]:
    monkeypatch.setenv("OPERATOR_SESSION_ID", "session-1")
    monkeypatch.setenv("OPERATOR_SESSION_MODE", "record")
    monkeypatch.setattr(sys, "argv", ["simulated-worker", "--behavior", behavior])
    monkeypatch.setattr(sys, "stdin", StringIO("\n".join(commands)))

    simulated_worker.main()

    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_normal_worker_validates_replays_and_completes_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages = _run(
        monkeypatch,
        capsys,
        behavior="normal",
        commands=[
            _command("other-session", "wrong-session", "save"),
            _command("session-1", "save-1", "save"),
            _command("session-1", "save-1", "save"),
            _command("session-1", "save-1", "rerecord"),
            _command("session-1", "finish-1", "finish"),
        ],
    )

    assert [message["type"] for message in messages] == [
        "ready",
        "error",
        "ack",
        "ack",
        "error",
        "ack",
    ]
    assert messages[1]["message"] == "worker protocol session mismatch"
    assert messages[2] == messages[3]
    assert messages[4]["message"] == "worker command_id payload conflict"
    assert messages[5]["cleanup_complete"] is True


def test_wrong_ack_version_is_emitted_for_protocol_failure_testing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages = _run(
        monkeypatch,
        capsys,
        behavior="wrong_ack_version",
        commands=[_command("session-1", "finish-1", "finish")],
    )

    assert messages[-1]["version"] == 999


@pytest.mark.parametrize("behavior", ["ignore_commands", "ignore_commands_and_sigterm"])
def test_ignore_behaviors_emit_only_readiness(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    behavior: str,
) -> None:
    signal_calls: list[tuple[signal.Signals, signal.Handlers]] = []
    monkeypatch.setattr(signal, "signal", lambda number, handler: signal_calls.append((number, handler)))

    messages = _run(
        monkeypatch,
        capsys,
        behavior=behavior,
        commands=[_command("session-1", "save-1", "save")],
    )

    assert [message["type"] for message in messages] == ["ready"]
    assert bool(signal_calls) is (behavior == "ignore_commands_and_sigterm")


@pytest.mark.parametrize(
    ("behavior", "exit_code", "emits_ready"),
    [
        ("crash_on_start", 2, False),
        ("crash_after_ready", 3, True),
    ],
)
def test_crash_behaviors_exit_at_the_declared_phase(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    behavior: str,
    exit_code: int,
    emits_ready: bool,
) -> None:
    monkeypatch.setenv("OPERATOR_SESSION_ID", "session-1")
    monkeypatch.setenv("OPERATOR_SESSION_MODE", "record")
    monkeypatch.setattr(sys, "argv", ["simulated-worker", "--behavior", behavior])
    monkeypatch.setattr(sys, "stdin", StringIO())
    monkeypatch.setattr(simulated_worker.time, "sleep", lambda _seconds: None)

    with pytest.raises(SystemExit) as error:
        simulated_worker.main()

    assert error.value.code == exit_code
    assert bool(capsys.readouterr().out) is emits_ready


def test_slow_start_waits_before_becoming_ready(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(simulated_worker.time, "sleep", delays.append)

    messages = _run(monkeypatch, capsys, behavior="slow_start", commands=[])

    assert delays == [0.2]
    assert messages[0]["type"] == "ready"


def test_invalid_command_exits_with_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERATOR_SESSION_ID", "session-1")
    monkeypatch.setenv("OPERATOR_SESSION_MODE", "record")
    monkeypatch.setattr(sys, "argv", ["simulated-worker"])
    monkeypatch.setattr(sys, "stdin", StringIO("not-json"))

    with pytest.raises(SystemExit) as error:
        simulated_worker.main()

    assert error.value.code == 4
    assert json.loads(capsys.readouterr().out)["type"] == "ready"
