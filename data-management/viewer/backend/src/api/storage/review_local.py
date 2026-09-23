"""Confined local filesystem persistence for immutable review records."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..models.releases import OperationalEvent, canonical_json_bytes
from ..models.reviews import AnnotationRevision, EditRevision, QualityReport, ReviewDecision, SourceIdentity
from .review_base import DuplicateReviewRecordError, ReviewStorageError, validate_review_identifier

_RecordT = TypeVar("_RecordT", bound=BaseModel)


class LocalReviewRepository:
    """Store immutable review records beneath one configured release root."""

    def __init__(self, release_root: str | Path, *, source_roots: tuple[str | Path, ...]) -> None:
        configured_root = Path(release_root).absolute()
        self._reject_symlink_components(configured_root)
        self.release_root = configured_root.resolve()
        self._validate_source_separation(tuple(Path(root).resolve() for root in source_roots))

    async def create_annotation_revision(self, revision: AnnotationRevision) -> None:
        await self._create_record("annotations", revision.revision_id, revision)

    async def get_annotation_revision(self, revision_id: str) -> AnnotationRevision | None:
        return await self._get_record("annotations", revision_id, AnnotationRevision)

    async def list_annotation_revisions(self, source: SourceIdentity) -> list[AnnotationRevision]:
        records = await self._list_records("annotations", AnnotationRevision, source)
        return sorted((record for record in records if record.source == source), key=lambda record: record.created_at)

    async def create_edit_revision(self, revision: EditRevision) -> None:
        await self._create_record("edits", revision.revision_id, revision)

    async def get_edit_revision(self, revision_id: str) -> EditRevision | None:
        return await self._get_record("edits", revision_id, EditRevision)

    async def list_edit_revisions(self, source: SourceIdentity) -> list[EditRevision]:
        records = await self._list_records("edits", EditRevision, source)
        return sorted((record for record in records if record.source == source), key=lambda record: record.created_at)

    async def create_quality_report(self, report: QualityReport) -> None:
        await self._create_record("quality", report.run_id, report)

    async def get_quality_report(self, run_id: str) -> QualityReport | None:
        return await self._get_record("quality", run_id, QualityReport)

    async def create_decision(self, decision: ReviewDecision) -> None:
        await self._create_record("decisions", decision.decision_id, decision)

    async def get_decision(self, decision_id: str) -> ReviewDecision | None:
        return await self._get_record("decisions", decision_id, ReviewDecision)

    async def append_event(self, event: OperationalEvent) -> None:
        operation_id = validate_review_identifier(event.operation_id)
        path = self._confined_path("events", f"{operation_id}.jsonl")
        await self._ensure_parent(path)
        payload = canonical_json_bytes(event)

        def append() -> None:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)

        try:
            await asyncio.to_thread(append)
        except OSError as exc:
            raise ReviewStorageError(f"Failed to append event for {operation_id}: {exc}") from exc

    async def _create_record(
        self,
        kind: str,
        record_id: str,
        record: AnnotationRevision | EditRevision | QualityReport | ReviewDecision,
    ) -> None:
        validated_id = validate_review_identifier(record_id)
        path = self._record_path(record.source, kind, validated_id)
        await self._ensure_parent(path)
        try:
            await asyncio.to_thread(self._write_exclusive, path, canonical_json_bytes(record))
        except FileExistsError as exc:
            raise DuplicateReviewRecordError(f"Review record already exists: {validated_id}") from exc
        except OSError as exc:
            raise ReviewStorageError(f"Failed to create review record {validated_id}: {exc}") from exc

    async def _get_record(self, kind: str, record_id: str, model: type[_RecordT]) -> _RecordT | None:
        validated_id = validate_review_identifier(record_id)
        reviews_root = self._confined_path("reviews")
        paths = await asyncio.to_thread(
            lambda: sorted(reviews_root.glob(f"*/episodes/episode-*/{kind}/{validated_id}.json"))
        )
        if not paths:
            return None
        if len(paths) > 1:
            raise ReviewStorageError(f"Review record identifier is not unique: {validated_id}")
        try:
            return model.model_validate_json(await asyncio.to_thread(paths[0].read_bytes))
        except (OSError, ValueError) as exc:
            raise ReviewStorageError(f"Failed to read review record {validated_id}: {exc}") from exc

    async def _list_records(self, kind: str, model: type[_RecordT], source: SourceIdentity) -> list[_RecordT]:
        directory = self._record_path(source, kind, "placeholder").parent
        if not await asyncio.to_thread(directory.exists):
            return []
        paths = await asyncio.to_thread(lambda: sorted(directory.glob("*.json")))
        try:
            return [model.model_validate_json(await asyncio.to_thread(path.read_bytes)) for path in paths]
        except (OSError, ValueError) as exc:
            raise ReviewStorageError(f"Failed to list {kind} review records: {exc}") from exc

    def _record_path(self, source: SourceIdentity, kind: str, record_id: str) -> Path:
        dataset_id = validate_review_identifier(source.dataset_id)
        return self._confined_path(
            "reviews",
            dataset_id,
            "episodes",
            f"episode-{source.episode_index:06d}",
            kind,
            f"{record_id}.json",
        )

    async def _ensure_parent(self, path: Path) -> None:
        self._reject_symlink_components(path.parent)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        self._reject_symlink_components(path.parent)

    def _confined_path(self, *parts: str) -> Path:
        candidate = self.release_root.joinpath(*parts)
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.release_root):
            raise ReviewStorageError("Review path escapes the configured release root")
        self._reject_symlink_components(candidate.parent)
        return candidate

    def _validate_source_separation(self, source_roots: tuple[Path, ...]) -> None:
        for source_root in source_roots:
            if self.release_root.is_relative_to(source_root) or source_root.is_relative_to(self.release_root):
                raise ReviewStorageError("Release root must not overlap a source root")

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ReviewStorageError(f"Path contains a symbolic link component: {component}")

    @staticmethod
    def _write_exclusive(path: Path, payload: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(payload)
