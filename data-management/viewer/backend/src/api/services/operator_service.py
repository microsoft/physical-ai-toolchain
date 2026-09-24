"""Authoritative operator session state and command idempotency."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
from ..operator.protocol import HardwareWorkerPreview, HardwareWorkerRate, HardwareWorkerTelemetry


class OperatorError(RuntimeError):
    """Base exception for operator contract failures."""


class OperatorDisabledError(OperatorError):
    """Raised when a disabled operator receives a control request."""


class OperatorConflictError(OperatorError):
    """Raised when a request conflicts with authoritative state."""


class OperatorPreconditionError(OperatorConflictError):
    """Raised when physical readiness evidence is missing or stale."""


@dataclass(frozen=True)
class _HandledCommand:
    payload: OperatorCommand
    status: OperatorStatus


@dataclass(frozen=True)
class _HandledStart:
    payload: StartSessionRequest
    status: OperatorStatus


@dataclass(frozen=True)
class OperatorCameraFrame:
    jpeg: bytes
    captured_at_s: float


@dataclass(frozen=True)
class _ResolvedSettings:
    public: OperatorSessionSettings
    worker: dict[str, object]


class OperatorService:
    """Own session state while process clients own subprocess lifecycle."""

    def __init__(
        self,
        *,
        adapter_mode: str | OperatorAdapterMode,
        preflight_service: Any | None = None,
        worker_factory: Any | None = None,
        worker_executable: str | None = None,
        host_lease_fd: int | None = None,
        command_timeout_s: float = 5.0,
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
        self._preflight_service = preflight_service
        self._worker_factory = worker_factory
        self._worker_executable = Path(worker_executable).resolve() if worker_executable else None
        self._host_lease_fd = host_lease_fd
        self._command_timeout_s = command_timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._stop_timeout_s = stop_timeout_s
        self._recovery_timeout_s = recovery_timeout_s
        self._data_root = (data_root or Path(".")).expanduser().resolve()
        self._now = now or (lambda: datetime.now(UTC))
        self._policy_python = Path(policy_python).expanduser().resolve() if policy_python else None
        self._policy_checkpoint = Path(policy_checkpoint).expanduser().resolve() if policy_checkpoint else None
        self._policy_cuda_visible_devices = policy_cuda_visible_devices
        self._worker: Any | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._expected_worker_exit: Any | None = None
        self._lock = asyncio.Lock()
        self._active_start: tuple[StartSessionRequest, asyncio.Task[OperatorStatus]] | None = None
        self._active_commands: dict[str, tuple[str, OperatorCommand, asyncio.Task[OperatorStatus]]] = {}
        self._handled_starts: dict[str, _HandledStart] = {}
        self._handled_commands: dict[str, _HandledCommand] = {}
        self._events: deque[OperatorStatus] = deque(maxlen=128)
        self._subscribers: set[asyncio.Queue[OperatorStatus]] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._camera_frames: dict[str, OperatorCameraFrame] = {}
        initial_state = (
            SessionState.DISABLED if self._adapter_mode is OperatorAdapterMode.DISABLED else SessionState.IDLE
        )
        self._status = OperatorStatus(service_instance_id=str(uuid4()), state=initial_state)

    def capabilities(self) -> OperatorCapabilities:
        enabled = self._adapter_mode is not OperatorAdapterMode.DISABLED
        profiles = sorted(self._preflight_service.profiles) if self._preflight_service is not None else []
        hardware_enabled = self._adapter_mode is OperatorAdapterMode.LEROBOT and self._hardware_worker_configured()
        policy_enabled = hardware_enabled and self._policy_python is not None and self._policy_checkpoint is not None
        robots: list[OperatorRobot] = []
        cameras: list[OperatorCamera] = []
        if self._preflight_service is not None:
            robot_keys: set[tuple[str, str]] = set()
            camera_names: set[str] = set()
            for profile in self._preflight_service.profiles.values():
                snapshot = profile.model_dump(mode="json")
                actuator_count = len(snapshot.get("actuator_names", []))
                for role in ("leader", "follower"):
                    arm = snapshot.get(role, {})
                    key = (role, str(arm.get("logical_id", "")))
                    if actuator_count and key[1] and key not in robot_keys:
                        robots.append(
                            OperatorRobot(
                                role=role,
                                name=key[1],
                                embodiment=str(snapshot.get("embodiment", "unknown")),
                                actuator_count=actuator_count,
                            )
                        )
                        robot_keys.add(key)
                for name, field_name in (("wrist", "wrist_camera"), ("front", "front_camera")):
                    camera = snapshot.get(field_name, {})
                    if name not in camera_names and camera.get("fps"):
                        cameras.append(OperatorCamera(name=name, default_fps=int(camera["fps"])))
                        camera_names.add(name)
        return OperatorCapabilities(
            enabled=enabled,
            adapter_mode=self._adapter_mode,
            adapter_version=1,
            protocol_version=2,
            modes=(
                [OperatorMode.TELEOPERATE, OperatorMode.RECORD] + ([OperatorMode.POLICY] if policy_enabled else [])
                if hardware_enabled
                else (list(OperatorMode) if self._adapter_mode is OperatorAdapterMode.SIMULATED else [])
            ),
            profiles=profiles,
            robots=robots,
            cameras=cameras,
            preflight_enabled=self._adapter_mode is OperatorAdapterMode.LEROBOT,
            session_start_enabled=(
                self._adapter_mode is OperatorAdapterMode.SIMULATED
                or self._worker_factory is not None
                or self._hardware_worker_configured()
            ),
            reason="Operator mode is disabled" if not enabled else None,
        )

    def status(self) -> OperatorStatus:
        return self._status.model_copy(deep=True)

    def camera_frame(self, camera: str) -> OperatorCameraFrame:
        if camera not in {item.name for item in self.capabilities().cameras}:
            raise KeyError(camera)
        frame = self._camera_frames.get(camera)
        if frame is None:
            raise LookupError(camera)
        return frame

    async def start(self, request: StartSessionRequest) -> OperatorStatus:
        async with self._lock:
            handled = self._handled_starts.get(request.command_id)
            if handled is not None:
                if handled.payload != request:
                    raise OperatorConflictError("command_id was already used with another payload")
                return handled.status.model_copy(deep=True)
            if self._active_start is not None:
                active_request, start_task = self._active_start
                if active_request.command_id != request.command_id:
                    raise OperatorConflictError("An operator session is already active")
                if active_request != request:
                    raise OperatorConflictError("command_id was already used with another payload")
            else:
                start_task = self._prepare_start(request)
                self._active_start = (request.model_copy(deep=True), start_task)
        return await asyncio.shield(start_task)

    def _prepare_start(self, request: StartSessionRequest) -> asyncio.Task[OperatorStatus]:
        if self._status.cleanup_unconfirmed:
            raise OperatorPreconditionError("Operator cleanup is unconfirmed")
        if self._adapter_mode is OperatorAdapterMode.DISABLED:
            raise OperatorDisabledError("Operator mode is disabled")
        if self._status.state in {SessionState.STARTING, SessionState.RUNNING, SessionState.STOPPING}:
            raise OperatorConflictError("An operator session is already active")
        if self._adapter_mode is OperatorAdapterMode.LEROBOT:
            preflight = self._validate_preflight(request)
            if self._worker_factory is None and not self._hardware_worker_configured():
                raise OperatorPreconditionError("LeRobot operator worker is not configured")
            resolved_settings = self._resolve_settings(request)
            if self._preflight_service is None or request.preflight_id is None:
                raise OperatorPreconditionError("Operator preflight service is unavailable")
            self._preflight_service.consume(request.preflight_id)
        else:
            preflight = None
            resolved_settings = self._resolve_settings(request)
        session_id = str(uuid4())
        self._handled_commands.clear()
        self._camera_frames.clear()
        self._replace_status(
            state=SessionState.STARTING,
            session_id=session_id,
            mode=request.mode,
            worker_pid=None,
            last_command=None,
            cleanup_unconfirmed=False,
            session_settings=resolved_settings.public,
            dataset_id=resolved_settings.public.dataset_id,
            episode_index=0,
            recording_phase=None,
            upload_status="not_requested",
            upload_error=None,
            error=None,
        )
        if self._worker_factory is not None:
            worker = self._worker_factory()
        elif preflight is not None:
            worker = self._build_hardware_worker(request, preflight, resolved_settings.worker)
        else:
            worker = None
        self._worker = worker
        return asyncio.create_task(self._complete_start(request, session_id, worker))

    async def _complete_start(
        self,
        request: StartSessionRequest,
        session_id: str,
        worker: Any | None,
    ) -> OperatorStatus:
        try:
            if worker is not None:
                await worker.launch(session_id, request.mode)
                self._monitor_task = asyncio.create_task(self._monitor_worker(worker, session_id))
                await worker.wait_ready()
            async with self._lock:
                if self._worker is not worker or self._status.state is SessionState.FAILED:
                    raise RuntimeError(self._status.error or "Operator worker exited during startup")
                self._replace_status(state=SessionState.RUNNING, worker_pid=worker.pid if worker is not None else None)
                result = self.status()
                self._remember_start(request, result)
                return result
        except Exception as error:
            cleanup_confirmed = await self._cleanup_worker(worker)
            message = self._redact_error(str(error))
            async with self._lock:
                self._worker = None
                self._replace_status(
                    state=SessionState.FAILED,
                    error=message,
                    cleanup_unconfirmed=not cleanup_confirmed,
                )
                self._remember_start(request, self.status())
            raise RuntimeError(message) from error
        finally:
            async with self._lock:
                if self._active_start is not None and self._active_start[0].command_id == request.command_id:
                    self._active_start = None

    async def command(self, session_id: str, command: OperatorCommand) -> OperatorStatus:
        async with self._lock:
            self._validate_session(session_id)
            handled = self._handled_commands.get(command.command_id)
            if handled is not None:
                if handled.payload != command:
                    raise OperatorConflictError("command_id was already used with another payload")
                return handled.status.model_copy(deep=True)
            active = self._active_commands.get(command.command_id)
            if active is not None:
                active_session_id, active_command, command_task = active
                if active_session_id != session_id or active_command != command:
                    raise OperatorConflictError("command_id was already used with another payload")
            else:
                if self._status.state is not SessionState.RUNNING:
                    raise OperatorConflictError("Operator session is not running")
                if command.expected_revision is not None and command.expected_revision != self._status.revision:
                    raise OperatorConflictError("Operator status revision is stale")
                worker = self._worker
                if command.action in {OperatorAction.FINISH, OperatorAction.CANCEL}:
                    self._expected_worker_exit = worker
                    self._replace_status(state=SessionState.STOPPING)
                command_task = asyncio.create_task(self._complete_command(session_id, command, worker))
                self._active_commands[command.command_id] = (
                    session_id,
                    command.model_copy(deep=True),
                    command_task,
                )
        return await asyncio.shield(command_task)

    async def _complete_command(self, session_id: str, command: OperatorCommand, worker: Any | None) -> OperatorStatus:
        try:
            if worker is not None:
                acknowledgement = await worker.command(session_id, command.command_id, command.action)
                cleanup_complete = bool(getattr(acknowledgement, "cleanup_complete", False))
                if command.action in {OperatorAction.FINISH, OperatorAction.CANCEL} and not cleanup_complete:
                    cleanup_complete = await self._cleanup_worker(worker)
            else:
                acknowledgement = None
                cleanup_complete = True
        except Exception as error:
            cleanup_confirmed = await self._cleanup_worker(worker)
            message = self._redact_error(str(error))
            async with self._lock:
                if self._worker is worker:
                    self._worker = None
                    self._camera_frames.clear()
                    self._replace_status(
                        state=SessionState.FAILED,
                        last_command=command.action,
                        cleanup_unconfirmed=not cleanup_confirmed,
                        error=message,
                    )
                self._active_commands.pop(command.command_id, None)
            raise RuntimeError(message) from error
        async with self._lock:
            terminal = command.action in {OperatorAction.FINISH, OperatorAction.CANCEL}
            self._replace_status(
                state=(SessionState.CANCELLED if command.action is OperatorAction.CANCEL else SessionState.COMPLETED)
                if terminal
                else SessionState.RUNNING,
                last_command=command.action,
                cleanup_unconfirmed=terminal and not cleanup_complete,
                dataset_id=getattr(acknowledgement, "dataset_id", None) or self._status.dataset_id,
                episode_index=(
                    getattr(acknowledgement, "episode_index", None)
                    if getattr(acknowledgement, "episode_index", None) is not None
                    else self._status.episode_index
                ),
                recording_phase=getattr(acknowledgement, "recording_phase", None) or self._status.recording_phase,
                upload_status=(
                    "succeeded"
                    if getattr(acknowledgement, "upload_succeeded", False)
                    else (
                        "failed" if getattr(acknowledgement, "upload_attempted", False) else self._status.upload_status
                    )
                ),
                upload_error=self._redact_error(getattr(acknowledgement, "upload_error", "") or "") or None,
            )
            if terminal:
                self._worker = None
                self._expected_worker_exit = None
                self._camera_frames.clear()
            result = self.status()
            self._remember_command(command, result)
            self._active_commands.pop(command.command_id, None)
            return result

    async def stop(self, session_id: str, *, command_id: str) -> OperatorStatus:
        return await self.command(session_id, OperatorCommand(command_id=command_id, action=OperatorAction.CANCEL))

    async def shutdown(self) -> None:
        worker = self._worker
        if worker is None:
            return
        confirmed = await self._cleanup_worker(worker)
        async with self._lock:
            self._worker = None
            self._expected_worker_exit = None
            self._camera_frames.clear()
            self._replace_status(
                state=SessionState.STOPPED if confirmed else SessionState.FAILED,
                cleanup_unconfirmed=not confirmed,
            )

    async def events(
        self,
        last_event_id: str | None,
        *,
        once: bool = False,
    ) -> AsyncIterator[tuple[str, OperatorStatus]]:
        queue: asyncio.Queue[OperatorStatus] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        try:
            event_type, replay = self._event_replay(last_event_id)
            for snapshot in replay:
                yield event_type, snapshot
            if once:
                return
            while True:
                try:
                    yield "status", await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield "heartbeat", self.status()
        finally:
            self._subscribers.discard(queue)

    def _validate_preflight(self, request: StartSessionRequest) -> PreflightResult:
        if not request.profile or not request.preflight_id or not request.preflight_fingerprint:
            raise OperatorPreconditionError("LeRobot start requires preflight evidence")
        if self._preflight_service is None:
            raise OperatorPreconditionError("Operator preflight service is unavailable")
        result = self._preflight_service.get(request.preflight_id)
        if result.lifecycle is not PreflightLifecycle.COMPLETED or not result.start_eligible:
            raise OperatorPreconditionError("Operator preflight is not eligible for start")
        if result.profile != request.profile or result.mode is not request.mode:
            raise OperatorPreconditionError("Operator preflight does not match the requested session")
        if result.resource_fingerprint != request.preflight_fingerprint:
            raise OperatorPreconditionError("Operator preflight fingerprint changed")
        profile = self._preflight_service.profiles.get(request.profile)
        if profile is None or result.profile_fingerprint != profile.fingerprint:
            raise OperatorPreconditionError("Operator profile fingerprint changed")
        return result

    def _hardware_worker_configured(self) -> bool:
        return (
            self._worker_executable is not None
            and self._worker_executable.is_file()
            and os.access(self._worker_executable, os.X_OK)
            and self._host_lease_fd is not None
        )

    def _build_hardware_worker(
        self,
        request: StartSessionRequest,
        preflight: PreflightResult,
        settings: dict[str, object],
    ) -> LerobotWorkerClient:
        if self._preflight_service is None or request.profile is None or self._worker_executable is None:
            raise OperatorPreconditionError("LeRobot operator worker is not configured")
        profile = self._preflight_service.profiles[request.profile]
        return LerobotWorkerClient(
            command=[str(self._worker_executable)],
            timeout_s=self._command_timeout_s,
            service_instance_id=self._status.service_instance_id,
            profile=profile.model_dump(mode="json"),
            profile_fingerprint=preflight.profile_fingerprint,
            resource_fingerprint=preflight.resource_fingerprint,
            settings=settings,
            lease_fd=self._host_lease_fd,
            startup_timeout_s=self._startup_timeout_s,
            stop_timeout_s=self._stop_timeout_s,
            recovery_timeout_s=self._recovery_timeout_s,
            on_rate=self._schedule_hardware_rate,
            on_log=self._schedule_hardware_log,
            on_telemetry=self._schedule_hardware_telemetry,
            on_preview=self._schedule_hardware_preview,
        )

    def _resolve_settings(self, request: StartSessionRequest) -> _ResolvedSettings:
        settings = request.settings or OperatorSessionSettings.for_mode(request.mode)
        unknown_cameras = set(settings.camera_fps) - {camera.name for camera in self.capabilities().cameras}
        if unknown_cameras:
            raise OperatorPreconditionError(f"Unknown operator camera: {sorted(unknown_cameras)[0]}")

        public_updates: dict[str, object] = {
            "dataset_root": None,
            "repo_id": None,
            "policy_python": None,
            "policy_checkpoint": None,
            "policy_cuda_visible_devices": None,
        }
        worker = settings.model_dump(mode="json")
        worker.update(public_updates)
        worker.update({"mode": request.mode.value, "execution_mode": "physical"})

        if request.mode is OperatorMode.POLICY and self._adapter_mode is OperatorAdapterMode.LEROBOT:
            if settings.max_relative_target is None or settings.max_relative_target > 5:
                raise OperatorPreconditionError("Policy mode requires a follower target clamp of at most 5 degrees")
            if self._policy_python is None or self._policy_checkpoint is None:
                raise OperatorPreconditionError("Physical policy runtime is not configured")
            worker["policy_python"] = str(self._policy_python)
            worker["policy_checkpoint"] = str(self._policy_checkpoint)
            worker["policy_cuda_visible_devices"] = self._policy_cuda_visible_devices

        if request.mode is OperatorMode.RECORD:
            dataset_id = self._resolve_dataset_id(settings.dataset_id or "operator-session")
            destination = self._data_root / dataset_id
            repo_id = settings.hub_repo_id if settings.save_destination == "local_and_hub" else f"local/{dataset_id}"
            public_updates.update({"dataset_id": dataset_id, "repo_id": repo_id})
            worker.update(
                {
                    "dataset_root": str(destination),
                    "dataset_id": dataset_id,
                    "repo_id": repo_id,
                }
            )

        public = settings.model_copy(update=public_updates)
        return _ResolvedSettings(public=public, worker=worker)

    def _resolve_dataset_id(self, requested_id: str) -> str:
        candidate = self._data_root / requested_id
        if not candidate.exists():
            return requested_id

        timestamp = self._now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{timestamp}"
        base = requested_id[: 128 - len(suffix)]
        dataset_id = f"{base}{suffix}"
        collision_index = 2
        while (self._data_root / dataset_id).exists():
            indexed_suffix = f"{suffix}_{collision_index}"
            dataset_id = f"{requested_id[: 128 - len(indexed_suffix)]}{indexed_suffix}"
            collision_index += 1
        return dataset_id

    @staticmethod
    def _redact_error(message: str) -> str:
        cleaned = message.replace("\r", "").replace("\n", " ")
        return re.sub(r"(?<!\w)/(?:[^\s:]+/?)+", "<path>", cleaned)[:500]

    def _validate_session(self, session_id: str) -> None:
        if not self._status.session_id or session_id != self._status.session_id:
            raise OperatorConflictError("Stale session ID")

    def _event_replay(self, last_event_id: str | None) -> tuple[str, list[OperatorStatus]]:
        if not last_event_id:
            return "snapshot", [self.status()]
        try:
            service_instance_id, revision_text = last_event_id.rsplit(":", 1)
            revision = int(revision_text)
        except (AttributeError, ValueError):
            return "snapshot", [self.status()]
        if service_instance_id != self._status.service_instance_id:
            return "snapshot", [self.status()]
        if self._events and revision < self._events[0].revision - 1:
            return "snapshot", [self.status()]
        replay = [event.model_copy(deep=True) for event in self._events if event.revision > revision]
        if not replay and revision < self._status.revision:
            return "snapshot", [self.status()]
        return "status", replay

    async def _monitor_worker(self, worker: Any, session_id: str) -> None:
        try:
            await worker.wait()
        except Exception as error:
            wait_error = self._redact_error(str(error))
        else:
            wait_error = None
        if worker is self._expected_worker_exit:
            return
        recovered = bool(getattr(worker, "torque_verified_off", False))
        if not recovered:
            try:
                recovered = bool(await worker.recover())
            except Exception:
                recovered = False
        async with self._lock:
            if self._worker is not worker or self._status.session_id != session_id:
                return
            self._worker = None
            self._camera_frames.clear()
            detail = f": {wait_error}" if wait_error else ""
            self._replace_status(
                state=SessionState.FAILED,
                worker_pid=None,
                cleanup_unconfirmed=not recovered,
                error=self._redact_error(
                    "Operator worker exited unexpectedly; torque-off recovery "
                    f"{'confirmed' if recovered else 'unconfirmed'}{detail}"
                ),
            )

    async def _cleanup_worker(self, worker: Any | None) -> bool:
        if worker is None:
            return True
        self._expected_worker_exit = worker
        try:
            confirmed = bool(await worker.terminate())
        except Exception:
            confirmed = False
        if confirmed:
            return True
        try:
            return bool(await worker.recover())
        except Exception:
            return False

    def _remember_start(self, request: StartSessionRequest, status: OperatorStatus) -> None:
        self._handled_starts[request.command_id] = _HandledStart(request.model_copy(deep=True), status)
        while len(self._handled_starts) > 128:
            self._handled_starts.pop(next(iter(self._handled_starts)))

    def _remember_command(self, command: OperatorCommand, status: OperatorStatus) -> None:
        self._handled_commands[command.command_id] = _HandledCommand(command.model_copy(deep=True), status)
        while len(self._handled_commands) > 128:
            self._handled_commands.pop(next(iter(self._handled_commands)))

    def _schedule_hardware_rate(self, event: HardwareWorkerRate) -> None:
        self._schedule_background(self._apply_hardware_rate(event))

    def _schedule_hardware_log(self, message: str) -> None:
        self._schedule_background(self._apply_hardware_log(message))

    def _schedule_hardware_telemetry(self, event: HardwareWorkerTelemetry) -> None:
        self._schedule_background(self._apply_hardware_telemetry(event))

    def _schedule_hardware_preview(self, event: HardwareWorkerPreview) -> None:
        self._schedule_background(self._apply_hardware_preview(event))

    def _schedule_background(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _apply_hardware_rate(self, event: HardwareWorkerRate) -> None:
        async with self._lock:
            if event.session_id == self._status.session_id:
                self._replace_status(
                    target_hz=event.target_hz,
                    actual_hz=event.actual_hz,
                    loop_p95_ms=event.loop_p95_ms,
                    loop_max_ms=event.loop_max_ms,
                    overruns=event.overruns,
                )

    async def _apply_hardware_log(self, message: str) -> None:
        async with self._lock:
            if self._status.state in {SessionState.STARTING, SessionState.RUNNING, SessionState.STOPPING}:
                self._replace_status(latest_worker_log=self._redact_error(message))

    async def _apply_hardware_telemetry(self, event: HardwareWorkerTelemetry) -> None:
        async with self._lock:
            if event.session_id == self._status.session_id:
                self._replace_status(
                    latest_telemetry=OperatorTelemetry(
                        elapsed_s=event.elapsed_s,
                        leader=dict(list(event.leader.items())[:64]),
                        follower=dict(list(event.follower.items())[:64]),
                        commanded=dict(list(event.commanded.items())[:64]),
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
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9") or len(jpeg) > 300_000:
            return
        self._camera_frames[event.camera] = OperatorCameraFrame(jpeg=jpeg, captured_at_s=event.captured_at_s)

    def _replace_status(self, **changes: object) -> None:
        self._status = self._status.model_copy(update={"revision": self._status.revision + 1, **changes})
        snapshot = self.status()
        self._events.append(snapshot)
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot.model_copy(deep=True))
