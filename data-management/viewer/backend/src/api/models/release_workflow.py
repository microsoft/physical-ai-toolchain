"""Typed HTTP contracts for review-gated release workflows."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, model_validator

from ..release.jobs import JobState
from ..validation import SanitizedModel
from .releases import ReleaseFormat
from .reviews import ContractId, Sha256Digest, SourceIdentity


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ReleaseApiModel(SanitizedModel):
    """Base model for camel-case release API contracts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class ReleaseEpisodeSelection(ReleaseApiModel):
    """One requested episode and its explicit review decision."""

    episode_index: int = Field(ge=0)
    decision_id: ContractId


class ReleaseSubmitRequest(ReleaseApiModel):
    """Normalized inputs for a durable release submission."""

    release_id: ContractId
    dataset_id: ContractId
    actor_id: ContractId
    reason: str = Field(min_length=1, max_length=2000)
    destination_kind: Literal["local", "azure"]
    idempotency_key: ContractId
    target_format: ReleaseFormat
    episodes: tuple[ReleaseEpisodeSelection, ...] = ()

    @model_validator(mode="after")
    def validate_unique_selections(self) -> ReleaseSubmitRequest:
        episode_indices = [selection.episode_index for selection in self.episodes]
        if len(episode_indices) != len(set(episode_indices)):
            raise ValueError("release episode selections must be unique")
        return self


class EligibleEpisode(ReleaseApiModel):
    """Episode with complete immutable release evidence."""

    episode_index: int = Field(ge=0)
    decision_id: ContractId
    quality_run_id: ContractId


class ExcludedEpisode(ReleaseApiModel):
    """Episode excluded from a release and its stable reason codes."""

    episode_index: int = Field(ge=0)
    reason_codes: tuple[ContractId, ...] = Field(min_length=1)
    decision_id: ContractId | None = None
    quality_run_id: ContractId | None = None
    failed_check_ids: tuple[ContractId, ...] = ()


class EligibilityCandidate(ReleaseApiModel):
    """Immutable release disposition for one authoritative dataset candidate."""

    episode_index: int = Field(ge=0)
    disposition: Literal["accepted", "rejected", "excluded"]
    reason_codes: tuple[ContractId, ...]
    review_reason_codes: tuple[ContractId, ...] = ()
    decision_id: ContractId | None = None
    quality_run_id: ContractId | None = None
    failed_check_ids: tuple[ContractId, ...] = ()
    source: SourceIdentity | None = None


class EligibilitySnapshot(ReleaseApiModel):
    """Normalized dataset candidate assessment persisted with a release job."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    dataset_id: ContractId
    candidates: tuple[EligibilityCandidate, ...] = Field(min_length=1)
    eligibility_fingerprint: Sha256Digest


class ReleaseJobRequest(ReleaseSubmitRequest):
    """Backend-normalized release input persisted for deterministic processing."""

    eligibility_snapshot: EligibilitySnapshot


class ReleaseVerification(ReleaseApiModel):
    """Inspectable application-owned release integrity references."""

    manifest_path: str | None = None
    checksums_path: str | None = None
    verified: bool = False


class ReleaseProgress(ReleaseApiModel):
    """Durable phase progress for a release job."""

    percent: int | None = Field(default=0, ge=0, le=100)
    message: str = "Waiting to start"
    updated_at: datetime | None = None


class ReleaseWorkflowResponse(ReleaseApiModel):
    """Durable release state returned by submit, status, cancel, and inspect."""

    release_id: ContractId
    job_id: ContractId
    state: JobState
    eligible_episodes: tuple[EligibleEpisode, ...]
    excluded_episodes: tuple[ExcludedEpisode, ...]
    rejected_episodes: tuple[ExcludedEpisode, ...] = ()
    eligibility_fingerprint: Sha256Digest
    conflict: str | None = None
    verification: ReleaseVerification = Field(default_factory=ReleaseVerification)
    progress: ReleaseProgress | None = Field(default=None, exclude_if=lambda value: value is None)


class ReleaseEligibility(ReleaseApiModel):
    """Eligibility result before a durable job is submitted."""

    eligible_episodes: tuple[EligibleEpisode, ...]
    excluded_episodes: tuple[ExcludedEpisode, ...]
    rejected_episodes: tuple[ExcludedEpisode, ...] = ()
    eligibility_fingerprint: Sha256Digest
