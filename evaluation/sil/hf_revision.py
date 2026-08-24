"""Helpers for pinned HuggingFace model and dataset downloads."""

from __future__ import annotations

import re
from pathlib import Path


def resolve_hf_revision(repo_id: str, revision: object, *, revision_name: str = "revision") -> str | None:
    """Return a 40-hex SHA revision for remote repos, or allow local paths.

    Remote HuggingFace repository IDs must be pinned by an explicit 40-hex commit SHA
    so callers do not resolve mutable HEAD. Explicitly-local paths (absolute, or a
    ./ or ../ prefix) are allowed without a revision because no Hub download occurs.
    """
    repo = str(repo_id or "").strip()
    if not repo:
        raise ValueError("policy repository or local path is required")

    # A local model/dataset path bypasses pinning (no Hub download occurs), but only when
    # it is *explicitly* local: an absolute path (an expanded ~ is absolute) or a ./ or ../
    # prefix. A bare "org/name" that merely shadows a directory in the current working
    # directory must NOT be exempted — snapshot_download() ignores local dirs and would
    # fetch that id from the Hub at mutable HEAD.
    expanded = Path(repo).expanduser()
    if expanded.is_absolute() or repo.startswith(("./", "../")):
        return None

    resolved = str(revision or "").strip()
    if not resolved:
        raise ValueError(
            f"{revision_name} is required when loading a remote HuggingFace policy repo; "
            "use a local path for development or pass an immutable 40-hex commit SHA"
        )

    if not re.fullmatch(r"^[0-9a-fA-F]{40}$", resolved):
        raise ValueError(
            f"Invalid {revision_name} '{resolved}': remote HuggingFace repositories require "
            "an immutable 40-hex commit SHA, not a branch, tag, or short hash"
        )

    return resolved
