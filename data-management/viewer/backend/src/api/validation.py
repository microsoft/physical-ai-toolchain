"""Input validation dependencies for the dataviewer API."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from fastapi import Header, HTTPException, Query
from fastapi import Path as PathParam
from physical_ai_curation.models import JsonInteger as JsonInteger
from physical_ai_curation.models import SanitizedModel as SanitizedModel
from physical_ai_curation.models import sanitize_user_string as sanitize_user_string
from physical_ai_curation.models import validate_json_integer as validate_json_integer
from pydantic import BeforeValidator
from pydantic.json_schema import SkipJsonSchema

__all__ = [
    "SAFE_CAMERA_NAME_PATTERN",
    "SAFE_DATASET_ID_PATTERN",
    "JsonInteger",
    "SanitizedModel",
    "path_int_param",
    "path_string_param",
    "query_bool_param",
    "query_csv_ints_param",
    "query_int_param",
    "query_string_param",
    "range_header_param",
    "sanitize_user_string",
    "validate_json_integer",
    "validate_path_containment",
    "validate_safe_string",
]

# Match Python's whitespace trimming independently of the schema validator's regex engine.
_WHITESPACE_CHARACTERS = r"\t-\r\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
WHITESPACE_PATTERN = rf"[{_WHITESPACE_CHARACTERS}]"
NON_WHITESPACE_PATTERN = rf"[^{_WHITESPACE_CHARACTERS}]"


def _identifier_patterns(tail: str, maximum_tail: int) -> tuple[str, str]:
    """Describe both canonical identifiers and wire values accepted by CR/LF sanitization."""
    canonical = rf"^[a-zA-Z0-9]{tail}{{0,{maximum_tail}}}$"
    wire = rf"^[\r\n]*[a-zA-Z0-9](?:[\r\n]*{tail}){{0,{maximum_tail}}}[\r\n]*$"
    return canonical, wire


SAFE_DATASET_ID_PATTERN, DATASET_ID_INPUT_PATTERN = _identifier_patterns(r"[a-zA-Z0-9._\- ]", 254)
SAFE_CAMERA_NAME_PATTERN, CAMERA_NAME_INPUT_PATTERN = _identifier_patterns(r"[a-zA-Z0-9._-]", 127)

_DATASET_ID_RE = re.compile(SAFE_DATASET_ID_PATTERN)
_CAMERA_NAME_RE = re.compile(SAFE_CAMERA_NAME_PATTERN)
_CSV_WHITESPACE = r"[ \t\r\n\f\v]*"
_CSV_INTEGER = r"[+-]?[\r\n]*[0-9](?:[\r\n]*[0-9])*"
_CSV_INTS_PATTERN = (
    rf"^{_CSV_WHITESPACE}(?:,{_CSV_WHITESPACE})*{_CSV_INTEGER}"
    rf"(?:{_CSV_WHITESPACE},{_CSV_WHITESPACE}(?:{_CSV_INTEGER})?)*{_CSV_WHITESPACE}$"
)
_CSV_INTS_RE = re.compile(_CSV_INTS_PATTERN)
_INTEGER_WHITESPACE = r"[\t-\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*"
_RANGE_DIGITS = r"[\r\n]*[0-9](?:[\r\n]*(?:_[\r\n]*)?[0-9])*"
_RANGE_UNIT = r"[bB][\r\n]*[yY][\r\n]*[tT][\r\n]*[eE][\r\n]*[sS]"
_RANGE_PATTERN = (
    rf"^{WHITESPACE_PATTERN}*{_RANGE_UNIT}{WHITESPACE_PATTERN}*="
    rf"{_INTEGER_WHITESPACE}\+?{_RANGE_DIGITS}{_INTEGER_WHITESPACE}-"
    rf"(?:{_INTEGER_WHITESPACE}[+-]?{_RANGE_DIGITS}{_INTEGER_WHITESPACE}|[\r\n]*)$"
)
_RANGE_RE = re.compile(_RANGE_PATTERN)


def _input_pattern(pattern: re.Pattern[str] | str | None, allow_empty: bool) -> str:
    canonical = pattern.pattern if isinstance(pattern, re.Pattern) else pattern
    wire_patterns = {
        SAFE_DATASET_ID_PATTERN: DATASET_ID_INPUT_PATTERN,
        SAFE_CAMERA_NAME_PATTERN: CAMERA_NAME_INPUT_PATTERN,
    }
    if canonical is not None:
        return wire_patterns.get(canonical, canonical)
    nonempty = "" if allow_empty else rf"(?=[\s\S]*{NON_WHITESPACE_PATTERN})"
    return rf"^(?!(?:[\r\n]*\.){{1,2}}[\r\n]*$){nonempty}[^/\\\x00]*$"


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
    schema_pattern = _input_pattern(pattern, allow_empty)

    def dependency(
        value: str = PathParam(
            ..., alias=param_name, description=description, json_schema_extra={"pattern": schema_pattern}
        ),
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
    schema_pattern = _input_pattern(pattern, allow_empty)

    def dependency(
        value: str | None = Query(
            default, alias=param_name, description=description, json_schema_extra={"pattern": schema_pattern}
        ),
    ) -> str | None:
        if value is None:
            return None
        return validate_safe_string(value, pattern=pattern, label=label, allow_empty=allow_empty)

    dependency.__name__ = f"query_string_{param_name}"
    return dependency


def _validate_integer_parameter(value: object) -> object:
    """Require integer wire text before the existing coercion and bounds checks."""
    if isinstance(value, str):
        try:
            int(value)
        except ValueError as exc:
            raise ValueError("Expected an integer parameter") from exc
    return value


def path_int_param(
    param_name: str,
    *,
    ge: int | None = None,
    le: int | None = None,
    description: str | None = None,
) -> Callable[..., int]:
    """Create a validated path-integer dependency."""
    parameter = PathParam(..., alias=param_name, ge=ge, le=le, description=description)
    parameter.metadata.append(BeforeValidator(_validate_integer_parameter))

    def dependency(
        value: int = parameter,
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
    parameter = Query(default, alias=param_name, ge=ge, le=le, description=description)
    parameter.metadata.append(BeforeValidator(_validate_integer_parameter))

    def dependency(
        value: int | None = parameter,
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
        if _CSV_INTS_RE.fullmatch(raw_value) is None:
            raise ValueError("Expected comma-separated ASCII decimal integers")
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
        raw_value: str = Query(
            default,
            alias=param_name,
            description=description,
            json_schema_extra={"pattern": _CSV_INTS_PATTERN},
        ),
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
        header_value: str | SkipJsonSchema[None] = Header(
            None,
            alias=header_name,
            description="One byte range with a nonnegative start and optional end not less than the start.",
            json_schema_extra={"pattern": _RANGE_PATTERN, "format": "http-byte-range"},
        ),
    ) -> tuple[int | None, int | None]:
        if header_value is None:
            return None, None

        if _RANGE_RE.fullmatch(header_value) is None:
            raise HTTPException(status_code=400, detail="Invalid Range header")

        sanitized_header = sanitize_user_string(header_value)
        parts = sanitized_header.partition("=")[2].split("-", 1)
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
