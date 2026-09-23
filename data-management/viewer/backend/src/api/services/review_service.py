"""Integrity rules for append-only review history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from ..models.reviews import AnnotationRevision, EditRevision, QualityReport, ReviewDecision, SourceIdentity
from ..storage.review_base import ReviewRepository

_Revision = AnnotationRevision | EditRevision


class ReviewIntegrityError(ValueError):
    """Raised when review evidence would form an invalid history."""


class ReviewService:
    """Validate immutable review relationships before persistence."""

    def __init__(self, repository: ReviewRepository) -> None:
        self._repository = repository

    async def create_annotation_revision(self, revision: AnnotationRevision) -> None:
        existing = await self._repository.list_annotation_revisions(revision.source)
        await self._validate_revision_chain(
            revision.source,
            revision.predecessor_revision_id,
            existing,
            self._repository.get_annotation_revision,
        )
        await self._repository.create_annotation_revision(revision)

    async def create_edit_revision(self, revision: EditRevision) -> None:
        existing = await self._repository.list_edit_revisions(revision.source)
        await self._validate_revision_chain(
            revision.source,
            revision.predecessor_revision_id,
            existing,
            self._repository.get_edit_revision,
        )
        await self._repository.create_edit_revision(revision)

    async def create_quality_report(self, report: QualityReport) -> None:
        await self._repository.create_quality_report(report)

    async def create_decision(self, decision: ReviewDecision) -> None:
        annotation = await self._repository.get_annotation_revision(decision.annotation_revision_id)
        if annotation is None:
            raise ReviewIntegrityError("Decision references a missing annotation revision")
        edit = await self._repository.get_edit_revision(decision.edit_revision_id)
        if edit is None:
            raise ReviewIntegrityError("Decision references a missing edit revision")
        quality = await self._repository.get_quality_report(decision.quality_run_id)
        if quality is None:
            raise ReviewIntegrityError("Decision references a missing quality run")
        if annotation.source != decision.source or edit.source != decision.source or quality.source != decision.source:
            raise ReviewIntegrityError("Decision evidence does not match the source identity")
        await self._repository.create_decision(decision)

    @staticmethod
    async def _validate_revision_chain(
        source: SourceIdentity,
        predecessor_id: str | None,
        existing: Sequence[_Revision],
        get_predecessor: Callable[[str], Awaitable[_Revision | None]],
    ) -> None:
        if predecessor_id is None:
            if existing:
                raise ReviewIntegrityError("A predecessor is required for an existing revision chain")
            return
        predecessor = await get_predecessor(predecessor_id)
        if predecessor is None:
            raise ReviewIntegrityError("Revision predecessor does not exist")
        if predecessor.source != source:
            raise ReviewIntegrityError("Revision predecessor has a different source identity")
        if existing and existing[-1].revision_id != predecessor_id:
            raise ReviewIntegrityError("Revision predecessor must be the latest revision")
