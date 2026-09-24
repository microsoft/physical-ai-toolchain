"""Behavior tests for the operator backend safety boundary."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.operator.authorization import require_hardware_access, require_operator_csrf
from src.api.operator.calibration import inspect_calibration_file
from src.api.operator.models import (
    OperatorAction,
    OperatorCommand,
    OperatorMode,
    OperatorSessionSettings,
    PreflightCheck,
    PreflightCheckOutcome,
    PreflightLifecycle,
    PreflightResult,
    SessionState,
    StartSessionRequest,
)
from src.api.operator.protocol import (
    HardwareActionCommand,
    HardwareInitializeCommand,
    HardwareWorkerPreview,
    HardwareWorkerRate,
    HardwareWorkerTelemetry,
)
from src.api.services.operator_service import OperatorConflictError, OperatorPreconditionError, OperatorService


def _request(
    method: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Request:
    raw_headers = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    if cookies:
        raw_headers.append((b"cookie", "; ".join(f"{name}={value}" for name, value in cookies.items()).encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/operator/sessions",
            "headers": raw_headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
    )


def _calibration() -> dict[str, dict[str, int]]:
    names = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    return {
        name: {
            "id": motor_id,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 100,
            "range_max": 3900,
        }
        for motor_id, name in enumerate(names, start=1)
    }


class _Profile:
    fingerprint = "profile-current"

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "name": "so101",
            "embodiment": "SO-101",
            "actuator_names": ["a", "b", "c", "d", "e", "f"],
            "leader": {"logical_id": "leader-arm"},
            "follower": {"logical_id": "follower-arm"},
            "wrist_camera": {"fps": 30},
            "front_camera": {"fps": 20},
        }


class _PreflightService:
    def __init__(
        self,
        *,
        mode: OperatorMode = OperatorMode.RECORD,
        profile_fingerprint: str = "profile-current",
    ) -> None:
        self.profiles = {"so101": _Profile()}
        now = datetime.now(UTC)
        self.result = PreflightResult(
            preflight_id="preflight-1",
            lifecycle=PreflightLifecycle.COMPLETED,
            profile="so101",
            mode=mode,
            profile_fingerprint=profile_fingerprint,
            resource_fingerprint="resource-current",
            created_at=now,
            expires_at=now + timedelta(seconds=30),
            checks=[
                PreflightCheck(
                    name="worker_contract",
                    outcome=PreflightCheckOutcome.PASSED,
                    detail="verified",
                )
            ],
            ownership_complete=True,
            start_eligible=True,
        )
        self.consume_calls = 0

    def get(self, _preflight_id: str) -> PreflightResult:
        return self.result

    def consume(self, _preflight_id: str) -> PreflightResult:
        self.consume_calls += 1
        return self.result.model_copy(update={"lifecycle": PreflightLifecycle.CONSUMED})


class _Worker:
    def __init__(self, **callbacks: object) -> None:
        self.pid = 4242
        self.torque_verified_off = False
        self.launch_calls = 0
        self.command_calls = 0
        self.terminate_calls = 0
        self.recover_calls = 0
        self.callbacks = callbacks
        self.ready = asyncio.Event()
        self.exited = asyncio.Event()

    async def launch(self, session_id: str, _mode: OperatorMode) -> None:
        self.launch_calls += 1
        self.session_id = session_id

    async def wait_ready(self) -> None:
        await self.ready.wait()

    async def wait(self) -> int:
        await self.exited.wait()
        return 1

    async def command(self, _session_id: str, _command_id: str, action: OperatorAction) -> SimpleNamespace:
        self.command_calls += 1
        if action in {OperatorAction.FINISH, OperatorAction.CANCEL}:
            self.exited.set()
        return SimpleNamespace(
            cleanup_complete=action is not OperatorAction.FINISH,
            dataset_id="dataset-1",
            episode_index=2,
            recording_phase="finalized" if action is OperatorAction.FINISH else "paused",
            upload_attempted=action is OperatorAction.FINISH,
            upload_succeeded=False,
            upload_error="upload failed /secret/path" if action is OperatorAction.FINISH else None,
        )

    async def terminate(self) -> bool:
        self.terminate_calls += 1
        self.exited.set()
        return self.torque_verified_off

    async def recover(self) -> bool:
        self.recover_calls += 1
        return self.torque_verified_off


def _start_request(
    *,
    mode: OperatorMode = OperatorMode.RECORD,
    settings: OperatorSessionSettings | None = None,
    task: str = "Record",
) -> StartSessionRequest:
    return StartSessionRequest(
        command_id="start-1",
        mode=mode,
        profile="so101",
        preflight_id="preflight-1",
        preflight_fingerprint="resource-current",
        settings=settings or OperatorSessionSettings(control_fps=30, task=task),
    )


async def test_given_auth_disabled_when_hardware_requested_then_access_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.setenv("OPERATOR_ADAPTER_MODE", "lerobot")

    # Act & Assert
    with pytest.raises(HTTPException) as error:
        await require_hardware_access(_request("POST"), None)
    assert error.value.status_code == 403


async def test_given_hardware_mutation_when_csrf_is_missing_then_request_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_ADAPTER_MODE", "lerobot")

    # Act & Assert
    with pytest.raises(HTTPException) as error:
        await require_operator_csrf(_request("POST"))
    assert error.value.status_code == 403


def test_given_valid_saved_calibration_when_inspected_then_hash_evidence_is_read_only(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "leader.json"
    original = json.dumps(_calibration())
    path.write_text(original, encoding="utf-8")

    # Act
    result = inspect_calibration_file(path, "leader")

    # Assert
    assert result.valid is True
    assert result.hardware_verified is False
    assert len(result.joints) == 6
    assert len(result.sha256 or "") == 64
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("missing_field", ["command_id", "correlation_id", "worker_pid"])
def test_given_hardware_command_without_identity_field_when_validated_then_it_is_rejected(
    missing_field: str,
) -> None:
    # Arrange
    payload: dict[str, object] = {
        "service_instance_id": "service-1",
        "session_id": "session-1",
        "sequence": 3,
        "command_id": "command-1",
        "correlation_id": "correlation-1",
        "worker_pid": 42,
        "action": "pause",
    }
    payload.pop(missing_field)

    # Act & Assert
    with pytest.raises(ValueError):
        HardwareActionCommand.model_validate(payload)


def test_given_initialize_command_when_created_then_every_identity_field_is_present() -> None:
    # Act
    command = HardwareInitializeCommand(
        service_instance_id="service-1",
        session_id="session-1",
        sequence=1,
        command_id="command-1",
        correlation_id="correlation-1",
        worker_pid=42,
        startup_nonce="0123456789abcdef",
        profile={"fingerprint": "profile-1"},
        profile_fingerprint="profile-1",
        resource_fingerprint="resource-1",
        settings={"mode": "teleoperate", "execution_mode": "physical", "control_fps": 30},
    )

    # Assert
    assert (command.command_id, command.correlation_id, command.worker_pid) == (
        "command-1",
        "correlation-1",
        42,
    )


@pytest.mark.parametrize("dataset_id", ["../escape", "owner/dataset", "dataset\\child", "dataset\nname"])
def test_given_unsafe_dataset_id_when_settings_are_validated_then_it_is_rejected(dataset_id: str) -> None:
    # Act & Assert
    with pytest.raises(ValueError, match="dataset_id"):
        OperatorSessionSettings(control_fps=30, dataset_id=dataset_id)


async def test_given_worker_callbacks_when_events_arrive_then_latest_state_and_frame_are_reduced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    worker = _Worker()
    monkeypatch.setattr(
        "src.api.services.operator_service.LerobotWorkerClient",
        lambda **callbacks: worker.callbacks.update(callbacks) or worker,
    )
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=_PreflightService(),
        worker_executable=str(executable),
        host_lease_fd=3,
    )
    start_task = asyncio.create_task(service.start(_start_request()))
    await asyncio.sleep(0)
    worker.ready.set()
    status = await start_task
    session_id = status.session_id

    # Act
    worker.callbacks["on_rate"](
        HardwareWorkerRate(
            service_instance_id=status.service_instance_id,
            session_id=session_id,
            sequence=3,
            target_hz=30,
            actual_hz=29.5,
            loop_p50_ms=2,
            loop_p95_ms=4,
            loop_max_ms=8,
            overruns=1,
        )
    )
    worker.callbacks["on_log"]("device failed at /dev/ttyUSB0\n" + "x" * 600)
    worker.callbacks["on_telemetry"](
        HardwareWorkerTelemetry(
            service_instance_id=status.service_instance_id,
            session_id=session_id,
            sequence=4,
            elapsed_s=1.5,
            leader={"joint": 1.0},
            follower={"joint": 2.0},
            commanded={"joint": 3.0},
        )
    )
    worker.callbacks["on_preview"](
        HardwareWorkerPreview(
            service_instance_id=status.service_instance_id,
            session_id=session_id,
            sequence=5,
            camera="wrist",
            captured_at_s=1.5,
            jpeg_base64=base64.b64encode(b"\xff\xd8jpeg\xff\xd9").decode(),
        )
    )
    await asyncio.sleep(0)

    # Assert
    reduced = service.status()
    assert (reduced.actual_hz, reduced.latest_telemetry.leader) == (29.5, {"joint": 1.0})
    assert len(reduced.latest_worker_log or "") <= 500
    assert "/dev/ttyUSB0" not in (reduced.latest_worker_log or "")
    assert service.camera_frame("wrist").jpeg == b"\xff\xd8jpeg\xff\xd9"
    with pytest.raises(KeyError):
        service.camera_frame("unconfigured")
    await service.shutdown()


async def test_given_untrusted_worker_settings_when_recording_then_only_resolved_trusted_values_are_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    data_root = tmp_path / "datasets"
    data_root.mkdir()
    (data_root / "demo").mkdir()
    worker = _Worker()
    worker_kwargs: dict[str, object] = {}

    def worker_factory(**kwargs: object) -> _Worker:
        worker_kwargs.update(kwargs)
        return worker

    monkeypatch.setattr("src.api.services.operator_service.LerobotWorkerClient", worker_factory)
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=_PreflightService(),
        worker_executable=str(executable),
        host_lease_fd=3,
        data_root=data_root,
        now=lambda: datetime(2026, 9, 24, 12, 30, tzinfo=UTC),
        policy_python="/trusted/policy-python",
        policy_checkpoint="/trusted/checkpoint",
        policy_cuda_visible_devices="7",
    )
    request = _start_request(
        settings=OperatorSessionSettings(
            control_fps=30,
            camera_fps={"wrist": 30},
            dataset_root="/client/root",
            dataset_id="demo",
            repo_id="client/repo",
            policy_python="/client/python",
            policy_checkpoint="/client/checkpoint",
            policy_cuda_visible_devices="0",
        )
    )

    # Act
    start_task = asyncio.create_task(service.start(request))
    await asyncio.sleep(0)
    worker.ready.set()
    status = await start_task

    # Assert
    worker_settings = worker_kwargs["settings"]
    assert isinstance(worker_settings, dict)
    assert worker_settings["dataset_root"] == str(data_root / "demo_20260924_123000")
    assert worker_settings["dataset_id"] == "demo_20260924_123000"
    assert worker_settings["repo_id"] == "local/demo_20260924_123000"
    assert worker_settings["policy_python"] is None
    assert worker_settings["policy_checkpoint"] is None
    assert worker_settings["policy_cuda_visible_devices"] is None
    assert not (data_root / "demo_20260924_123000").exists()
    assert status.dataset_id == "demo_20260924_123000"
    assert status.session_settings is not None
    assert status.session_settings.dataset_root is None
    assert status.session_settings.repo_id == "local/demo_20260924_123000"
    assert status.session_settings.policy_python is None
    assert status.session_settings.policy_checkpoint is None
    assert status.session_settings.policy_cuda_visible_devices is None
    await service.shutdown()


async def test_given_unknown_camera_when_starting_then_request_is_rejected_before_preflight_consumption(
    tmp_path: Path,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    preflight = _PreflightService()
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=preflight,
        worker_executable=str(executable),
        host_lease_fd=3,
        data_root=tmp_path / "datasets",
    )

    # Act & Assert
    with pytest.raises(OperatorPreconditionError, match="Unknown operator camera: overhead"):
        await service.start(
            _start_request(settings=OperatorSessionSettings(control_fps=30, camera_fps={"overhead": 30}))
        )
    assert preflight.consume_calls == 0


@pytest.mark.parametrize(
    ("max_relative_target", "policy_python", "policy_checkpoint", "message"),
    [
        (6.0, "/trusted/python", "/trusted/checkpoint", "at most 5 degrees"),
        (2.0, None, None, "policy runtime is not configured"),
    ],
)
async def test_given_unsafe_policy_settings_when_starting_then_request_is_rejected(
    tmp_path: Path,
    max_relative_target: float,
    policy_python: str | None,
    policy_checkpoint: str | None,
    message: str,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    preflight = _PreflightService(mode=OperatorMode.POLICY)
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=preflight,
        worker_executable=str(executable),
        host_lease_fd=3,
        data_root=tmp_path / "datasets",
        policy_python=policy_python,
        policy_checkpoint=policy_checkpoint,
    )

    # Act & Assert
    with pytest.raises(OperatorPreconditionError, match=message):
        await service.start(
            _start_request(
                mode=OperatorMode.POLICY,
                settings=OperatorSessionSettings(control_fps=30, max_relative_target=max_relative_target),
            )
        )
    assert preflight.consume_calls == 0


async def test_given_configured_policy_when_starting_then_only_trusted_runtime_reaches_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    worker = _Worker()
    worker_kwargs: dict[str, object] = {}

    def worker_factory(**kwargs: object) -> _Worker:
        worker_kwargs.update(kwargs)
        return worker

    monkeypatch.setattr("src.api.services.operator_service.LerobotWorkerClient", worker_factory)
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=_PreflightService(mode=OperatorMode.POLICY),
        worker_executable=str(executable),
        host_lease_fd=3,
        policy_python="/trusted/python",
        policy_checkpoint="/trusted/checkpoint",
        policy_cuda_visible_devices="2",
    )
    request = _start_request(
        mode=OperatorMode.POLICY,
        settings=OperatorSessionSettings(
            control_fps=30,
            max_relative_target=5,
            policy_python="/client/python",
            policy_checkpoint="/client/checkpoint",
            policy_cuda_visible_devices="0",
        ),
    )

    # Act
    start_task = asyncio.create_task(service.start(request))
    await asyncio.sleep(0)
    worker.ready.set()
    status = await start_task

    # Assert
    worker_settings = worker_kwargs["settings"]
    assert isinstance(worker_settings, dict)
    assert worker_settings["policy_python"] == "/trusted/python"
    assert worker_settings["policy_checkpoint"] == "/trusted/checkpoint"
    assert worker_settings["policy_cuda_visible_devices"] == "2"
    assert status.session_settings is not None
    assert status.session_settings.policy_python is None
    assert status.session_settings.policy_checkpoint is None
    assert status.session_settings.policy_cuda_visible_devices is None
    await service.shutdown()


def test_given_physical_policy_runtime_configuration_when_reading_capabilities_then_policy_is_gated(
    tmp_path: Path,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    base_kwargs = {
        "adapter_mode": "lerobot",
        "preflight_service": _PreflightService(),
        "worker_executable": str(executable),
        "host_lease_fd": 3,
    }

    # Act
    without_policy = OperatorService(**base_kwargs).capabilities()
    with_policy = OperatorService(
        **base_kwargs,
        policy_python="/trusted/python",
        policy_checkpoint="/trusted/checkpoint",
    ).capabilities()

    # Assert
    assert OperatorMode.POLICY not in without_policy.modes
    assert OperatorMode.POLICY in with_policy.modes


async def test_given_duplicate_concurrent_payloads_when_dispatched_then_each_worker_operation_runs_once() -> None:
    # Arrange
    worker = _Worker()
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)
    request = StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD)

    # Act
    first_start = asyncio.create_task(service.start(request))
    replayed_start = asyncio.create_task(service.start(request.model_copy(deep=True)))
    await asyncio.sleep(0)
    worker.ready.set()
    first_status, replayed_status = await asyncio.gather(first_start, replayed_start)
    command = OperatorCommand(command_id="command-1", action=OperatorAction.PAUSE)
    first_command, replayed_command = await asyncio.gather(
        service.command(first_status.session_id, command),
        service.command(first_status.session_id, command.model_copy(deep=True)),
    )

    # Assert
    assert worker.launch_calls == 1
    assert worker.command_calls == 1
    assert replayed_status == first_status
    assert replayed_command == first_command
    assert (first_command.dataset_id, first_command.episode_index, first_command.recording_phase) == (
        "dataset-1",
        2,
        "paused",
    )


async def test_given_reused_idempotency_key_when_full_payload_changes_then_request_conflicts() -> None:
    # Arrange
    worker = _Worker()
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)
    first = asyncio.create_task(service.start(StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD)))
    await asyncio.sleep(0)

    # Act & Assert
    with pytest.raises(OperatorConflictError, match="another payload"):
        await service.start(
            StartSessionRequest(
                command_id="start-1",
                mode=OperatorMode.RECORD,
                settings=OperatorSessionSettings(control_fps=30, task="different"),
            )
        )
    worker.ready.set()
    await first


async def test_given_changed_profile_when_starting_then_preflight_is_not_consumed(tmp_path: Path) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    preflight = _PreflightService(profile_fingerprint="profile-stale")
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=preflight,
        worker_executable=str(executable),
        host_lease_fd=3,
    )

    # Act & Assert
    with pytest.raises(OperatorPreconditionError, match="profile fingerprint changed"):
        await service.start(_start_request())
    assert preflight.consume_calls == 0


async def test_given_worker_is_not_configured_when_starting_then_preflight_is_not_consumed() -> None:
    # Arrange
    preflight = _PreflightService()
    service = OperatorService(adapter_mode="lerobot", preflight_service=preflight)

    # Act & Assert
    with pytest.raises(OperatorPreconditionError, match="worker is not configured"):
        await service.start(_start_request())
    assert preflight.consume_calls == 0


async def test_given_worker_command_failure_when_recovery_cannot_confirm_torque_off_then_state_is_latched() -> None:
    # Arrange
    worker = _Worker()

    async def fail_command(*_args: object) -> None:
        raise RuntimeError("failed at /private/device")

    worker.command = fail_command  # type: ignore[method-assign]
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)
    start = asyncio.create_task(service.start(StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD)))
    await asyncio.sleep(0)
    worker.ready.set()
    status = await start

    # Act & Assert
    with pytest.raises(RuntimeError, match="<path>"):
        await service.command(
            status.session_id,
            OperatorCommand(command_id="command-1", action=OperatorAction.PAUSE),
        )
    failed = service.status()
    assert (failed.state, failed.cleanup_unconfirmed) == (SessionState.FAILED, True)
    assert worker.terminate_calls == 1
    assert worker.recover_calls == 1


async def test_given_worker_launch_failure_when_recovery_confirms_torque_off_then_failure_is_not_latched() -> None:
    # Arrange
    worker = _Worker()
    worker.torque_verified_off = True

    async def fail_launch(*_args: object) -> None:
        raise RuntimeError("launch failed at /private/device")

    worker.launch = fail_launch  # type: ignore[method-assign]
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)

    # Act & Assert
    with pytest.raises(RuntimeError, match="<path>"):
        await service.start(StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD))
    failed = service.status()
    assert (failed.state, failed.cleanup_unconfirmed) == (SessionState.FAILED, False)
    assert worker.terminate_calls == 1


async def test_given_unexpected_worker_exit_when_recovery_fails_then_service_fails_closed() -> None:
    # Arrange
    worker = _Worker()
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)
    start = asyncio.create_task(service.start(StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD)))
    await asyncio.sleep(0)
    worker.ready.set()
    await start

    # Act
    worker.exited.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Assert
    failed = service.status()
    assert (failed.state, failed.cleanup_unconfirmed) == (SessionState.FAILED, True)
    assert worker.recover_calls == 1


async def test_given_last_event_revision_when_stream_reconnects_then_only_newer_history_is_replayed() -> None:
    # Arrange
    worker = _Worker()
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)
    start = asyncio.create_task(service.start(StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD)))
    await asyncio.sleep(0)
    starting_revision = service.status().revision
    worker.ready.set()
    running = await start

    # Act
    replay = [item async for item in service.events(f"{running.service_instance_id}:{starting_revision}", once=True)]

    # Assert
    assert [(event_type, item.revision) for event_type, item in replay] == [("status", running.revision)]


async def test_given_revision_older_than_bounded_history_when_reconnecting_then_current_snapshot_is_sent() -> None:
    # Arrange
    service = OperatorService(adapter_mode="simulated")
    instance_id = service.status().service_instance_id
    for index in range(130):
        service._replace_status(latest_worker_log=f"event-{index}")

    # Act
    replay = [item async for item in service.events(f"{instance_id}:1", once=True)]

    # Assert
    assert [(event_type, item.revision) for event_type, item in replay] == [("snapshot", service.status().revision)]


async def test_given_finish_acknowledgement_when_reduced_then_upload_failure_is_bounded_and_redacted() -> None:
    # Arrange
    worker = _Worker()
    service = OperatorService(adapter_mode="simulated", worker_factory=lambda: worker)
    start = asyncio.create_task(service.start(StartSessionRequest(command_id="start-1", mode=OperatorMode.RECORD)))
    await asyncio.sleep(0)
    worker.ready.set()
    running = await start

    # Act
    completed = await service.command(
        running.session_id,
        OperatorCommand(command_id="finish-1", action=OperatorAction.FINISH),
    )

    # Assert
    assert (completed.state, completed.dataset_id, completed.recording_phase) == (
        SessionState.COMPLETED,
        "dataset-1",
        "finalized",
    )
    assert (completed.upload_status, completed.upload_error) == ("failed", "upload failed <path>")


def test_given_configured_profile_when_capabilities_requested_then_physical_metadata_and_modes_are_exact(
    tmp_path: Path,
) -> None:
    # Arrange
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    service = OperatorService(
        adapter_mode="lerobot",
        preflight_service=_PreflightService(),
        worker_executable=str(executable),
        host_lease_fd=3,
    )

    # Act
    capabilities = service.capabilities()

    # Assert
    assert capabilities.modes == [OperatorMode.TELEOPERATE, OperatorMode.RECORD]
    assert [(robot.role, robot.name, robot.actuator_count) for robot in capabilities.robots] == [
        ("leader", "leader-arm", 6),
        ("follower", "follower-arm", 6),
    ]
    assert [(camera.name, camera.default_fps) for camera in capabilities.cameras] == [
        ("wrist", 30),
        ("front", 20),
    ]
