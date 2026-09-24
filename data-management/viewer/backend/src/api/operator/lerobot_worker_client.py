"""Protocol-v2 supervisor for the isolated LeRobot hardware worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from .models import OperatorAction, OperatorMode
from .protocol import (
    HARDWARE_WORKER_EVENT_ADAPTER,
    MAX_EVENT_BYTES,
    HardwareActionCommand,
    HardwareInitializeCommand,
    HardwareRunCommand,
    HardwareStopCommand,
    HardwareWorkerCleanup,
    HardwareWorkerCommandAcknowledgement,
    HardwareWorkerHello,
    HardwareWorkerInitialized,
    HardwareWorkerPreview,
    HardwareWorkerRate,
    HardwareWorkerRunning,
    HardwareWorkerTelemetry,
)


@dataclass(frozen=True)
class WorkerAcknowledgement:
    session_id: str
    command_id: str
    action: OperatorAction
    cleanup_complete: bool = False
    dataset_id: str | None = None
    episode_index: int | None = None
    recording_phase: str | None = None
    upload_attempted: bool = False
    upload_succeeded: bool = False
    upload_error: str | None = None


class LerobotWorkerClient:
    """Launch and identity-bind one isolated hardware worker."""

    def __init__(
        self,
        *,
        command: list[str],
        timeout_s: float,
        service_instance_id: str,
        profile: dict[str, object],
        profile_fingerprint: str,
        resource_fingerprint: str,
        settings: dict[str, object],
        environment: dict[str, str] | None = None,
        lease_fd: int | None = None,
        on_rate: Callable[[HardwareWorkerRate], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_telemetry: Callable[[HardwareWorkerTelemetry], None] | None = None,
        on_preview: Callable[[HardwareWorkerPreview], None] | None = None,
        startup_timeout_s: float | None = None,
        stop_timeout_s: float | None = None,
        recovery_timeout_s: float | None = None,
    ) -> None:
        self.command_line = command
        self.timeout_s = timeout_s
        self.service_instance_id = service_instance_id
        self.profile = profile
        self.profile_fingerprint = profile_fingerprint
        self.resource_fingerprint = resource_fingerprint
        self.settings = settings
        self.environment = environment or {}
        self.lease_fd = lease_fd
        self.pass_fds = (lease_fd,) if lease_fd is not None else ()
        self.on_rate = on_rate
        self.on_log = on_log
        self.on_telemetry = on_telemetry
        self.on_preview = on_preview
        self.startup_timeout_s = startup_timeout_s or timeout_s
        self.stop_timeout_s = stop_timeout_s or timeout_s
        self.recovery_timeout_s = recovery_timeout_s or timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._handshake_task: asyncio.Task[None] | None = None
        self._hello: asyncio.Future[HardwareWorkerHello] | None = None
        self._initialized: asyncio.Future[HardwareWorkerInitialized] | None = None
        self._running: asyncio.Future[HardwareWorkerRunning] | None = None
        self._cleanup: asyncio.Future[HardwareWorkerCleanup] | None = None
        self._session_id = ""
        self._startup_nonce = ""
        self._stderr: deque[str] = deque(maxlen=200)
        self._last_worker_sequence = 0
        self._next_command_sequence = 3
        self._expected_cleanup_command_id: str | None = None
        self._expected_cleanup_correlation_id: str | None = None
        self._event_phase = "hello"
        self._mode = OperatorMode.TELEOPERATE
        self._command_acks: dict[str, asyncio.Future[HardwareWorkerCommandAcknowledgement]] = {}
        self._expected_command_acks: dict[str, tuple[str, OperatorAction]] = {}
        self.torque_verified_off = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr)

    def build_environment(self, session_id: str) -> dict[str, str]:
        environment = {
            "OPERATOR_SESSION_ID": session_id,
            "OPERATOR_PARENT_PID": str(os.getpid()),
            "PYTHONUNBUFFERED": "1",
            **self.environment,
        }
        if self.lease_fd is not None:
            environment["OPERATOR_HOST_LEASE_FD"] = str(self.lease_fd)
        return environment

    async def launch(self, session_id: str, mode: OperatorMode) -> None:
        self._session_id = session_id
        self._mode = mode
        self._startup_nonce = secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        self._hello = loop.create_future()
        self._initialized = loop.create_future()
        self._running = loop.create_future()
        self._cleanup = loop.create_future()
        self._process = await asyncio.create_subprocess_exec(
            *self.command_line,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.build_environment(session_id),
            pass_fds=self.pass_fds,
            limit=MAX_EVENT_BYTES + 1,
        )
        self._reader_task = asyncio.create_task(self._read_events())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._handshake_task = asyncio.create_task(self._handshake())

    async def wait_ready(self) -> None:
        if self._handshake_task is None:
            raise RuntimeError("LeRobot worker was not launched")
        try:
            await asyncio.wait_for(asyncio.shield(self._handshake_task), timeout=self.startup_timeout_s)
        except TimeoutError as error:
            raise RuntimeError("LeRobot worker handshake timed out") from error

    async def wait(self) -> int:
        if self._process is None:
            raise RuntimeError("LeRobot worker was not launched")
        return await self._process.wait()

    async def command(self, session_id: str, command_id: str, action: OperatorAction) -> WorkerAcknowledgement:
        if session_id != self._session_id or self.pid is None:
            raise RuntimeError("Invalid LeRobot worker command")
        sequence = self._next_command_sequence
        self._next_command_sequence += 1
        correlation_id = secrets.token_hex(16)
        if action is OperatorAction.CANCEL:
            cleanup = await self._send_stop(session_id, command_id, correlation_id, sequence)
            return WorkerAcknowledgement(
                session_id=session_id,
                command_id=command_id,
                action=action,
                cleanup_complete=cleanup.cleanup_complete and cleanup.torque_verified_off,
            )
        if self._mode is not OperatorMode.RECORD:
            raise RuntimeError("Episode commands require record mode")
        future = asyncio.get_running_loop().create_future()
        self._command_acks[command_id] = future
        self._expected_command_acks[command_id] = (correlation_id, action)
        if action is OperatorAction.FINISH:
            self._expected_cleanup_command_id = command_id
            self._expected_cleanup_correlation_id = correlation_id
            self._event_phase = "cleanup"
        await self._send(
            HardwareActionCommand(
                service_instance_id=self.service_instance_id,
                session_id=session_id,
                sequence=sequence,
                command_id=command_id,
                correlation_id=correlation_id,
                worker_pid=self.pid,
                action=action.value,
            ).model_dump_json()
        )
        try:
            progress = await asyncio.wait_for(asyncio.shield(future), timeout=max(self.timeout_s, 120.0))
        finally:
            self._command_acks.pop(command_id, None)
            self._expected_command_acks.pop(command_id, None)
        cleanup = await self._await_cleanup(max(self.timeout_s, 120.0)) if action is OperatorAction.FINISH else None
        return WorkerAcknowledgement(
            session_id=session_id,
            command_id=command_id,
            action=action,
            cleanup_complete=bool(cleanup and cleanup.cleanup_complete and cleanup.torque_verified_off),
            dataset_id=progress.dataset_id,
            episode_index=progress.episode_index,
            recording_phase=progress.phase,
            upload_attempted=cleanup.upload_attempted if cleanup else False,
            upload_succeeded=cleanup.upload_succeeded if cleanup else False,
            upload_error=cleanup.upload_error if cleanup else None,
        )

    async def _send_stop(
        self,
        session_id: str,
        command_id: str,
        correlation_id: str,
        sequence: int,
    ) -> HardwareWorkerCleanup:
        if self._cleanup is None or self.pid is None:
            raise RuntimeError("LeRobot worker cleanup channel is unavailable")
        self._expected_cleanup_command_id = command_id
        self._expected_cleanup_correlation_id = correlation_id
        self._event_phase = "cleanup"
        await self._send(
            HardwareStopCommand(
                service_instance_id=self.service_instance_id,
                session_id=session_id,
                sequence=sequence,
                command_id=command_id,
                correlation_id=correlation_id,
                worker_pid=self.pid,
            ).model_dump_json()
        )
        return await self._await_cleanup(self.stop_timeout_s)

    async def _await_cleanup(self, timeout_s: float) -> HardwareWorkerCleanup:
        if self._cleanup is None:
            raise RuntimeError("LeRobot worker cleanup channel is unavailable")
        cleanup = await asyncio.wait_for(asyncio.shield(self._cleanup), timeout=timeout_s)
        self.torque_verified_off = cleanup.torque_verified_off
        if self._process is not None:
            await asyncio.wait_for(self._process.wait(), timeout=timeout_s)
        return cleanup

    async def terminate(self) -> bool:
        process = self._process
        if process is None:
            return False
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.stop_timeout_s)
            except TimeoutError:
                process.kill()
                await process.wait()
        await self._close_tasks()
        return self.torque_verified_off

    async def recover(self) -> bool:
        environment = self.build_environment(self._session_id or "recovery")
        environment["OPERATOR_PROFILE_JSON"] = json.dumps(self.profile, separators=(",", ":"))
        process = await asyncio.create_subprocess_exec(
            *self.command_line,
            "--deenergize",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            pass_fds=self.pass_fds,
        )
        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(self._read_limited(process.stdout), self._read_limited(process.stderr), process.wait()),
                timeout=self.recovery_timeout_s,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return False
        self._stderr.extend(self._sanitize_log(stderr.decode(errors="replace")).splitlines(keepends=True))
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return False
        self.torque_verified_off = bool(
            process.returncode == 0 and result.get("cleanup_complete") and result.get("torque_verified_off")
        )
        return self.torque_verified_off

    async def _handshake(self) -> None:
        hello = await self._await_future(self._hello, "hello")
        if hello.session_id != self._session_id or hello.pid != self.pid:
            raise RuntimeError("LeRobot worker protocol process identity mismatch")
        if (
            hello.worker_version != "0.1.0"
            or not hello.python_version.startswith("3.12.")
            or hello.lerobot_version != "0.6.1"
            or hello.supported_modes != ["teleoperate", "record", "policy"]
        ):
            raise RuntimeError("LeRobot worker runtime contract mismatch")
        initialize_correlation = secrets.token_hex(16)
        self._event_phase = "initialized"
        await self._send(
            HardwareInitializeCommand(
                service_instance_id=self.service_instance_id,
                session_id=self._session_id,
                sequence=1,
                command_id=f"initialize-{initialize_correlation}",
                correlation_id=initialize_correlation,
                worker_pid=hello.pid,
                startup_nonce=self._startup_nonce,
                profile=self.profile,
                profile_fingerprint=self.profile_fingerprint,
                resource_fingerprint=self.resource_fingerprint,
                settings=self.settings,
            ).model_dump_json()
        )
        initialized = await self._await_future(self._initialized, "initialized")
        if initialized.startup_nonce != self._startup_nonce:
            raise RuntimeError("LeRobot worker startup nonce mismatch")
        run_correlation = secrets.token_hex(16)
        self._event_phase = "running"
        await self._send(
            HardwareRunCommand(
                service_instance_id=self.service_instance_id,
                session_id=self._session_id,
                sequence=2,
                command_id=f"run-{run_correlation}",
                correlation_id=run_correlation,
                worker_pid=hello.pid,
            ).model_dump_json()
        )
        running = await self._await_future(self._running, "running")
        if not running.torque_enabled:
            raise RuntimeError("LeRobot worker did not confirm follower torque")
        self._event_phase = "active"

    async def _read_events(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        try:
            while line := await self._process.stdout.readline():
                if len(line) > MAX_EVENT_BYTES:
                    self._fail_futures(RuntimeError("LeRobot worker event exceeds size limit"))
                    return
                try:
                    event = HARDWARE_WORKER_EVENT_ADAPTER.validate_json(line)
                except ValidationError:
                    self._fail_futures(RuntimeError("LeRobot worker protocol violation"))
                    return
                if isinstance(event, HardwareWorkerHello):
                    if self._hello is not None and not self._hello.done():
                        self._hello.set_result(event)
                    continue
                if (
                    event.service_instance_id != self.service_instance_id
                    or event.session_id != self._session_id
                    or event.sequence <= self._last_worker_sequence
                ):
                    self._fail_futures(RuntimeError("LeRobot worker protocol context violation"))
                    return
                self._last_worker_sequence = event.sequence
                if isinstance(event, HardwareWorkerInitialized) and self._initialized is not None:
                    self._initialized.set_result(event)
                elif isinstance(event, HardwareWorkerRunning) and self._running is not None:
                    self._running.set_result(event)
                elif isinstance(event, HardwareWorkerCleanup) and self._cleanup is not None:
                    if (
                        event.command_id != self._expected_cleanup_command_id
                        or event.correlation_id != self._expected_cleanup_correlation_id
                    ):
                        self._fail_futures(RuntimeError("LeRobot worker cleanup identity mismatch"))
                        return
                    self._cleanup.set_result(event)
                elif isinstance(event, HardwareWorkerCommandAcknowledgement):
                    self._handle_command_acknowledgement(event)
                elif isinstance(event, HardwareWorkerRate) and self.on_rate is not None:
                    self.on_rate(event)
                elif isinstance(event, HardwareWorkerTelemetry) and self.on_telemetry is not None:
                    self.on_telemetry(event)
                elif isinstance(event, HardwareWorkerPreview) and self.on_preview is not None:
                    self.on_preview(event)
        finally:
            self._fail_futures(RuntimeError("LeRobot worker exited during protocol exchange"))

    def _handle_command_acknowledgement(self, event: HardwareWorkerCommandAcknowledgement) -> None:
        future = self._command_acks.get(event.command_id)
        expected = self._expected_command_acks.get(event.command_id)
        if (
            future is None
            or future.done()
            or expected is None
            or expected != (event.correlation_id, OperatorAction(event.action))
        ):
            self._fail_futures(RuntimeError("LeRobot worker command identity mismatch"))
            return
        future.set_result(event)

    async def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while line := await self._process.stderr.readline():
            message = self._sanitize_log(line.decode(errors="replace"))[:500]
            self._stderr.append(message)
            if self.on_log is not None:
                self.on_log(message.rstrip())

    async def _send(self, payload: str) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("LeRobot worker input is unavailable")
        self._process.stdin.write((payload + "\n").encode())
        await self._process.stdin.drain()

    @staticmethod
    async def _await_future(future, event_name: str):
        if future is None:
            raise RuntimeError(f"LeRobot worker {event_name} channel is unavailable")
        return await future

    def _fail_futures(self, error: RuntimeError) -> None:
        for future in (self._hello, self._initialized, self._running, self._cleanup, *self._command_acks.values()):
            if future is not None and not future.done():
                future.set_exception(error)

    async def _close_tasks(self) -> None:
        for task in (self._reader_task, self._stderr_task, self._handshake_task):
            if task is not None:
                with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                    await task

    def _sanitize_log(self, message: str) -> str:
        sanitized = re.sub(r"(?<!\w)/(?:[^\s:]+/?)+", "<path>", message.replace("\r", "").replace("\n", ""))
        for key, value in self._profile_secrets(self.profile):
            if key and value:
                sanitized = sanitized.replace(value, "<redacted>")
        return sanitized

    @classmethod
    def _profile_secrets(cls, value: object, key: str = "") -> list[tuple[str, str]]:
        if isinstance(value, dict):
            return [item for child_key, child in value.items() for item in cls._profile_secrets(child, str(child_key))]
        if isinstance(value, list):
            return [item for child in value for item in cls._profile_secrets(child, key)]
        sensitive_terms = ("serial", "port", "path", "token", "secret", "key")
        if isinstance(value, str) and any(term in key.lower() for term in sensitive_terms):
            return [(key, value)]
        return []

    @staticmethod
    async def _read_limited(stream: asyncio.StreamReader | None, limit: int = 65_536) -> bytes:
        if stream is None:
            return b""
        data = await stream.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError("LeRobot recovery output exceeded the safety limit")
        return data
