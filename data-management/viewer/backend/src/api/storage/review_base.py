"""Append-only review repository contract and errors."""

from __future__ import annotations

import re
from typing import Protocol

from ..models.releases import OperationalEvent
from ..models.reviews import AnnotationRevision, EditRevision, QualityReport, ReviewDecision, SourceIdentity

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,254}$")


class ReviewStorageError(Exception):
    """Base error for immutable review persistence."""


class DuplicateReviewRecordError(ReviewStorageError):
    """Raised when an immutable record ID already exists."""


class ReviewRepository(Protocol):
    """Persistence boundary for immutable review records."""

    async def create_annotation_revision(self, revision: AnnotationRevision) -> None: ...

    async def get_annotation_revision(self, revision_id: str) -> AnnotationRevision | None: ...

    async def list_annotation_revisions(self, source: SourceIdentity) -> list[AnnotationRevision]: ...

    async def create_edit_revision(self, revision: EditRevision) -> None: ...

    async def get_edit_revision(self, revision_id: str) -> EditRevision | None: ...

    async def list_edit_revisions(self, source: SourceIdentity) -> list[EditRevision]: ...

    async def create_quality_report(self, report: QualityReport) -> None: ...

    async def get_quality_report(self, run_id: str) -> QualityReport | None: ...

    async def create_decision(self, decision: ReviewDecision) -> None: ...

    async def get_decision(self, decision_id: str) -> ReviewDecision | None: ...

    async def append_event(self, event: OperationalEvent) -> None: ...


def validate_review_identifier(value: str) -> str:
    """Validate an identifier before deriving a local path or Blob name."""
    if _SAFE_ID.fullmatch(value) is None:
        raise ReviewStorageError("Invalid review record identifier")
    return value
