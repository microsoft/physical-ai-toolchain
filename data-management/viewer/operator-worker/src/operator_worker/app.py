"""Fail-closed protocol dispatch for one operator worker lifecycle."""

from __future__ import annotations

import base64
import importlib.metadata
import os
import queue
import sys
import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TextIO

from .identity import compute_profile_fingerprint, compute_resource_fingerprint, compute_simulation_resource_fingerprint
from .protocol import (
    MAX_COMMAND_BYTES,
    ActionCommand,
    InitializeCommand,
    RunCommand,
    SessionSettings,
    StopCommand,
    WorkerCleanup,
    WorkerCommand,
    WorkerCommandAcknowledgement,
    WorkerHello,
    WorkerInitialized,
    WorkerPreview,
    WorkerRate,
    WorkerRunning,
    WorkerTelemetry,
    parse_worker_command,
    serialize_worker_event,
)
from .resources import CleanupReport


class Runtime(Protocol):
    def acquire(self) -> None: ...

    def enable_motion(self) -> None: ...

    def teleoperate(self) -> None: ...

    def record(self) -> None: ...

    def policy(self) -> None: ...

    def command(self, action: str) -> Any: ...

    def request_stop(self) -> None: ...

    def set_rate_callback(self, callback: Callable[[Any], None]) -> None: ...

    def set_telemetry_callback(self, callback: Callable[[Any], None]) -> None: ...

    def set_preview_callback(self, callback: Callable[[str, bytes, float], None]) -> None: ...

    def cleanup(self) -> CleanupReport: ...

    def upload_after_cleanup(self) -> tuple[bool, bool, str | None]: ...


class WorkerApplication:
    """Own sequencing, dispatch, and terminal cleanup state for one session."""

    def __init__(
        self,
        *,
        session_id: str,
        input_stream: TextIO,
        output_stream: TextIO,
        runtime_factory: Callable[[Mapping[str, Any], SessionSettings], Runtime],
        resource_fingerprint: Callable[[Mapping[str, Any], SessionSettings], str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.runtime_factory = runtime_factory
        self.resource_fingerprint = resource_fingerprint or self._compute_resource_fingerprint
        self._events_sequence = 0
        self._command_sequence = 0
        self._service_instance_id: str | None = None
        self._command_ids: set[str] = set()
        self._commands: queue.Queue[WorkerCommand | BaseException | None] = queue.Queue()
        self._runtime_errors: queue.Queue[BaseException] = queue.Queue(maxsize=1)
        self._output_lock = threading.Lock()
        self._has_run = False
        self._terminal = False

    def run(self) -> int:
        """Execute one initialize, run, and cleanup lifecycle."""
        if self._has_run or self._terminal:
            raise RuntimeError("Worker application cannot be restarted")
        self._has_run = True
        self._start_reader()
        self._emit(
            WorkerHello(
                session_id=self.session_id,
                worker_version="0.1.0",
                python_version=".".join(str(value) for value in sys.version_info[:3]),
                lerobot_version=importlib.metadata.version("lerobot"),
                pid=os.getpid(),
                supported_modes=["teleoperate", "record", "policy"],
            )
        )
        initialize = self._next_command(InitializeCommand)
        self._validate_command(initialize)
        profile = initialize.profile
        if initialize.profile_fingerprint != profile.get("fingerprint"):
            raise RuntimeError("Initialize profile fingerprint does not match the profile snapshot")
        if compute_profile_fingerprint(profile) != initialize.profile_fingerprint:
            raise RuntimeError("Initialize profile fingerprint is invalid")
        if self.resource_fingerprint(profile, initialize.settings) != initialize.resource_fingerprint:
            raise RuntimeError("Initialize resource fingerprint does not match current hardware identity")
        runtime = self.runtime_factory(profile, initialize.settings)
        self._register_callbacks(runtime, initialize.service_instance_id)
        command_id = "worker-exit"
        correlation_id = initialize.correlation_id
        primary_error: BaseException | None = None
        runtime_thread: threading.Thread | None = None
        cleanup = CleanupReport(False, (), ("cleanup did not run",), False)
        try:
            runtime.acquire()
            self._emit(
                WorkerInitialized(
                    service_instance_id=initialize.service_instance_id,
                    session_id=self.session_id,
                    sequence=self._next_event_sequence(),
                    startup_nonce=initialize.startup_nonce,
                    resources={"hardware": "acquired_torque_off"},
                )
            )
            command = self._next_command((RunCommand, StopCommand))
            self._validate_command(command)
            if isinstance(command, StopCommand):
                command_id = command.command_id
                correlation_id = command.correlation_id
                runtime.request_stop()
            else:
                runtime.enable_motion()
                self._emit(
                    WorkerRunning(
                        service_instance_id=initialize.service_instance_id,
                        session_id=self.session_id,
                        sequence=self._next_event_sequence(),
                        torque_enabled=True,
                    )
                )
                runtime_thread = threading.Thread(
                    target=self._run_runtime,
                    args=(runtime, initialize.settings.mode),
                    daemon=True,
                )
                runtime_thread.start()
                command_id, correlation_id = self._dispatch_until_stopped(
                    runtime, runtime_thread, command_id, correlation_id
                )
        except BaseException as error:
            primary_error = error
        finally:
            runtime.request_stop()
            if runtime_thread is not None:
                runtime_thread.join(timeout=2.0)
                if runtime_thread.is_alive() and primary_error is None:
                    primary_error = RuntimeError("Runtime did not stop within the cleanup deadline")
            try:
                cleanup = runtime.cleanup()
            except Exception as error:
                cleanup = CleanupReport(False, (), (f"cleanup raised: {error}",), False)
            upload_attempted = False
            upload_succeeded = False
            upload_error: str | None = None
            if hasattr(runtime, "upload_after_cleanup"):
                upload_attempted, upload_succeeded, upload_error = runtime.upload_after_cleanup()
            self._terminal = not cleanup.cleanup_complete or not cleanup.torque_verified_off
            self._emit(
                WorkerCleanup(
                    service_instance_id=initialize.service_instance_id,
                    session_id=self.session_id,
                    sequence=self._next_event_sequence(),
                    command_id=command_id,
                    correlation_id=correlation_id,
                    cleanup_complete=cleanup.cleanup_complete,
                    torque_verified_off=cleanup.torque_verified_off,
                    released=list(cleanup.released),
                    errors=list(cleanup.errors),
                    upload_attempted=upload_attempted,
                    upload_succeeded=upload_succeeded,
                    upload_error=upload_error,
                )
            )
        if primary_error is not None:
            raise primary_error
        return 0 if cleanup.cleanup_complete and cleanup.torque_verified_off else 1

    def _start_reader(self) -> None:
        def read_commands() -> None:
            while line := self.input_stream.readline(MAX_COMMAND_BYTES + 1):
                try:
                    self._commands.put(parse_worker_command(line))
                except BaseException as error:
                    self._commands.put(error)
                    return
            self._commands.put(None)

        threading.Thread(target=read_commands, daemon=True).start()

    def _next_command(self, expected_type: Any) -> WorkerCommand:
        command = self._commands.get()
        if command is None:
            raise EOFError("Parent closed the worker command stream")
        if isinstance(command, BaseException):
            raise command
        if not isinstance(command, expected_type):
            raise RuntimeError(f"Unexpected worker command: {command.type}")
        return command

    def _validate_command(self, command: WorkerCommand) -> None:
        if command.session_id != self.session_id:
            raise RuntimeError("Command session does not match the worker session")
        if command.worker_pid != os.getpid():
            raise RuntimeError("Command process identity does not match this worker")
        if self._service_instance_id is None:
            self._service_instance_id = command.service_instance_id
        elif command.service_instance_id != self._service_instance_id:
            raise RuntimeError("Command service instance does not match initialization")
        if command.sequence != self._command_sequence + 1:
            raise RuntimeError("Command sequence is stale, duplicate, or out of order")
        if command.command_id in self._command_ids:
            raise RuntimeError("Command identifier is duplicated")
        self._command_ids.add(command.command_id)
        self._command_sequence = command.sequence

    def _dispatch_until_stopped(
        self,
        runtime: Runtime,
        runtime_thread: threading.Thread,
        command_id: str,
        correlation_id: str,
    ) -> tuple[str, str]:
        while runtime_thread.is_alive():
            try:
                command = self._commands.get(timeout=0.1)
            except queue.Empty:
                self._raise_runtime_error()
                continue
            if command is None:
                raise EOFError("Parent closed the worker command stream")
            if isinstance(command, BaseException):
                raise command
            self._validate_command(command)
            if isinstance(command, StopCommand):
                runtime.request_stop()
                return command.command_id, command.correlation_id
            if not isinstance(command, ActionCommand):
                raise RuntimeError(f"Unexpected worker command: {command.type}")
            result = runtime.command(command.action)
            self._emit(
                WorkerCommandAcknowledgement(
                    service_instance_id=command.service_instance_id,
                    session_id=self.session_id,
                    sequence=self._next_event_sequence(),
                    command_id=command.command_id,
                    correlation_id=command.correlation_id,
                    action=command.action,
                    dataset_id=result.dataset_id,
                    episode_index=result.episode_index,
                    phase=result.phase,
                    finalized=result.phase == "finalized",
                )
            )
            if result.should_stop:
                return command.command_id, command.correlation_id
        self._raise_runtime_error()
        return command_id, correlation_id

    def _run_runtime(self, runtime: Runtime, mode: str) -> None:
        try:
            if mode == "record":
                runtime.record()
            elif mode == "policy":
                runtime.policy()
            else:
                runtime.teleoperate()
        except BaseException as error:
            self._runtime_errors.put(error)

    def _register_callbacks(self, runtime: Runtime, service_instance_id: str) -> None:
        if hasattr(runtime, "set_rate_callback"):
            runtime.set_rate_callback(lambda metrics: self._emit_rate(service_instance_id, metrics))
        if hasattr(runtime, "set_telemetry_callback"):
            runtime.set_telemetry_callback(lambda sample: self._emit_telemetry(service_instance_id, sample))
        if hasattr(runtime, "set_preview_callback"):
            runtime.set_preview_callback(
                lambda camera, jpeg, captured_at_s: self._emit_preview(service_instance_id, camera, jpeg, captured_at_s)
            )

    def _raise_runtime_error(self) -> None:
        try:
            error = self._runtime_errors.get_nowait()
        except queue.Empty:
            return
        raise error

    def _emit(self, event: Any) -> None:
        with self._output_lock:
            self.output_stream.write(serialize_worker_event(event))
            self.output_stream.flush()

    def _emit_rate(self, service_instance_id: str, metrics: Any) -> None:
        self._emit(
            WorkerRate(
                service_instance_id=service_instance_id,
                session_id=self.session_id,
                sequence=self._next_event_sequence(),
                target_hz=metrics.target_hz,
                actual_hz=metrics.actual_hz,
                loop_p50_ms=metrics.loop_p50_ms,
                loop_p95_ms=metrics.loop_p95_ms,
                loop_max_ms=metrics.loop_max_ms,
                overruns=metrics.overruns,
            )
        )

    def _emit_telemetry(self, service_instance_id: str, sample: Any) -> None:
        self._emit(
            WorkerTelemetry(
                service_instance_id=service_instance_id,
                session_id=self.session_id,
                sequence=self._next_event_sequence(),
                elapsed_s=sample.elapsed_s,
                leader=sample.leader,
                follower=sample.follower,
                commanded=sample.commanded,
            )
        )

    def _emit_preview(self, service_instance_id: str, camera: str, jpeg: bytes, captured_at_s: float) -> None:
        if len(jpeg) > 300_000:
            raise RuntimeError("Camera preview exceeds the protocol size limit")
        self._emit(
            WorkerPreview(
                service_instance_id=service_instance_id,
                session_id=self.session_id,
                sequence=self._next_event_sequence(),
                camera=camera,
                captured_at_s=captured_at_s,
                jpeg_base64=base64.b64encode(jpeg).decode("ascii"),
            )
        )

    def _next_event_sequence(self) -> int:
        self._events_sequence += 1
        return self._events_sequence

    @staticmethod
    def _compute_resource_fingerprint(profile: Mapping[str, Any], settings: SessionSettings) -> str:
        if settings.execution_mode == "simulation":
            return compute_simulation_resource_fingerprint(profile, mode=settings.mode)
        return compute_resource_fingerprint(profile, mode=settings.mode)
