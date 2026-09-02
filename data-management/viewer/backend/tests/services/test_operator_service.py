"""Behavior tests for the operator session service."""

from __future__ import annotations

import asyncio
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.api.operator.models import (
    OperatorCommand,
    OperatorMode,
    OperatorSessionSettings,
    PreflightLifecycle,
    PreflightResult,
    SessionState,
    StartSessionRequest,
)
from src.api.operator.protocol import (
    HardwareWorkerPreview,
    HardwareWorkerRate,
    HardwareWorkerTelemetry,
)
from src.api.services.operator_service import (
    OperatorConflictError,
    OperatorDisabledError,
    OperatorPreconditionError,
    OperatorService,
    _resolve_worker_settings,
)


class TestDisabledOperatorService:
    async def test_reports_disabled_capabilities_and_status(self) -> None:
        service = OperatorService(adapter_mode="disabled")

        assert service.capabilities().model_dump() == {
            "enabled": False,
            "adapter_mode": "disabled",
            "adapter_version": 1,
            "protocol_version": 1,
            "modes": [],
            "profiles": [],
            "robots": [],
            "cameras": [],
            "preflight_enabled": False,
            "session_start_enabled": False,
            "reason": "Operator mode is disabled",
        }
        assert service.status().state is SessionState.DISABLED

    async def test_rejects_start(self) -> None:
        service = OperatorService(adapter_mode="disabled")

        with pytest.raises(OperatorDisabledError, match="disabled"):
            await service.start(StartSessionRequest(command_id="disabled-start", mode=OperatorMode.TELEOPERATE))


class TestSimulatedOperatorService:
    @pytest.fixture
    async def service(self) -> OperatorService:
        instance = OperatorService(adapter_mode="simulated", command_timeout_s=2.0)
        yield instance
        await instance.shutdown()

    async def test_starts_one_subprocess_session(self, service: OperatorService) -> None:
        request = StartSessionRequest(command_id="start-teleoperate", mode=OperatorMode.TELEOPERATE)
        status = await service.start(request)

        assert status.state is SessionState.RUNNING
        assert status.mode is OperatorMode.TELEOPERATE
        assert status.session_id
        assert status.worker_pid is not None

        with pytest.raises(OperatorConflictError, match="already active"):
            await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))

    async def test_new_session_clears_previous_session_fields(self) -> None:
        service = OperatorService(adapter_mode="simulated", command_timeout_s=2.0)
        service._status = service.status().model_copy(
            update={
                "state": SessionState.CANCELLED,
                "session_id": "old-session",
                "mode": OperatorMode.RECORD,
                "cleanup_unconfirmed": False,
                "target_hz": 30.0,
                "actual_hz": 29.9,
                "loop_p95_ms": 4.0,
                "loop_max_ms": 5.0,
                "overruns": 2,
                "latest_worker_log": "old log",
                "latest_telemetry": {
                    "elapsed_s": 1.0,
                    "leader": {},
                    "follower": {},
                    "commanded": {},
                },
                "dataset_id": "old-dataset",
                "episode_index": 11,
                "recording_phase": "finalized",
                "upload_status": "failed",
                "upload_error": "old error",
            }
        )

        started = await service.start(StartSessionRequest(command_id="fresh-session", mode=OperatorMode.TELEOPERATE))

        assert started.target_hz is None
        assert started.actual_hz is None
        assert started.latest_worker_log is None
        assert started.latest_telemetry is None
        assert started.dataset_id is None
        assert started.episode_index == 0
        assert started.recording_phase is None
        assert started.upload_status == "not_requested"
        assert started.upload_error is None
        await service.shutdown()

    async def test_replays_idempotent_start_and_rejects_conflicting_reuse(
        self,
        service: OperatorService,
    ) -> None:
        request = StartSessionRequest(command_id="start-once", mode=OperatorMode.TELEOPERATE)

        first = await service.start(request)
        replay = await service.start(request)

        assert replay == first
        with pytest.raises(OperatorConflictError, match="command_id"):
            await service.start(StartSessionRequest(command_id="start-once", mode=OperatorMode.RECORD))

    async def test_rejects_stale_session_commands(self, service: OperatorService) -> None:
        await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))

        with pytest.raises(OperatorConflictError, match="Stale session"):
            await service.command(
                "previous-session",
                OperatorCommand(command_id="stale-command", action="save"),
            )

    async def test_replays_an_idempotent_command_without_advancing_revision(
        self,
        service: OperatorService,
    ) -> None:
        started = await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))
        command = OperatorCommand(
            command_id="save-episode-1",
            action="save",
            expected_revision=started.revision,
        )

        first = await service.command(started.session_id, command)
        replay = await service.command(started.session_id, command)

        assert first == replay
        assert first.last_command == "save"
        assert first.revision == started.revision + 1
        assert first.cleanup_unconfirmed is False

    async def test_telemetry_revision_does_not_invalidate_episode_command(self, service: OperatorService) -> None:
        started = await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))
        service._replace_status(actual_hz=30.0)
        assert service.status().revision > started.revision

        saved = await service.command(
            started.session_id,
            OperatorCommand(
                command_id="save-after-telemetry",
                action="save",
                expected_revision=started.revision,
            ),
        )

        assert saved.last_command == "save"
        assert saved.state is SessionState.RUNNING

    async def test_idempotent_command_ignores_changed_telemetry_revision(self, service: OperatorService) -> None:
        started = await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))
        first = await service.command(
            started.session_id,
            OperatorCommand(
                command_id="same-save",
                action="save",
                expected_revision=started.revision,
            ),
        )

        replay = await service.command(
            started.session_id,
            OperatorCommand(
                command_id="same-save",
                action="save",
                expected_revision=first.revision,
            ),
        )

        assert replay == first

    async def test_rejects_command_id_reuse_with_another_payload(self, service: OperatorService) -> None:
        started = await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))
        await service.command(
            started.session_id,
            OperatorCommand(command_id="episode-command", action="save"),
        )

        with pytest.raises(OperatorConflictError, match="command_id"):
            await service.command(
                started.session_id,
                OperatorCommand(command_id="episode-command", action="rerecord"),
            )

    async def test_finish_completes_the_named_recording_session(self, service: OperatorService) -> None:
        started = await service.start(StartSessionRequest(command_id="start-record", mode=OperatorMode.RECORD))

        completed = await service.command(
            started.session_id,
            OperatorCommand(command_id="finish-session", action="finish"),
        )

        assert completed.state is SessionState.COMPLETED
        assert completed.last_command == "finish"
        assert completed.cleanup_unconfirmed is False

    async def test_stop_cancels_the_named_session(self, service: OperatorService) -> None:
        started = await service.start(
            StartSessionRequest(command_id="start-teleoperate", mode=OperatorMode.TELEOPERATE)
        )

        cancelled = await service.stop(started.session_id, command_id="stop-session")

        assert cancelled.state is SessionState.CANCELLED
        assert cancelled.last_command == "cancel"
        assert cancelled.cleanup_unconfirmed is False

    async def test_worker_crash_marks_cleanup_unconfirmed(self) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=1.0,
            simulated_behavior="crash_on_start",
        )

        with pytest.raises(RuntimeError, match="worker exited"):
            await service.start(StartSessionRequest(command_id="crash-start", mode=OperatorMode.TELEOPERATE))

        status = service.status()
        assert status.state is SessionState.FAILED
        assert status.cleanup_unconfirmed is True
        await service.shutdown()

    async def test_post_ready_worker_crash_is_supervised(self) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=1.0,
            simulated_behavior="crash_after_ready",
        )
        await service.start(StartSessionRequest(command_id="post-ready-crash", mode=OperatorMode.TELEOPERATE))

        for _ in range(50):
            if service.status().state is SessionState.FAILED:
                break
            await asyncio.sleep(0.01)

        status = service.status()
        assert status.state is SessionState.FAILED
        assert status.cleanup_unconfirmed is True
        assert status.error == "Operator worker exited unexpectedly; torque-off recovery unconfirmed"
        await service.shutdown()

    async def test_torque_safe_record_worker_exit_marks_session_failed(self) -> None:
        class TorqueSafeExitedWorker:
            torque_verified_off = True

            async def wait(self) -> int:
                return 1

            async def recover(self) -> bool:
                raise AssertionError("torque-safe exits must not run recovery")

        service = OperatorService(adapter_mode="simulated")
        worker = TorqueSafeExitedWorker()
        service._worker = worker
        service._status = service.status().model_copy(
            update={
                "state": SessionState.RUNNING,
                "session_id": "record-session",
                "mode": OperatorMode.RECORD,
                "worker_pid": 1234,
                "latest_worker_log": "follower bus read failed",
            }
        )

        await service._monitor_worker(worker, "record-session")  # type: ignore[arg-type]

        status = service.status()
        assert status.state is SessionState.FAILED
        assert status.worker_pid is None
        assert status.cleanup_unconfirmed is False
        assert status.error == "Operator worker exited unexpectedly; torque off verified"

    async def test_cancel_during_starting_waits_for_cleanup_acknowledgement(
        self,
    ) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=2.0,
            simulated_behavior="slow_start",
        )
        start_task = asyncio.create_task(
            service.start(StartSessionRequest(command_id="slow-start", mode=OperatorMode.TELEOPERATE))
        )

        for _ in range(50):
            if service.status().state is SessionState.STARTING:
                break
            await asyncio.sleep(0.01)
        cancelled = await service.stop(service.status().session_id, command_id="cancel-start")
        start_result = await start_task

        assert cancelled.state is SessionState.CANCELLED
        assert cancelled.cleanup_unconfirmed is False
        assert start_result.state is SessionState.CANCELLED
        await service.shutdown()

    async def test_slow_start_uses_dedicated_startup_timeout(self) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=0.05,
            startup_timeout_s=1.0,
            simulated_behavior="slow_start",
        )

        started = await service.start(
            StartSessionRequest(
                command_id="slow-start-timeout",
                mode=OperatorMode.TELEOPERATE,
            )
        )

        assert started.state is SessionState.RUNNING
        await service.shutdown()

    async def test_command_timeout_escalates_and_marks_cleanup_unconfirmed(
        self,
    ) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=0.1,
            simulated_behavior="ignore_commands",
        )
        started = await service.start(StartSessionRequest(command_id="timeout-start", mode=OperatorMode.RECORD))

        with pytest.raises(RuntimeError, match="timed out"):
            await service.command(
                started.session_id,
                OperatorCommand(command_id="timeout-save", action="save"),
            )

        assert service.status().state is SessionState.FAILED
        assert service.status().cleanup_unconfirmed is True
        await service.shutdown()

    async def test_shutdown_requests_graceful_cancel(self) -> None:
        service = OperatorService(adapter_mode="simulated", command_timeout_s=1.0)
        await service.start(StartSessionRequest(command_id="shutdown-start", mode=OperatorMode.TELEOPERATE))

        await service.shutdown()

        status = service.status()
        assert status.state is SessionState.CANCELLED
        assert status.cleanup_unconfirmed is False
        assert status.last_command == "cancel"

    async def test_protocol_mismatch_fails_closed(self) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=1.0,
            simulated_behavior="wrong_ack_version",
        )
        started = await service.start(StartSessionRequest(command_id="protocol-start", mode=OperatorMode.RECORD))

        with pytest.raises(RuntimeError, match="protocol"):
            await service.command(
                started.session_id,
                OperatorCommand(command_id="protocol-save", action="save"),
            )

        assert service.status().state is SessionState.FAILED
        await service.shutdown()

    async def test_unresponsive_worker_escalates_past_terminate(self) -> None:
        service = OperatorService(
            adapter_mode="simulated",
            command_timeout_s=0.1,
            simulated_behavior="ignore_commands_and_sigterm",
        )
        await service.start(StartSessionRequest(command_id="kill-start", mode=OperatorMode.TELEOPERATE))

        await service.shutdown()

        status = service.status()
        assert status.state is SessionState.FAILED
        assert status.cleanup_unconfirmed is True
        assert status.error == "worker command timed out"

    async def test_stop_preempts_a_pending_episode_save(self) -> None:
        save_started = asyncio.Event()
        cancelled = asyncio.Event()

        class PreemptibleWorker:
            pid = 42

            async def command(self, _session_id: str, command_id: str, action: str):
                if action == "save":
                    save_started.set()
                    await cancelled.wait()
                else:
                    cancelled.set()
                return type(
                    "Ack",
                    (),
                    {
                        "command_id": command_id,
                        "action": action,
                        "cleanup_complete": action == "cancel",
                        "dataset_id": None,
                        "episode_index": None,
                        "recording_phase": None,
                        "upload_succeeded": False,
                        "upload_attempted": False,
                        "upload_error": None,
                    },
                )()

            async def terminate(self) -> bool:
                return True

        service = OperatorService(adapter_mode="simulated")
        service._status = service.status().model_copy(
            update={
                "state": SessionState.RUNNING,
                "session_id": "record-session",
                "mode": OperatorMode.RECORD,
            }
        )
        service._worker = PreemptibleWorker()
        save_task = asyncio.create_task(
            service.command(
                "record-session",
                OperatorCommand(command_id="save-1", action="save"),
            )
        )
        await save_started.wait()

        stopped = await asyncio.wait_for(service.stop("record-session", command_id="stop-1"), timeout=0.5)
        save_result = await save_task

        assert stopped.state is SessionState.CANCELLED
        assert save_result.state is SessionState.CANCELLED
        assert stopped.cleanup_unconfirmed is False

    async def test_non_record_session_rejects_episode_command(self) -> None:
        service = OperatorService(adapter_mode="simulated")
        started = await service.start(
            StartSessionRequest(
                command_id="policy-start",
                mode=OperatorMode.POLICY,
            )
        )

        with pytest.raises(OperatorConflictError, match="only accept cancel"):
            await service.command(
                started.session_id,
                OperatorCommand(command_id="policy-save", action="save"),
            )

        await service.shutdown()

    async def test_command_requires_running_worker(self) -> None:
        service = OperatorService(adapter_mode="simulated")
        service._status = service.status().model_copy(
            update={
                "state": SessionState.RUNNING,
                "session_id": "session-1",
                "mode": OperatorMode.RECORD,
            }
        )

        with pytest.raises(OperatorConflictError, match="unavailable"):
            await service.command(
                "session-1",
                OperatorCommand(command_id="save-1", action="save"),
            )

    async def test_recovery_success_and_error_update_terminal_status(self) -> None:
        class ExitedWorker:
            torque_verified_off = False

            def __init__(self, error: Exception | None = None) -> None:
                self.error = error

            async def wait(self) -> int:
                return 1

            async def recover(self) -> bool:
                if self.error is not None:
                    raise self.error
                return True

        recovered_service = OperatorService(adapter_mode="simulated")
        recovered = ExitedWorker()
        recovered_service._worker = recovered
        recovered_service._status = recovered_service.status().model_copy(
            update={"state": SessionState.RUNNING, "session_id": "recovered"}
        )
        await recovered_service._monitor_worker(recovered, "recovered")  # type: ignore[arg-type]
        assert recovered_service.status().cleanup_unconfirmed is False
        assert recovered_service.status().error == (
            "Operator worker exited unexpectedly; torque-off recovery confirmed"
        )

        failed_service = OperatorService(adapter_mode="simulated")
        failed = ExitedWorker(RuntimeError("bus unavailable"))
        failed_service._worker = failed
        failed_service._status = failed_service.status().model_copy(
            update={"state": SessionState.RUNNING, "session_id": "failed"}
        )
        await failed_service._monitor_worker(failed, "failed")  # type: ignore[arg-type]
        assert failed_service.status().cleanup_unconfirmed is True
        assert failed_service.status().error.endswith(": bus unavailable")


class TestOperatorStatusUpdates:
    @staticmethod
    def _profile() -> object:
        return type(
            "Profile",
            (),
            {
                "model_dump": lambda self, mode: {
                    "wrist_camera": {"fps": 30},
                    "front_camera": {"fps": 30},
                }
            },
        )()

    @staticmethod
    def _service() -> OperatorService:
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=type(
                "Preflight",
                (),
                {"profiles": {"so101": TestOperatorStatusUpdates._profile()}},
            )(),
        )
        service._status = service.status().model_copy(update={"state": SessionState.RUNNING, "session_id": "session-1"})
        return service

    async def test_hardware_events_update_only_the_active_session(self) -> None:
        service = self._service()
        original_revision = service.status().revision

        await service._apply_hardware_rate(
            HardwareWorkerRate(
                service_instance_id=service.status().service_instance_id,
                session_id="other-session",
                sequence=1,
                target_hz=30,
                actual_hz=29,
                loop_p50_ms=1,
                loop_p95_ms=2,
                loop_max_ms=3,
                overruns=4,
            )
        )
        await service._apply_hardware_telemetry(
            HardwareWorkerTelemetry(
                service_instance_id=service.status().service_instance_id,
                session_id="other-session",
                sequence=2,
                elapsed_s=1,
                leader={},
                follower={},
                commanded={},
            )
        )
        await service._apply_hardware_log("ignored")
        assert service.status().revision == original_revision + 1

        rate = HardwareWorkerRate(
            service_instance_id=service.status().service_instance_id,
            session_id="session-1",
            sequence=3,
            target_hz=30,
            actual_hz=29.5,
            loop_p50_ms=1,
            loop_p95_ms=2,
            loop_max_ms=3,
            overruns=4,
        )
        telemetry = HardwareWorkerTelemetry(
            service_instance_id=service.status().service_instance_id,
            session_id="session-1",
            sequence=4,
            elapsed_s=2,
            leader={"joint": 1},
            follower={"joint": 2},
            commanded={"joint": 3},
        )
        await service._apply_hardware_rate(rate)
        await service._apply_hardware_telemetry(telemetry)
        await service._apply_hardware_log("x" * 600)

        status = service.status()
        assert status.actual_hz == 29.5
        assert status.latest_telemetry is not None
        assert status.latest_telemetry.model_dump() == telemetry.model_dump(
            exclude={"protocol_version", "type", "service_instance_id", "session_id", "sequence"}
        )
        assert len(status.latest_worker_log or "") == 500

    async def test_preview_validation_and_camera_lookup(self) -> None:
        service = self._service()

        def preview(session_id: str, camera: str, payload: str) -> HardwareWorkerPreview:
            return HardwareWorkerPreview(
                service_instance_id=service.status().service_instance_id,
                session_id=session_id,
                sequence=1,
                camera=camera,
                captured_at_s=1.25,
                jpeg_base64=payload,
            )

        await service._apply_hardware_preview(preview("other", "wrist", "anBlZw=="))
        await service._apply_hardware_preview(preview("session-1", "side", "anBlZw=="))
        await service._apply_hardware_preview(preview("session-1", "wrist", "invalid"))
        await service._apply_hardware_preview(preview("session-1", "wrist", ""))
        with pytest.raises(LookupError):
            service.camera_frame("wrist")
        with pytest.raises(KeyError):
            service.camera_frame("side")

        await service._apply_hardware_preview(preview("session-1", "wrist", "anBlZw=="))
        frame = service.camera_frame("wrist")
        assert frame.jpeg == b"jpeg"
        assert frame.captured_at_s == 1.25

    async def test_bounded_subscriber_queue_keeps_latest_statuses(self) -> None:
        service = OperatorService(adapter_mode="simulated")
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(service.status())
        service._subscribers.add(queue)

        service._replace_status(actual_hz=30)

        assert queue.qsize() == 1
        assert queue.get_nowait().actual_hz == 30


class _PreflightResults:
    def __init__(self, result: PreflightResult) -> None:
        self.result = result
        self.profiles = {
            "so101": type(
                "Profile",
                (),
                {
                    "fingerprint": result.profile_fingerprint,
                    "model_dump": lambda self, mode: {
                        "name": "so101",
                        "fingerprint": self.fingerprint,
                    },
                },
            )()
        }

    def get(self, _preflight_id: str) -> PreflightResult:
        return self.result

    def consume(self, _preflight_id: str) -> PreflightResult:
        self.result = self.result.model_copy(update={"lifecycle": PreflightLifecycle.CONSUMED, "start_eligible": False})
        return self.result


def _passing_preflight(mode: OperatorMode = OperatorMode.RECORD) -> PreflightResult:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    return PreflightResult(
        preflight_id="preflight-1",
        lifecycle=PreflightLifecycle.COMPLETED,
        profile="so101",
        mode=mode,
        profile_fingerprint="profile",
        resource_fingerprint="resource",
        created_at=now,
        expires_at=now + timedelta(seconds=30),
        checks=[],
        ownership_complete=True,
        start_eligible=True,
    )


class TestLerobotStartGate:
    def test_session_defaults_match_established_scripts(self) -> None:
        teleoperate = OperatorSessionSettings.for_mode(OperatorMode.TELEOPERATE)
        record = OperatorSessionSettings.for_mode(OperatorMode.RECORD)

        assert teleoperate.control_fps == 60
        assert record.control_fps == 30
        assert record.task == "Pick <obj> from <loc1> and place in <obj2>"
        assert teleoperate.camera_fps == {"wrist": 30, "front": 30}
        assert teleoperate.max_relative_target is None
        assert record.save_destination == "local"
        assert record.num_episodes == 50

    def test_configured_hardware_reports_policy_capability(self, tmp_path: Path) -> None:
        service = OperatorService(
            adapter_mode="lerobot",
            worker_executable=str(tmp_path / "worker"),
            host_lease_fd=1,
            policy_python=str(tmp_path / "python"),
            policy_checkpoint=str(tmp_path / "checkpoint"),
        )

        capabilities = service.capabilities()

        assert capabilities.reason is None
        assert capabilities.session_start_enabled is True
        assert capabilities.modes == [
            OperatorMode.TELEOPERATE,
            OperatorMode.RECORD,
            OperatorMode.POLICY,
        ]

    def test_record_settings_resolve_timestamped_local_dataset(self, tmp_path: Path) -> None:
        (tmp_path / "so101-demo").mkdir()
        request = StartSessionRequest(
            command_id="record-settings",
            mode=OperatorMode.RECORD,
            settings=OperatorSessionSettings.for_mode(OperatorMode.RECORD),
        )

        resolved = _resolve_worker_settings(
            request,
            data_root=tmp_path,
            camera_names={"wrist", "front"},
            now=lambda: datetime(2026, 7, 22, 12, 34, 56, tzinfo=UTC),
        )

        assert resolved["mode"] == "record"
        assert resolved["dataset_id"] == "so101-demo_20260722_123456"
        assert resolved["dataset_root"] == str(tmp_path / "so101-demo_20260722_123456")
        assert resolved["control_fps"] == 30
        assert resolved["max_relative_target"] is None

    def test_unknown_camera_setting_is_rejected(self, tmp_path: Path) -> None:
        settings = OperatorSessionSettings.for_mode(OperatorMode.TELEOPERATE).model_copy(
            update={"camera_fps": {"side": 30}}
        )
        request = StartSessionRequest(
            command_id="unknown-camera",
            mode=OperatorMode.TELEOPERATE,
            settings=settings,
        )

        with pytest.raises(OperatorPreconditionError, match="camera"):
            _resolve_worker_settings(
                request,
                data_root=tmp_path,
                camera_names={"wrist", "front"},
            )

    def test_policy_settings_require_bounded_targets_and_runtime(self, tmp_path: Path) -> None:
        settings = OperatorSessionSettings.for_mode(OperatorMode.POLICY)
        request = StartSessionRequest(
            command_id="policy-settings",
            mode=OperatorMode.POLICY,
            settings=settings,
        )

        with pytest.raises(OperatorPreconditionError, match="runtime"):
            _resolve_worker_settings(
                request,
                data_root=tmp_path,
                camera_names={"wrist", "front"},
            )

        unbounded = request.model_copy(update={"settings": settings.model_copy(update={"max_relative_target": 6})})
        with pytest.raises(OperatorPreconditionError, match="at most 5"):
            _resolve_worker_settings(
                unbounded,
                data_root=tmp_path,
                camera_names={"wrist", "front"},
                policy_python=tmp_path / "python",
                policy_checkpoint=tmp_path / "checkpoint",
            )

        resolved = _resolve_worker_settings(
            request,
            data_root=tmp_path,
            camera_names={"wrist", "front"},
            policy_python=tmp_path / "python",
            policy_checkpoint=tmp_path / "checkpoint",
            policy_cuda_visible_devices="0",
        )
        assert resolved["policy_python"] == str(tmp_path / "python")
        assert resolved["policy_checkpoint"] == str(tmp_path / "checkpoint")
        assert resolved["policy_cuda_visible_devices"] == "0"

    def test_record_settings_avoid_repeated_timestamp_collisions(self, tmp_path: Path) -> None:
        timestamp = "20260722_123456"
        for dataset_id in (
            "so101-demo",
            f"so101-demo_{timestamp}",
            f"so101-demo_{timestamp}_2",
        ):
            (tmp_path / dataset_id).mkdir()
        settings = OperatorSessionSettings.for_mode(OperatorMode.RECORD).model_copy(
            update={
                "save_destination": "local_and_hub",
                "hub_repo_id": "owner/operator-dataset",
            }
        )

        resolved = _resolve_worker_settings(
            StartSessionRequest(
                command_id="record-collision",
                mode=OperatorMode.RECORD,
                settings=settings,
            ),
            data_root=tmp_path,
            camera_names={"wrist", "front"},
            now=lambda: datetime(2026, 7, 22, 12, 34, 56, tzinfo=UTC),
        )

        assert resolved["dataset_id"] == f"so101-demo_{timestamp}_3"
        assert resolved["repo_id"] == "owner/operator-dataset"

    async def test_fresh_passing_preflight_still_returns_not_implemented(self) -> None:
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=_PreflightResults(_passing_preflight()),
        )

        with pytest.raises(NotImplementedError):
            await service.start(
                StartSessionRequest(
                    command_id="lerobot-start",
                    mode=OperatorMode.RECORD,
                    profile="so101",
                    preflight_id="preflight-1",
                    preflight_fingerprint="resource",
                )
            )

    async def test_cleanup_unconfirmed_blocks_restart(self) -> None:
        service = OperatorService(adapter_mode="simulated")
        service._status = service.status().model_copy(
            update={"state": SessionState.FAILED, "cleanup_unconfirmed": True}
        )

        with pytest.raises(OperatorPreconditionError, match="cleanup"):
            await service.start(StartSessionRequest(command_id="blocked-restart", mode=OperatorMode.TELEOPERATE))

    @pytest.mark.parametrize("field", ["preflight_id", "preflight_fingerprint"])
    async def test_missing_preflight_evidence_is_rejected(self, field: str) -> None:
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=_PreflightResults(_passing_preflight()),
        )
        request = StartSessionRequest(
            command_id="lerobot-start",
            mode=OperatorMode.RECORD,
            profile="so101",
            preflight_id="preflight-1",
            preflight_fingerprint="resource",
        ).model_copy(update={field: None})

        with pytest.raises(OperatorConflictError, match="preflight"):
            await service.start(request)

    async def test_changed_or_ineligible_preflight_is_rejected(self) -> None:
        result = _passing_preflight().model_copy(update={"start_eligible": False})
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=_PreflightResults(result),
        )

        with pytest.raises(OperatorConflictError, match="eligible"):
            await service.start(
                StartSessionRequest(
                    command_id="lerobot-start",
                    mode=OperatorMode.RECORD,
                    profile="so101",
                    preflight_id="preflight-1",
                    preflight_fingerprint="resource",
                )
            )

    async def test_configured_worker_runs_through_service_lifecycle(self, tmp_path: Path) -> None:
        source = Path(__file__).parents[1] / "scripts/fake_operator_worker.py"
        executable = tmp_path / "fake-worker"
        executable.write_text(
            "#!/usr/bin/python3.12\n" + source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        lease_fd = os.open("/dev/null", os.O_RDONLY)
        preflight = _passing_preflight(OperatorMode.TELEOPERATE)
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=_PreflightResults(preflight),
            worker_executable=str(executable),
            host_lease_fd=lease_fd,
            command_timeout_s=2.0,
        )
        try:
            started = await service.start(
                StartSessionRequest(
                    command_id="hardware-start",
                    mode=OperatorMode.TELEOPERATE,
                    profile="so101",
                    preflight_id=preflight.preflight_id,
                    preflight_fingerprint=preflight.resource_fingerprint,
                )
            )
            stopped = await service.stop(started.session_id, command_id="hardware-stop")
        finally:
            await service.shutdown()
            os.close(lease_fd)

        assert started.state is SessionState.RUNNING
        assert stopped.state is SessionState.CANCELLED
        assert stopped.cleanup_unconfirmed is False

    async def test_incomplete_process_visibility_does_not_block_transactional_acquisition(self, tmp_path: Path) -> None:
        source = Path(__file__).parents[1] / "scripts/fake_operator_worker.py"
        executable = tmp_path / "fake-worker"
        executable.write_text(
            "#!/usr/bin/python3.12\n" + source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        lease_fd = os.open("/dev/null", os.O_RDONLY)
        preflight = _passing_preflight(OperatorMode.TELEOPERATE).model_copy(update={"ownership_complete": False})
        service = OperatorService(
            adapter_mode="lerobot",
            preflight_service=_PreflightResults(preflight),
            worker_executable=str(executable),
            host_lease_fd=lease_fd,
            command_timeout_s=2.0,
        )
        try:
            started = await service.start(
                StartSessionRequest(
                    command_id="visibility-warning-start",
                    mode=OperatorMode.TELEOPERATE,
                    profile="so101",
                    preflight_id=preflight.preflight_id,
                    preflight_fingerprint=preflight.resource_fingerprint,
                )
            )
            stopped = await service.stop(started.session_id, command_id="visibility-warning-stop")
        finally:
            await service.shutdown()
            os.close(lease_fd)

        assert started.state is SessionState.RUNNING
        assert stopped.cleanup_unconfirmed is False
