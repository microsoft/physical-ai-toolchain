"""Errors raised by release contract parsing and verification."""

from __future__ import annotations


class ReleaseVerificationError(ValueError):
    """A release package, control artifact, or path violates the contract."""


class IncompleteReleaseError(ReleaseVerificationError):
    """A package lacks the final marker required for public verification."""
