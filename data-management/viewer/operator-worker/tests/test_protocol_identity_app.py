from __future__ import annotations

import io
import json
import os
import threading
from types import SimpleNamespace

import pytest

from operator_worker.app import WorkerApplication
from operator_worker.identity import compute_profile_fingerprint, compute_simulation_resource_fingerprint
from operator_worker.protocol import MAX_COMMAND_BYTES, ProtocolError, parse_worker_command
from operator_worker.resources import CleanupReport


def _profile() -> dict[str, object]:
    profile: dict[str, object] = {"version": 1, "name": "so101"}
    profile["fingerprint"] = compute_profile_fingerprint(profile)
    return profile


def _command(command_type: str, sequence: int, **extra: object) -> dict[str, object]:
    command: dict[str, object] = {
        "protocol_version": 2,
        "type": command_type,
        "service_instance_id": "service-1",
        "session_id": "session-1",
        "sequence": sequence,
        "command_id": f"{command_type}-{sequence}",
        "correlation_id": f"correlation-{sequence}",
        "worker_pid": os.getpid(),
    }
    command.update(extra)
    return command


class FakeRuntime:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.started = threading.Event()

    def acquire(self) -> None:
        self.events.append("acquire")

    def enable_motion(self) -> None:
        self.events.append("enable_motion")

    def teleoperate(self) -> None:
        self.events.append("teleoperate")
        self.started.set()
        self.started.wait(0.05)

    def record(self) -> None:
        self.events.append("record")

    def policy(self) -> None:
        self.events.append("policy")

    def command(self, action: str) -> SimpleNamespace:
        return SimpleNamespace(dataset_id="demo", episode_index=1, phase="finalized", should_stop=True)

    def request_stop(self) -> None:
        self.events.append("request_stop")

    def cleanup(self) -> CleanupReport:
        self.events.append("cleanup")
        return CleanupReport(True, ("arm",), (), torque_verified_off=True)


def _run_app(commands: list[dict[str, object]], runtime: FakeRuntime) -> tuple[int, list[dict[str, object]]]:
    stream = io.StringIO("".join(json.dumps(command) + "\n" for command in commands))
    output = io.StringIO()
    app = WorkerApplication(
        session_id="session-1",
        input_stream=stream,
        output_stream=output,
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )
    result = app.run()
    return result, [json.loads(line) for line in output.getvalue().splitlines()]


def test_given_oversized_or_malformed_command_when_parsed_then_fails_closed() -> None:
    with pytest.raises(ProtocolError):
        parse_worker_command(b"{" + b" " * MAX_COMMAND_BYTES + b"}")

    with pytest.raises(ProtocolError):
        parse_worker_command("{}\n{}")


def test_given_identity_matched_sequence_when_dispatched_then_cleanup_is_correlated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    commands = [
        _command(
            "initialize",
            1,
            startup_nonce="0123456789abcdef",
            profile=profile,
            profile_fingerprint=profile["fingerprint"],
            resource_fingerprint="resource",
            settings={"mode": "teleoperate", "control_fps": 30},
        ),
        _command("stop", 2, command_id="stop-1"),
    ]
    runtime = FakeRuntime()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")

    result, events = _run_app(commands, runtime)

    assert result == 0
    assert [event["type"] for event in events] == ["hello", "initialized", "cleanup"]
    assert events[0]["session_id"] == "session-1"
    assert events[0]["pid"] > 0
    assert events[-1]["command_id"] == "stop-1"
    assert events[-1]["correlation_id"] == "correlation-2"
    assert events[-1]["torque_verified_off"] is True


@pytest.mark.parametrize(
    "replacement",
    [
        {"session_id": "wrong-session"},
        {"service_instance_id": "wrong-service"},
        {"worker_pid": 999_999},
        {"sequence": 1},
    ],
)
def test_given_mismatched_or_duplicate_command_when_dispatched_then_motion_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    replacement: dict[str, object],
) -> None:
    profile = _profile()
    initialize = _command(
        "initialize",
        1,
        startup_nonce="0123456789abcdef",
        profile=profile,
        profile_fingerprint=profile["fingerprint"],
        resource_fingerprint="resource",
        settings={"mode": "teleoperate", "control_fps": 30},
    )
    run = _command("run", 2)
    run.update(replacement)
    runtime = FakeRuntime()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")

    with pytest.raises(RuntimeError):
        _run_app([initialize, run], runtime)

    assert "enable_motion" not in runtime.events
    assert runtime.events[-1] == "cleanup"


def test_given_unconfirmed_cleanup_when_run_finishes_then_restart_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnconfirmedRuntime(FakeRuntime):
        def cleanup(self) -> CleanupReport:
            return CleanupReport(False, (), ("torque state unknown",), torque_verified_off=False)

    profile = _profile()
    commands = [
        _command(
            "initialize",
            1,
            startup_nonce="0123456789abcdef",
            profile=profile,
            profile_fingerprint=profile["fingerprint"],
            resource_fingerprint="resource",
            settings={"mode": "teleoperate", "control_fps": 30},
        ),
        _command("stop", 2, command_id="stop-1"),
    ]
    stream = io.StringIO("".join(json.dumps(command) + "\n" for command in commands))
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=stream,
        output_stream=io.StringIO(),
        runtime_factory=lambda _profile, _settings: UnconfirmedRuntime(),
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    assert app.run() == 1
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        app.run()


def test_given_default_simulation_when_initialized_then_hardware_identity_is_not_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    settings = {"mode": "teleoperate", "control_fps": 30}
    commands = [
        _command(
            "initialize",
            1,
            startup_nonce="0123456789abcdef",
            profile=profile,
            profile_fingerprint=profile["fingerprint"],
            resource_fingerprint=compute_simulation_resource_fingerprint(profile, mode="teleoperate"),
            settings=settings,
        ),
        _command("stop", 2),
    ]
    stream = io.StringIO("".join(json.dumps(command) + "\n" for command in commands))
    runtime = FakeRuntime()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    monkeypatch.setattr(
        "operator_worker.app.compute_resource_fingerprint",
        lambda *_args, **_kwargs: pytest.fail("simulation must not inspect hardware"),
    )
    app = WorkerApplication(
        session_id="session-1",
        input_stream=stream,
        output_stream=io.StringIO(),
        runtime_factory=lambda _profile, _settings: runtime,
    )

    assert app.run() == 0
