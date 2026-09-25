"""Immutable release metadata and operational event contracts."""

from __future__ import annotations

import json
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, JsonValue, model_validator

from .reviews import ContractId, ImmutableContract, QualityOutcome, Sha256Digest, SourceIdentity, UtcTimestamp


class ReleaseFormat(ImmutableContract):
    """One declared dataset format and version."""

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)


class ReleaseFile(ImmutableContract):
    """Integrity metadata for one file in a release package."""

    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    sha256: Sha256Digest


class QualityEvidenceReference(ImmutableContract):
    """Manifest reference binding one accepted episode to source quality evidence."""

    source_episode_index: int = Field(ge=0)
    release_episode_index: int = Field(ge=0)
    decision_id: ContractId
    quality_run_id: ContractId
    quality_report_path: str = Field(min_length=1, max_length=1024)
    check_set_version: str = Field(min_length=1, max_length=64)
    required_outcome: QualityOutcome


class VisualReadbackSample(ImmutableContract):
    """One successfully decoded visual sample in the finalized package."""

    release_episode_index: int = Field(ge=0)
    feature_name: str = Field(min_length=1, max_length=256)
    frame_index: int = Field(ge=0)
    outcome: QualityOutcome = QualityOutcome.PASS


class PackageQualityReport(ImmutableContract):
    """Bounded semantic read-back evidence for one assembled release package."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    target_format: ReleaseFormat
    episode_count: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    episode_frame_counts: dict[int, int]
    features: tuple[str, ...]
    nonvisual_rows_read_back: int = Field(ge=0)
    visual_samples: tuple[VisualReadbackSample, ...]
    inventory_verified: bool
    checksums_verified: bool


class ReleaseStatisticsProfile(ImmutableContract):
    """Versioned parameters used to compute release trajectory statistics."""

    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    smoothness_mode: Literal["log-scaled", "radian-based"] = "log-scaled"
    velocity_threshold: float = Field(default=0.01, gt=0)
    hesitation_min_frames: int = Field(default=5, ge=1)
    jitter_frequency_threshold: float = Field(default=10.0, gt=0)


class ReleaseEpisodeStatistics(ImmutableContract):
    """Descriptive trajectory statistics for one released episode."""

    release_episode_index: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    smoothness: float = Field(ge=0, le=1)
    normalized_smoothness: float = Field(ge=0, le=1)
    efficiency: float = Field(ge=0, le=1)
    jitter: float = Field(ge=0, le=1)
    hesitation_count: int = Field(ge=0)
    correction_count: int = Field(ge=0)
    overall_score: int = Field(ge=1, le=5)
    flags: tuple[str, ...]


class ReleaseStatistics(ImmutableContract):
    """Descriptive statistics protected by the immutable release checksum."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    statistics_profile: ReleaseStatisticsProfile
    tool_versions: dict[str, str]
    feature_schema_sha256: Sha256Digest
    episodes: tuple[ReleaseEpisodeStatistics, ...]


class ReleaseManifest(ImmutableContract):
    """Canonical metadata for one immutable, single-format release."""

    schema_version: str = Field(default="2.0.0", pattern=r"^\d+\.\d+\.\d+$")
    release_id: ContractId
    created_at: UtcTimestamp
    actor_id: ContractId
    source_provenance: tuple[SourceIdentity, ...] = Field(min_length=1)
    accepted_decision_ids: tuple[ContractId, ...] = Field(min_length=1)
    rejected_decision_ids: tuple[ContractId, ...]
    excluded_episode_indices: tuple[int, ...]
    episode_index_mapping: dict[int, int]
    source_formats: tuple[ReleaseFormat, ...] = Field(min_length=1)
    target_format: ReleaseFormat
    adapter_versions: dict[str, str]
    tool_versions: dict[str, str]
    feature_schema: dict[str, JsonValue]
    candidate_count: int = Field(ge=1)
    accepted_count: int = Field(ge=1)
    rejected_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    nonincluded_count: int = Field(ge=0)
    episode_count: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    quality_evidence: tuple[QualityEvidenceReference, ...]
    package_quality_path: str = "metadata/package-quality.json"
    files: tuple[ReleaseFile, ...]

    @model_validator(mode="after")
    def validate_counts_and_identity(self) -> ReleaseManifest:
        if self.accepted_count != self.episode_count:
            raise ValueError("accepted_count must match episode_count")
        if self.rejected_count != len(self.rejected_decision_ids):
            raise ValueError("rejected_count must match rejected_decision_ids")
        if self.excluded_count != len(self.excluded_episode_indices):
            raise ValueError("excluded_count must match excluded_episode_indices")
        if self.nonincluded_count != self.rejected_count + self.excluded_count:
            raise ValueError("nonincluded_count must equal rejected_count plus excluded_count")
        if self.candidate_count != self.accepted_count + self.nonincluded_count:
            raise ValueError("candidate_count must equal accepted_count plus nonincluded_count")
        if self.episode_count != len(self.episode_index_mapping):
            raise ValueError("episode_count must match episode_index_mapping")
        if len(self.accepted_decision_ids) != self.episode_count:
            raise ValueError("accepted_decision_ids must contain one decision per episode")
        if len(self.accepted_decision_ids) != len(set(self.accepted_decision_ids)):
            raise ValueError("accepted_decision_ids must be unique")
        if len(set(self.episode_index_mapping.values())) != len(self.episode_index_mapping):
            raise ValueError("release episode indices must be unique")
        if set(self.episode_index_mapping.values()) != set(range(self.episode_count)):
            raise ValueError("release episode indices must be contiguous")
        if len(self.rejected_decision_ids) != len(set(self.rejected_decision_ids)):
            raise ValueError("rejected_decision_ids must be unique")
        if set(self.accepted_decision_ids).intersection(self.rejected_decision_ids):
            raise ValueError("accepted and rejected decision IDs must be disjoint")
        if tuple(sorted(set(self.excluded_episode_indices))) != self.excluded_episode_indices:
            raise ValueError("excluded_episode_indices must be unique and sorted")
        if set(self.episode_index_mapping).intersection(self.excluded_episode_indices):
            raise ValueError("accepted and excluded episode indices must be disjoint")
        if len(self.quality_evidence) != self.episode_count:
            raise ValueError("quality_evidence must contain one reference per episode")
        if {reference.decision_id for reference in self.quality_evidence} != set(self.accepted_decision_ids):
            raise ValueError("quality_evidence decisions must match accepted_decision_ids")
        evidence_mapping = {
            reference.source_episode_index: reference.release_episode_index for reference in self.quality_evidence
        }
        if evidence_mapping != self.episode_index_mapping:
            raise ValueError("quality_evidence episode mapping must match episode_index_mapping")
        run_ids = [reference.quality_run_id for reference in self.quality_evidence]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("quality_evidence run IDs must be unique")
        if any(reference.required_outcome is not QualityOutcome.PASS for reference in self.quality_evidence):
            raise ValueError("quality_evidence required outcomes must pass")
        file_paths = [file.path for file in self.files]
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("release file paths must be unique")
        return self


class OperationalEvent(ImmutableContract):
    """Versioned OpenTelemetry-aligned application event."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    operation_id: ContractId
    release_id: ContractId | None = None
    event_name: str = Field(min_length=1, max_length=256)
    timestamp: UtcTimestamp
    observed_timestamp: UtcTimestamp
    actor_id: ContractId
    status: str = Field(min_length=1, max_length=64)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    body: dict[str, JsonValue] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


def canonical_json_bytes(contract: ImmutableContract) -> bytes:
    """Serialize a contract as deterministic UTF-8 JSON with one trailing newline."""
    payload = contract.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return f"{serialized}\n".encode()
