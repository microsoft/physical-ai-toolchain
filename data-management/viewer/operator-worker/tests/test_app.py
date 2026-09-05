from __future__ import annotations

import io
import json
from threading import Event
from types import SimpleNamespace

import pytest

from operator_worker.acquisition import CleanupReport
from operator_worker.app import WorkerApplication
from operator_worker.config import WorkerProfile


class FakeRuntime:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.stop = Event()
        self.cleanup_calls = 0
        self.started = Event()

    def acquire(self) -> None:
        self.events.append("acquire")

    def enable_motion(self) -> None:
        self.events.append("enable_motion")

    def teleoperate(self) -> None:
        self.events.append("teleoperate")
        self.preview_callback("wrist", b"jpeg", 1.25)
        self.started.set()
        self.stop.wait(1)

    def record(self) -> None:
        self.events.append("record")
        self.started.set()
        self.stop.wait(1)

    def command(self, action: str):
        self.events.append(action)
        if action in {"finish", "cancel"}:
            self.stop.set()
        return SimpleNamespace(
            dataset_id="demo",
            episode_index=1 if action in {"save", "finish"} else 0,
            phase="finalized" if action == "finish" else "recording",
            should_stop=action in {"finish", "cancel"},
        )

    def cleanup(self) -> CleanupReport:
        self.events.append("cleanup")
        self.cleanup_calls += 1
        self.stop.set()
        return CleanupReport(
            True,
            ("follower", "leader", "front", "wrist"),
            (),
            torque_verified_off=True,
        )

    def request_stop(self) -> None:
        self.stop.set()

    def discard_recording(self) -> None:
        self.events.append("discard_recording")
        self.stop.set()

    def set_rate_callback(self, callback) -> None:
        self.rate_callback = callback

    def set_telemetry_callback(self, callback) -> None:
        self.telemetry_callback = callback

    def set_preview_callback(self, callback) -> None:
        self.preview_callback = callback

    def upload_after_cleanup(self) -> tuple[bool, bool, str | None]:
        self.events.append("upload_after_cleanup")
        return False, False, None


def _profile() -> dict:
    arm = {
        "port": "/dev/null",
        "logical_id": "arm",
        "usb_vendor_id": "1a86",
        "usb_product_id": "55d3",
        "usb_serial": "serial",
        "calibration_file": "/tmp/calibration.json",
    }
    profile = {
        "version": 1,
        "name": "so101",
        "embodiment": "SO-101",
        "actuator_names": [
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
            "gripper",
        ],
        "minimum_free_bytes": 0,
        "teleoperation_fps": 30,
        "max_relative_target": 2.0,
        "leader": {**arm, "logical_id": "leader", "usb_serial": "leader"},
        "follower": {**arm, "logical_id": "follower", "usb_serial": "follower"},
        "wrist_camera": {
            "path": "/dev/null",
            "usb_vendor_id": "05a3",
            "usb_product_id": "9230",
            "width": 640,
            "height": 480,
            "fps": 30,
        },
        "front_camera": {
            "usb_vendor_id": "8086",
            "usb_product_id": "0b5b",
            "usb_serial": "218622278289",
            "usb_descriptor_serial": "323743071153",
            "product": "Intel RealSense D405",
            "width": 640,
            "height": 480,
            "fps": 30,
        },
        "recording": {
            "fps": 30,
            "episode_time_s": 60,
            "reset_time_s": 30,
            "upload_default": False,
        },
        "fingerprint": "",
    }
    parsed = WorkerProfile.model_validate(profile)
    return parsed.model_copy(update={"fingerprint": parsed.computed_fingerprint()}).model_dump(mode="json")


def _settings() -> dict:
    return {
        "mode": "teleoperate",
        "control_fps": 60,
        "camera_fps": {"wrist": 30, "front": 30},
        "max_relative_target": None,
        "dataset_root": None,
        "dataset_id": None,
        "repo_id": None,
        "task": "Pick the orange",
        "save_destination": "local",
        "hub_repo_id": None,
        "num_episodes": 50,
        "episode_time_s": 60,
        "reset_time_s": 30,
    }


def test_worker_accepts_backend_profile_display_metadata() -> None:
    profile = _profile()

    parsed = WorkerProfile.model_validate(profile)

    assert parsed.embodiment == "SO-101"
    assert len(parsed.actuator_names) == 6


class DelayedStopStream:
    def __init__(self, immediate: list[dict], stop: dict, started: Event) -> None:
        self.immediate = immediate
        self.stop = stop
        self.started = started

    def __iter__(self):
        for command in self.immediate:
            yield json.dumps(command) + "\n"
        self.started.wait(1)
        yield json.dumps(self.stop) + "\n"


def test_initialize_run_stop_emits_one_cleanup(monkeypatch) -> None:
    runtime = FakeRuntime()
    captured_settings = []
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": {
                "mode": "teleoperate",
                "control_fps": 60,
                "camera_fps": {"wrist": 24, "front": 30},
                "max_relative_target": None,
                "dataset_root": None,
                "dataset_id": None,
                "task": "Pick the orange",
                "save_destination": "local",
                "num_episodes": 50,
                "episode_time_s": 60,
                "reset_time_s": 30,
            },
        },
        {
            "protocol_version": 2,
            "type": "run",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
        },
        {
            "protocol_version": 2,
            "type": "stop",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 3,
            "command_id": "stop-1",
        },
    ]
    input_stream = DelayedStopStream(commands[:2], commands[2], runtime.started)
    output_stream = io.StringIO()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=input_stream,
        output_stream=output_stream,
        runtime_factory=lambda _profile, settings: captured_settings.append(settings) or runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    assert app.run() == 0

    output = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert [event["type"] for event in output] == [
        "hello",
        "initialized",
        "running",
        "preview",
        "cleanup",
    ]
    assert output[3]["camera"] == "wrist"
    assert output[3]["jpeg_base64"] == "anBlZw=="
    assert output[-1]["command_id"] == "stop-1"
    assert output[-1]["torque_verified_off"] is True
    assert runtime.events == [
        "acquire",
        "enable_motion",
        "teleoperate",
        "cleanup",
        "upload_after_cleanup",
    ]
    assert runtime.cleanup_calls == 1
    assert captured_settings[0].control_fps == 60
    assert captured_settings[0].camera_fps == {"wrist": 24, "front": 30}
    assert captured_settings[0].max_relative_target is None


def test_record_mode_acknowledges_episode_commands_and_finishes(monkeypatch) -> None:
    runtime = FakeRuntime()
    settings = {
        **_settings(),
        "mode": "record",
        "control_fps": 30,
        "dataset_root": "/tmp/demo",
        "dataset_id": "demo",
    }
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": settings,
        },
        {
            "protocol_version": 2,
            "type": "run",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
        },
        {
            "protocol_version": 2,
            "type": "action",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 3,
            "command_id": "save-1",
            "action": "save",
        },
        {
            "protocol_version": 2,
            "type": "action",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 4,
            "command_id": "finish-1",
            "action": "finish",
        },
    ]
    output = io.StringIO()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO("".join(json.dumps(command) + "\n" for command in commands)),
        output_stream=output,
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    assert app.run() == 0

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["type"] for event in events] == [
        "hello",
        "initialized",
        "running",
        "command_ack",
        "command_ack",
        "cleanup",
    ]
    assert events[3]["action"] == "save"
    assert events[3]["episode_index"] == 1
    assert events[4]["phase"] == "finalized"
    assert events[-1]["command_id"] == "finish-1"
    assert runtime.cleanup_calls == 1


def test_record_stop_discards_the_session_before_cleanup(monkeypatch) -> None:
    runtime = FakeRuntime()
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": {**_settings(), "mode": "record", "dataset_root": "/tmp/demo", "dataset_id": "demo"},
        },
        {
            "protocol_version": 2,
            "type": "run",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
        },
        {
            "protocol_version": 2,
            "type": "stop",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 3,
            "command_id": "discard-session",
        },
    ]
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=DelayedStopStream(commands[:2], commands[2], runtime.started),
        output_stream=io.StringIO(),
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    assert app.run() == 0
    assert "discard_recording" in runtime.events
    assert runtime.events.index("discard_recording") < runtime.events.index("cleanup")


def test_wrong_session_run_fails_before_motion_and_cleans_once(monkeypatch) -> None:
    runtime = FakeRuntime()
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": _settings(),
        },
        {
            "protocol_version": 2,
            "type": "run",
            "service_instance_id": "service-1",
            "session_id": "wrong-session",
            "sequence": 2,
        },
    ]
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO("".join(json.dumps(command) + "\n" for command in commands)),
        output_stream=io.StringIO(),
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    with pytest.raises(RuntimeError, match="session"):
        app.run()

    assert "enable_motion" not in runtime.events
    assert runtime.cleanup_calls == 1


def test_stop_before_run_cleans_without_enabling_motion(monkeypatch) -> None:
    runtime = FakeRuntime()
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": _settings(),
        },
        {
            "protocol_version": 2,
            "type": "stop",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
            "command_id": "cancel-start",
        },
    ]
    output = io.StringIO()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO("".join(json.dumps(command) + "\n" for command in commands)),
        output_stream=output,
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    assert app.run() == 0
    assert "enable_motion" not in runtime.events
    assert runtime.cleanup_calls == 1
    assert json.loads(output.getvalue().splitlines()[-1])["command_id"] == "cancel-start"


def test_stop_latched_after_run_prevents_motion_enable(monkeypatch) -> None:
    runtime = FakeRuntime()
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": _settings(),
        },
        {
            "protocol_version": 2,
            "type": "run",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
        },
        {
            "protocol_version": 2,
            "type": "stop",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 3,
            "command_id": "stop-before-enable",
        },
    ]
    output = io.StringIO()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO("".join(json.dumps(command) + "\n" for command in commands)),
        output_stream=output,
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    assert app.run() == 0
    assert "enable_motion" not in runtime.events
    assert json.loads(output.getvalue().splitlines()[-1])["command_id"] == "stop-before-enable"


def test_stop_during_cancelled_acquisition_correlates_cleanup(monkeypatch) -> None:
    class CancelledRuntime(FakeRuntime):
        def acquire(self) -> None:
            self._stop_requested = True
            raise RuntimeError("Resource acquisition cancelled")

    runtime = CancelledRuntime()
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": _settings(),
        },
        {
            "protocol_version": 2,
            "type": "stop",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
            "command_id": "cancel-acquisition",
        },
    ]
    output = io.StringIO()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO("".join(json.dumps(command) + "\n" for command in commands)),
        output_stream=output,
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        app.run()

    assert json.loads(output.getvalue().splitlines()[-1])["command_id"] == "cancel-acquisition"


def test_profile_fingerprint_mismatch_fails_before_acquisition(monkeypatch) -> None:
    runtime = FakeRuntime()
    command = {
        "protocol_version": 2,
        "type": "initialize",
        "service_instance_id": "service-1",
        "session_id": "session-1",
        "sequence": 1,
        "startup_nonce": "0123456789abcdef",
        "profile": _profile(),
        "profile_fingerprint": "different",
        "resource_fingerprint": "resource",
        "settings": _settings(),
    }
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO(json.dumps(command) + "\n"),
        output_stream=io.StringIO(),
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    with pytest.raises(RuntimeError, match="fingerprint"):
        app.run()

    assert runtime.events == []


def test_teleoperation_failure_preserves_first_cleanup_failure(monkeypatch) -> None:
    class FailingRuntime(FakeRuntime):
        def teleoperate(self) -> None:
            raise RuntimeError("control failed")

        def cleanup(self) -> CleanupReport:
            self.cleanup_calls += 1
            return CleanupReport(
                False,
                ("leader",),
                ("follower torque verification failed",),
                torque_verified_off=False,
            )

    runtime = FailingRuntime()
    commands = [
        {
            "protocol_version": 2,
            "type": "initialize",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 1,
            "startup_nonce": "0123456789abcdef",
            "profile": _profile(),
            "profile_fingerprint": _profile()["fingerprint"],
            "resource_fingerprint": "resource",
            "settings": _settings(),
        },
        {
            "protocol_version": 2,
            "type": "run",
            "service_instance_id": "service-1",
            "session_id": "session-1",
            "sequence": 2,
        },
    ]
    output = io.StringIO()
    monkeypatch.setattr("operator_worker.app.importlib.metadata.version", lambda _name: "0.6.1")
    app = WorkerApplication(
        session_id="session-1",
        input_stream=io.StringIO("".join(json.dumps(command) + "\n" for command in commands)),
        output_stream=output,
        runtime_factory=lambda _profile, _settings: runtime,
        resource_fingerprint=lambda _profile, _mode: "resource",
    )

    with pytest.raises(RuntimeError, match="control failed"):
        app.run()

    cleanup = json.loads(output.getvalue().splitlines()[-1])
    assert cleanup["cleanup_complete"] is False
    assert cleanup["errors"] == ["follower torque verification failed"]
    assert runtime.cleanup_calls == 1
