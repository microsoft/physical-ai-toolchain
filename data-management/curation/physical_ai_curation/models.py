"""Shared persisted curation ownership and label models, independent of HTTP and storage."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


def sanitize_user_string(value: str) -> str:
    """Strip CR/LF characters from user-provided strings."""
    return value.replace("\r", "").replace("\n", "")


def _sanitize_nested_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_user_string(value)
    if isinstance(value, list):
        return [_sanitize_nested_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_nested_value(item) for item in value)
    if isinstance(value, set):
        return {_sanitize_nested_value(item) for item in value}
    if isinstance(value, dict):
        return {_sanitize_nested_value(key): _sanitize_nested_value(item) for key, item in value.items()}
    return value


class SanitizedModel(BaseModel):
    """Pydantic base model that strips CR/LF from nested string values."""

    @model_validator(mode="after")
    def sanitize_strings(self) -> SanitizedModel:
        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, _sanitize_nested_value(getattr(self, field_name)))
        return self


ContributionOrigin = Literal["human", "machine", "template", "retroactive", "legacy-unknown"]


class MachineOrigin(SanitizedModel):
    """References to the immutable run and saved inputs that produced a value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    run_order: int = Field(ge=0, strict=True)
    source_revision: str = Field(min_length=1)
    input_revision: str = Field(min_length=1)
    config_revision: str = Field(min_length=1)


class Contribution(SanitizedModel):
    """One immutable authored value; edits create another contribution."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    origin: ContributionOrigin
    value: JsonValue
    sequence: int = Field(ge=0, strict=True)
    timestamp: AwareDatetime
    author_id: str | None = Field(default=None, min_length=1)
    machine: MachineOrigin | None = None
    derived_from: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_origin(self) -> Self:
        if self.origin == "human" and not self.author_id:
            raise ValueError("Human contributions require an author")
        if self.origin == "machine" and self.machine is None:
            raise ValueError("Machine contributions require run provenance")
        if self.origin == "human" and self.machine is not None:
            raise ValueError("Human authorship must not replace machine origin")
        return self


class EffectiveContribution(SanitizedModel):
    """Resolved value with ownership, including explicitly unknown legacy data."""

    value: JsonValue = None
    origin: ContributionOrigin = "legacy-unknown"
    contribution_ids: list[str] = Field(default_factory=list)


class ContributionLedger(SanitizedModel):
    """Versioned ownership and independent acceptance/withdrawal relationships."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    contributions: list[Contribution] = Field(default_factory=list)
    acceptances: dict[str, list[str]] = Field(default_factory=dict)
    withdrawn: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        identities = {item.id for item in self.contributions}
        if len(identities) != len(self.contributions):
            raise ValueError("Contribution identities must be unique")
        if not set(self.acceptances).union(self.withdrawn).issubset(identities):
            raise ValueError("Contribution relationships require an existing identity")
        preceding = set()
        for item in self.contributions:
            if not set(item.derived_from).issubset(preceding):
                raise ValueError("Derived contributions require preceding source identities")
            preceding.add(item.id)
        return self

    def record(self, contribution: Contribution) -> None:
        """Append a value without allowing retries to rewrite its origin."""
        for existing in self.contributions:
            if existing.id == contribution.id:
                if existing != contribution:
                    raise ValueError("Contribution identity and origin are immutable")
                return
        if not set(contribution.derived_from).issubset({item.id for item in self.contributions}):
            raise ValueError("Derived contributions require existing source identities")
        self.contributions.append(contribution.model_copy(deep=True))

    def derive(self, source_id: str, field: str, value: JsonValue) -> str:
        """Retain source ownership when promoting evidence into another field."""
        source = next((item for item in self.contributions if item.id == source_id), None)
        if source is None or source_id in self.withdrawn:
            raise ValueError("Promotion requires active source evidence")
        identity = str(uuid5(NAMESPACE_URL, json.dumps([source_id, field, value], sort_keys=True)))
        if any(item.id == identity for item in self.contributions):
            return identity
        self.record(
            Contribution(
                id=identity,
                field=field,
                value=value,
                origin=source.origin,
                author_id=source.author_id,
                machine=source.machine,
                derived_from=[source_id],
                sequence=max((item.sequence for item in self.contributions), default=0) + 1,
                timestamp=datetime.now(UTC),
            )
        )
        return identity

    def accept(self, contribution_id: str, author_id: str) -> None:
        """Record acceptance without manufacturing a human-authored value."""
        if not author_id.strip():
            raise ValueError("Acceptance requires an author")
        if not any(item.id == contribution_id for item in self.contributions):
            raise ValueError("Unknown contribution")
        if contribution_id in self.withdrawn:
            raise ValueError("Cannot accept a withdrawn contribution")
        authors = self.acceptances.setdefault(contribution_id, [])
        if author_id not in authors:
            authors.append(author_id)

    def record_changes(
        self,
        previous: dict[str, JsonValue],
        current: dict[str, JsonValue],
        *,
        author_id: str,
        machine_origin: MachineOrigin | None = None,
        origin: ContributionOrigin | None = None,
    ) -> None:
        """Record actual human deltas or independently owned machine proposals."""
        timestamp = datetime.now(UTC)
        contribution_origin = origin or ("machine" if machine_origin else "human")
        sequence = max((item.sequence for item in self.contributions), default=0)
        for field in sorted(previous.keys() | current.keys()):
            value = current.get(field)
            if (
                machine_origin is None
                and previous.get(field) == value
                and contribution_origin not in ("template", "retroactive")
            ):
                continue
            identity = str(uuid4())
            if machine_origin is not None:
                identity = str(
                    uuid5(
                        NAMESPACE_URL, json.dumps([author_id, machine_origin.run_id, machine_origin.result_id, field])
                    )
                )
                existing = next((item for item in self.contributions if item.id == identity), None)
                if existing is not None:
                    if existing.value != value or existing.machine != machine_origin:
                        raise ValueError("Contribution identity and origin are immutable")
                    continue
            if field in previous and not any(item.field == field for item in self.contributions):
                self.record(
                    Contribution(
                        id=str(uuid4()),
                        field=field,
                        origin="legacy-unknown",
                        value=previous[field],
                        sequence=sequence,
                        timestamp=timestamp,
                    )
                )
            sequence += 1
            self.record(
                Contribution(
                    id=identity,
                    field=field,
                    origin=contribution_origin,
                    value=value,
                    author_id=author_id if contribution_origin == "human" else None,
                    machine=machine_origin,
                    sequence=sequence,
                    timestamp=timestamp,
                )
            )

    def withdraw(self, contribution_ids: list[str]) -> None:
        """Retain tombstones so subsequent resets cannot revive withdrawn values."""
        if not set(contribution_ids).issubset({item.id for item in self.contributions}):
            raise ValueError("Unknown contribution")
        self.withdrawn.extend(
            identity for identity in dict.fromkeys(contribution_ids) if identity not in self.withdrawn
        )
        retired = set(self.withdrawn)
        for item in self.contributions:
            if item.id not in retired and retired.intersection(item.derived_from):
                self.withdrawn.append(item.id)
                retired.add(item.id)

    def resolve(
        self, field: str, *, human_author_id: str | None = None, legacy_value: JsonValue = None
    ) -> EffectiveContribution:
        """Prefer an explicitly scoped human revision, then persisted run order."""
        active = [item for item in self.contributions if item.field == field and item.id not in self.withdrawn]
        humans = [item for item in active if item.origin == "human"]
        authors = {item.author_id for item in humans}
        if len(authors) > 1 and human_author_id is None:
            raise ValueError("Select a human author before resolving contributions")
        if human_author_id is not None:
            humans = [item for item in humans if item.author_id == human_author_id]
            if authors and not humans:
                raise ValueError("Select a human author with a contribution")
        if humans:
            chosen = max(humans, key=lambda item: (item.sequence, item.id))
        elif active:
            chosen = max(
                active,
                key=lambda item: (
                    item.machine.run_order if item.machine else -1,
                    item.machine.run_id if item.machine else "",
                    item.machine.result_id if item.machine else "",
                    item.sequence if item.machine is None else 0,
                    item.id,
                ),
            )
        else:
            value = None if any(item.field == field for item in self.contributions) else legacy_value
            return EffectiveContribution(value=deepcopy(value))
        return EffectiveContribution(value=deepcopy(chosen.value), origin=chosen.origin, contribution_ids=[chosen.id])


DEFAULT_LABELS = ["SUCCESS", "FAILURE", "PARTIAL"]


def validate_task_result(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = {"episode_id", "instruction", "judge_model", "prompt_version"}
    if not isinstance(payload, dict) or set(payload) != metadata | {"findings"}:
        raise ValueError("Invalid task findings result")
    if any(not isinstance(payload[field], str) or not payload[field].strip() for field in metadata):
        raise ValueError("Invalid task findings identity")
    findings = payload["findings"]
    text_fields = {"pick_from", "object", "movement_quality", "notes"}
    boolean_fields = {"grasp_success", "place_success"}
    if not isinstance(findings, dict) or set(findings) != text_fields | boolean_fields:
        raise ValueError("Invalid task findings schema")
    if any(
        not isinstance(findings[field], str) or (field != "notes" and not findings[field].strip())
        for field in text_fields
    ):
        raise ValueError("Invalid task findings text")
    if any(type(findings[field]) is not bool for field in boolean_fields):
        raise ValueError("Invalid task findings outcome")
    return deepcopy(payload)


def _normalize_label(label: str) -> str:
    return label.strip().upper()


class EpisodeAnalysisRecord(SanitizedModel):
    """Structured per-episode analysis: VLM-derived labels plus computed motion metrics.

    Persisted beside the dataset in ``meta/episode_labels.json`` so it loads
    automatically with the dataset. All fields are optional so partial records
    (VLM-only or motion-only) round-trip cleanly.
    """

    pick_from: str | None = None
    object: str | None = None
    grasp_success: bool | None = None
    place_success: bool | None = None
    movement_quality: str | None = None
    notes: str | None = None
    instruction: str | None = None
    duration_s: float | None = None
    smoothness: float | None = None
    normalized_smoothness: float | None = None
    efficiency: float | None = None
    jitter: float | None = None
    hesitation_count: int | None = None
    correction_count: int | None = None
    motion_score: int | None = None
    motion_flags: list[str] = Field(default_factory=list)
    source: str | None = None


class DatasetLabelsFile(SanitizedModel):
    """All episode labels and available options for a dataset."""

    dataset_id: str
    available_labels: list[str] = Field(default_factory=lambda: DEFAULT_LABELS.copy())
    episodes: dict[str, list[str]] = Field(default_factory=dict)
    analysis: dict[str, EpisodeAnalysisRecord] = Field(default_factory=dict)
    provenance: dict[str, ContributionLedger] = Field(default_factory=dict)

    def apply_labels(
        self,
        episode_idx: int,
        labels: list[str],
        *,
        author_id: str,
        machine_origin: MachineOrigin | None = None,
        origin: Literal["human", "legacy-unknown"] | None = None,
    ) -> None:
        """Merge independently owned label values into the existing resource."""
        key = str(episode_idx)
        normalized = list(dict.fromkeys(value for label in labels if (value := _normalize_label(label))))
        previous = self.episodes.get(key, [])
        fields = {f"labels/{label}": True for label in previous}
        proposed = {f"labels/{label}": label in normalized for label in set(previous) | set(normalized)}
        ledger = self.provenance.setdefault(key, ContributionLedger())
        ledger.record_changes(fields, proposed, author_id=author_id, machine_origin=machine_origin, origin=origin)
        self.episodes[key] = normalized
        self.materialize_episode(episode_idx, author_id=author_id)
        for label in self.episodes[key]:
            if label not in self.available_labels:
                self.available_labels.append(label)

    def apply_analysis(
        self,
        episode_idx: int,
        update: EpisodeAnalysisRecord,
        *,
        author_id: str,
        machine_origin: MachineOrigin | None = None,
        origin: Literal["human", "legacy-unknown"] | None = None,
    ) -> None:
        """Apply only supplied analysis fields without acquiring unrelated ownership."""
        key = str(episode_idx)
        previous = self.analysis.get(key, EpisodeAnalysisRecord()).model_dump(mode="json")
        changes = update.model_dump(mode="json", exclude_unset=True)
        ledger = self.provenance.setdefault(key, ContributionLedger())
        ledger.record_changes(
            {f"analysis/{field}": previous[field] for field in changes},
            {f"analysis/{field}": value for field, value in changes.items()},
            author_id=author_id,
            machine_origin=machine_origin,
            origin=origin,
        )
        self.analysis[key] = EpisodeAnalysisRecord.model_validate(previous | changes)
        self.materialize_episode(episode_idx, author_id=author_id)

    def materialize_episode(self, episode_idx: int, *, author_id: str) -> None:
        """Resolve stored ownership after a mutation or contribution withdrawal."""
        key = str(episode_idx)
        ledger = self.provenance.get(key, ContributionLedger())
        existing = self.episodes.get(key, [])
        label_fields = {
            item.field.removeprefix("labels/") for item in ledger.contributions if item.field.startswith("labels/")
        }
        if key in self.episodes or label_fields:
            ordered = list(dict.fromkeys([*existing, *sorted(label_fields)]))
            self.episodes[key] = [
                label
                for label in ordered
                if ledger.resolve(f"labels/{label}", human_author_id=author_id, legacy_value=label in existing).value
                is True
            ]
        analysis_fields = {
            item.field.removeprefix("analysis/") for item in ledger.contributions if item.field.startswith("analysis/")
        }
        if key in self.analysis or analysis_fields:
            values = self.analysis.get(key, EpisodeAnalysisRecord()).model_dump(mode="json")
            for field in analysis_fields:
                value = ledger.resolve(
                    f"analysis/{field}", human_author_id=author_id, legacy_value=values.get(field)
                ).value
                values[field] = [] if field == "motion_flags" and value is None else value
            self.analysis[key] = EpisodeAnalysisRecord.model_validate(values)
