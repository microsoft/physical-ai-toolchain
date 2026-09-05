"""Protocol v2 supervisor for the isolated LeRobot hardware worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
from collections import deque
from collections.abc import Callable

from pydantic import ValidationError

from .models import OperatorAction, OperatorMode
from .protocol import (
    HARDWARE_WORKER_EVENT_ADAPTER,
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
    WorkerAcknowledgement,
)


class LerobotWorkerClient:
    """Launch, initialize, run, and stop one isolated hardware worker."""

    def __init__(
        self,
        *,
        command: list[str],
        timeout_s: float,
        service_instance_id: str,
        profile: dict[str, object],
        profile_fingerprint: str,
        resource_fingerprint: str,
        settings: dict[str, object] | None = None,
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
        self.settings = settings or {
            "mode": "teleoperate",
            "control_fps": 60,
            "camera_fps": {"wrist": 30, "front": 30},
            "max_relative_target": None,
            "dataset_root": None,
            "dataset_id": None,
            "task": "Pick <obj> from <loc1> and place in <obj2>",
            "save_destination": "local",
            "num_episodes": 50,
            "episode_time_s": 60,
            "reset_time_s": 30,
        }
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
        self.torque_verified_off = False
        self._last_worker_sequence = 0
        self._expected_cleanup_command_id: str | None = None
        self._event_phase = "hello"
        self._mode = OperatorMode.TELEOPERATE
        self._next_command_sequence = 3
        self._command_acks: dict[str, asyncio.Future[HardwareWorkerCommandAcknowledgement]] = {}

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr)

    def build_environment(self, session_id: str) -> dict[str, str]:
        """Build the minimal nonsecret worker environment."""
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
        self._mode = mode
        self._session_id = session_id
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
            limit=512 * 1024,
        )
        self._reader_task = asyncio.create_task(self._read_events())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._handshake_task = asyncio.create_task(self._handshake())

    async def wait_ready(self) -> None:
        if self._handshake_task is None:
            raise RuntimeError("LeRobot worker was not launched")
        try:
            await asyncio.wait_for(
                asyncio.shield(self._handshake_task),
                timeout=self.startup_timeout_s,
            )
        except TimeoutError as error:
            raise RuntimeError("LeRobot worker handshake timed out") from error

    async def wait(self) -> int:
        if self._process is None:
            raise RuntimeError("LeRobot worker was not launched")
        return await self._process.wait()

    async def command(
        self,
        session_id: str,
        command_id: str,
        action: OperatorAction,
    ) -> WorkerAcknowledgement:
        if session_id != self._session_id:
            raise RuntimeError("Invalid LeRobot worker command")
        sequence = self._next_command_sequence
        self._next_command_sequence += 1
        if action == "cancel":
            cleanup_timeout_s = (
                max(self.stop_timeout_s, 120.0) if self._mode is OperatorMode.RECORD else self.stop_timeout_s
            )
            cleanup = await self._send_stop(
                session_id,
                command_id,
                sequence,
                timeout_s=cleanup_timeout_s,
            )
            return WorkerAcknowledgement(
                session_id=session_id,
                command_id=command_id,
                action="cancel",
                cleanup_complete=(cleanup.cleanup_complete and cleanup.torque_verified_off),
            )
        if self._mode is not OperatorMode.RECORD:
            raise RuntimeError("Episode commands require record mode")
        loop = asyncio.get_running_loop()
        acknowledgement = loop.create_future()
        self._command_acks[command_id] = acknowledgement
        if action == "finish":
            self._expected_cleanup_command_id = command_id
            self._event_phase = "cleanup"
        await self._send(
            HardwareActionCommand(
                service_instance_id=self.service_instance_id,
                session_id=session_id,
                sequence=sequence,
                command_id=command_id,
                action=action,
            ).model_dump_json()
        )
        try:
            progress = await asyncio.wait_for(asyncio.shield(acknowledgement), timeout=max(self.timeout_s, 120.0))
        except TimeoutError as error:
            raise RuntimeError("LeRobot worker command timed out") from error
        cleanup = None
        cleanup_complete = False
        if action == "finish":
            cleanup = await self._await_cleanup(timeout_s=max(self.timeout_s, 120.0))
            cleanup_complete = cleanup.cleanup_complete and cleanup.torque_verified_off
        return WorkerAcknowledgement(
            session_id=session_id,
            command_id=command_id,
            action=action,
            cleanup_complete=cleanup_complete,
            dataset_id=progress.dataset_id,
            episode_index=progress.episode_index,
            recording_phase=progress.phase,
            upload_attempted=(cleanup.upload_attempted if cleanup is not None else False),
            upload_succeeded=(cleanup.upload_succeeded if cleanup is not None else False),
            upload_error=cleanup.upload_error if cleanup is not None else None,
        )

    async def _send_stop(
        self,
        session_id: str,
        command_id: str,
        sequence: int,
        *,
        timeout_s: float | None = None,
    ) -> HardwareWorkerCleanup:
        if self._cleanup is None:
            raise RuntimeError("LeRobot worker cleanup channel is unavailable")
        self._expected_cleanup_command_id = command_id
        self._event_phase = "cleanup"
        await self._send(
            HardwareStopCommand(
                service_instance_id=self.service_instance_id,
                session_id=session_id,
                sequence=sequence,
                command_id=command_id,
            ).model_dump_json()
        )
        return await self._await_cleanup(timeout_s=timeout_s)

    async def _await_cleanup(self, *, timeout_s: float | None = None) -> HardwareWorkerCleanup:
        if self._cleanup is None:
            raise RuntimeError("LeRobot worker cleanup channel is unavailable")
        try:
            cleanup = await asyncio.wait_for(
                asyncio.shield(self._cleanup),
                timeout=timeout_s or self.stop_timeout_s,
            )
        except TimeoutError as error:
            raise RuntimeError("LeRobot worker cleanup timed out") from error
        self.torque_verified_off = cleanup.torque_verified_off
        if self._process is not None:
            await asyncio.wait_for(self._process.wait(), timeout=timeout_s or self.stop_timeout_s)
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
        """Run the bus-only torque-off recovery mode under the inherited lease."""
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
            stdout, stderr, _return_code = await asyncio.wait_for(
                asyncio.gather(
                    self._read_limited(process.stdout),
                    self._read_limited(process.stderr),
                    process.wait(),
                ),
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
        recovered = bool(
            process.returncode == 0 and result.get("cleanup_complete") and result.get("torque_verified_off")
        )
        self.torque_verified_off = recovered
        return recovered

    async def _handshake(self) -> None:
        hello = await self._await_future(self._hello, "hello")
        if hello.session_id != self._session_id:
            raise RuntimeError("LeRobot worker protocol session mismatch")
        if (
            hello.worker_version != "0.1.0"
            or not hello.python_version.startswith("3.12.")
            or hello.lerobot_version != "0.6.1"
            or hello.supported_modes != ["teleoperate", "record", "policy"]
        ):
            raise RuntimeError("LeRobot worker runtime contract mismatch")
        self._event_phase = "initialized"
        await self._send(
            HardwareInitializeCommand(
                service_instance_id=self.service_instance_id,
                session_id=self._session_id,
                sequence=1,
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
        self._event_phase = "running"
        await self._send(
            HardwareRunCommand(
                service_instance_id=self.service_instance_id,
                session_id=self._session_id,
                sequence=2,
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
                try:
                    event = HARDWARE_WORKER_EVENT_ADAPTER.validate_json(line)
                except ValidationError:
                    self._fail_futures(RuntimeError("LeRobot worker protocol violation"))
                    return
                if not isinstance(event, HardwareWorkerHello):
                    if (
                        event.service_instance_id != self.service_instance_id
                        or event.session_id != self._session_id
                        or event.sequence <= self._last_worker_sequence
                    ):
                        self._fail_futures(RuntimeError("LeRobot worker protocol context violation"))
                        return
                    self._last_worker_sequence = event.sequence
                if isinstance(event, HardwareWorkerHello):
                    if self._event_phase != "hello" or event.session_id != self._session_id:
                        self._fail_futures(RuntimeError("LeRobot worker protocol session mismatch"))
                        return
                    if self._hello is not None and not self._hello.done():
                        self._hello.set_result(event)
                elif isinstance(event, HardwareWorkerInitialized):
                    if self._event_phase != "initialized":
                        self._fail_futures(RuntimeError("LeRobot worker event order violation"))
                        return
                    if self._initialized is not None and not self._initialized.done():
                        self._initialized.set_result(event)
                elif isinstance(event, HardwareWorkerRunning):
                    if self._event_phase != "running":
                        self._fail_futures(RuntimeError("LeRobot worker event order violation"))
                        return
                    if self._running is not None and not self._running.done():
                        self._event_phase = "active"
                        self._running.set_result(event)
                elif (
                    isinstance(event, HardwareWorkerCleanup) and self._cleanup is not None and not self._cleanup.done()
                ):
                    if event.command_id == "worker-exit" and self._event_phase in {
                        "initialized",
                        "running",
                        "active",
                    }:
                        self.torque_verified_off = event.cleanup_complete and event.torque_verified_off
                        self._cleanup.set_result(event)
                        self._fail_futures(
                            RuntimeError(
                                "LeRobot worker failed during startup; cleanup confirmed"
                                if self.torque_verified_off
                                else "LeRobot worker failed during startup; cleanup unconfirmed"
                            )
                        )
                        return
                    if self._event_phase != "cleanup" or event.command_id != self._expected_cleanup_command_id:
                        self._fail_futures(RuntimeError("LeRobot worker cleanup command mismatch"))
                        return
                    self._cleanup.set_result(event)
                elif isinstance(event, HardwareWorkerRate):
                    if self._event_phase not in {"active", "cleanup"}:
                        self._fail_futures(RuntimeError("LeRobot worker event order violation"))
                        return
                    if self.on_rate is not None:
                        self.on_rate(event)
                elif isinstance(event, HardwareWorkerTelemetry):
                    if self._event_phase not in {"active", "cleanup"}:
                        self._fail_futures(RuntimeError("LeRobot worker event order violation"))
                        return
                    if self.on_telemetry is not None:
                        self.on_telemetry(event)
                elif isinstance(event, HardwareWorkerPreview):
                    if self._event_phase not in {"active", "cleanup"}:
                        self._fail_futures(RuntimeError("LeRobot worker event order violation"))
                        return
                    if self.on_preview is not None:
                        self.on_preview(event)
                elif isinstance(event, HardwareWorkerCommandAcknowledgement):
                    if self._event_phase not in {"active", "cleanup"}:
                        self._fail_futures(RuntimeError("LeRobot worker event order violation"))
                        return
                    future = self._command_acks.get(event.command_id)
                    if future is None or future.done():
                        self._fail_futures(RuntimeError("LeRobot worker command mismatch"))
                        return
                    future.set_result(event)
        finally:
            self._fail_futures(RuntimeError("LeRobot worker exited during protocol exchange"))

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

    async def _await_future(self, future, event_name: str):
        if future is None:
            raise RuntimeError(f"LeRobot worker {event_name} channel is unavailable")
        return await future

    def _fail_futures(self, error: RuntimeError) -> None:
        for future in (self._hello, self._initialized, self._running, self._cleanup):
            if future is not None and not future.done():
                future.set_exception(error)
        for command_future in self._command_acks.values():
            if not command_future.done():
                command_future.set_exception(error)

    async def _close_tasks(self) -> None:
        for task in (self._reader_task, self._stderr_task, self._handshake_task):
            if task is not None:
                with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                    await task
        for future in (self._hello, self._initialized, self._running, self._cleanup):
            if future is not None and future.done() and not future.cancelled():
                with contextlib.suppress(RuntimeError):
                    future.exception()
        for command_future in self._command_acks.values():
            if command_future.done() and not command_future.cancelled():
                with contextlib.suppress(RuntimeError):
                    command_future.exception()

    @staticmethod
    def _sanitize_log(message: str) -> str:
        return re.sub(r"(?<!\w)/(?:[^\s:]+/?)+", "<path>", message)

    @staticmethod
    async def _read_limited(stream: asyncio.StreamReader | None, limit: int = 65_536) -> bytes:
        if stream is None:
            return b""
        data = await stream.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError("LeRobot recovery output exceeded the safety limit")
        return data
