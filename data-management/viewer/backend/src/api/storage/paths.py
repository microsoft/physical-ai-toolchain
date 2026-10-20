"""Shared path utilities for mapping dataset IDs to storage paths."""

from __future__ import annotations

import hashlib
import json
import re


def edit_resource_scope(source_id: str, author_id: str) -> str:
    """Produce a path-safe, unambiguous source/author namespace."""
    return hashlib.sha256(json.dumps([source_id, author_id], separators=(",", ":")).encode()).hexdigest()


def resource_directory(resource_scope: str | None) -> str:
    """Select an independent curation resource directory."""
    if resource_scope is None:
        return "episodes"
    if re.fullmatch(r"[0-9a-f]{64}", resource_scope) is None:
        raise ValueError("Invalid curation resource scope")
    return f"edits/{resource_scope}"


def dataset_id_to_blob_prefix(dataset_id: str) -> str:
    """Convert a --separated dataset ID to a /-separated blob prefix."""
    return dataset_id.replace("--", "/")
