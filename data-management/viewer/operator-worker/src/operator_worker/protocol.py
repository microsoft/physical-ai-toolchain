"""Bounded, versioned messages for the operator worker."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION: Final = 2
MAX_COMMAND_BYTES: Final = 1_048_576
MAX_EVENT_BYTES: Final = 600_000
MAX_IDENTIFIER_LENGTH: Final = 128

Identifier = Annotated[str, Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH)]


class ProtocolError(RuntimeError):
    """Raised when a message cannot be accepted safely."""


class StrictMessage(BaseModel):
    """Forbid protocol extensions until both processes support them."""

    model_config = ConfigDict(extra="forbid", strict=True)


class SessionSettings(StrictMessage):
    """Settings needed to dispatch one worker session."""

    mode: Literal["teleoperate", "record", "policy"]
    execution_mode: Literal["simulation", "physical"] = "simulation"
    control_fps: int = Field(ge=1, le=120)
    camera_fps: dict[str, Annotated[int, Field(ge=1, le=60)]] = Field(default_factory=dict)
    max_relative_target: float | None = Field(default=None, gt=0, le=180)
    dataset_root: str | None = None
    dataset_id: str | None = None
    repo_id: str | None = None
    task: str = Field(default="Operate the robot", min_length=1, max_length=500)
    save_destination: Literal["local", "local_and_hub"] = "local"
    hub_repo_id: str | None = None
    num_episodes: int = Field(default=1, ge=1, le=1_000)
    episode_time_s: int = Field(default=60, ge=1, le=3_600)
    reset_time_s: int = Field(default=30, ge=0, le=600)
    rollout_time_s: int = Field(default=30, ge=1, le=300)
    policy_python: str | None = None
    policy_checkpoint: str | None = None
    policy_cuda_visible_devices: str | None = None


class WorkerCommandEnvelope(StrictMessage):
    """Identity and correlation fields required on every inbound command."""

    protocol_version: Literal[2] = 2
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    command_id: Identifier
    correlation_id: Identifier
    worker_pid: int = Field(gt=0)


class WorkerHello(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["hello"] = "hello"
    session_id: Identifier
    worker_version: Identifier
    python_version: Identifier
    lerobot_version: Identifier
    pid: int = Field(gt=0)
    supported_modes: list[Literal["teleoperate", "record", "policy"]] = Field(min_length=1, max_length=3)


class InitializeCommand(WorkerCommandEnvelope):
    type: Literal["initialize"] = "initialize"
    startup_nonce: str = Field(min_length=16, max_length=256)
    profile: dict[str, Any]
    profile_fingerprint: Identifier
    resource_fingerprint: Identifier
    settings: SessionSettings


class RunCommand(WorkerCommandEnvelope):
    type: Literal["run"] = "run"


class StopCommand(WorkerCommandEnvelope):
    type: Literal["stop"] = "stop"


class ActionCommand(WorkerCommandEnvelope):
    type: Literal["action"] = "action"
    action: Literal["save", "rerecord", "pause", "resume", "finish"]


class WorkerInitialized(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["initialized"] = "initialized"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    startup_nonce: str
    resources: dict[str, str]


class WorkerRunning(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["running"] = "running"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    torque_enabled: bool


class WorkerCleanup(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["cleanup"] = "cleanup"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    command_id: Identifier
    correlation_id: Identifier
    cleanup_complete: bool
    torque_verified_off: bool
    released: list[str]
    errors: list[str]
    upload_attempted: bool = False
    upload_succeeded: bool = False
    upload_error: str | None = None


class WorkerRate(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["rate"] = "rate"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    target_hz: float
    actual_hz: float
    loop_p50_ms: float
    loop_p95_ms: float
    loop_max_ms: float
    overruns: int


class WorkerTelemetry(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["telemetry"] = "telemetry"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    elapsed_s: float
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class WorkerPreview(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["preview"] = "preview"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    camera: Identifier
    captured_at_s: float
    jpeg_base64: str = Field(max_length=500_000)


class WorkerCommandAcknowledgement(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["command_ack"] = "command_ack"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int
    command_id: Identifier
    correlation_id: Identifier
    action: Literal["save", "rerecord", "pause", "resume", "finish"]
    dataset_id: str
    episode_index: int
    phase: Literal["recording", "paused", "complete", "finalized", "cancelled"]
    finalized: bool


WorkerCommand = InitializeCommand | RunCommand | StopCommand | ActionCommand
WorkerEvent = (
    WorkerHello
    | WorkerInitialized
    | WorkerRunning
    | WorkerCleanup
    | WorkerRate
    | WorkerTelemetry
    | WorkerPreview
    | WorkerCommandAcknowledgement
)
WORKER_COMMAND_ADAPTER: TypeAdapter[WorkerCommand] = TypeAdapter(WorkerCommand)


def parse_worker_command(raw_message: str | bytes) -> WorkerCommand:
    """Validate one bounded JSON-line command."""
    encoded = raw_message.encode("utf-8") if isinstance(raw_message, str) else raw_message
    if not encoded or len(encoded) > MAX_COMMAND_BYTES:
        raise ProtocolError("Worker command size is outside the protocol limit")
    message = encoded.rstrip(b"\r\n")
    if b"\n" in message or b"\r" in message:
        raise ProtocolError("Worker command must contain exactly one JSON object")
    try:
        return WORKER_COMMAND_ADAPTER.validate_json(message)
    except Exception as error:
        raise ProtocolError("Worker command is malformed") from error


def serialize_worker_event(event: WorkerEvent) -> str:
    """Serialize one event while enforcing the outbound size limit."""
    message = event.model_dump_json()
    if len(message.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ProtocolError("Worker event exceeds the protocol size limit")
    return message + "\n"
