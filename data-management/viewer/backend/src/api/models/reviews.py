"""Immutable review, source identity, and quality contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, ClassVar

from pydantic import AfterValidator, ConfigDict, Field, JsonValue, model_validator

from ..validation import SanitizedModel

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,254}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

ContractId = Annotated[str, Field(pattern=_ID_PATTERN)]
Sha256Digest = Annotated[str, Field(pattern=_SHA256_PATTERN)]


def _validate_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must include a UTC offset")
    return value


UtcTimestamp = Annotated[datetime, AfterValidator(_validate_utc)]


class ImmutableContract(SanitizedModel):
    """Base for frozen versioned domain contracts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class SourceFileIdentity(ImmutableContract):
    """Content identity for one source file."""

    relative_path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    sha256: Sha256Digest

    @model_validator(mode="after")
    def validate_relative_path(self) -> SourceFileIdentity:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or "\\" in self.relative_path or ".." in path.parts or path.as_posix() in {".", ""}:
            raise ValueError("relative_path must be a normalized relative POSIX path")
        return self


class SourceIdentity(ImmutableContract):
    """Immutable identity of one episode and all source files that define it."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    dataset_id: ContractId
    episode_index: int = Field(ge=0)
    source_format: str = Field(min_length=1, max_length=64)
    format_version: str = Field(min_length=1, max_length=64)
    source_digest: Sha256Digest
    files: tuple[SourceFileIdentity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_files(self) -> SourceIdentity:
        paths = [file.relative_path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("source identity file paths must be unique")
        return self


class AnnotationRevision(ImmutableContract):
    """Append-only snapshot of episode annotations."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    revision_id: ContractId
    source: SourceIdentity
    actor_id: ContractId
    created_at: UtcTimestamp
    annotation: dict[str, JsonValue]
    predecessor_revision_id: ContractId | None = None

    @model_validator(mode="after")
    def validate_predecessor(self) -> AnnotationRevision:
        if self.predecessor_revision_id == self.revision_id:
            raise ValueError("predecessor revision must differ from revision_id")
        return self


class EditOperation(ImmutableContract):
    """One source-preserving transformation in an edit revision."""

    operation: str = Field(min_length=1, max_length=128)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class EditRevision(ImmutableContract):
    """Append-only description of episode edit operations."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    revision_id: ContractId
    source: SourceIdentity
    actor_id: ContractId
    created_at: UtcTimestamp
    operations: tuple[EditOperation, ...]
    predecessor_revision_id: ContractId | None = None

    @model_validator(mode="after")
    def validate_predecessor(self) -> EditRevision:
        if self.predecessor_revision_id == self.revision_id:
            raise ValueError("predecessor revision must differ from revision_id")
        return self


class ReviewDecisionValue(StrEnum):
    """Explicit episode disposition."""

    ACCEPT = "accept"
    REJECT = "reject"


class ReviewDecision(ImmutableContract):
    """Append-only review decision bound to exact review evidence."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    decision_id: ContractId
    decision: ReviewDecisionValue
    reason_codes: tuple[ContractId, ...] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=4000)
    actor_id: ContractId
    created_at: UtcTimestamp
    source: SourceIdentity
    annotation_revision_id: ContractId
    edit_revision_id: ContractId
    quality_run_id: ContractId


class QualityOutcome(StrEnum):
    """Machine-readable quality-check outcome."""

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class QualityCheckResult(ImmutableContract):
    """Result of one versioned quality check."""

    check_id: ContractId
    required: bool
    outcome: QualityOutcome
    measurements: dict[str, JsonValue] = Field(default_factory=dict)
    thresholds: dict[str, JsonValue] = Field(default_factory=dict)
    reason_codes: tuple[ContractId, ...] = ()


class QualityReport(ImmutableContract):
    """Versioned quality evidence for one source episode."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    run_id: ContractId
    check_set_version: str = Field(min_length=1, max_length=64)
    source: SourceIdentity
    actor_id: ContractId
    created_at: UtcTimestamp
    episode_checks: tuple[QualityCheckResult, ...]
    package_checks: tuple[QualityCheckResult, ...]

    @model_validator(mode="after")
    def validate_unique_checks(self) -> QualityReport:
        check_ids = [check.check_id for check in (*self.episode_checks, *self.package_checks)]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("quality check IDs must be unique")
        return self
