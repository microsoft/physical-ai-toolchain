"""Authoritative operator session state and worker supervision."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ..operator.lerobot_worker_client import LerobotWorkerClient
from ..operator.models import (
    OperatorAction,
    OperatorAdapterMode,
    OperatorCamera,
    OperatorCapabilities,
    OperatorCommand,
    OperatorMode,
    OperatorRobot,
    OperatorSessionSettings,
    OperatorStatus,
    OperatorTelemetry,
    PreflightLifecycle,
    PreflightResult,
    SessionState,
    StartSessionRequest,
)
from ..operator.protocol import (
    HARDWARE_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    HardwareWorkerPreview,
    HardwareWorkerRate,
    HardwareWorkerTelemetry,
)
from ..operator.worker_client import SimulatedWorkerClient


class WorkerClient(Protocol):
    @property
    def pid(self) -> int | None: ...

    async def launch(self, session_id: str, mode: OperatorMode) -> None: ...

    async def wait_ready(self) -> None: ...

    async def wait(self) -> int: ...

    async def command(self, session_id: str, command_id: str, action: OperatorAction): ...

    async def terminate(self) -> bool: ...

    async def recover(self) -> bool: ...


class ProfileSnapshot(Protocol):
    fingerprint: str

    def model_dump(self, *, mode: str) -> dict[str, Any]: ...


class PreflightService(Protocol):
    profiles: dict[str, ProfileSnapshot]

    def get(self, preflight_id: str) -> PreflightResult: ...

    def consume(self, preflight_id: str) -> PreflightResult: ...


class OperatorError(RuntimeError):
    """Base exception for operator contract failures."""


class OperatorDisabledError(OperatorError):
    """Raised when a disabled operator receives a control request."""


class OperatorConflictError(OperatorError):
    """Raised when a request conflicts with authoritative session state."""


class OperatorPreconditionError(OperatorConflictError):
    """Raised when hardware readiness evidence is missing or stale."""


def _resolve_worker_settings(
    request: StartSessionRequest,
    *,
    data_root: Path,
    camera_names: set[str],
    now: Callable[[], datetime] | None = None,
    policy_python: Path | None = None,
    policy_checkpoint: Path | None = None,
    policy_cuda_visible_devices: str | None = None,
) -> dict[str, object]:
    """Resolve one validated session snapshot below the configured data root."""
    settings = request.settings or OperatorSessionSettings.for_mode(request.mode)
    unknown_cameras = set(settings.camera_fps) - camera_names
    if unknown_cameras:
        raise OperatorPreconditionError(f"Unknown operator camera: {sorted(unknown_cameras)[0]}")
    resolved = settings.model_dump(mode="json")
    resolved["mode"] = request.mode.value
    resolved["dataset_root"] = None
    resolved["dataset_id"] = None
    resolved["repo_id"] = None
    resolved["policy_python"] = None
    resolved["policy_checkpoint"] = None
    resolved["policy_cuda_visible_devices"] = None
    if request.mode is OperatorMode.POLICY:
        if settings.max_relative_target is None or settings.max_relative_target > 5:
            raise OperatorPreconditionError("Policy mode requires a follower target clamp of at most 5 degrees")
        if policy_python is None or policy_checkpoint is None:
            raise OperatorPreconditionError("GR00T policy runtime is not configured")
        resolved["policy_python"] = str(policy_python)
        resolved["policy_checkpoint"] = str(policy_checkpoint)
        resolved["policy_cuda_visible_devices"] = policy_cuda_visible_devices
    if request.mode is OperatorMode.RECORD:
        root = data_root.expanduser().resolve()
        dataset_id = settings.dataset_name
        candidate = root / dataset_id
        if candidate.exists():
            timestamp = (now or (lambda: datetime.now(UTC)))().strftime("%Y%m%d_%H%M%S")
            dataset_id = f"{dataset_id}_{timestamp}"
            candidate = root / dataset_id
            collision_index = 2
            while candidate.exists():
                candidate = root / f"{dataset_id}_{collision_index}"
                collision_index += 1
            dataset_id = candidate.name
        if candidate.parent != root:
            raise OperatorPreconditionError("Recording dataset path must remain below the configured data root")
        resolved["dataset_root"] = str(candidate)
        resolved["dataset_id"] = dataset_id
        resolved["repo_id"] = (
            settings.hub_repo_id if settings.save_destination == "local_and_hub" else f"local/{dataset_id}"
        )
    return resolved


@dataclass(frozen=True)
class _HandledCommand:
    payload: OperatorAction
    status: OperatorStatus


@dataclass(frozen=True)
class _HandledStart:
    mode: OperatorMode
    status: OperatorStatus


@dataclass(frozen=True)
class OperatorCameraFrame:
    jpeg: bytes
    captured_at_s: float


class OperatorService:
    """Own one operator worker and reduce its facts into API state."""

    def __init__(
        self,
        *,
        adapter_mode: str | OperatorAdapterMode,
        command_timeout_s: float = 5.0,
        simulated_behavior: str = "normal",
        preflight_service: PreflightService | None = None,
        worker_executable: str | None = None,
        host_lease_fd: int | None = None,
        startup_timeout_s: float = 30.0,
        stop_timeout_s: float = 5.0,
        recovery_timeout_s: float = 10.0,
        data_root: Path | None = None,
        now: Callable[[], datetime] | None = None,
        policy_python: str | None = None,
        policy_checkpoint: str | None = None,
        policy_cuda_visible_devices: str | None = None,
    ) -> None:
        self._adapter_mode = OperatorAdapterMode(adapter_mode)
        self._command_timeout_s = command_timeout_s
        self._simulated_behavior = simulated_behavior
        self._preflight_service = preflight_service
        self._worker_executable = Path(worker_executable).resolve() if worker_executable else None
        self._host_lease_fd = host_lease_fd
        self._startup_timeout_s = startup_timeout_s
        self._stop_timeout_s = stop_timeout_s
        self._recovery_timeout_s = recovery_timeout_s
        self._data_root = (data_root or Path(".")).expanduser().resolve()
        self._now = now
        self._policy_python = Path(policy_python).resolve() if policy_python else None
        self._policy_checkpoint = Path(policy_checkpoint).resolve() if policy_checkpoint else None
        self._policy_cuda_visible_devices = policy_cuda_visible_devices
        self._lock = asyncio.Lock()
        self._worker: WorkerClient | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._expected_worker_exit: WorkerClient | None = None
        self._terminal_transition = asyncio.Event()
        self._terminal_transition.set()
        self._active_start: tuple[str, OperatorMode, asyncio.Task[OperatorStatus]] | None = None
        self._handled_starts: dict[str, _HandledStart] = {}
        self._handled_commands: dict[str, _HandledCommand] = {}
        self._events: deque[OperatorStatus] = deque(maxlen=128)
        self._subscribers: set[asyncio.Queue[OperatorStatus]] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._camera_frames: dict[str, OperatorCameraFrame] = {}
        initial_state = (
            SessionState.DISABLED if self._adapter_mode is OperatorAdapterMode.DISABLED else SessionState.IDLE
        )
        self._status = OperatorStatus(
            service_instance_id=str(uuid4()),
            state=initial_state,
        )

    def capabilities(self) -> OperatorCapabilities:
        """Return immutable feature capabilities."""
        enabled = self._adapter_mode is not OperatorAdapterMode.DISABLED
        hardware_start_enabled = (
            self._adapter_mode is OperatorAdapterMode.LEROBOT
            and self._worker_executable is not None
            and self._host_lease_fd is not None
        )
        policy_enabled = (
            hardware_start_enabled and self._policy_python is not None and self._policy_checkpoint is not None
        )
        if self._adapter_mode is OperatorAdapterMode.DISABLED:
            reason = "Operator mode is disabled"
        elif self._adapter_mode is OperatorAdapterMode.LEROBOT and not hardware_start_enabled:
            reason = "LeRobot operator worker is not configured"
        else:
            reason = None
        cameras: list[OperatorCamera] = []
        robots: list[OperatorRobot] = []
        if self._preflight_service is not None:
            profile = self._preflight_service.profiles.get("so101")
            if profile is not None:
                snapshot = profile.model_dump(mode="json")
                actuator_count = len(snapshot.get("actuator_names", []))
                if actuator_count:
                    robots = [
                        OperatorRobot(
                            role="leader",
                            name=snapshot["leader"]["logical_id"],
                            embodiment=snapshot["embodiment"],
                            actuator_count=actuator_count,
                        ),
                        OperatorRobot(
                            role="follower",
                            name=snapshot["follower"]["logical_id"],
                            embodiment=snapshot["embodiment"],
                            actuator_count=actuator_count,
                        ),
                    ]
                cameras = [
                    OperatorCamera(name=name, default_fps=snapshot[key]["fps"])
                    for name, key in (
                        ("wrist", "wrist_camera"),
                        ("front", "front_camera"),
                    )
                    if key in snapshot
                ]
        return OperatorCapabilities(
            enabled=enabled,
            adapter_mode=self._adapter_mode,
            adapter_version=1,
            protocol_version=(
                HARDWARE_PROTOCOL_VERSION if self._adapter_mode is OperatorAdapterMode.LEROBOT else PROTOCOL_VERSION
            ),
            modes=(
                [
                    OperatorMode.TELEOPERATE,
                    OperatorMode.RECORD,
                    *([OperatorMode.POLICY] if policy_enabled else []),
                ]
                if hardware_start_enabled
                else (list(OperatorMode) if self._adapter_mode is OperatorAdapterMode.SIMULATED else [])
            ),
            profiles=["so101"] if enabled else [],
            robots=robots,
            cameras=cameras,
            preflight_enabled=self._adapter_mode is not OperatorAdapterMode.DISABLED,
            session_start_enabled=(self._adapter_mode is OperatorAdapterMode.SIMULATED or hardware_start_enabled),
            reason=reason,
        )

    def status(self) -> OperatorStatus:
        """Return a detached authoritative snapshot."""
        return self._status.model_copy(deep=True)

    def camera_frame(self, camera: str) -> OperatorCameraFrame:
        """Return the latest worker-owned JPEG for one configured camera."""
        if camera not in {item.name for item in self.capabilities().cameras}:
            raise KeyError(camera)
        frame = self._camera_frames.get(camera)
        if frame is None:
            raise LookupError(camera)
        return frame

    async def start(self, request: StartSessionRequest) -> OperatorStatus:
        """Start or replay one idempotent subprocess-backed session request."""
        async with self._lock:
            if handled := self._handled_starts.get(request.command_id):
                if handled.mode is not request.mode:
                    raise OperatorConflictError("command_id was already used with another payload")
                return handled.status.model_copy(deep=True)
            if self._active_start is not None:
                command_id, mode, task = self._active_start
                if command_id == request.command_id:
                    if mode is not request.mode:
                        raise OperatorConflictError("command_id was already used with another payload")
                    start_task = task
                else:
                    raise OperatorConflictError("An operator session is already active")
            else:
                if self._status.cleanup_unconfirmed:
                    raise OperatorPreconditionError(
                        "Operator cleanup is unconfirmed; verify hardware and restart the backend"
                    )
                if self._adapter_mode is OperatorAdapterMode.DISABLED:
                    raise OperatorDisabledError("Operator mode is disabled")
                if self._adapter_mode is OperatorAdapterMode.LEROBOT:
                    preflight = self._validate_lerobot_preflight(request)
                    if self._worker_executable is None:
                        raise NotImplementedError("LeRobot operator worker is not configured")
                    if not self._worker_executable.is_file() or not os.access(self._worker_executable, os.X_OK):
                        raise OperatorPreconditionError("LeRobot operator worker is not executable")
                    preflight_service = self._preflight_service
                    profile_name = request.profile
                    preflight_id = request.preflight_id
                    if preflight_service is None or profile_name is None or preflight_id is None:
                        raise OperatorPreconditionError("LeRobot start requires preflight evidence")
                    profile = preflight_service.profiles[profile_name]
                    camera_names = {"wrist", "front"}
                    worker_settings = _resolve_worker_settings(
                        request,
                        data_root=self._data_root,
                        camera_names=camera_names,
                        now=self._now,
                        policy_python=self._policy_python,
                        policy_checkpoint=self._policy_checkpoint,
                        policy_cuda_visible_devices=self._policy_cuda_visible_devices,
                    )
                    preflight_service.consume(preflight_id)
                    worker: WorkerClient = LerobotWorkerClient(
                        command=[str(self._worker_executable)],
                        timeout_s=self._command_timeout_s,
                        service_instance_id=self._status.service_instance_id,
                        profile=profile.model_dump(mode="json"),
                        profile_fingerprint=preflight.profile_fingerprint,
                        resource_fingerprint=preflight.resource_fingerprint,
                        settings=worker_settings,
                        lease_fd=self._host_lease_fd,
                        on_rate=self._schedule_hardware_rate,
                        on_log=self._schedule_hardware_log,
                        on_telemetry=self._schedule_hardware_telemetry,
                        on_preview=self._schedule_hardware_preview,
                        startup_timeout_s=self._startup_timeout_s,
                        stop_timeout_s=self._stop_timeout_s,
                        recovery_timeout_s=self._recovery_timeout_s,
                    )
                elif self._adapter_mode is OperatorAdapterMode.SIMULATED:
                    worker_settings = (request.settings or OperatorSessionSettings.for_mode(request.mode)).model_dump(
                        mode="json"
                    )
                    worker = SimulatedWorkerClient(
                        timeout_s=self._command_timeout_s,
                        behavior=self._simulated_behavior,
                        startup_timeout_s=self._startup_timeout_s,
                    )
                else:
                    raise OperatorDisabledError("LeRobot operator adapter is not implemented")
                if self._status.state in {
                    SessionState.STARTING,
                    SessionState.RUNNING,
                    SessionState.STOPPING,
                }:
                    raise OperatorConflictError("An operator session is already active")
                session_id = str(uuid4())
                self._handled_commands.clear()
                self._camera_frames.clear()
                self._worker = worker
                self._expected_worker_exit = None
                self._replace_status(
                    state=SessionState.STARTING,
                    session_id=session_id,
                    mode=request.mode,
                    worker_pid=None,
                    last_command=None,
                    cleanup_unconfirmed=False,
                    error=None,
                    target_hz=None,
                    actual_hz=None,
                    loop_p95_ms=None,
                    loop_max_ms=None,
                    overruns=0,
                    latest_worker_log=None,
                    latest_telemetry=None,
                    session_settings=OperatorSessionSettings.model_validate(worker_settings),
                    dataset_id=worker_settings.get("dataset_id"),
                    episode_index=0,
                    recording_phase=None,
                    upload_status="not_requested",
                    upload_error=None,
                )
                start_task = asyncio.create_task(self._complete_start(request, session_id, worker))
                self._active_start = (request.command_id, request.mode, start_task)
        return await asyncio.shield(start_task)

    async def command(self, session_id: str, command: OperatorCommand) -> OperatorStatus:
        """Apply one idempotent command to the named active session."""
        async with self._lock:
            self._validate_session(session_id)
            payload = command.action
            handled = self._handled_commands.get(command.command_id)
            if handled is not None:
                if handled.payload != payload:
                    raise OperatorConflictError("command_id was already used with another payload")
                return handled.status.model_copy(deep=True)
            allowed_states = {SessionState.RUNNING}
            if command.action == "cancel":
                allowed_states.add(SessionState.STARTING)
            if self._status.state not in allowed_states:
                raise OperatorConflictError("Operator session is not running")
            if self._status.mode in {OperatorMode.TELEOPERATE, OperatorMode.POLICY} and command.action != "cancel":
                raise OperatorConflictError("Teleoperation and policy sessions only accept cancel")

            worker = self._worker
            if worker is None:
                raise OperatorConflictError("Operator worker is unavailable")
            if command.action in {"finish", "cancel"}:
                self._expected_worker_exit = worker
                self._terminal_transition.clear()
                self._replace_status(state=SessionState.STOPPING)
        try:
            acknowledgement = await worker.command(
                session_id,
                command.command_id,
                command.action,
            )
        except Exception as error:
            cleanup_confirmed = await worker.terminate()
            async with self._lock:
                if self._worker is worker:
                    self._worker = None
                    self._expected_worker_exit = None
                    self._replace_status(
                        state=SessionState.FAILED,
                        last_command=command.action,
                        cleanup_unconfirmed=not cleanup_confirmed,
                        error=str(error),
                    )
                self._terminal_transition.set()
            raise RuntimeError(str(error)) from error

        async with self._lock:
            if self._worker is not worker or self._status.session_id != session_id:
                result = self.status()
                self._handled_commands[command.command_id] = _HandledCommand(payload, result)
                return result
            if command.action == "finish":
                next_state = SessionState.COMPLETED
            elif command.action == "cancel":
                next_state = SessionState.CANCELLED
            else:
                next_state = SessionState.RUNNING
            if command.action in {"finish", "cancel"}:
                self._worker = None
                self._expected_worker_exit = None
            self._replace_status(
                state=next_state,
                last_command=command.action,
                dataset_id=acknowledgement.dataset_id or self._status.dataset_id,
                episode_index=(
                    acknowledgement.episode_index
                    if acknowledgement.episode_index is not None
                    else self._status.episode_index
                ),
                recording_phase=(acknowledgement.recording_phase or self._status.recording_phase),
                upload_status=(
                    "succeeded"
                    if acknowledgement.upload_succeeded
                    else ("failed" if acknowledgement.upload_attempted else self._status.upload_status)
                ),
                upload_error=acknowledgement.upload_error,
                cleanup_unconfirmed=(command.action in {"finish", "cancel"} and not acknowledgement.cleanup_complete),
            )
            if command.action in {"finish", "cancel"}:
                self._terminal_transition.set()
            result = self.status()
            self._handled_commands[command.command_id] = _HandledCommand(payload, result)
            return result

    async def stop(self, session_id: str, *, command_id: str) -> OperatorStatus:
        """Cancel the named session."""
        result = await self.command(
            session_id,
            OperatorCommand(command_id=command_id, action="cancel"),
        )
        if self._monitor_task is not None:
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        return result

    async def shutdown(self) -> None:
        """Request graceful cleanup before escalating application shutdown."""
        worker = self._worker
        if worker is None:
            return
        session_id = self._status.session_id
        try:
            await self.stop(session_id, command_id=f"shutdown-{uuid4()}")
        except Exception:
            await worker.terminate()
            async with self._lock:
                if self._worker is worker:
                    self._worker = None
                    self._replace_status(
                        state=SessionState.FAILED,
                        cleanup_unconfirmed=True,
                        error="Backend stopped before worker cleanup was confirmed",
                    )
        monitor = self._monitor_task
        if monitor is not None:
            await asyncio.gather(monitor, return_exceptions=True)

    async def events(
        self,
        last_event_id: str | None,
        *,
        once: bool = False,
    ) -> AsyncIterator[tuple[str, OperatorStatus]]:
        """Replay status revisions and then stream bounded per-subscriber updates."""
        queue: asyncio.Queue[OperatorStatus] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        try:
            event_type, replay = self._event_replay(last_event_id)
            for item in replay:
                yield event_type, item
            if once:
                return
            while True:
                try:
                    yield "status", await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield "heartbeat", self.status()
        finally:
            self._subscribers.discard(queue)

    async def _complete_start(
        self,
        request: StartSessionRequest,
        session_id: str,
        worker: WorkerClient,
    ) -> OperatorStatus:
        try:
            await worker.launch(session_id, request.mode)
            self._monitor_task = asyncio.create_task(self._monitor_worker(worker, session_id))
            await worker.wait_ready()
            wait_for_terminal = False
            async with self._lock:
                if self._worker is worker and self._status.state is SessionState.STARTING:
                    self._replace_status(state=SessionState.RUNNING, worker_pid=worker.pid)
                elif self._status.state is SessionState.STOPPING:
                    wait_for_terminal = True
                if wait_for_terminal:
                    result = None
                else:
                    result = self.status()
                    self._handled_starts[request.command_id] = _HandledStart(request.mode, result)
            if wait_for_terminal:
                await self._terminal_transition.wait()
                async with self._lock:
                    result = self.status()
                    self._handled_starts[request.command_id] = _HandledStart(request.mode, result)
            if result is None:
                raise RuntimeError("Operator start did not produce a status")
            return result
        except Exception as error:
            cleanup_confirmed = await worker.terminate()
            async with self._lock:
                if self._worker is worker:
                    self._worker = None
                    self._replace_status(
                        state=SessionState.FAILED,
                        cleanup_unconfirmed=not cleanup_confirmed,
                        error=str(error),
                    )
                result = self.status()
                self._handled_starts[request.command_id] = _HandledStart(request.mode, result)
            raise RuntimeError(str(error)) from error
        finally:
            async with self._lock:
                active = self._active_start
                if active is not None and active[0] == request.command_id:
                    self._active_start = None

    async def _monitor_worker(
        self,
        worker: WorkerClient,
        session_id: str,
    ) -> None:
        await worker.wait()
        if worker is self._expected_worker_exit:
            return
        if getattr(worker, "torque_verified_off", False):
            async with self._lock:
                if self._worker is worker and self._status.session_id == session_id:
                    self._worker = None
                    self._camera_frames.clear()
                    if self._status.mode is OperatorMode.POLICY:
                        self._replace_status(
                            state=SessionState.COMPLETED,
                            worker_pid=None,
                            cleanup_unconfirmed=False,
                            latest_worker_log="Policy rollout completed; torque off verified",
                        )
                    else:
                        self._replace_status(
                            state=SessionState.FAILED,
                            worker_pid=None,
                            cleanup_unconfirmed=False,
                            error="Operator worker exited unexpectedly; torque off verified",
                        )
            return
        recovery_error: str | None = None
        try:
            recovered = await worker.recover()
        except Exception as error:
            recovered = False
            recovery_error = str(error)
        async with self._lock:
            if self._worker is not worker or self._status.session_id != session_id:
                return
            self._worker = None
            self._replace_status(
                state=SessionState.FAILED,
                cleanup_unconfirmed=not recovered,
                error=(
                    "Operator worker exited unexpectedly; torque-off recovery confirmed"
                    if recovered
                    else "Operator worker exited unexpectedly; torque-off recovery unconfirmed"
                    + (f": {recovery_error}" if recovery_error else "")
                ),
            )

    def _event_replay(self, last_event_id: str | None) -> tuple[str, list[OperatorStatus]]:
        if not last_event_id:
            return "snapshot", [self.status()]
        try:
            service_instance_id, revision_text = last_event_id.rsplit(":", 1)
            revision = int(revision_text)
        except (ValueError, AttributeError):
            return "snapshot", [self.status()]
        if service_instance_id != self._status.service_instance_id:
            return "snapshot", [self.status()]
        replay = [event.model_copy(deep=True) for event in self._events if event.revision > revision]
        if not replay and revision < self._status.revision:
            return "snapshot", [self.status()]
        return "status", replay

    def _validate_session(self, session_id: str) -> None:
        if not self._status.session_id or session_id != self._status.session_id:
            raise OperatorConflictError("Stale session ID")

    def _validate_lerobot_preflight(self, request: StartSessionRequest) -> PreflightResult:
        if not request.profile or not request.preflight_id or not request.preflight_fingerprint:
            raise OperatorPreconditionError("LeRobot start requires preflight evidence")
        if self._preflight_service is None:
            raise OperatorPreconditionError("Operator preflight service is unavailable")
        try:
            result = self._preflight_service.get(request.preflight_id)
        except KeyError as error:
            raise OperatorPreconditionError("Operator preflight was not found") from error
        if result.lifecycle is not PreflightLifecycle.COMPLETED:
            raise OperatorPreconditionError("Operator preflight is not current")
        if result.profile != request.profile or result.mode is not request.mode:
            raise OperatorPreconditionError("Operator preflight does not match the requested session")
        if result.resource_fingerprint != request.preflight_fingerprint:
            raise OperatorPreconditionError("Operator preflight fingerprint changed")
        if result.profile_fingerprint != self._preflight_service.profiles[request.profile].fingerprint:
            raise OperatorPreconditionError("Operator profile fingerprint changed")
        if not result.start_eligible:
            raise OperatorPreconditionError("Operator preflight is not eligible for start")
        return result

    def _replace_status(self, **changes: object) -> None:
        self._status = self._status.model_copy(
            update={"revision": self._status.revision + 1, **changes},
        )
        snapshot = self.status()
        self._events.append(snapshot)
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot.model_copy(deep=True))

    def _schedule_hardware_rate(self, event: HardwareWorkerRate) -> None:
        self._schedule_background(self._apply_hardware_rate(event))

    def _schedule_hardware_log(self, message: str) -> None:
        self._schedule_background(self._apply_hardware_log(message))

    def _schedule_hardware_telemetry(self, event: HardwareWorkerTelemetry) -> None:
        self._schedule_background(self._apply_hardware_telemetry(event))

    def _schedule_hardware_preview(self, event: HardwareWorkerPreview) -> None:
        self._schedule_background(self._apply_hardware_preview(event))

    def _schedule_background(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _apply_hardware_rate(self, event: HardwareWorkerRate) -> None:
        async with self._lock:
            if event.session_id != self._status.session_id:
                return
            self._replace_status(
                target_hz=event.target_hz,
                actual_hz=event.actual_hz,
                loop_p95_ms=event.loop_p95_ms,
                loop_max_ms=event.loop_max_ms,
                overruns=event.overruns,
            )

    async def _apply_hardware_log(self, message: str) -> None:
        async with self._lock:
            if self._status.state not in {
                SessionState.STARTING,
                SessionState.RUNNING,
                SessionState.STOPPING,
            }:
                return
            self._replace_status(latest_worker_log=message[:500])

    async def _apply_hardware_telemetry(self, event: HardwareWorkerTelemetry) -> None:
        async with self._lock:
            if event.session_id != self._status.session_id:
                return
            self._replace_status(
                latest_telemetry=OperatorTelemetry(
                    elapsed_s=event.elapsed_s,
                    leader=event.leader,
                    follower=event.follower,
                    commanded=event.commanded,
                )
            )

    async def _apply_hardware_preview(self, event: HardwareWorkerPreview) -> None:
        if event.session_id != self._status.session_id:
            return
        if event.camera not in {item.name for item in self.capabilities().cameras}:
            return
        try:
            jpeg = base64.b64decode(event.jpeg_base64, validate=True)
        except (binascii.Error, ValueError):
            return
        if not jpeg or len(jpeg) > 300_000:
            return
        self._camera_frames[event.camera] = OperatorCameraFrame(
            jpeg=jpeg,
            captured_at_s=event.captured_at_s,
        )
