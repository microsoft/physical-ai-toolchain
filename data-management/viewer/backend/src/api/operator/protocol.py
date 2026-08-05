"""Versioned JSON-line protocol shared by the operator service and worker."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, TypeAdapter

from .models import OperatorAction, OperatorMode

PROTOCOL_VERSION: Final = 1
HARDWARE_PROTOCOL_VERSION: Final = 2


class WorkerCommand(BaseModel):
    """Command sent from the service to one worker session."""

    version: Literal[1] = 1
    type: Literal["command"] = "command"
    session_id: str
    command_id: str
    action: OperatorAction


class WorkerReady(BaseModel):
    """Worker readiness event emitted after initialization."""

    version: Literal[1] = 1
    type: Literal["ready"] = "ready"
    session_id: str
    mode: OperatorMode


class WorkerAcknowledgement(BaseModel):
    """Worker acknowledgement for an idempotent command."""

    version: Literal[1] = 1
    type: Literal["ack"] = "ack"
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


class WorkerProtocolError(BaseModel):
    """Worker rejection of an invalid or conflicting command."""

    version: Literal[1] = 1
    type: Literal["error"] = "error"
    session_id: str
    command_id: str
    message: str


WorkerEvent = Annotated[
    WorkerReady | WorkerAcknowledgement | WorkerProtocolError,
    Field(discriminator="type"),
]
WORKER_EVENT_ADAPTER: TypeAdapter[WorkerEvent] = TypeAdapter(WorkerEvent)


class HardwareWorkerHello(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["hello"] = "hello"
    session_id: str
    worker_version: str
    python_version: str
    lerobot_version: str
    pid: int
    supported_modes: list[Literal["teleoperate", "record", "policy"]]


class HardwareInitializeCommand(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["initialize"] = "initialize"
    service_instance_id: str
    session_id: str
    sequence: int
    startup_nonce: str
    profile: dict[str, Any]
    profile_fingerprint: str
    resource_fingerprint: str
    settings: dict[str, Any]


class HardwareRunCommand(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["run"] = "run"
    service_instance_id: str
    session_id: str
    sequence: int


class HardwareStopCommand(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["stop"] = "stop"
    service_instance_id: str
    session_id: str
    sequence: int
    command_id: str


class HardwareActionCommand(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["action"] = "action"
    service_instance_id: str
    session_id: str
    sequence: int
    command_id: str
    action: Literal["save", "rerecord", "pause", "resume", "finish"]


class HardwareWorkerInitialized(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["initialized"] = "initialized"
    service_instance_id: str
    session_id: str
    sequence: int
    startup_nonce: str
    resources: dict[str, str]


class HardwareWorkerRunning(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["running"] = "running"
    service_instance_id: str
    session_id: str
    sequence: int
    torque_enabled: bool


class HardwareWorkerCleanup(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["cleanup"] = "cleanup"
    service_instance_id: str
    session_id: str
    sequence: int
    command_id: str
    cleanup_complete: bool
    torque_verified_off: bool
    released: list[str]
    errors: list[str]
    upload_attempted: bool = False
    upload_succeeded: bool = False
    upload_error: str | None = None


class HardwareWorkerRate(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["rate"] = "rate"
    service_instance_id: str
    session_id: str
    sequence: int
    target_hz: float
    actual_hz: float
    loop_p50_ms: float
    loop_p95_ms: float
    loop_max_ms: float
    overruns: int


class HardwareWorkerTelemetry(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["telemetry"] = "telemetry"
    service_instance_id: str
    session_id: str
    sequence: int
    elapsed_s: float
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class HardwareWorkerPreview(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["preview"] = "preview"
    service_instance_id: str
    session_id: str
    sequence: int
    camera: str
    captured_at_s: float
    jpeg_base64: str = Field(max_length=500_000)


class HardwareWorkerCommandAcknowledgement(BaseModel):
    protocol_version: Literal[2] = 2
    type: Literal["command_ack"] = "command_ack"
    service_instance_id: str
    session_id: str
    sequence: int
    command_id: str
    action: Literal["save", "rerecord", "pause", "resume", "finish"]
    dataset_id: str
    episode_index: int
    phase: Literal["recording", "paused", "complete", "finalized", "cancelled"]
    finalized: bool


HardwareWorkerEvent = Annotated[
    HardwareWorkerHello
    | HardwareWorkerInitialized
    | HardwareWorkerRunning
    | HardwareWorkerCleanup
    | HardwareWorkerRate
    | HardwareWorkerTelemetry
    | HardwareWorkerPreview
    | HardwareWorkerCommandAcknowledgement,
    Field(discriminator="type"),
]
HARDWARE_WORKER_EVENT_ADAPTER: TypeAdapter[HardwareWorkerEvent] = TypeAdapter(
    HardwareWorkerEvent
)
