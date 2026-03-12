"""Input validation dependencies for the dataviewer API."""

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Query
from fastapi import Path as PathParam
from pydantic import BaseModel, model_validator

SAFE_DATASET_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,254}$"
SAFE_CAMERA_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$"

_DATASET_ID_RE = re.compile(SAFE_DATASET_ID_PATTERN)
_CAMERA_NAME_RE = re.compile(SAFE_CAMERA_NAME_PATTERN)


def sanitize_user_string(value: str) -> str:
    """Strip CR/LF characters from user-provided strings."""
    return value.replace("\r", "").replace("\n", "")


def _sanitize_nested_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_user_string(value)
    if isinstance(value, list):
        return [_sanitize_nested_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_nested_value(item) for item in value)
    if isinstance(value, set):
        return {_sanitize_nested_value(item) for item in value}
    if isinstance(value, dict):
        return {_sanitize_nested_value(key): _sanitize_nested_value(item) for key, item in value.items()}
    return value


class SanitizedModel(BaseModel):
    """Pydantic base model that strips CR/LF from nested string values."""

    @model_validator(mode="after")
    def sanitize_strings(self) -> "SanitizedModel":
        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, _sanitize_nested_value(getattr(self, field_name)))
        return self


def validate_safe_string(
    value: str,
    *,
    pattern: re.Pattern[str] | str | None = None,
    label: str = "value",
    allow_empty: bool = False,
) -> str:
    """Sanitize and validate a user-provided string value."""
    sanitized = sanitize_user_string(value)
    if "\x00" in sanitized or sanitized in (".", "..") or "/" in sanitized or "\\" in sanitized:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: '{sanitized}'")
    if not allow_empty and not sanitized.strip():
        raise HTTPException(status_code=400, detail=f"{label.capitalize()} cannot be empty")

    compiled_pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
    if compiled_pattern is not None and not compiled_pattern.match(sanitized):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: '{sanitized}'")
    return sanitized


def path_string_param(
    param_name: str,
    *,
    pattern: re.Pattern[str] | str | None = None,
    label: str = "value",
    description: str | None = None,
    allow_empty: bool = False,
) -> Callable[..., str]:
    """Create a validated path-string dependency."""

    def dependency(
        value: str = PathParam(..., alias=param_name, description=description),
    ) -> str:
        return validate_safe_string(value, pattern=pattern, label=label, allow_empty=allow_empty)

    dependency.__name__ = f"path_string_{param_name}"
    return dependency


def query_string_param(
    param_name: str,
    *,
    default: str | None,
    pattern: re.Pattern[str] | str | None = None,
    label: str = "value",
    description: str | None = None,
    allow_empty: bool = False,
) -> Callable[..., str | None]:
    """Create a validated query-string dependency."""

    def dependency(
        value: str | None = Query(default, alias=param_name, description=description),
    ) -> str | None:
        if value is None:
            return None
        return validate_safe_string(value, pattern=pattern, label=label, allow_empty=allow_empty)

    dependency.__name__ = f"query_string_{param_name}"
    return dependency


def path_int_param(
    param_name: str,
    *,
    ge: int | None = None,
    le: int | None = None,
    description: str | None = None,
) -> Callable[..., int]:
    """Create a validated path-integer dependency."""

    def dependency(
        value: int = PathParam(..., alias=param_name, ge=ge, le=le, description=description),
    ) -> int:
        return value

    dependency.__name__ = f"path_int_{param_name}"
    return dependency


def query_int_param(
    param_name: str,
    *,
    default: int | None,
    ge: int | None = None,
    le: int | None = None,
    description: str | None = None,
) -> Callable[..., int | None]:
    """Create a validated query-integer dependency."""

    def dependency(
        value: int | None = Query(default, alias=param_name, ge=ge, le=le, description=description),
    ) -> int | None:
        return value

    dependency.__name__ = f"query_int_{param_name}"
    return dependency


def query_bool_param(
    param_name: str,
    *,
    default: bool | None,
    description: str | None = None,
) -> Callable[..., bool | None]:
    """Create a validated query-boolean dependency."""

    def dependency(
        value: bool | None = Query(default, alias=param_name, description=description),
    ) -> bool | None:
        return value

    dependency.__name__ = f"query_bool_{param_name}"
    return dependency


def _parse_int_csv(raw_value: str, parameter_name: str) -> list[int]:
    """Parse a comma-separated integer list from a sanitized string."""
    sanitized = sanitize_user_string(raw_value)
    items = [item.strip() for item in sanitized.split(",") if item.strip()]
    if not items:
        raise HTTPException(status_code=400, detail=f"{parameter_name} must contain at least one integer")
    try:
        return [int(item) for item in items]
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {parameter_name} format. Use comma-separated integers.",
        ) from exc


def query_csv_ints_param(
    param_name: str,
    *,
    description: str | None = None,
    required: bool = True,
    as_set: bool = False,
) -> Callable[..., list[int] | set[int]]:
    """Create a query dependency for comma-separated integers."""

    default = ... if required else None

    def dependency(
        raw_value: str | None = Query(default, alias=param_name, description=description),
    ) -> list[int] | set[int]:
        if raw_value is None:
            return set() if as_set else []
        parsed = _parse_int_csv(raw_value, param_name)
        return set(parsed) if as_set else parsed

    dependency.__name__ = f"query_csv_ints_{param_name}"
    return dependency


def range_header_param(
    header_name: str = "Range",
) -> Callable[..., tuple[int | None, int | None]]:
    """Create a dependency that parses an HTTP Range header into offset and length."""

    def dependency(
        header_value: str | None = Header(None, alias=header_name),
    ) -> tuple[int | None, int | None]:
        if header_value is None:
            return None, None

        sanitized_header = sanitize_user_string(header_value)
        if not sanitized_header or not sanitized_header.startswith("bytes="):
            return None, None

        range_spec = sanitized_header[6:]
        parts = range_spec.split("-", 1)
        if len(parts) != 2 or not parts[0]:
            raise HTTPException(status_code=400, detail="Invalid Range header")

        try:
            start = int(parts[0])
            if parts[1]:
                end = int(parts[1])
                if end < start:
                    raise HTTPException(status_code=400, detail="Invalid Range header")
                return start, end - start + 1
            return start, None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Range header") from exc

    dependency.__name__ = f"range_header_{header_name.lower()}"
    return dependency


def validate_path_containment(path: Path, base_path: Path) -> Path:
    """Verify a path resolves within the expected base directory.

    Returns the normalized, validated path directly so that static analysis
    tools recognize the normpath+startswith sanitizer pattern on the same
    data-flow value.
    """
    safe_base = os.path.realpath(str(base_path))
    normalized = os.path.normpath(os.path.realpath(str(path)))
    if not normalized.startswith(safe_base + os.sep) and normalized != safe_base:
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: resolved path escapes base directory",
        )
    return Path(normalized)
