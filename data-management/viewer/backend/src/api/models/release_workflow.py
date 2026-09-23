"""Typed HTTP contracts for review-gated release workflows."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, model_validator

from ..release.jobs import JobState
from ..validation import SanitizedModel
from .releases import ReleaseFormat
from .reviews import ContractId


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
    episodes: tuple[ReleaseEpisodeSelection, ...] = Field(min_length=1)

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
    conflict: str | None = None
    verification: ReleaseVerification = Field(default_factory=ReleaseVerification)
    progress: ReleaseProgress | None = Field(default=None, exclude_if=lambda value: value is None)


class ReleaseEligibility(ReleaseApiModel):
    """Eligibility result before a durable job is submitted."""

    eligible_episodes: tuple[EligibleEpisode, ...]
    excluded_episodes: tuple[ExcludedEpisode, ...]
