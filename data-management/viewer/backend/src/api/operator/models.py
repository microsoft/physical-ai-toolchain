"""Typed operator capability, calibration, preflight, and session contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OperatorAdapterMode(StrEnum):
    DISABLED = "disabled"
    SIMULATED = "simulated"
    LEROBOT = "lerobot"


class OperatorMode(StrEnum):
    TELEOPERATE = "teleoperate"
    RECORD = "record"
    POLICY = "policy"


class OperatorAction(StrEnum):
    SAVE = "save"
    RERECORD = "rerecord"
    PAUSE = "pause"
    RESUME = "resume"
    FINISH = "finish"
    CANCEL = "cancel"


class SessionState(StrEnum):
    DISABLED = "disabled"
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OperatorSessionSettings(_StrictModel):
    control_fps: int = Field(ge=1, le=120)
    camera_fps: dict[str, int] = Field(default_factory=dict)
    max_relative_target: float | None = Field(default=None, gt=0, le=180)
    dataset_root: str | None = Field(default=None, max_length=4096)
    dataset_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    repo_id: str | None = Field(default=None, max_length=256)
    task: str = Field(default="Operate the robot", min_length=1, max_length=500)
    save_destination: Literal["local", "local_and_hub"] = "local"
    hub_repo_id: str | None = Field(default=None, max_length=256)
    num_episodes: int = Field(default=1, ge=1, le=1_000)
    episode_time_s: int = Field(default=60, ge=1, le=3_600)
    reset_time_s: int = Field(default=30, ge=0, le=600)
    rollout_time_s: int = Field(default=30, ge=1, le=300)
    policy_python: str | None = Field(default=None, max_length=4096)
    policy_checkpoint: str | None = Field(default=None, max_length=4096)
    policy_cuda_visible_devices: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_destination(self) -> Self:
        if self.save_destination == "local_and_hub" and not self.hub_repo_id:
            raise ValueError("hub_repo_id is required for local and Hugging Face storage")
        if any(not 1 <= int(fps) <= 60 for fps in self.camera_fps.values()):
            raise ValueError("camera FPS must be between 1 and 60")
        return self

    @classmethod
    def for_mode(cls, mode: OperatorMode) -> Self:
        return cls(control_fps=60 if mode is OperatorMode.TELEOPERATE else 30)


class OperatorCapabilities(_StrictModel):
    enabled: bool
    adapter_mode: OperatorAdapterMode
    adapter_version: int = Field(ge=1)
    protocol_version: int = Field(ge=1)
    modes: list[OperatorMode]
    profiles: list[str]
    robots: list[OperatorRobot] = Field(default_factory=list)
    cameras: list[OperatorCamera] = Field(default_factory=list)
    preflight_enabled: bool = False
    session_start_enabled: bool = False
    reason: str | None = None


class OperatorCamera(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    default_fps: int = Field(ge=1, le=60)


class OperatorRobot(_StrictModel):
    role: Literal["leader", "follower"]
    name: str = Field(min_length=1, max_length=128)
    embodiment: str = Field(min_length=1, max_length=128)
    actuator_count: int = Field(ge=1, le=64)


class StartSessionRequest(_StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    mode: OperatorMode
    profile: str | None = Field(default=None, max_length=64)
    preflight_id: str | None = Field(default=None, max_length=128)
    preflight_fingerprint: str | None = Field(default=None, max_length=128)
    settings: OperatorSessionSettings | None = None


class OperatorCommand(_StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    action: OperatorAction
    expected_revision: int | None = Field(default=None, ge=0)


class OperatorTelemetry(_StrictModel):
    elapsed_s: float = Field(ge=0)
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class OperatorStatus(_StrictModel):
    service_instance_id: str
    revision: int = Field(default=0, ge=0)
    state: SessionState
    session_id: str = ""
    mode: OperatorMode | None = None
    worker_pid: int | None = Field(default=None, gt=0)
    last_command: OperatorAction | None = None
    cleanup_unconfirmed: bool = False
    error: str | None = None
    target_hz: float | None = None
    actual_hz: float | None = None
    loop_p95_ms: float | None = None
    loop_max_ms: float | None = None
    overruns: int = Field(default=0, ge=0)
    latest_worker_log: str | None = None
    latest_telemetry: OperatorTelemetry | None = None
    session_settings: OperatorSessionSettings | None = None
    dataset_id: str | None = None
    episode_index: int = Field(default=0, ge=0)
    recording_phase: str | None = None
    upload_status: Literal["not_requested", "succeeded", "failed"] = "not_requested"
    upload_error: str | None = None


class PreflightCheckOutcome(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKING = "blocking"
    SKIPPED = "skipped"


class PreflightLifecycle(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class PreflightCheck(_StrictModel):
    name: str
    outcome: PreflightCheckOutcome
    detail: str
    remediation: str | None = None


class PreflightRequest(_StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    profile: str = Field(min_length=1, max_length=64)
    mode: OperatorMode
    upload_requested: bool = False


class PreflightResult(_StrictModel):
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


class CalibrationJoint(_StrictModel):
    name: str
    id: int = Field(ge=1, le=6)
    drive_mode: int = Field(ge=0, le=1)
    homing_offset: int = Field(ge=-2047, le=2047)
    range_min: int = Field(ge=0, le=4095)
    range_max: int = Field(ge=0, le=4095)


class CalibrationFileCheck(_StrictModel):
    role: Literal["leader", "follower"]
    file_name: str
    valid: bool
    hardware_verified: Literal[False] = False
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    joints: list[CalibrationJoint] = Field(default_factory=list, max_length=6)
    issues: list[str] = Field(default_factory=list, max_length=32)


class OperatorCalibrationReport(_StrictModel):
    profile: str
    checked_at: datetime
    valid: bool
    hardware_verified: Literal[False] = False
    arms: list[CalibrationFileCheck] = Field(min_length=2, max_length=2)
