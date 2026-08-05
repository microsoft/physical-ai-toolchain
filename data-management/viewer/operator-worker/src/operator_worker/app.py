"""Protocol-driven operator worker lifecycle."""

from __future__ import annotations

import base64
import importlib.metadata
import os
import queue
import sys
import threading
from collections.abc import Callable
from typing import Protocol, TextIO

from .acquisition import CleanupReport
from .config import SessionSettings, WorkerProfile
from .identity import compute_resource_fingerprint
from .protocol import (
    WORKER_COMMAND_ADAPTER,
    ActionCommand,
    InitializeCommand,
    RunCommand,
    StopCommand,
    WorkerCleanup,
    WorkerCommandAcknowledgement,
    WorkerHello,
    WorkerInitialized,
    WorkerPreview,
    WorkerRate,
    WorkerRunning,
    WorkerTelemetry,
)
from .teleoperate import JointTelemetry, LoopMetrics


class Runtime(Protocol):
    """Runtime behavior required by the worker application."""

    def acquire(self) -> None: ...

    def enable_motion(self) -> None: ...

    def teleoperate(self) -> None: ...

    def record(self) -> None: ...

    def policy(self) -> None: ...

    def command(self, action: str): ...

    def set_rate_callback(self, callback: Callable[[LoopMetrics], None]) -> None: ...

    def set_telemetry_callback(
        self, callback: Callable[[JointTelemetry], None]
    ) -> None: ...

    def set_preview_callback(
        self, callback: Callable[[str, bytes, float], None]
    ) -> None: ...

    def request_stop(self) -> None: ...

    def discard_recording(self) -> None: ...

    def cleanup(self) -> CleanupReport: ...

    def upload_after_cleanup(self) -> tuple[bool, bool, str | None]: ...


class WorkerApplication:
    """Own protocol sequencing around one hardware runtime."""

    def __init__(
        self,
        *,
        session_id: str,
        input_stream: TextIO,
        output_stream: TextIO,
        runtime_factory: Callable[[WorkerProfile, SessionSettings], Runtime],
        resource_fingerprint: Callable[[WorkerProfile, str], str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.runtime_factory = runtime_factory
        self.resource_fingerprint = resource_fingerprint or (
            lambda profile, mode: compute_resource_fingerprint(profile, mode=mode)
        )
        self._sequence = 0
        self._commands: queue.Queue[str | None] = queue.Queue()
        self._teleop_errors: queue.Queue[BaseException] = queue.Queue(maxsize=1)
        self._output_lock = threading.Lock()
        self._sequence_lock = threading.Lock()
        self._runtime: Runtime | None = None
        self._stop_requested = threading.Event()
        self._pending_stop: StopCommand | None = None
        self._mode = ""

    def run(self) -> int:
        """Execute one initialize, run, and cleanup lifecycle."""
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
        self._mode = initialize.settings.mode
        profile = WorkerProfile.model_validate(initialize.profile)
        self._validate_context(
            initialize.service_instance_id,
            initialize.session_id,
            initialize.sequence,
            expected_sequence=1,
        )
        if (
            initialize.profile_fingerprint != profile.fingerprint
            or profile.computed_fingerprint() != profile.fingerprint
        ):
            raise RuntimeError(
                "Initialize profile fingerprint does not match the profile snapshot"
            )
        if (
            self.resource_fingerprint(profile, initialize.settings.mode)
            != initialize.resource_fingerprint
        ):
            raise RuntimeError(
                "Initialize resource fingerprint does not match current hardware identity"
            )
        runtime = self.runtime_factory(profile, initialize.settings)
        self._runtime = runtime
        if self._stop_requested.is_set():
            self._request_cancel(runtime)
        runtime.set_rate_callback(
            lambda metrics: self._emit_rate(initialize.service_instance_id, metrics)
        )
        runtime.set_telemetry_callback(
            lambda sample: self._emit_telemetry(initialize.service_instance_id, sample)
        )
        runtime.set_preview_callback(
            lambda camera, jpeg, captured_at_s: self._emit_preview(
                initialize.service_instance_id, camera, jpeg, captured_at_s
            )
        )
        command_id = "worker-exit"
        cleanup: CleanupReport | None = None
        primary_error: BaseException | None = None
        teleop_thread: threading.Thread | None = None
        try:
            runtime.acquire()
            self._emit(
                WorkerInitialized(
                    service_instance_id=initialize.service_instance_id,
                    session_id=self.session_id,
                    sequence=self._next_sequence(),
                    startup_nonce=initialize.startup_nonce,
                    resources={
                        "wrist": "acquired",
                        "front": "acquired",
                        "follower": "acquired_torque_off",
                        **(
                            {"policy": "ready"}
                            if initialize.settings.mode == "policy"
                            else {"leader": "acquired"}
                        ),
                    },
                )
            )
            next_command = self._next_command((RunCommand, StopCommand))
            if isinstance(next_command, StopCommand):
                self._validate_context(
                    next_command.service_instance_id,
                    next_command.session_id,
                    next_command.sequence,
                    minimum_sequence=2,
                    expected_service_instance_id=initialize.service_instance_id,
                )
                command_id = next_command.command_id
                self._request_cancel(runtime)
                return 0
            run_command = next_command
            self._validate_context(
                run_command.service_instance_id,
                run_command.session_id,
                run_command.sequence,
                expected_sequence=2,
                expected_service_instance_id=initialize.service_instance_id,
            )
            if self._stop_requested.is_set():
                if self._pending_stop is not None:
                    command_id = self._pending_stop.command_id
                self._request_cancel(runtime)
                return 0
            runtime.enable_motion()
            self._emit(
                WorkerRunning(
                    service_instance_id=run_command.service_instance_id,
                    session_id=self.session_id,
                    sequence=self._next_sequence(),
                    torque_enabled=True,
                )
            )
            teleop_thread = threading.Thread(
                target=self._run_session,
                args=(runtime, initialize.settings.mode),
                daemon=True,
            )
            teleop_thread.start()
            while teleop_thread.is_alive():
                try:
                    raw_command = self._commands.get(timeout=0.1)
                except queue.Empty:
                    self._raise_teleop_error()
                    continue
                if raw_command is None:
                    runtime.request_stop()
                    break
                command = WORKER_COMMAND_ADAPTER.validate_json(raw_command)
                if isinstance(command, StopCommand):
                    self._validate_context(
                        command.service_instance_id,
                        command.session_id,
                        command.sequence,
                        minimum_sequence=3,
                        expected_service_instance_id=initialize.service_instance_id,
                    )
                    command_id = command.command_id
                    self._request_cancel(runtime)
                    break
                if isinstance(command, ActionCommand):
                    self._validate_context(
                        command.service_instance_id,
                        command.session_id,
                        command.sequence,
                        minimum_sequence=3,
                        expected_service_instance_id=initialize.service_instance_id,
                    )
                    result = runtime.command(command.action)
                    self._emit(
                        WorkerCommandAcknowledgement(
                            service_instance_id=initialize.service_instance_id,
                            session_id=self.session_id,
                            sequence=self._next_sequence(),
                            command_id=command.command_id,
                            action=command.action,
                            dataset_id=result.dataset_id,
                            episode_index=result.episode_index,
                            phase=result.phase,
                            finalized=result.phase == "finalized",
                        )
                    )
                    if result.should_stop:
                        command_id = command.command_id
                        break
            if teleop_thread is not None:
                teleop_thread.join(timeout=2.0)
                if teleop_thread.is_alive():
                    raise RuntimeError(
                        "Teleoperation loop did not stop within the cleanup deadline"
                    )
            self._raise_teleop_error()
            if command_id == "worker-exit" and self._pending_stop is not None:
                self._validate_context(
                    self._pending_stop.service_instance_id,
                    self._pending_stop.session_id,
                    self._pending_stop.sequence,
                    minimum_sequence=3,
                    expected_service_instance_id=initialize.service_instance_id,
                )
                command_id = self._pending_stop.command_id
        except BaseException as error:
            primary_error = error
        finally:
            if command_id == "worker-exit" and self._pending_stop is not None:
                command_id = self._pending_stop.command_id
            runtime.request_stop()
            if teleop_thread is not None and teleop_thread.is_alive():
                teleop_thread.join(timeout=2.0)
            cleanup = runtime.cleanup()
            upload_attempted, upload_succeeded, upload_error = (
                runtime.upload_after_cleanup()
            )
            self._emit(
                WorkerCleanup(
                    service_instance_id=initialize.service_instance_id,
                    session_id=self.session_id,
                    sequence=self._next_sequence(),
                    command_id=command_id,
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
        return 0 if cleanup.cleanup_complete else 1

    def _start_reader(self) -> None:
        def read_commands() -> None:
            for line in self.input_stream:
                try:
                    command = WORKER_COMMAND_ADAPTER.validate_json(line)
                except Exception:
                    command = None
                if isinstance(command, StopCommand) and self._runtime is not None:
                    self._stop_requested.set()
                    self._pending_stop = command
                    self._request_cancel(self._runtime)
                elif isinstance(command, StopCommand):
                    self._stop_requested.set()
                    self._pending_stop = command
                self._commands.put(line)
            self._commands.put(None)

        threading.Thread(target=read_commands, daemon=True).start()

    def _request_cancel(self, runtime: Runtime) -> None:
        if self._mode == "record":
            runtime.discard_recording()
        else:
            runtime.request_stop()

    def _next_command(self, expected_type):
        raw_command = self._commands.get()
        if raw_command is None:
            raise EOFError("Parent closed the worker command stream")
        command = WORKER_COMMAND_ADAPTER.validate_json(raw_command)
        if not isinstance(command, expected_type):
            expected_name = (
                "/".join(item.__name__ for item in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            raise RuntimeError(
                f"Expected {expected_name}, received {type(command).__name__}"
            )
        return command

    def _run_session(self, runtime: Runtime, mode: str) -> None:
        try:
            if mode == "record":
                runtime.record()
            elif mode == "policy":
                runtime.policy()
            else:
                runtime.teleoperate()
        except BaseException as error:
            self._teleop_errors.put(error)

    def _raise_teleop_error(self) -> None:
        try:
            error = self._teleop_errors.get_nowait()
        except queue.Empty:
            return
        raise error

    def _emit(self, event) -> None:
        with self._output_lock:
            self.output_stream.write(event.model_dump_json() + "\n")
            self.output_stream.flush()

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence

    def _emit_rate(self, service_instance_id: str, metrics: LoopMetrics) -> None:
        self._emit(
            WorkerRate(
                service_instance_id=service_instance_id,
                session_id=self.session_id,
                sequence=self._next_sequence(),
                target_hz=metrics.target_hz,
                actual_hz=metrics.actual_hz,
                loop_p50_ms=metrics.loop_p50_ms,
                loop_p95_ms=metrics.loop_p95_ms,
                loop_max_ms=metrics.loop_max_ms,
                overruns=metrics.overruns,
            )
        )

    def _emit_telemetry(self, service_instance_id: str, sample: JointTelemetry) -> None:
        self._emit(
            WorkerTelemetry(
                service_instance_id=service_instance_id,
                session_id=self.session_id,
                sequence=self._next_sequence(),
                elapsed_s=sample.elapsed_s,
                leader=sample.leader,
                follower=sample.follower,
                commanded=sample.commanded,
            )
        )

    def _emit_preview(
        self,
        service_instance_id: str,
        camera: str,
        jpeg: bytes,
        captured_at_s: float,
    ) -> None:
        if len(jpeg) > 300_000:
            raise RuntimeError("Camera preview exceeds the protocol size limit")
        self._emit(
            WorkerPreview(
                service_instance_id=service_instance_id,
                session_id=self.session_id,
                sequence=self._next_sequence(),
                camera=camera,
                captured_at_s=captured_at_s,
                jpeg_base64=base64.b64encode(jpeg).decode("ascii"),
            )
        )

    def _validate_context(
        self,
        service_instance_id: str,
        session_id: str,
        sequence: int,
        *,
        expected_sequence: int | None = None,
        expected_service_instance_id: str | None = None,
        minimum_sequence: int | None = None,
    ) -> None:
        if session_id != self.session_id:
            raise RuntimeError("Command session does not match the worker session")
        if (
            expected_service_instance_id is not None
            and service_instance_id != expected_service_instance_id
        ):
            raise RuntimeError("Command service instance does not match initialization")
        if minimum_sequence is not None:
            if sequence < minimum_sequence:
                raise RuntimeError("Command sequence is out of order")
        elif expected_sequence is None or sequence != expected_sequence:
            raise RuntimeError("Command sequence is out of order")
