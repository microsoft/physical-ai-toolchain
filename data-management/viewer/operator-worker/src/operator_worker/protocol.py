"""Worker-side protocol v2 models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .config import SessionSettings

PROTOCOL_VERSION = 2


class StrictMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkerHello(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["hello"] = "hello"
    session_id: str = Field(min_length=1)
    worker_version: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    lerobot_version: str = Field(min_length=1)
    pid: int = Field(gt=0)
    supported_modes: list[Literal["teleoperate", "record", "policy"]]


class InitializeCommand(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["initialize"] = "initialize"
    service_instance_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    startup_nonce: str = Field(min_length=16)
    profile: dict[str, Any]
    profile_fingerprint: str = Field(min_length=1)
    resource_fingerprint: str = Field(min_length=1)
    settings: SessionSettings


class RunCommand(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["run"] = "run"
    service_instance_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)


class StopCommand(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["stop"] = "stop"
    service_instance_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    command_id: str = Field(min_length=1)


class ActionCommand(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["action"] = "action"
    service_instance_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    command_id: str = Field(min_length=1)
    action: Literal["save", "rerecord", "pause", "resume", "finish"]


class WorkerInitialized(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["initialized"] = "initialized"
    service_instance_id: str
    session_id: str
    sequence: int
    startup_nonce: str
    resources: dict[str, str]


class WorkerRunning(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["running"] = "running"
    service_instance_id: str
    session_id: str
    sequence: int
    torque_enabled: bool


class WorkerCleanup(StrictMessage):
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


class WorkerRate(StrictMessage):
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


class WorkerTelemetry(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["telemetry"] = "telemetry"
    service_instance_id: str
    session_id: str
    sequence: int
    elapsed_s: float
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class WorkerPreview(StrictMessage):
    protocol_version: Literal[2] = 2
    type: Literal["preview"] = "preview"
    service_instance_id: str
    session_id: str
    sequence: int
    camera: str
    captured_at_s: float
    jpeg_base64: str = Field(max_length=500_000)


class WorkerCommandAcknowledgement(StrictMessage):
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


WorkerCommand = InitializeCommand | RunCommand | StopCommand | ActionCommand
WORKER_COMMAND_ADAPTER: TypeAdapter[WorkerCommand] = TypeAdapter(WorkerCommand)
