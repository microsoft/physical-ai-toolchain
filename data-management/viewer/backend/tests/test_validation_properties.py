"""Property-based tests for request validation and path containment helpers."""

import string
from pathlib import Path

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.api.validation import sanitize_user_string, validate_path_containment, validate_safe_string


@settings(max_examples=200)
@given(st.text())
def test_sanitize_user_string_removes_crlf(value: str):
    sanitized = sanitize_user_string(value)
    assert "\r" not in sanitized
    assert "\n" not in sanitized


@settings(max_examples=200)
@given(
    st.text(alphabet=string.ascii_letters + string.digits + "._-", min_size=1, max_size=64).filter(
        lambda value: value not in {".", ".."}
    )
)
def test_validate_safe_string_accepts_safe_input(value: str):
    assert validate_safe_string(value, label="dataset_id") == value


@settings(max_examples=200)
@given(st.text())
def test_validate_safe_string_rejects_path_separators_or_null(value: str):
    candidate = value + "/"
    with pytest.raises(HTTPException) as exc_info:
        validate_safe_string(candidate, label="dataset_id")
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("candidate", [".", "..", "", "   ", "abc\\def", "abc\x00def"])
def test_validate_safe_string_rejects_known_invalid_values(candidate: str):
    with pytest.raises(HTTPException) as exc_info:
        validate_safe_string(candidate, label="dataset_id")
    assert exc_info.value.status_code == 400


@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    st.lists(
        st.from_regex(r"[a-zA-Z0-9._-]{1,16}", fullmatch=True).filter(lambda segment: segment not in {".", ".."}),
        min_size=1,
        max_size=4,
    )
)
def test_validate_path_containment_accepts_child_paths(tmp_path: Path, segments: list[str]):
    child = tmp_path
    for segment in segments:
        child = child / segment
    child.mkdir(parents=True, exist_ok=True)

    validated = validate_path_containment(child, tmp_path)
    assert validated == child.resolve()


@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.from_regex(r"[a-zA-Z0-9._-]{1,16}", fullmatch=True))
def test_validate_path_containment_rejects_escape_paths(tmp_path: Path, leaf: str):
    escape = tmp_path / ".." / ".." / leaf
    with pytest.raises(HTTPException) as exc_info:
        validate_path_containment(escape, tmp_path)
    assert exc_info.value.status_code == 400


def test_validate_path_containment_rejects_prefix_confusion(tmp_path: Path):
    base = tmp_path / "data"
    imposter = tmp_path / "data-backup" / "nested"
    base.mkdir(parents=True)
    imposter.mkdir(parents=True)

    with pytest.raises(HTTPException) as exc_info:
        validate_path_containment(imposter, base)
    assert exc_info.value.status_code == 400
