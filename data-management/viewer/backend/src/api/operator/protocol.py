"""Protocol-v2 models shared by the backend client and operator worker."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION: Final = 2
MAX_EVENT_BYTES: Final = 600_000
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _CommandEnvelope(_Message):
    protocol_version: Literal[2] = 2
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    command_id: Identifier
    correlation_id: Identifier
    worker_pid: int = Field(gt=0)


class HardwareInitializeCommand(_CommandEnvelope):
    type: Literal["initialize"] = "initialize"
    startup_nonce: str = Field(min_length=16, max_length=256)
    profile: dict[str, Any]
    profile_fingerprint: Identifier
    resource_fingerprint: Identifier
    settings: dict[str, Any]


class HardwareRunCommand(_CommandEnvelope):
    type: Literal["run"] = "run"


class HardwareStopCommand(_CommandEnvelope):
    type: Literal["stop"] = "stop"


class HardwareActionCommand(_CommandEnvelope):
    type: Literal["action"] = "action"
    action: Literal["save", "rerecord", "pause", "resume", "finish"]


class HardwareWorkerHello(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["hello"] = "hello"
    session_id: Identifier
    worker_version: Identifier
    python_version: Identifier
    lerobot_version: Identifier
    pid: int = Field(gt=0)
    supported_modes: list[Literal["teleoperate", "record", "policy"]]


class HardwareWorkerInitialized(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["initialized"] = "initialized"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    startup_nonce: str
    resources: dict[str, str]


class HardwareWorkerRunning(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["running"] = "running"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    torque_enabled: bool


class HardwareWorkerCleanup(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["cleanup"] = "cleanup"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    command_id: Identifier
    correlation_id: Identifier
    cleanup_complete: bool
    torque_verified_off: bool
    released: list[str]
    errors: list[str]
    upload_attempted: bool = False
    upload_succeeded: bool = False
    upload_error: str | None = None


class HardwareWorkerRate(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["rate"] = "rate"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    target_hz: float
    actual_hz: float
    loop_p50_ms: float
    loop_p95_ms: float
    loop_max_ms: float
    overruns: int = Field(ge=0)


class HardwareWorkerTelemetry(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["telemetry"] = "telemetry"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    elapsed_s: float
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class HardwareWorkerPreview(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["preview"] = "preview"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    camera: Identifier
    captured_at_s: float
    jpeg_base64: str = Field(max_length=500_000)


class HardwareWorkerCommandAcknowledgement(_Message):
    protocol_version: Literal[2] = 2
    type: Literal["command_ack"] = "command_ack"
    service_instance_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=1)
    command_id: Identifier
    correlation_id: Identifier
    action: Literal["save", "rerecord", "pause", "resume", "finish"]
    dataset_id: str
    episode_index: int = Field(ge=0)
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
HARDWARE_WORKER_EVENT_ADAPTER: TypeAdapter[HardwareWorkerEvent] = TypeAdapter(HardwareWorkerEvent)
