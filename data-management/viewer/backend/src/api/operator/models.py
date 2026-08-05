"""Typed models for operator capabilities, sessions, and commands."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class OperatorAdapterMode(StrEnum):
    """Configured operator implementation."""

    DISABLED = "disabled"
    SIMULATED = "simulated"
    LEROBOT = "lerobot"


class OperatorMode(StrEnum):
    """Supported operator session modes."""

    TELEOPERATE = "teleoperate"
    RECORD = "record"
    POLICY = "policy"


class SessionState(StrEnum):
    """Authoritative operator session lifecycle."""

    DISABLED = "disabled"
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


OperatorAction = Literal[
    "save", "rerecord", "pause", "resume", "finish", "cancel"
]
SaveDestination = Literal["local", "local_and_hub"]


class OperatorSessionSettings(BaseModel):
    """Immutable operator choices applied to one worker session."""

    control_fps: int = Field(ge=1, le=120)
    camera_fps: dict[str, Annotated[int, Field(ge=1, le=60)]] = Field(
        default_factory=lambda: {"wrist": 30, "front": 30}
    )
    max_relative_target: float | None = Field(default=None, gt=0, le=180)
    dataset_name: str = Field(
        default="so101-demo",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    task: str = Field(
        default="Pick <obj> from <loc1> and place in <obj2>",
        min_length=1,
        max_length=500,
    )
    save_destination: SaveDestination = "local"
    hub_repo_id: str | None = Field(
        default=None,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    num_episodes: int = Field(default=50, ge=1, le=1_000)
    episode_time_s: int = Field(default=60, ge=1, le=3_600)
    reset_time_s: int = Field(default=30, ge=0, le=600)
    rollout_time_s: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def require_hub_repository(self) -> OperatorSessionSettings:
        if self.save_destination == "local_and_hub" and not self.hub_repo_id:
            raise ValueError(
                "hub_repo_id is required for local and Hugging Face storage"
            )
        return self

    @classmethod
    def for_mode(cls, mode: OperatorMode) -> OperatorSessionSettings:
        """Return defaults matching the established LeRobot scripts."""
        return cls(
            control_fps=60 if mode is OperatorMode.TELEOPERATE else 30,
            max_relative_target=2.0 if mode is OperatorMode.POLICY else None,
        )


class OperatorCapabilities(BaseModel):
    """Operator feature availability exposed to the frontend."""

    enabled: bool
    adapter_mode: OperatorAdapterMode
    adapter_version: int
    protocol_version: int
    modes: list[OperatorMode]
    profiles: list[str]
    robots: list[OperatorRobot] = Field(default_factory=list)
    cameras: list[OperatorCamera] = Field(default_factory=list)
    preflight_enabled: bool = False
    session_start_enabled: bool = False
    reason: str | None = None


class OperatorCamera(BaseModel):
    name: str
    default_fps: int


class OperatorRobot(BaseModel):
    role: Literal["leader", "follower"]
    name: str
    embodiment: str
    actuator_count: int = Field(ge=1)


class StartSessionRequest(BaseModel):
    """Request to start an operator session."""

    command_id: str = Field(min_length=1, max_length=128)
    mode: OperatorMode
    profile: str | None = None
    preflight_id: str | None = None
    preflight_fingerprint: str | None = None
    settings: OperatorSessionSettings | None = None


class OperatorCommand(BaseModel):
    """Idempotent command addressed to one session."""

    command_id: str = Field(min_length=1, max_length=128)
    action: OperatorAction
    expected_revision: int | None = Field(default=None, ge=0)


class OperatorStatus(BaseModel):
    """Authoritative operator state snapshot."""

    service_instance_id: str
    revision: int = 0
    state: SessionState
    session_id: str = ""
    mode: OperatorMode | None = None
    worker_pid: int | None = None
    last_command: OperatorAction | None = None
    cleanup_unconfirmed: bool = False
    error: str | None = None
    target_hz: float | None = None
    actual_hz: float | None = None
    loop_p95_ms: float | None = None
    loop_max_ms: float | None = None
    overruns: int = 0
    latest_worker_log: str | None = None
    latest_telemetry: OperatorTelemetry | None = None
    session_settings: OperatorSessionSettings | None = None
    dataset_id: str | None = None
    episode_index: int = 0
    recording_phase: str | None = None
    upload_status: Literal["not_requested", "succeeded", "failed"] = "not_requested"
    upload_error: str | None = None


class OperatorTelemetry(BaseModel):
    """Latest downsampled joint telemetry from the hardware worker."""

    elapsed_s: float
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class PreflightCheckOutcome(StrEnum):
    """Result severity for one readiness check."""

    PASSED = "passed"
    WARNING = "warning"
    BLOCKING = "blocking"
    SKIPPED = "skipped"


class PreflightLifecycle(StrEnum):
    """Lifecycle of an expiring preflight resource."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class PreflightCheck(BaseModel):
    """One ordered readiness result."""

    name: str
    outcome: PreflightCheckOutcome
    detail: str
    remediation: str | None = None


class PreflightRequest(BaseModel):
    """Request for a side-effect-free readiness run."""

    command_id: str = Field(min_length=1, max_length=128)
    profile: str = Field(min_length=1, max_length=64)
    mode: OperatorMode
    upload_requested: bool = False


class PreflightResult(BaseModel):
    """Expiring evidence produced by a readiness run."""

    preflight_id: str
    lifecycle: PreflightLifecycle
    profile: str
    mode: OperatorMode
    profile_fingerprint: str
    resource_fingerprint: str
    created_at: datetime
    expires_at: datetime
    checks: list[PreflightCheck]
    ownership_complete: bool
    start_eligible: bool
