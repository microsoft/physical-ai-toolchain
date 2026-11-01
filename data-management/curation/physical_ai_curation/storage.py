"""Conditional curation storage and verified machine contribution application."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from physical_ai_curation.models import ContributionLedger, DatasetLabelsFile, EpisodeAnalysisRecord


@dataclass(frozen=True)
class VersionedValue[T]:
    value: T | None
    etag: str | None


class RevisionConflictError(Exception):
    def __init__(self, current_etag: str | None) -> None:
        super().__init__("Resource revision precondition failed")
        self.current_etag = current_etag


def content_etag(content: bytes) -> str:
    return f'"{hashlib.sha256(content).hexdigest()}"'


class JobStore(Protocol):
    def transaction(self) -> Any:
        """Open a serialized state transaction."""

    async def read(self) -> dict[str, Any]:
        """Read the current durable job state."""


class CurationStorage(Protocol):
    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        """Read labels with their revision validator."""

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        """Persist labels conditionally and return the new revision validator."""


def _present(namespace: str, value: Any) -> bool:
    # Label flags are absent when False; an analysis False is a real finding.
    return value is True if namespace == "labels" else value is not None and value != []


def plan_withdrawal(labels: DatasetLabelsFile, run_ids: set[str]) -> tuple[DatasetLabelsFile, dict[str, Any]]:
    projected = labels.model_copy(deep=True)
    summary: dict[str, Any] = {
        "removable_fields": 0,
        "accepted_unchanged": 0,
        "preserved_human": 0,
        "legacy_unknown": 0,
        "conflicts": 0,
        "episodes": 0,
        "fields": [],
        "unlisted_run_ids": sorted(
            {
                item.machine.run_id
                for ledger in labels.provenance.values()
                for item in ledger.contributions
                if item.machine and item.machine.run_id not in run_ids and item.id not in ledger.withdrawn
            }
        ),
    }
    for episode_key in sorted(labels.provenance.keys() | labels.episodes.keys() | labels.analysis.keys()):
        ledger = labels.provenance.get(episode_key, ContributionLedger())
        updated = projected.provenance.get(episode_key, ContributionLedger())
        covered = {
            item.id
            for item in ledger.contributions
            if item.machine and item.machine.run_id in run_ids and item.id not in ledger.withdrawn
        }
        updated.withdraw(sorted(covered))
        covered.update(set(updated.withdrawn) - set(ledger.withdrawn))
        fields = {item.field for item in ledger.contributions}
        fields.update(f"labels/{label}" for label in labels.episodes.get(episode_key, []))
        analysis = labels.analysis.get(episode_key, EpisodeAnalysisRecord()).model_dump(mode="json")
        fields.update(f"analysis/{name}" for name, value in analysis.items() if value is not None and value != [])
        removed = False
        for field in sorted(fields):
            namespace, name = field.split("/", 1)
            current = name in labels.episodes.get(episode_key, []) if namespace == "labels" else analysis.get(name)
            field_items = [item for item in ledger.contributions if item.field == field]
            withdrawn_items = [item for item in field_items if item.id in covered]
            detail: dict[str, Any] = {
                "contribution_ids": [item.id for item in withdrawn_items],
                "run_ids": sorted({item.machine.run_id for item in withdrawn_items if item.machine}),
            }
            try:
                before = ledger.resolve(field, legacy_value=current)
                after = updated.resolve(field, legacy_value=current)
            except ValueError:
                disposition = "conflicts"
                detail.update(
                    origin="human",
                    reason="multiple_human_authors",
                    human_authors=len({item.author_id for item in field_items if item.origin == "human"}),
                )
            else:
                if not any(_present(namespace, value) for value in (current, before.value, after.value)):
                    continue
                detail["origin"] = before.origin
                if before.origin == "human":
                    disposition = "preserved_human"
                elif covered.intersection(before.contribution_ids):
                    if after.origin == "machine":
                        blocking = [item for item in field_items if item.id in after.contribution_ids]
                        updated.withdrawn = [
                            identity
                            for identity in updated.withdrawn
                            if identity in ledger.withdrawn
                            or not any(item.id == identity and item.field == field for item in ledger.contributions)
                        ]
                        disposition = "conflicts"
                        detail.update(
                            reason="unlisted_machine_run",
                            blocking_contribution_ids=[item.id for item in blocking],
                            blocking_run_ids=sorted({item.machine.run_id for item in blocking if item.machine}),
                        )
                    elif after.origin == "legacy-unknown" and _present(namespace, after.value):
                        # The pre-existing unprovenanced value is restored rather than guessed removable.
                        disposition = "legacy_unknown"
                        detail["reason"] = "legacy_value_restored"
                        if namespace == "analysis":
                            analysis[name] = after.value
                            projected.analysis[episode_key] = EpisodeAnalysisRecord.model_validate(analysis)
                    else:
                        disposition = "removable_fields"
                        removed = True
                        if any(ledger.acceptances.get(identity) for identity in before.contribution_ids):
                            summary["accepted_unchanged"] += 1
                        if namespace == "labels":
                            projected.episodes[episode_key] = [
                                label for label in projected.episodes.get(episode_key, []) if label != name
                            ]
                        elif namespace == "analysis":
                            analysis[name] = [] if name == "motion_flags" and after.value is None else after.value
                            projected.analysis[episode_key] = EpisodeAnalysisRecord.model_validate(analysis)
                elif before.origin == "machine":
                    blocking = [item for item in field_items if item.id in before.contribution_ids]
                    disposition = "conflicts"
                    detail.update(
                        reason="unlisted_machine_run",
                        blocking_contribution_ids=[item.id for item in blocking],
                        blocking_run_ids=sorted({item.machine.run_id for item in blocking if item.machine}),
                    )
                elif before.origin == "legacy-unknown" and _present(namespace, current):
                    disposition = "legacy_unknown"
                else:
                    continue
            summary[disposition] += 1
            summary["fields"].append(
                {"episode_index": int(episode_key), "field": field, "disposition": disposition, **detail}
            )
        if removed:
            summary["episodes"] += 1
    return projected, summary
