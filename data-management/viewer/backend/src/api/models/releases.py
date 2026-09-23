"""Immutable release metadata and operational event contracts."""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import ConfigDict, Field, JsonValue, model_validator

from .reviews import ContractId, ImmutableContract, Sha256Digest, SourceIdentity, UtcTimestamp


class ReleaseFormat(ImmutableContract):
    """One declared dataset format and version."""

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)


class ReleaseFile(ImmutableContract):
    """Integrity metadata for one file in a release package."""

    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    sha256: Sha256Digest


class ReleaseManifest(ImmutableContract):
    """Canonical metadata for one immutable, single-format release."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    release_id: ContractId
    created_at: UtcTimestamp
    actor_id: ContractId
    source_provenance: tuple[SourceIdentity, ...] = Field(min_length=1)
    accepted_decision_ids: tuple[ContractId, ...] = Field(min_length=1)
    episode_index_mapping: dict[int, int]
    source_formats: tuple[ReleaseFormat, ...] = Field(min_length=1)
    target_format: ReleaseFormat
    adapter_versions: dict[str, str]
    tool_versions: dict[str, str]
    feature_schema: dict[str, JsonValue]
    episode_count: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    files: tuple[ReleaseFile, ...]

    @model_validator(mode="after")
    def validate_counts_and_identity(self) -> ReleaseManifest:
        if self.episode_count != len(self.episode_index_mapping):
            raise ValueError("episode_count must match episode_index_mapping")
        if len(self.accepted_decision_ids) != self.episode_count:
            raise ValueError("accepted_decision_ids must contain one decision per episode")
        if len(set(self.episode_index_mapping.values())) != len(self.episode_index_mapping):
            raise ValueError("release episode indices must be unique")
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
