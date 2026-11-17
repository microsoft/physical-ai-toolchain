"""Builders for closed release inventories."""

from __future__ import annotations

from collections.abc import Mapping

from physical_ai_curation.release.canonical import normalize_release_path, sha256_hex
from physical_ai_curation.release.models import Inventory, InventoryEntry


def build_inventory(files: Mapping[str, bytes]) -> Inventory:
    """Build a sorted inventory covering every payload and evidence object."""
    entries = [
        InventoryEntry(path=normalize_release_path(path), length=len(content), sha256=sha256_hex(content))
        for path, content in sorted(files.items())
    ]
    return Inventory(entries=entries)
