"""Supervise the operator worker through the versioned JSON-line protocol."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

from pydantic import ValidationError

from .models import OperatorAction, OperatorMode
from .protocol import (
    WORKER_EVENT_ADAPTER,
    WorkerAcknowledgement,
    WorkerCommand,
    WorkerProtocolError,
    WorkerReady,
)


class SimulatedWorkerClient:
    """Executable subprocess client used by the Phase 1 simulator."""

    def __init__(
        self,
        *,
        timeout_s: float,
        behavior: str = "normal",
        startup_timeout_s: float | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._startup_timeout_s = startup_timeout_s or timeout_s
        self._behavior = behavior
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._launched = asyncio.Event()
        self._ready: asyncio.Future[WorkerReady] | None = None
        self._pending: dict[str, asyncio.Future[WorkerAcknowledgement]] = {}
        self._acknowledgements: dict[str, WorkerAcknowledgement] = {}
        self._session_id = ""
        self._mode: OperatorMode | None = None

    @property
    def pid(self) -> int | None:
        """Return the child PID while a worker exists."""
        return self._process.pid if self._process is not None else None

    async def launch(self, session_id: str, mode: OperatorMode) -> None:
        """Launch the subprocess and start its event dispatcher."""
        self._session_id = session_id
        self._mode = mode
        self._ready = asyncio.get_running_loop().create_future()
        worker_environment = {
            "OPERATOR_SESSION_ID": session_id,
            "OPERATOR_SESSION_MODE": mode.value,
            "PYTHONUNBUFFERED": "1",
        }
        if python_path := os.environ.get("PYTHONPATH"):
            worker_environment["PYTHONPATH"] = python_path
        try:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "src.api.operator.simulated_worker",
                "--behavior",
                self._behavior,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=worker_environment,
            )
            self._reader_task = asyncio.create_task(self._dispatch_events())
        finally:
            self._launched.set()

    async def wait_ready(self) -> None:
        """Wait for the validated readiness event."""
        await self._wait_launched()
        if self._ready is None:
            raise RuntimeError("worker readiness is unavailable")
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout=self._startup_timeout_s)
        except TimeoutError as error:
            raise RuntimeError("worker startup timed out") from error

    async def wait(self) -> int:
        """Wait for subprocess exit."""
        await self._wait_launched()
        return await self._require_process(allow_exited=True).wait()

    async def command(
        self,
        session_id: str,
        command_id: str,
        action: OperatorAction,
    ) -> WorkerAcknowledgement:
        """Send one command and wait for its validated acknowledgement."""
        if session_id != self._session_id:
            raise RuntimeError("worker session mismatch")
        if acknowledgement := self._acknowledgements.get(command_id):
            if acknowledgement.action != action:
                raise RuntimeError("worker command_id payload conflict")
            return acknowledgement

        await self._wait_launched()
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("worker input is unavailable")
        acknowledgement_future: asyncio.Future[WorkerAcknowledgement] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = acknowledgement_future
        message = WorkerCommand(
            session_id=session_id,
            command_id=command_id,
            action=action,
        )
        process.stdin.write((message.model_dump_json() + "\n").encode())
        await process.stdin.drain()
        try:
            acknowledgement = await asyncio.wait_for(
                asyncio.shield(acknowledgement_future),
                timeout=self._timeout_s,
            )
        except TimeoutError as error:
            raise RuntimeError("worker command timed out") from error
        finally:
            self._pending.pop(command_id, None)
        self._acknowledgements[command_id] = acknowledgement
        if action in {"finish", "cancel"}:
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_s)
            except TimeoutError as error:
                raise RuntimeError("worker cleanup timed out") from error
        return acknowledgement

    async def terminate(self) -> bool:
        """Escalate from terminate to kill and close subprocess transports."""
        await self._wait_launched()
        process = self._process
        if process is None:
            return False
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_s)
            except TimeoutError:
                process.kill()
                await process.wait()
        if process.stdin is not None:
            process.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        if self._reader_task is not None:
            with contextlib.suppress(RuntimeError):
                await self._reader_task
        return False

    async def recover(self) -> bool:
        """Simulation has no physical torque recovery path."""
        return False

    async def _dispatch_events(self) -> None:
        process = self._require_process(allow_exited=True)
        if process.stdout is None:
            self._fail_waiters(RuntimeError("worker output is unavailable"))
            return
        try:
            while line := await process.stdout.readline():
                try:
                    event = WORKER_EVENT_ADAPTER.validate_json(line)
                except ValidationError:
                    self._fail_waiters(RuntimeError("worker protocol violation"))
                    return
                if event.session_id != self._session_id:
                    self._fail_waiters(RuntimeError("worker protocol session mismatch"))
                    return
                if isinstance(event, WorkerReady):
                    if event.mode is not self._mode:
                        self._fail_waiters(RuntimeError("worker protocol mode mismatch"))
                        return
                    if self._ready is not None and not self._ready.done():
                        self._ready.set_result(event)
                elif isinstance(event, WorkerAcknowledgement):
                    pending = self._pending.get(event.command_id)
                    if pending is not None and not pending.done():
                        pending.set_result(event)
                elif isinstance(event, WorkerProtocolError):
                    pending = self._pending.get(event.command_id)
                    if pending is not None and not pending.done():
                        pending.set_exception(RuntimeError(event.message))
        finally:
            self._fail_waiters(RuntimeError("worker exited before completing the request"))

    async def _wait_launched(self) -> None:
        try:
            await asyncio.wait_for(self._launched.wait(), timeout=self._startup_timeout_s)
        except TimeoutError as error:
            raise RuntimeError("worker launch timed out") from error

    def _fail_waiters(self, error: RuntimeError) -> None:
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(error)
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(error)

    def _require_process(self, *, allow_exited: bool = False) -> asyncio.subprocess.Process:
        process = self._process
        if process is None or (not allow_exited and process.returncode is not None):
            raise RuntimeError("worker is not running")
        return process
