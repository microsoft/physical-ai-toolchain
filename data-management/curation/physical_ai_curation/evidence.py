"""Versioned caller-bound evidence and explicit, non-migrating legacy reads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from physical_ai_curation.judge_port import JudgeInvocation
from physical_ai_curation.models import validate_task_result


class _Milestone(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str = Field(min_length=1)
    completed: bool
    frame_range: str
    evidence: str


class _JudgePayload(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    episode_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    n_frames: int = Field(ge=2)
    outcome_success: bool | None
    outcome_confidence: float = Field(ge=0, le=1)
    outcome_n_valid_votes: int = Field(ge=0)
    progress_per_frame: list[int]
    voc: float = Field(ge=-1, le=1)
    milestones: list[_Milestone] = Field(default_factory=list)
    failure_mode: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> _JudgePayload:
        if any(
            not value.strip() for value in (self.episode_id, self.instruction, self.judge_model, self.prompt_version)
        ):
            raise ValueError("Invalid judge identity")
        if self.progress_per_frame and len(self.progress_per_frame) != self.n_frames:
            raise ValueError("Judge progress does not match frame count")
        if any(not 0 <= value <= 100 for value in self.progress_per_frame):
            raise ValueError("Invalid judge progress")
        if any(not milestone.name.strip() for milestone in self.milestones):
            raise ValueError("Invalid judge milestone")
        return self


def validate_payload(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """Validate without evaluator imports or numeric coercion of stored payloads."""
    if kind == "task-findings":
        return validate_task_result(payload)
    if kind != "judge":
        raise ValueError("Unsupported evidence kind")
    try:
        _JudgePayload.model_validate(payload)
    except ValidationError:
        raise ValueError("Invalid judge evidence") from None
    result = {name: deepcopy(payload[name]) for name in _JudgePayload.model_fields if name in payload}
    result.setdefault("milestones", [])
    result.setdefault("failure_mode", None)
    return result


class EvidenceRecord(BaseModel):
    """Immutable evidence binding; applicability and actor authority live outside it."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: Literal[1] | None
    kind: Literal["judge", "task-findings"]
    payload: dict[str, JsonValue]
    invocation_id: str | None = None
    input_id: str | None = None
    declared_identity: dict[str, JsonValue] | None = None
    observed_identity: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Keep legacy bytes logically unchanged; versioned evidence is additive."""
        return deepcopy(self.payload) if self.schema_version is None else self.model_dump(mode="json")


def bind_evidence(invocation: JudgeInvocation, payload: dict[str, Any], *, kind: str = "judge") -> EvidenceRecord:
    """Associate evidence with caller-owned identity, never with echoed authority."""
    record = {
        "schema_version": 1,
        "kind": kind,
        "payload": payload,
        "invocation_id": invocation.invocation_id,
        "input_id": invocation.input_id,
        "declared_identity": invocation.declared_identity,
        "observed_identity": {"model_id": payload.get("judge_model"), "prompt_version": payload.get("prompt_version")},
    }
    return read_evidence(record, expected=invocation)


def read_evidence(
    value: dict[str, Any], *, kind: str = "judge", expected: JudgeInvocation | None = None
) -> EvidenceRecord:
    """Read v1 or explicit legacy payloads, rejecting unsupported versions and mismatches."""
    if "schema_version" not in value:
        if expected is not None:
            raise ValueError("Legacy evidence has no invocation binding")
        return EvidenceRecord(schema_version=None, kind=kind, payload=validate_payload(value, kind))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported evidence version")
    try:
        record = EvidenceRecord.model_validate(value)
    except ValidationError:
        raise ValueError("Invalid evidence envelope") from None
    if not record.invocation_id or not record.input_id or not record.declared_identity:
        raise ValueError("Missing evidence binding")
    validate_payload(record.payload, record.kind)
    if any(
        record.declared_identity.get(name) != observed for name, observed in (record.observed_identity or {}).items()
    ):
        raise ValueError("Observed evidence identity mismatch")
    for field, name in (("judge_model", "model_id"), ("prompt_version", "prompt_version")):
        if record.payload[field] != record.declared_identity.get(name):
            raise ValueError("Evidence payload identity mismatch")
    if expected is not None and (
        record.invocation_id != expected.invocation_id
        or record.input_id != expected.input_id
        or record.declared_identity != expected.declared_identity
        or record.payload["episode_id"] != expected.episode_id
        or record.payload["instruction"] != expected.instruction
    ):
        raise ValueError("Evidence invocation mismatch")
    return record
