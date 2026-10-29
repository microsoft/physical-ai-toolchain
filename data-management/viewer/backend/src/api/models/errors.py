"""OpenAPI models for the API's error responses."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Literal

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """An HTTP exception raised by an endpoint or dependency."""

    detail: str


class EditSourceConflictDetails(BaseModel):
    """The current source identity when saved edits belong to an older generation."""

    message: str
    source_id: str
    source_revision: str


class EditSourceConflictResponse(BaseModel):
    """A saved-edit conflict with the source identity needed for reconciliation."""

    detail: EditSourceConflictDetails


class RevisionDetails(BaseModel):
    """The revision available after a failed conditional write."""

    currentEtag: str | None


class RevisionConflictResponse(BaseModel):
    """A failed optimistic-concurrency precondition."""

    code: Literal["PRECONDITION_FAILED"]
    message: str
    details: RevisionDetails


class ValidationErrorDetail(BaseModel):
    """A sanitized request-validation error."""

    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorResponse(BaseModel):
    """Request validation errors without submitted values."""

    detail: list[ValidationErrorDetail]


class RateLimitResponse(BaseModel):
    """A rate limit enforced by SlowAPI."""

    error: str


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Describe only the error statuses supported by the caller."""
    models = {412: RevisionConflictResponse, 429: RateLimitResponse}
    return {
        status: {"model": models.get(status, ErrorResponse), "description": HTTPStatus(status).phrase}
        for status in statuses
    }
