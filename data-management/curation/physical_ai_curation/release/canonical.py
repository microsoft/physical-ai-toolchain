"""Deterministic serialization, strict JSON parsing, digests, and path rules.

Canonical form: UTF-8, sorted object keys, compact separators, no NaN or
infinity. Release identities are SHA-256 digests of canonical bytes or of
stored object bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from pydantic import BaseModel

from physical_ai_curation.release.errors import ReleaseVerificationError

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def _require_string_keys(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON object keys must be strings: {key!r}")
            _require_string_keys(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _require_string_keys(item)


def canonical_json_bytes(payload: object) -> bytes:
    """Serialize a JSON-compatible payload to canonical UTF-8 bytes.

    Raises ValueError for non-string object keys and non-finite numbers.
    """
    _require_string_keys(payload)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


def serialize_model(model: BaseModel) -> bytes:
    """Serialize a release model to canonical bytes."""
    return canonical_json_bytes(model.model_dump(mode="json"))


def sha256_hex(content: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of content."""
    return hashlib.sha256(content).hexdigest()


def parse_strict_json(content: bytes) -> Any:
    """Parse UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseVerificationError("release JSON must be UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
        )
    except ReleaseVerificationError:
        raise
    except ValueError as exc:
        raise ReleaseVerificationError(f"invalid release JSON: {exc}") from exc


def normalize_release_path(raw: object) -> str:
    """Validate a package-relative POSIX path.

    Rejects empty, absolute, drive-qualified, backslash, NUL, empty, dot, and
    parent segments. Returns the path unchanged when it is safe.
    """
    if not isinstance(raw, str) or not raw:
        raise ReleaseVerificationError("release path must be a non-empty string")
    if "\x00" in raw or "\\" in raw or raw.startswith("/") or _WINDOWS_DRIVE.match(raw):
        raise ReleaseVerificationError(f"unsafe release path: {raw!r}")
    if any(segment in ("", ".", "..") for segment in raw.split("/")):
        raise ReleaseVerificationError(f"unsafe release path: {raw!r}")
    return raw


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseVerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ReleaseVerificationError(f"non-finite JSON number: {token}")


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ReleaseVerificationError(f"non-finite JSON number: {token}")
    return value
