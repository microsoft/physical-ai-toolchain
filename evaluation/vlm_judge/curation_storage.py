"""Conditional curation storage and verified machine contribution application."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .curation import ContributionLedger, DatasetLabelsFile, EpisodeAnalysisRecord, MachineOrigin


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


@contextmanager
def _resource_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f"{path.name}.lock"), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "r+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _check_revision(path: Path, if_match: str | None, if_none_match: bool) -> bool:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = None
    current_etag = content_etag(content) if content is not None else None
    if (if_none_match and content is not None) or (if_match is not None and if_match != current_etag):
        raise RevisionConflictError(current_etag)
    return content is not None


def write_conditional(path: Path, content: str, *, if_match: str | None = None, if_none_match: bool = False) -> str:
    with _resource_lock(path):
        _check_revision(path, if_match, if_none_match)
        encoded = content.encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return content_etag(encoded)


def delete_conditional(path: Path, *, if_match: str | None = None) -> bool:
    with _resource_lock(path):
        if not _check_revision(path, if_match, False):
            return False
        path.unlink()
        return True


class CurationStorage(Protocol):
    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]: ...

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str: ...


class LocalCurationStorage:
    """Use explicitly configured roots and the same conditional lock as the viewer."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self.roots = {identifier: root.resolve() for identifier, root in roots.items()}

    def _path(self, dataset_id: str) -> Path:
        root = self.roots[dataset_id]
        path = (root / "meta" / "episode_labels.json").resolve()
        path.relative_to(root)
        return path

    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        try:
            content = await asyncio.to_thread(self._path(dataset_id).read_bytes)
        except FileNotFoundError:
            return VersionedValue(DatasetLabelsFile(dataset_id=dataset_id), None)
        value = DatasetLabelsFile.model_validate_json(content)
        if value.dataset_id != dataset_id:
            raise ValueError("Curation dataset identity mismatch")
        return VersionedValue(value, content_etag(content))

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        return await asyncio.to_thread(
            write_conditional,
            self._path(dataset_id),
            json.dumps(labels_file.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False),
            if_match=if_match,
            if_none_match=if_none_match,
        )


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


async def apply_judge_result(storage: CurationStorage, job: dict[str, Any], target: dict[str, Any]) -> bool:
    """Apply typed machine proposals from a verified durable result."""
    from .curation import EpisodeAnalysisRecord, validate_task_result
    from .judge import JudgeResult

    task_result = None
    if job.get("result_kind") == "task-findings":
        task_result = validate_task_result(target["result"])
    else:
        result = JudgeResult.from_dict(target["result"])
        if result.outcome_success is None:
            return False
    resource = await storage.load_versioned(job["dataset_id"])
    labels = resource.value or DatasetLabelsFile(dataset_id=job["dataset_id"])
    origin = MachineOrigin(
        run_id=job["id"],
        result_id=target["result_id"],
        run_order=job["run_order"],
        source_revision=target["input"]["source_revision"],
        input_revision=target["input"]["snapshot_id"],
        config_revision=job["config_revision"],
    )
    if task_result is not None:
        labels.apply_analysis(
            target["episode_index"],
            EpisodeAnalysisRecord(
                **task_result["findings"], instruction=task_result["instruction"], source=task_result["judge_model"]
            ),
            author_id=job["actor"],
            machine_origin=origin,
        )
        await storage.save(job["dataset_id"], labels, if_match=resource.etag, if_none_match=resource.etag is None)
        return True
    key = str(target["episode_index"])
    outcome = "SUCCESS" if result.outcome_success else "FAILURE"
    previous = labels.episodes.get(key, [])
    ledger = labels.provenance.setdefault(key, ContributionLedger())
    ledger.record_changes(
        {f"labels/{label}": label in previous for label in ("SUCCESS", "FAILURE", "PARTIAL")},
        {f"labels/{label}": label == outcome for label in ("SUCCESS", "FAILURE", "PARTIAL")},
        author_id=job["actor"],
        machine_origin=origin,
    )
    labels.materialize_episode(target["episode_index"], author_id=job["actor"])
    if outcome not in labels.available_labels:
        labels.available_labels.append(outcome)
    await storage.save(job["dataset_id"], labels, if_match=resource.etag, if_none_match=resource.etag is None)
    return True
